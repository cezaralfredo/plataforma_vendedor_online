from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.availability import Availability
from app.models.payment import Payment
from app.models.professional import Professional
from app.models.professional_service import ProfessionalService as ProfessionalOffering
from app.repositories import ProfessionalRepository
from app.repositories.base import RelatedRecordsError
from app.schemas.professional import ProfessionalCreate, ProfessionalUpdate


class ProfessionalService:
    def __init__(self, db: Session):
        self.repo = ProfessionalRepository(db)

    def create(self, data: ProfessionalCreate):
        return self.repo.create(**data.model_dump())

    def get(self, professional_id: int):
        return self.repo.get(professional_id)

    def list(self, skip: int = 0, limit: int = 100, active_only: bool = False):
        if active_only:
            return self.repo.list_active()
        return self.repo.list(skip=skip, limit=limit)

    list_professionals = list

    def update(self, professional_id: int, data: ProfessionalUpdate):
        return self.repo.update(
            professional_id, **data.model_dump(exclude_unset=True)
        )

    def delete(self, professional_id: int):
        has_history = (
            self.repo.db.query(Appointment.id)
            .filter(Appointment.professional_id == professional_id)
            .first()
        )
        if has_history:
            raise RelatedRecordsError(
                "Profissional possui histórico; desative-o em vez de excluir"
            )
        return self.repo.delete(professional_id)


class ProfessionalManagementService:
    """Applies safe archival rules to professional records."""

    def __init__(self, db: Session):
        self.db = db

    def archive_or_delete(self, professional_id: int) -> str | None:
        professional = self.db.get(Professional, professional_id)
        if not professional:
            return None

        has_appointments = self.db.query(Appointment.id).filter(
            Appointment.professional_id == professional_id
        ).first()
        has_payments = self.db.query(Payment.id).join(Appointment).filter(
            Appointment.professional_id == professional_id
        ).first()
        has_offerings = self.db.query(ProfessionalOffering.id).filter(
            ProfessionalOffering.professional_id == professional_id
        ).first()
        has_availability = self.db.query(Availability.id).filter(
            Availability.professional_id == professional_id
        ).first()

        if (
            has_appointments
            or has_payments
            or has_offerings
            or has_availability
        ):
            professional.active = False
            for offering in professional.service_offerings:
                offering.active = False
            self.db.commit()
            return "archived"

        self.db.delete(professional)
        self.db.commit()
        return "deleted"
