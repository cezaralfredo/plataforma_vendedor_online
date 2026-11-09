from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.business_time import as_business_time
from app.models.appointment import Appointment
from app.models.notification_log import NotificationLog
from app.models.payment import Payment
from app.models.professional import Professional
from app.models.professional_service import ProfessionalService
from app.models.service import Service
from app.models.user import User
from app.repositories import AppointmentRepository
from app.schemas.appointment import AppointmentCreate, AppointmentUpdate
from app.services.availability_service import AvailabilityService
from app.services.payment_state_service import _is_expired


class AppointmentService:
    def __init__(self, db: Session):
        self.repo = AppointmentRepository(db)
        self.availability_service = AvailabilityService(db)

    def _expire_pending(self) -> None:
        self.repo.expire_reservations(datetime.now(UTC))
        self.repo.db.commit()

    def create(self, data: AppointmentCreate):
        now = datetime.now(UTC)
        self._expire_pending()
        start_time, end_time, offering = self._validate_booking(data)
        from app.models.company_settings import CompanySettings

        settings = self.repo.db.query(CompanySettings).first()
        payment_required = settings.payment_required if settings is not None else True

        initial_status = "pending" if payment_required else "confirmed"
        expires_at = (now + timedelta(minutes=30)) if payment_required else None

        try:
            return self.repo.create(
                **data.model_dump(exclude={"start_time", "end_time"}),
                start_time=start_time,
                end_time=end_time,
                status=initial_status,
                expires_at=expires_at,
                created_at=now,
                service_price_cents=offering.service.price_cents,
                service_duration_minutes=offering.service.duration_minutes,
                professional_commission_percent=offering.commission_percent,
            )
        except IntegrityError as exc:
            self.repo.db.rollback()
            constraint_name = getattr(
                getattr(exc.orig, "diag", None), "constraint_name", None
            )
            if constraint_name == "exclude_professional_overlapping_appointments":
                raise ValueError("Já existe uma reserva neste horário") from exc
            raise

    def _validate_booking(
        self, data: AppointmentCreate, exclude_appointment_id: int | None = None
    ) -> tuple[datetime, datetime, ProfessionalService]:
        client = self.repo.db.get(User, data.user_id)
        if not client:
            raise ValueError("Cliente não encontrado")
        if not client.active:
            raise ValueError("Cliente arquivado não pode receber novos agendamentos.")
        professional = self.repo.db.get(Professional, data.professional_id)
        if not professional or not professional.active:
            raise ValueError("Profissional não encontrado ou inativo")
        service = self.repo.db.get(Service, data.service_id)
        if not service or not service.active:
            raise ValueError("Serviço indisponível")
        offering = (
            self.repo.db.query(ProfessionalService)
            .filter(
                ProfessionalService.professional_id == data.professional_id,
                ProfessionalService.service_id == data.service_id,
                ProfessionalService.active.is_(True),
            )
            .first()
        )
        if not offering:
            raise ValueError("Serviço indisponível para o profissional informado")

        start_time = as_business_time(data.start_time)
        end_time = as_business_time(data.end_time)
        expected_end = start_time + timedelta(
            minutes=offering.service.duration_minutes
        )
        if end_time != expected_end:
            raise ValueError(
                "A duração da reserva deve corresponder à duração do serviço"
            )
        if not self.availability_service.is_interval_available(
            data.professional_id, start_time, end_time
        ):
            raise ValueError(
                "O horário solicitado está fora da disponibilidade do profissional"
            )
        conflicts = self.repo.find_conflicting(
            data.professional_id,
            start_time,
            end_time,
            exclude_id=exclude_appointment_id,
        )
        if conflicts:
            raise ValueError("Já existe uma reserva neste horário")
        return start_time, end_time, offering

    def update_booking(self, appointment_id: int, data: AppointmentCreate):
        appointment = self.get(appointment_id)
        if not appointment:
            return None
        if appointment.status != "pending":
            raise ValueError("Somente reservas pendentes podem ser alteradas")
        if self.repo.db.query(Payment.id).filter(
            Payment.appointment_id == appointment_id
        ).first():
            raise ValueError("Não é possível alterar um agendamento com pagamento")

        start_time, end_time, offering = self._validate_booking(
            data, exclude_appointment_id=appointment_id
        )
        appointment.user_id = data.user_id
        appointment.professional_id = data.professional_id
        appointment.service_id = data.service_id
        appointment.start_time = start_time
        appointment.end_time = end_time
        appointment.notes = data.notes
        appointment.service_price_cents = offering.service.price_cents
        appointment.service_duration_minutes = offering.service.duration_minutes
        appointment.professional_commission_percent = offering.commission_percent
        self.repo.db.commit()
        self.repo.db.refresh(appointment)
        return appointment

    def get(self, appointment_id: int):
        self._expire_pending()
        return self.repo.get(appointment_id)

    def list(
        self,
        user_id: int | None = None,
        professional_id: int | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ):
        self._expire_pending()
        if user_id is not None:
            return self.repo.list_by_user(user_id)
        if professional_id is not None:
            return self.repo.list_by_professional(professional_id)
        if status is not None:
            return self.repo.list_by_status(status)
        return self.repo.list(skip=skip, limit=limit)

    def update(self, appointment_id: int, data: AppointmentUpdate):
        values = data.model_dump(exclude_unset=True)
        if "status" in values:
            raise ValueError("Use as ações para alterar o status do agendamento")
        return self.repo.update(appointment_id, **values)

    def cancel(self, appointment_id: int):
        return self.transition(appointment_id, "cancel")

    cancel_appointment = cancel

    def confirm(self, appointment_id: int):
        return self.transition(appointment_id, "confirm")

    def transition(
        self, appointment_id: int, action: str, notes: str | None = None
    ):
        appointment = self.get(appointment_id)
        if not appointment:
            return None
        if action == "confirm" and appointment.status == "confirmed":
            return appointment

        transitions = {
            "confirm": ({"pending"}, "confirmed", "Confirmado"),
            "cancel": ({"pending", "confirmed", "awaiting_payment"}, "cancelled", "Cancelado"),
            "complete": ({"confirmed"}, "completed", "Concluído"),
        }
        if action not in transitions:
            raise ValueError("Ação de agendamento inválida")
        allowed_statuses, next_status, label = transitions[action]
        if appointment.status not in allowed_statuses:
            raise ValueError("Esta ação não é permitida para o status atual")
        if (
            action == "confirm"
            and appointment.expires_at
            and _is_expired(appointment.expires_at, datetime.now(UTC))
        ):
            appointment.status = "cancelled"
            self.repo.db.commit()
            raise ValueError("A reserva expirou")

        if action == "confirm":
            payment_received = (
                self.repo.db.query(Payment.id)
                .filter(
                    Payment.appointment_id == appointment_id,
                    Payment.status.in_({"received", "confirmed"}),
                )
                .first()
            )
            if not payment_received:
                raise ValueError(
                    "O agendamento só pode ser confirmado após o pagamento"
                )

        appointment.status = next_status
        if notes:
            appointment.notes = f"{appointment.notes or ''}\n[Admin] {label}: {notes}".strip()
        self.repo.db.commit()
        self.repo.db.refresh(appointment)
        return appointment

    def delete(self, appointment_id: int):
        db = self.repo.db
        appointment = db.query(Appointment).filter(
            Appointment.id == appointment_id
        ).with_for_update().first()
        if not appointment:
            return False
        if appointment.status in {"confirmed", "completed"}:
            raise ValueError(
                "Não é possível excluir um agendamento confirmado ou concluído"
            )
        payments = db.query(Payment).filter(
            Payment.appointment_id == appointment_id
        ).with_for_update().all()
        if any(
            payment.status not in {"overdue", "cancelled"}
            or payment.received_at is not None
            for payment in payments
        ):
            raise ValueError(
                "Não é possível excluir: há pagamento pendente ou histórico financeiro. "
                "Somente cobranças vencidas ou canceladas, sem recebimento, permitem exclusão."
            )
        try:
            db.query(NotificationLog).filter(
                NotificationLog.appointment_id == appointment_id
            ).delete(synchronize_session="fetch")
            db.query(Payment).filter(
                Payment.appointment_id == appointment_id
            ).delete(synchronize_session="fetch")
            db.query(Appointment).filter(
                Appointment.id == appointment_id
            ).delete(synchronize_session="fetch")
            db.commit()
        except Exception:
            db.rollback()
            raise
        return True
