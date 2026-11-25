import csv
import io
from datetime import date, datetime, timedelta

from sqlalchemy import String, and_, case, cast, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.admin import auth
from app.config import Settings
from app.models.admin_user import AdminUser
from app.models.appointment import Appointment
from app.models.availability import Availability
from app.models.notification_delivery import NotificationDelivery
from app.models.notification_log import NotificationLog
from app.models.payment import Payment
from app.models.product import Product
from app.models.professional import Professional
from app.models.professional_service import ProfessionalService
from app.models.service import Service
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.schemas.appointment import AppointmentCreate, AppointmentUpdate
from app.schemas.user import UserCreate
from app.services.appointment_service import AppointmentService
from app.services.professional_service import ProfessionalManagementService
from app.services.professional_service_offering_service import ProfessionalOfferingService
from app.services.service_service import ServiceCatalogService
from app.services.user_service import UserService

# dias da semana p/ agenda (0=segunda ... 6=domingo)
WEEKDAY_NAMES = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]


class AdminUserService:
    """Gerenciamento das credenciais do painel admin (login real)."""

    def __init__(self, db: Session):
        self.db = db

    def list_admins(self) -> list[AdminUser]:
        return self.db.query(AdminUser).order_by(AdminUser.id).all()

    def get_by_username(self, username: str) -> AdminUser | None:
        return (
            self.db.query(AdminUser)
            .filter(func.lower(AdminUser.username) == username.strip().lower())
            .first()
        )

    def get(self, admin_id: int) -> AdminUser | None:
        return self.db.query(AdminUser).filter(AdminUser.id == admin_id).first()

    def count(self) -> int:
        return self.db.query(func.count(AdminUser.id)).scalar() or 0

    def create(self, username: str, password: str, display_name: str = "Administrador") -> AdminUser:
        if self.get_by_username(username):
            raise ValueError("Já existe um usuário com esse nome.")
        admin = AdminUser(
            username=username.strip(),
            password_hash=auth.hash_password(password),
            display_name=display_name.strip() or "Administrador",
            is_active=True,
        )
        self.db.add(admin)
        self.db.commit()
        self.db.refresh(admin)
        return admin

    def authenticate(self, username: str, password: str) -> AdminUser | None:
        admin = self.get_by_username(username)
        if not admin or not admin.is_active:
            return None
        if not auth.verify_password(password, admin.password_hash):
            return None
        admin.last_login_at = datetime.now()
        self.db.commit()
        return admin

    def update_password(self, admin: AdminUser, new_password: str) -> None:
        admin.password_hash = auth.hash_password(new_password)
        self.db.commit()

    def set_active(self, admin: AdminUser, active: bool) -> None:
        admin.is_active = active
        self.db.commit()

    def delete(self, admin: AdminUser) -> None:
        self.db.delete(admin)
        self.db.commit()

    def update_display_name(self, admin: AdminUser, display_name: str) -> None:
        admin.display_name = (display_name.strip() or "Administrador")
        self.db.commit()


class AdminService:
    def __init__(self, db: Session):
        self.db = db

    def get_system_status(self, settings: Settings) -> dict:
        try:
            self.db.execute(text("SELECT 1"))
            database_status = "connected"
        except SQLAlchemyError:
            self.db.rollback()
            database_status = "unavailable"

        asaas_mode = "sandbox" if "sandbox" in settings.asaas_base_url.lower() else "production"
        asaas_configured = bool(settings.asaas_api_key)
        payment_gateway_status = {
            "provider": "asaas",
            "label": "Gateway Asaas",
            "configured": asaas_configured,
            "mode": asaas_mode,
            "detail": (
                f"Configurado · {'Produção' if asaas_mode == 'production' else 'Sandbox'}"
                if asaas_configured
                else f"Não configurado · {'Produção' if asaas_mode == 'production' else 'Sandbox'}"
            ),
        }

        if database_status == "connected":
            try:
                from app.services.company_settings_service import CompanySettingsService

                cs = CompanySettingsService(self.db).get_or_create_default()
                gw = (cs.default_gateway or "asaas").strip().lower()
                creds = cs.gateway_credentials or {}

                gateway_labels = {
                    "direct_pix": "PIX Direto",
                    "inter": "Banco Inter",
                    "nubank": "Nubank PJ",
                    "itau": "Itaú Empresas",
                    "bradesco": "Bradesco",
                    "banco_do_brasil": "Banco do Brasil",
                    "santander": "Santander",
                    "caixa": "Caixa Econômica",
                    "c6bank": "C6 Bank",
                    "efipay": "Efí Bank",
                    "asaas": "Gateway Asaas",
                    "mercadopago": "Mercado Pago",
                    "pagbank": "PagBank",
                    "stone": "Stone / Pagar.me",
                    "stripe": "Stripe",
                    "manual": "Manual / Balcão",
                    "none": "Sem cobrança",
                }
                base_label = gateway_labels.get(gw, gw.replace("_", " ").title())

                if gw == "asaas":
                    eff_key = (creds.get("api_key") or creds.get("asaas_api_key") or settings.asaas_api_key or "").strip()
                    eff_url = (creds.get("base_url") or creds.get("asaas_base_url") or settings.asaas_base_url or "").lower()
                    eff_mode = "sandbox" if "sandbox" in eff_url else "production"
                    asaas_configured = bool(eff_key)
                    asaas_mode = eff_mode
                    payment_gateway_status = {
                        "provider": gw,
                        "label": base_label,
                        "configured": asaas_configured,
                        "mode": eff_mode,
                        "detail": (
                            f"Configurado · {'Produção' if eff_mode == 'production' else 'Sandbox'}"
                            if asaas_configured
                            else f"Não configurado · {'Produção' if eff_mode == 'production' else 'Sandbox'}"
                        ),
                    }
                elif gw in {"mercadopago", "mercado_pago"}:
                    mp_ok = bool((creds.get("access_token") or "").strip())
                    payment_gateway_status = {
                        "provider": "mercadopago",
                        "label": base_label,
                        "configured": mp_ok,
                        "mode": "production",
                        "detail": "Configurado · Produção" if mp_ok else "Token não configurado",
                    }
                elif gw in {"efipay", "efi", "gerencianet"}:
                    efi_ok = bool((creds.get("client_id") or "").strip() and (creds.get("client_secret") or "").strip())
                    efi_mode = "sandbox" if creds.get("sandbox") else "production"
                    payment_gateway_status = {
                        "provider": "efipay",
                        "label": base_label,
                        "configured": efi_ok,
                        "mode": efi_mode,
                        "detail": (
                            f"Configurado · {'Produção' if efi_mode == 'production' else 'Sandbox'}"
                            if efi_ok
                            else "Credenciais não configuradas"
                        ),
                    }
                elif gw in {"manual", "none", "in_person", "balcao"}:
                    payment_gateway_status = {
                        "provider": gw,
                        "label": base_label,
                        "configured": True,
                        "mode": "local",
                        "detail": "Ativo · Presencial" if gw != "none" else "Desativado",
                    }
                else:
                    pix_ok = bool((creds.get("pix_key") or "").strip() or (creds.get("pix_copy_paste") or "").strip())
                    bank_name = (creds.get("bank_name") or "").strip()
                    display_label = f"PIX Direto · {bank_name}" if gw == "direct_pix" and bank_name else base_label
                    payment_gateway_status = {
                        "provider": gw,
                        "label": display_label,
                        "configured": pix_ok,
                        "mode": "direct_pix",
                        "detail": "Chave PIX ativa" if pix_ok else "Chave PIX pendente",
                    }
            except SQLAlchemyError:
                self.db.rollback()

        return {
            "api": {"status": "online"},
            "database": {"status": database_status},
            "asaas": {"configured": asaas_configured, "mode": asaas_mode},
            "payment_gateway": payment_gateway_status,
            "mcp": {"endpoint_enabled": True},
        }

    def archive_or_delete_user(self, user_id: int) -> str | None:
        user = self.db.get(User, user_id)
        if user is None:
            return None
        has_appointments = self.db.query(Appointment.id).filter(
            Appointment.user_id == user_id
        ).first() is not None
        has_payments = self.db.query(Payment.id).filter(
            Payment.user_id == user_id
        ).first() is not None
        has_history = has_appointments or has_payments
        if has_history:
            user.active = False
            self.db.commit()
            self.db.refresh(user)
            return "archived"
        self.db.delete(user)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError("Cliente possui vínculos e não pode ser excluído") from exc
        return "deleted"

    def reactivate_user(self, user_id: int) -> User | None:
        user = self.db.get(User, user_id)
        if user is None:
            return None
        user.active = True
        self.db.commit()
        self.db.refresh(user)
        return user

    def list_clients(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        status: str = "active",
    ) -> tuple[list[dict], int]:
        appointments_total = select(func.count(Appointment.id)).where(
            Appointment.user_id == User.id
        ).correlate(User).scalar_subquery()
        last_appointment_at = select(func.max(Appointment.start_time)).where(
            Appointment.user_id == User.id
        ).correlate(User).scalar_subquery()
        payments_received_total = select(func.count(Payment.id)).where(
            or_(
                Payment.user_id == User.id,
                Payment.appointment_id.in_(
                    select(Appointment.id).where(Appointment.user_id == User.id)
                ),
            ),
            Payment.status.in_(["received", "confirmed"]),
        ).correlate(User).scalar_subquery()
        query = self.db.query(
            User,
            appointments_total.label("appointments_total"),
            payments_received_total.label("payments_received_total"),
            last_appointment_at.label("last_appointment_at"),
        )
        if status == "active":
            query = query.filter(User.active.is_(True))
        elif status == "archived":
            query = query.filter(User.active.is_(False))
        if search:
            term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    User.name.ilike(term),
                    User.phone.ilike(term),
                    User.email.ilike(term),
                    User.city.ilike(term),
                    User.cpf_cnpj.ilike(term),
                )
            )
        total = query.count()
        rows = query.order_by(User.name, User.id).offset((page - 1) * page_size).limit(page_size).all()
        return [
            {
                "id": user.id,
                "name": user.name,
                "phone": user.phone,
                "email": user.email,
                "whatsapp_number": user.whatsapp_number,
                "postal_code": user.postal_code,
                "city": user.city,
                "state": user.state,
                "address": user.address,
                "address_number": user.address_number,
                "address_complement": user.address_complement,
                "neighborhood": user.neighborhood,
                "delivery_instructions": user.delivery_instructions,
                "preferred_bank": user.preferred_bank,
                "birth_date": user.birth_date,
                "gender": user.gender,
                "tags": user.tags if isinstance(user.tags, list) else [],
                "notes": user.notes,
                "active": user.active,
                "created_at": user.created_at,
                "appointments_total": appointment_count,
                "payments_received_total": payment_count,
                "last_appointment_at": last_at,
            }
            for user, appointment_count, payment_count, last_at in rows
        ], total

    def create_client(self, data) -> User:
        self._ensure_unique_client_contacts(data.phone, data.email)
        user = User(
            name=data.name.strip(),
            phone=data.phone.strip(),
            email=data.email.strip() if data.email else None,
            whatsapp_number=data.whatsapp_number.strip() if data.whatsapp_number else (data.phone.strip() if data.phone else None),
            cpf_cnpj=data.cpf_cnpj.strip() if getattr(data, "cpf_cnpj", None) else None,
            postal_code=data.postal_code.strip() if getattr(data, "postal_code", None) else None,
            city=data.city.strip() if getattr(data, "city", None) else None,
            state=data.state.strip().upper()[:2] if getattr(data, "state", None) else None,
            address=data.address.strip() if getattr(data, "address", None) else None,
            address_number=data.address_number.strip() if getattr(data, "address_number", None) else None,
            address_complement=data.address_complement.strip() if getattr(data, "address_complement", None) else None,
            neighborhood=data.neighborhood.strip() if getattr(data, "neighborhood", None) else None,
            delivery_instructions=data.delivery_instructions.strip() if getattr(data, "delivery_instructions", None) else None,
            preferred_bank=data.preferred_bank.strip() if getattr(data, "preferred_bank", None) else None,
            birth_date=getattr(data, "birth_date", None),
            gender=getattr(data, "gender", None),
            tags=data.tags if isinstance(getattr(data, "tags", None), list) else [],
            notes=data.notes.strip() if getattr(data, "notes", None) else None,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update_client(self, user_id: int, data) -> User | None:
        user = self.db.get(User, user_id)
        if user is None:
            return None
        values = data.model_dump(exclude_unset=True)
        phone = values.get("phone", user.phone)
        email = values.get("email", user.email)
        self._ensure_unique_client_contacts(phone, email, exclude_id=user_id)
        for field, value in values.items():
            if field == "state" and value:
                value = str(value).strip().upper()[:2]
            elif field == "tags":
                if isinstance(value, str):
                    import re
                    value = [t.strip() for t in re.split(r"[,;|]", value) if t.strip()]
                elif not isinstance(value, list):
                    value = []
            elif isinstance(value, str):
                value = value.strip()
            setattr(user, field, value)
        self.db.commit()
        self.db.refresh(user)
        return user

    def _ensure_unique_client_contacts(
        self, phone: str, email: str | None, exclude_id: int | None = None
    ) -> None:
        query = self.db.query(User.id).filter(User.phone == phone.strip())
        if email:
            query = self.db.query(User.id).filter(
                or_(User.phone == phone.strip(), User.email == email.strip())
            )
        if exclude_id is not None:
            query = query.filter(User.id != exclude_id)
        if query.first():
            raise ValueError("Telefone ou e-mail já cadastrado.")

    def get_kpis(self) -> dict:
        today = date.today()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        # Appointments today
        appointments_today = self.db.query(func.count(Appointment.id)).filter(
            func.date(Appointment.start_time) == today
        ).scalar() or 0

        # Appointments by status
        appointments_pending = self.db.query(func.count(Appointment.id)).filter(
            Appointment.status == "pending"
        ).scalar() or 0

        appointments_confirmed = self.db.query(func.count(Appointment.id)).filter(
            Appointment.status == "confirmed"
        ).scalar() or 0

        # Revenue (confirmed + completed appointments with payments received)
        revenue_today = self.db.query(func.coalesce(func.sum(Payment.amount_cents), 0)).join(
            Appointment, Payment.appointment_id == Appointment.id
        ).filter(
            and_(
                func.date(Appointment.start_time) == today,
                Payment.status.in_(["received", "confirmed"])
            )
        ).scalar() or 0

        revenue_week = self.db.query(func.coalesce(func.sum(Payment.amount_cents), 0)).join(
            Appointment, Payment.appointment_id == Appointment.id
        ).filter(
            and_(
                func.date(Appointment.start_time) >= week_ago,
                Payment.status.in_(["received", "confirmed"])
            )
        ).scalar() or 0

        revenue_month = self.db.query(func.coalesce(func.sum(Payment.amount_cents), 0)).join(
            Appointment, Payment.appointment_id == Appointment.id
        ).filter(
            and_(
                func.date(Appointment.start_time) >= month_ago,
                Payment.status.in_(["received", "confirmed"])
            )
        ).scalar() or 0

        # Total revenue received all time
        revenue_total = self.db.query(func.coalesce(func.sum(Payment.amount_cents), 0)).filter(
            Payment.status.in_(["received", "confirmed"])
        ).scalar() or 0

        # Total pending/overdue amount all time
        pending_total = self.db.query(func.coalesce(func.sum(Payment.amount_cents), 0)).filter(
            Payment.status.in_(["pending", "overdue"])
        ).scalar() or 0

        # Payments pending/overdue
        payments_pending = self.db.query(func.count(Payment.id)).filter(
            Payment.status == "pending"
        ).scalar() or 0

        payments_overdue = self.db.query(func.count(Payment.id)).filter(
            Payment.status == "overdue"
        ).scalar() or 0

        # Professionals
        professionals_active = self.db.query(func.count(Professional.id)).filter(
            Professional.active.is_(True)
        ).scalar() or 0

        professionals_total = self.db.query(func.count(Professional.id)).scalar() or 0

        # Users
        users_total = self.db.query(func.count(User.id)).scalar() or 0

        return {
            "appointments_today": appointments_today,
            "appointments_pending": appointments_pending,
            "appointments_confirmed": appointments_confirmed,
            "revenue_today_cents": int(revenue_today),
            "revenue_week_cents": int(revenue_week),
            "revenue_month_cents": int(revenue_month),
            "revenue_total_cents": int(revenue_total),
            "pending_total_cents": int(pending_total),
            "payments_pending": payments_pending,
            "payments_overdue": payments_overdue,
            "professionals_active": professionals_active,
            "professionals_total": professionals_total,
            "users_total": users_total,
        }

    def list_appointments(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        professional_id: int | None = None,
        status: str | None = None,
        search: str | None = None,
        hide_unpaid_cancelled: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        latest_payment_id = (
            select(func.max(Payment.id))
            .where(Payment.appointment_id == Appointment.id)
            .correlate(Appointment)
            .scalar_subquery()
        )
        query = self.db.query(
            Appointment,
            User.name.label("client_name"),
            User.phone.label("client_phone"),
            Professional.name.label("professional_name"),
            Service.name.label("service_name"),
            Service.price_cents.label("service_price_cents"),
            Payment.status.label("payment_status"),
            Payment.id.label("payment_id"),
        ).outerjoin(User, Appointment.user_id == User.id).outerjoin(
            Professional, Appointment.professional_id == Professional.id
        ).outerjoin(Service, Appointment.service_id == Service.id).outerjoin(
            Payment, Payment.id == latest_payment_id
        )

        if hide_unpaid_cancelled:
            query = query.filter(
                or_(
                    Appointment.status != "cancelled",
                    Payment.status.in_(["received", "confirmed", "refunded"]),
                )
            )

        if date_from:
            query = query.filter(func.date(Appointment.start_time) >= date_from)
        if date_to:
            query = query.filter(func.date(Appointment.start_time) <= date_to)
        if professional_id:
            query = query.filter(Appointment.professional_id == professional_id)
        if status:
            query = query.filter(Appointment.status == status)
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    User.name.ilike(search_term),
                    User.phone.ilike(search_term),
                    Professional.name.ilike(search_term),
                    Service.name.ilike(search_term),
                )
            )

        total = query.count()

        query = query.order_by(Appointment.start_time.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        results = query.all()

        appointments = []
        for row in results:
            apt, client_name, client_phone, prof_name, svc_name, svc_price, pay_status, pay_id = row
            appointments.append({
                "id": apt.id,
                "user_id": apt.user_id,
                "professional_id": apt.professional_id,
                "service_id": apt.service_id,
                "start_time": apt.start_time,
                "end_time": apt.end_time,
                "status": apt.status,
                "expires_at": apt.expires_at,
                "notes": apt.notes,
                "created_at": apt.created_at,
                "client_name": client_name,
                "client_phone": client_phone,
                "professional_name": prof_name,
                "service_name": svc_name,
                "service_price_cents": (
                    apt.service_price_cents
                    if apt.service_price_cents is not None
                    else svc_price
                ),
                "payment_status": pay_status,
                "payment_id": pay_id,
            })

        return appointments, total

    def list_payments(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        date_type: str = "payment",
        professional_id: int | None = None,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int, dict]:
        query = self.db.query(
            Payment,
            User.name.label("client_name"),
            User.phone.label("client_phone"),
            Professional.name.label("professional_name"),
            Service.name.label("service_name"),
            Product.name.label("product_name"),
            Appointment.start_time.label("appointment_start"),
        ).outerjoin(Appointment, Payment.appointment_id == Appointment.id).outerjoin(
            User, or_(Payment.user_id == User.id, Appointment.user_id == User.id)
        ).outerjoin(Professional, Appointment.professional_id == Professional.id).outerjoin(
            Service, Appointment.service_id == Service.id
        ).outerjoin(
            Product, Payment.product_id == Product.id
        )

        if date_from:
            if date_type == "appointment":
                query = query.filter(func.date(Appointment.start_time) >= date_from)
            else:
                query = query.filter(func.date(Payment.created_at) >= date_from)

        if date_to:
            if date_type == "appointment":
                query = query.filter(func.date(Appointment.start_time) <= date_to)
            else:
                query = query.filter(func.date(Payment.created_at) <= date_to)

        if professional_id:
            query = query.filter(Appointment.professional_id == professional_id)

        if status:
            if status == "paid":
                query = query.filter(Payment.status.in_(["received", "confirmed"]))
            elif status == "unpaid":
                query = query.filter(Payment.status.in_(["pending", "overdue"]))
            else:
                query = query.filter(Payment.status == status)

        if search:
            clean_search = search.strip().lstrip("#")
            search_term = f"%{clean_search}%"
            query = query.filter(
                or_(
                    User.name.ilike(search_term),
                    User.phone.ilike(search_term),
                    User.email.ilike(search_term),
                    User.cpf_cnpj.ilike(search_term),
                    Professional.name.ilike(search_term),
                    Service.name.ilike(search_term),
                    Product.name.ilike(search_term),
                    Payment.description.ilike(search_term),
                    Payment.asaas_payment_id.ilike(search_term),
                    Payment.billing_type.ilike(search_term),
                    cast(Payment.id, String).ilike(search_term),
                    cast(Appointment.id, String).ilike(search_term),
                )
            )

        summary_query = query.with_entities(
            func.count(Payment.id).label("total_count"),
            func.coalesce(func.sum(Payment.amount_cents), 0).label("total_cents"),
            func.coalesce(
                func.sum(case((Payment.status.in_(["received", "confirmed"]), Payment.amount_cents), else_=0)), 0
            ).label("received_cents"),
            func.coalesce(
                func.sum(case((Payment.status.in_(["received", "confirmed"]), 1), else_=0)), 0
            ).label("received_count"),
            func.coalesce(
                func.sum(case((Payment.status.in_(["pending", "overdue"]), Payment.amount_cents), else_=0)), 0
            ).label("pending_cents"),
            func.coalesce(
                func.sum(case((Payment.status.in_(["pending", "overdue"]), 1), else_=0)), 0
            ).label("pending_count"),
            func.coalesce(
                func.sum(case((Payment.status == "overdue", Payment.amount_cents), else_=0)), 0
            ).label("overdue_cents"),
            func.coalesce(
                func.sum(case((Payment.status == "overdue", 1), else_=0)), 0
            ).label("overdue_count"),
            func.coalesce(
                func.sum(case((Payment.status == "refunded", Payment.amount_cents), else_=0)), 0
            ).label("refunded_cents"),
            func.coalesce(
                func.sum(case((Payment.status == "refunded", 1), else_=0)), 0
            ).label("refunded_count"),
            func.coalesce(
                func.sum(case((Payment.status == "cancelled", Payment.amount_cents), else_=0)), 0
            ).label("cancelled_cents"),
            func.coalesce(
                func.sum(case((Payment.status == "cancelled", 1), else_=0)), 0
            ).label("cancelled_count"),
        ).first()

        total = int(summary_query.total_count or 0) if summary_query else 0
        summary = {
            "total_cents": int(summary_query.total_cents or 0) if summary_query else 0,
            "received_cents": int(summary_query.received_cents or 0) if summary_query else 0,
            "received_count": int(summary_query.received_count or 0) if summary_query else 0,
            "pending_cents": int(summary_query.pending_cents or 0) if summary_query else 0,
            "pending_count": int(summary_query.pending_count or 0) if summary_query else 0,
            "overdue_cents": int(summary_query.overdue_cents or 0) if summary_query else 0,
            "overdue_count": int(summary_query.overdue_count or 0) if summary_query else 0,
            "refunded_cents": int(summary_query.refunded_cents or 0) if summary_query else 0,
            "refunded_count": int(summary_query.refunded_count or 0) if summary_query else 0,
            "cancelled_cents": int(summary_query.cancelled_cents or 0) if summary_query else 0,
            "cancelled_count": int(summary_query.cancelled_count or 0) if summary_query else 0,
        }

        query = query.order_by(Payment.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        results = query.all()

        payments = [self._serialize_payment_row(row) for row in results]

        return payments, total, summary

    def get_payment(self, payment_id: int) -> dict | None:
        row = self.db.query(
            Payment,
            User.name.label("client_name"),
            User.phone.label("client_phone"),
            Professional.name.label("professional_name"),
            Service.name.label("service_name"),
            Product.name.label("product_name"),
            Appointment.start_time.label("appointment_start"),
        ).outerjoin(Appointment, Payment.appointment_id == Appointment.id).outerjoin(
            User, or_(Payment.user_id == User.id, Appointment.user_id == User.id)
        ).outerjoin(Professional, Appointment.professional_id == Professional.id).outerjoin(
            Service, Appointment.service_id == Service.id
        ).outerjoin(
            Product, Payment.product_id == Product.id
        ).filter(Payment.id == payment_id).first()
        return self._serialize_payment_row(row) if row else None

    @staticmethod
    def _serialize_payment_row(row) -> dict:
        pay, client_name, client_phone, prof_name, svc_name, prod_name, apt_start = row
        return {
            "id": pay.id,
            "appointment_id": pay.appointment_id,
            "user_id": pay.user_id,
            "product_id": pay.product_id,
            "description": pay.description,
            "asaas_payment_id": pay.asaas_payment_id,
            "amount_cents": pay.amount_cents,
            "billing_type": pay.billing_type,
            "status": pay.status,
            "invoice_url": pay.invoice_url,
            "received_at": pay.received_at,
            "created_at": pay.created_at,
            "updated_at": pay.updated_at,
            "client_name": client_name,
            "client_phone": client_phone,
            "professional_name": prof_name,
            "service_name": svc_name,
            "product_name": prod_name,
            "appointment_start": apt_start,
        }

    def list_professionals(self) -> list[dict]:
        today = date.today()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        professionals = self.db.query(Professional).all()

        result = []
        for prof in professionals:
            services_count = self.db.query(func.count(ProfessionalService.id)).filter(
                ProfessionalService.professional_id == prof.id,
                ProfessionalService.active.is_(True),
            ).scalar() or 0

            appointments_today = self.db.query(func.count(Appointment.id)).filter(
                and_(
                    Appointment.professional_id == prof.id,
                    func.date(Appointment.start_time) == today
                )
            ).scalar() or 0

            appointments_week = self.db.query(func.count(Appointment.id)).filter(
                and_(
                    Appointment.professional_id == prof.id,
                    func.date(Appointment.start_time) >= week_ago
                )
            ).scalar() or 0

            revenue_month = self.db.query(func.coalesce(func.sum(Payment.amount_cents), 0)).join(
                Appointment, Payment.appointment_id == Appointment.id
            ).filter(
                and_(
                    Appointment.professional_id == prof.id,
                    func.date(Appointment.start_time) >= month_ago,
                    Payment.status.in_(["received", "confirmed"])
                )
            ).scalar() or 0

            result.append({
                "id": prof.id,
                "name": prof.name,
                "phone": prof.phone,
                "email": prof.email,
                "bio": prof.bio,
                "photo_url": prof.photo_url,
                "active": prof.active,
                "services_count": services_count,
                "appointments_today": appointments_today,
                "appointments_week": appointments_week,
                "revenue_month_cents": int(revenue_month),
            })

        return result

    def _catalog_service_response(self, service: Service) -> dict:
        active_offerings_count = (
            self.db.query(func.count(ProfessionalService.id))
            .join(ProfessionalService.professional)
            .filter(
                ProfessionalService.service_id == service.id,
                ProfessionalService.active.is_(True),
                Professional.active.is_(True),
            )
            .scalar()
            or 0
        )
        return {
            "id": service.id,
            "name": service.name,
            "description": service.description,
            "category": service.category,
            "price_cents": service.price_cents,
            "duration_minutes": service.duration_minutes,
            "active": service.active,
            "active_offerings_count": active_offerings_count,
        }

    def list_catalog_services(self) -> list[dict]:
        services = self.db.query(Service).order_by(Service.active.desc(), Service.name).all()
        return [self._catalog_service_response(service) for service in services]

    def get_catalog_service_dashboard(self) -> dict:
        """Return operational health information for the shared service catalog."""
        active_services = (
            self.db.query(Service)
            .filter(Service.active.is_(True))
            .order_by(Service.id)
            .all()
        )
        active_offerings = (
            self.db.query(ProfessionalService)
            .join(ProfessionalService.service)
            .join(ProfessionalService.professional)
            .filter(
                ProfessionalService.active.is_(True),
                Service.active.is_(True),
                Professional.active.is_(True),
            )
            .all()
        )
        offering_service_ids = {offering.service_id for offering in active_offerings}
        normalized_names: dict[str, list[Service]] = {}
        unnamed_ids: list[int] = []

        for service in active_services:
            name = " ".join(service.name.split())
            if not name:
                unnamed_ids.append(service.id)
                continue
            normalized_names.setdefault(name.casefold(), []).append(service)

        issues = []
        if unnamed_ids:
            issues.append(
                {
                    "kind": "unnamed",
                    "label": "Serviço sem denominação",
                    "service_ids": unnamed_ids,
                }
            )
        for _normalized_name, services in sorted(normalized_names.items()):
            if len(services) < 2:
                continue
            label = " ".join(services[0].name.split())
            commercial_groups: dict[tuple[str, int, int], list[int]] = {}
            for service in services:
                identity = (
                    " ".join((service.category or "").split()).casefold(),
                    service.price_cents,
                    service.duration_minutes,
                )
                commercial_groups.setdefault(identity, []).append(service.id)

            for service_ids in commercial_groups.values():
                if len(service_ids) < 2:
                    continue
                issues.append(
                    {
                        "kind": "duplicate",
                        "label": label,
                        "service_ids": service_ids,
                    }
                )
            if len(commercial_groups) > 1:
                issues.append(
                    {
                        "kind": "similar_name",
                        "label": label,
                        "service_ids": [service.id for service in services],
                    }
                )

        return {
            "metrics": {
                "active_services": len(active_services),
                "active_offerings": len(active_offerings),
                "professionals_with_offerings": len(
                    {offering.professional_id for offering in active_offerings}
                ),
                "services_without_professionals": sum(
                    service.id not in offering_service_ids for service in active_services
                ),
                "inconsistencies": len(issues),
            },
            "issues": issues,
            "services": self.list_catalog_services(),
        }

    def create_catalog_service(self, data) -> dict:
        service = ServiceCatalogService(self.db).create(
            name=data.name,
            description=data.description,
            category=data.category,
            price_cents=data.price_cents,
            duration_minutes=data.duration_minutes,
        )
        return self._catalog_service_response(service)

    def update_catalog_service(self, service_id: int, data) -> dict | None:
        values = data.model_dump(exclude_unset=True)
        service = ServiceCatalogService(self.db).update(service_id, **values)
        return self._catalog_service_response(service) if service else None

    def archive_or_delete_catalog_service(self, service_id: int) -> str | None:
        return ServiceCatalogService(self.db).archive_or_delete(service_id)

    def reactivate_catalog_service(self, service_id: int) -> dict | None:
        service = ServiceCatalogService(self.db).reactivate(service_id)
        return self._catalog_service_response(service) if service else None

    @staticmethod
    def _offering_response(offering: ProfessionalService) -> dict:
        return {
            "id": offering.id,
            "professional_id": offering.professional_id,
            "service_id": offering.service_id,
            "price_cents": offering.service.price_cents,
            "duration_minutes": offering.service.duration_minutes,
            "commission_percent": f"{offering.commission_percent:.2f}",
            "active": offering.active,
            "service_name": offering.service.name if offering.service else None,
            "service_description": (
                offering.service.description if offering.service else None
            ),
        }

    def get_professional_management(self, professional_id: int) -> dict | None:
        professional = self.db.get(Professional, professional_id)
        if not professional:
            return None
        offerings = (
            self.db.query(ProfessionalService)
            .filter(ProfessionalService.professional_id == professional_id)
            .order_by(ProfessionalService.id)
            .all()
        )
        availability = (
            self.db.query(Availability)
            .filter(Availability.professional_id == professional_id)
            .order_by(Availability.day_of_week, Availability.start_time)
            .all()
        )
        catalog = (
            self.db.query(Service)
            .filter(Service.active.is_(True))
            .order_by(Service.name)
            .all()
        )
        return {
            "professional": professional,
            "offerings": [self._offering_response(offering) for offering in offerings],
            "availability": availability,
            "catalog": catalog,
        }

    def save_professional_offering(self, professional_id: int, data) -> dict:
        offering = ProfessionalOfferingService(self.db).create_or_update(
            professional_id=professional_id,
            service_id=data.service_id,
            commission_percent=data.commission_percent,
        )
        return self._offering_response(offering)

    def remove_professional_offering(
        self, professional_id: int, service_id: int
    ) -> str | None:
        return ProfessionalOfferingService(self.db).archive_or_delete(
            professional_id, service_id
        )

    def archive_or_delete_professional(self, professional_id: int) -> str | None:
        return ProfessionalManagementService(self.db).archive_or_delete(professional_id)

    def _appointment_response(self, appointment: Appointment) -> dict:
        client = self.db.get(User, appointment.user_id)
        return {
            "id": appointment.id,
            "user_id": appointment.user_id,
            "professional_id": appointment.professional_id,
            "service_id": appointment.service_id,
            "start_time": appointment.start_time,
            "end_time": appointment.end_time,
            "status": appointment.status,
            "notes": appointment.notes,
            "service_price_cents": appointment.service_price_cents,
            "client_name": client.name if client else None,
            "client_phone": client.phone if client else None,
        }

    def create_appointment(self, data) -> dict:
        user_id = data.user_id
        if data.new_client:
            client = UserService(self.db).create(
                UserCreate(**data.new_client.model_dump())
            )
            user_id = client.id
        elif user_id is not None:
            existing_client = self.db.get(User, user_id)
            if existing_client is not None and not existing_client.active:
                raise ValueError(
                    "Cliente arquivado não pode receber novos agendamentos."
                )
        appointment = AppointmentService(self.db).create(
            AppointmentCreate(
                user_id=user_id,
                professional_id=data.professional_id,
                service_id=data.service_id,
                start_time=data.start_time,
                end_time=data.end_time,
                notes=data.notes,
            )
        )
        return self._appointment_response(appointment)

    def update_appointment(self, appointment_id: int, data) -> dict | None:
        appointment = self.db.get(Appointment, appointment_id)
        if not appointment:
            return None
        values = data.model_dump(exclude_unset=True)
        if "user_id" in values:
            client = self.db.get(User, values["user_id"])
            if client is not None and not client.active:
                raise ValueError(
                    "Cliente arquivado não pode receber novos agendamentos."
                )
        booking_fields = {
            "user_id",
            "professional_id",
            "service_id",
            "start_time",
            "end_time",
        }
        appointment_service = AppointmentService(self.db)
        if booking_fields.intersection(values):
            appointment = appointment_service.update_booking(
                appointment_id,
                AppointmentCreate(
                    user_id=values.get("user_id", appointment.user_id),
                    professional_id=values.get(
                        "professional_id", appointment.professional_id
                    ),
                    service_id=values.get("service_id", appointment.service_id),
                    start_time=values.get("start_time", appointment.start_time),
                    end_time=values.get("end_time", appointment.end_time),
                    notes=values.get("notes", appointment.notes),
                ),
            )
        elif "notes" in values:
            appointment = appointment_service.update(
                appointment_id, AppointmentUpdate(notes=values["notes"])
            )
        return self._appointment_response(appointment)

    def delete_appointment(self, appointment_id: int) -> bool:
        return AppointmentService(self.db).delete(appointment_id)

    def appointment_action(
        self, appointment_id: int, action: str, notes: str | None = None
    ) -> dict | None:
        try:
            appointment = AppointmentService(self.db).transition(
                appointment_id, action, notes
            )
        except ValueError as exc:
            return {"error": str(exc)}
        if not appointment:
            return None
        return {
            "id": appointment.id,
            "status": appointment.status,
            "notes": appointment.notes,
        }

    def get_appointment_detail(self, appointment_id: int) -> dict | None:
        latest_payment_id = (
            select(func.max(Payment.id))
            .where(Payment.appointment_id == Appointment.id)
            .correlate(Appointment)
            .scalar_subquery()
        )
        row = self.db.query(
            Appointment,
            User.name.label("client_name"),
            User.phone.label("client_phone"),
            User.email.label("client_email"),
            Professional.name.label("professional_name"),
            Professional.phone.label("professional_phone"),
            Service.name.label("service_name"),
            Service.duration_minutes.label("service_duration"),
            Service.price_cents.label("service_price_cents"),
            Payment.id.label("payment_id"),
            Payment.asaas_payment_id.label("payment_asaas_id"),
            Payment.amount_cents.label("payment_amount"),
            Payment.billing_type.label("payment_type"),
            Payment.status.label("payment_status"),
            Payment.invoice_url.label("payment_invoice_url"),
        ).outerjoin(User, Appointment.user_id == User.id).outerjoin(
            Professional, Appointment.professional_id == Professional.id
        ).outerjoin(Service, Appointment.service_id == Service.id).outerjoin(
            Payment, Payment.id == latest_payment_id
        ).filter(Appointment.id == appointment_id).first()

        if not row:
            return None

        apt, client_name, client_phone, client_email, prof_name, prof_phone, svc_name, svc_duration, svc_price, pay_id, pay_asaas_id, pay_amount, pay_type, pay_status, pay_invoice = row

        return {
            "appointment": {
                "id": apt.id,
                "start_time": apt.start_time,
                "end_time": apt.end_time,
                "status": apt.status,
                "expires_at": apt.expires_at,
                "notes": apt.notes,
                "created_at": apt.created_at,
            },
            "client": {
                "id": apt.user_id,
                "name": client_name,
                "phone": client_phone,
                "email": client_email,
            },
            "professional": {
                "id": apt.professional_id,
                "name": prof_name,
                "phone": prof_phone,
            },
            "service": {
                "id": apt.service_id,
                "name": svc_name,
                "duration_minutes": (
                    apt.service_duration_minutes
                    if apt.service_duration_minutes is not None
                    else svc_duration
                ),
                "price_cents": (
                    apt.service_price_cents
                    if apt.service_price_cents is not None
                    else svc_price
                ),
            },
            "payment": {
                "id": pay_id,
                "asaas_payment_id": pay_asaas_id,
                "amount_cents": pay_amount,
                "billing_type": pay_type,
                "status": pay_status,
                "invoice_url": pay_invoice,
            } if pay_id else None,
        }


    # ------------------------------------------------------------------
    # CRUD: Clientes (User)
    # ------------------------------------------------------------------
    def list_users(self, search=None, page=1, page_size=100):
        q = self.db.query(User)
        if search:
            s = f"%{search}%"
            q = q.filter(or_(User.name.ilike(s), User.phone.ilike(s), User.email.ilike(s)))
        total = q.count()
        rows = q.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def get_user(self, user_id):
        return self.db.query(User).filter(User.id == user_id).first()

    def create_user(self, name, phone, email=None, whatsapp_number=None, cpf_cnpj=None):
        from app.schemas.user import UserCreate
        data = UserCreate(name=name, phone=phone, email=email, whatsapp_number=whatsapp_number, cpf_cnpj=cpf_cnpj)
        user = User(**data.model_dump())
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update_user(self, user, **fields):
        for k, v in fields.items():
            if hasattr(user, k):
                setattr(user, k, v)
        self.db.commit()
        self.db.refresh(user)
        return user

    def delete_user(self, user):
        try:
            self.db.delete(user)
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            return False

    # ------------------------------------------------------------------
    # CRUD: Profissionais (Professional)
    # ------------------------------------------------------------------
    def list_professionals_raw(self, search=None):
        q = self.db.query(Professional)
        if search:
            s = f"%{search}%"
            q = q.filter(or_(Professional.name.ilike(s), Professional.phone.ilike(s)))
        return q.order_by(Professional.name).all()

    def get_professional(self, pid):
        return self.db.query(Professional).filter(Professional.id == pid).first()

    def create_professional(self, name, phone=None, email=None, bio=None, active=True):
        p = Professional(name=name, phone=phone, email=email, bio=bio, active=active)
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        return p

    def update_professional(self, p, **fields):
        for k, v in fields.items():
            if hasattr(p, k):
                setattr(p, k, v)
        self.db.commit()
        self.db.refresh(p)
        return p

    def delete_professional(self, p):
        try:
            self.db.delete(p)
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            return False

    # ------------------------------------------------------------------
    # CRUD: Servicos (Service) e valores
    # ------------------------------------------------------------------
    def list_services_raw(self, professional_id=None):
        q = self.db.query(Service)
        if professional_id:
            q = q.filter(Service.professional_id == professional_id)
        return q.order_by(Service.professional_id, Service.name).all()

    def get_service(self, sid):
        return self.db.query(Service).filter(Service.id == sid).first()

    def create_service(self, professional_id, name, duration_minutes, price_cents, description=None, category=None):
        s = Service(professional_id=professional_id, name=name, duration_minutes=duration_minutes,
                    price_cents=price_cents, description=description, category=category)
        self.db.add(s)
        self.db.commit()
        self.db.refresh(s)
        return s

    def update_service(self, s, **fields):
        for k, v in fields.items():
            if hasattr(s, k):
                setattr(s, k, v)
        self.db.commit()
        self.db.refresh(s)
        return s

    def delete_service(self, s):
        try:
            self.db.delete(s)
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            return False

    # ------------------------------------------------------------------
    # CRUD: Horarios / Disponibilidade (Availability)
    # ------------------------------------------------------------------
    def list_availability_raw(self, professional_id=None):
        q = self.db.query(Availability)
        if professional_id:
            q = q.filter(Availability.professional_id == professional_id)
        return q.order_by(Availability.professional_id, Availability.day_of_week, Availability.start_time).all()

    def get_availability(self, aid):
        return self.db.query(Availability).filter(Availability.id == aid).first()

    def create_availability(self, professional_id, start_time, end_time, day_of_week=None, specific_date=None):
        a = Availability(professional_id=professional_id, start_time=start_time, end_time=end_time,
                         day_of_week=day_of_week, specific_date=specific_date)
        self.db.add(a)
        self.db.commit()
        self.db.refresh(a)
        return a

    def update_availability(self, a, **fields):
        for k, v in fields.items():
            if hasattr(a, k):
                setattr(a, k, v)
        self.db.commit()
        self.db.refresh(a)
        return a

    def delete_availability(self, a):
        try:
            self.db.delete(a)
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            return False

    # ------------------------------------------------------------------
    # Limpeza e Retenção de Dados (Data Retention & Maintenance)
    # ------------------------------------------------------------------
    def _get_maintenance_target_ids(
        self, cutoff_date: datetime, unpaid_cancelled_only: bool = False
    ) -> list[int]:
        """Retorna os IDs de agendamento que se enquadram nos critérios de corte."""
        base_query = self.db.query(Appointment.id).filter(
            Appointment.end_time < cutoff_date
        )
        if unpaid_cancelled_only:
            paid_subquery = (
                select(Payment.appointment_id)
                .where(
                    and_(
                        Payment.appointment_id.isnot(None),
                        Payment.status.in_(["received", "confirmed", "refunded"]),
                    )
                )
                .scalar_subquery()
            )
            base_query = base_query.filter(
                Appointment.status.in_(["cancelled", "pending"]),
                Appointment.id.notin_(paid_subquery),
            )
        return [row[0] for row in base_query.all()]

    def preview_maintenance(
        self, cutoff_date: datetime, unpaid_cancelled_only: bool = False
    ) -> dict:
        target_ids = self._get_maintenance_target_ids(cutoff_date, unpaid_cancelled_only)
        total_appointments = len(target_ids)

        if total_appointments == 0:
            return {
                "cutoff_date": cutoff_date.isoformat(),
                "unpaid_cancelled_only": unpaid_cancelled_only,
                "appointments_count": 0,
                "completed_count": 0,
                "cancelled_unpaid_count": 0,
                "payments_count": 0,
                "notifications_count": 0,
                "estimated_kb_freed": 0.0,
            }

        completed_count = (
            self.db.query(func.count(Appointment.id))
            .filter(Appointment.id.in_(target_ids), Appointment.status.in_(["completed", "confirmed"]))
            .scalar()
            or 0
        )
        cancelled_unpaid_count = total_appointments - completed_count

        payments_count = (
            self.db.query(func.count(Payment.id))
            .filter(Payment.appointment_id.in_(target_ids))
            .scalar()
            or 0
        )

        notif_deliveries_count = (
            self.db.query(func.count(NotificationDelivery.id))
            .filter(NotificationDelivery.appointment_id.in_(target_ids))
            .scalar()
            or 0
        )

        notif_logs_count = (
            self.db.query(func.count(NotificationLog.id))
            .filter(NotificationLog.appointment_id.in_(target_ids))
            .scalar()
            or 0
        )

        total_notifs = notif_deliveries_count + notif_logs_count
        estimated_kb = (total_appointments * 1.5) + (payments_count * 0.8) + (total_notifs * 0.5)

        return {
            "cutoff_date": cutoff_date.isoformat(),
            "unpaid_cancelled_only": unpaid_cancelled_only,
            "appointments_count": total_appointments,
            "completed_count": completed_count,
            "cancelled_unpaid_count": cancelled_unpaid_count,
            "payments_count": payments_count,
            "notifications_count": total_notifs,
            "estimated_kb_freed": round(estimated_kb, 1),
        }

    def export_maintenance_csv(
        self, cutoff_date: datetime, unpaid_cancelled_only: bool = False
    ) -> str:
        target_ids = self._get_maintenance_target_ids(cutoff_date, unpaid_cancelled_only)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID Agendamento",
            "Data Início",
            "Data Fim",
            "Status",
            "Cliente Nome",
            "Cliente Telefone",
            "Cliente Email",
            "Profissional",
            "Serviço",
            "Valor (R$)",
            "ID Pagamento",
            "Status Pagamento",
            "ID Gateway",
            "Data Pagamento",
            "Observações",
        ])

        if not target_ids:
            return output.getvalue()

        latest_payment_id = (
            select(func.max(Payment.id))
            .where(Payment.appointment_id == Appointment.id)
            .correlate(Appointment)
            .scalar_subquery()
        )
        query = (
            self.db.query(
                Appointment,
                User.name.label("client_name"),
                User.phone.label("client_phone"),
                User.email.label("client_email"),
                Professional.name.label("professional_name"),
                Service.name.label("service_name"),
                Payment.id.label("payment_id"),
                Payment.status.label("payment_status"),
                Payment.asaas_payment_id.label("asaas_id"),
                Payment.amount_cents.label("payment_amount_cents"),
                Payment.received_at.label("payment_received_at"),
            )
            .outerjoin(User, Appointment.user_id == User.id)
            .outerjoin(Professional, Appointment.professional_id == Professional.id)
            .outerjoin(Service, Appointment.service_id == Service.id)
            .outerjoin(Payment, Payment.id == latest_payment_id)
            .filter(Appointment.id.in_(target_ids))
            .order_by(Appointment.start_time.asc())
        )

        def _csv_safe(val: object) -> str:
            text = "" if val is None else str(val)
            if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
                return "'" + text
            return text

        for row in query.all():
            apt = row.Appointment
            price_reais = ""
            amount = row.payment_amount_cents or apt.service_price_cents
            if amount is not None:
                price_reais = f"{(amount / 100):.2f}".replace(".", ",")

            writer.writerow([
                apt.id,
                apt.start_time.strftime("%d/%m/%Y %H:%M") if apt.start_time else "",
                apt.end_time.strftime("%d/%m/%Y %H:%M") if apt.end_time else "",
                apt.status,
                _csv_safe(row.client_name or ""),
                _csv_safe(row.client_phone or ""),
                _csv_safe(row.client_email or ""),
                _csv_safe(row.professional_name or ""),
                _csv_safe(row.service_name or ""),
                price_reais,
                row.payment_id or "",
                row.payment_status or "",
                _csv_safe(row.asaas_id or ""),
                row.payment_received_at.strftime("%d/%m/%Y %H:%M") if row.payment_received_at else "",
                _csv_safe((apt.notes or "").replace("\n", " ")),
            ])

        return output.getvalue()

    def execute_maintenance_purge(
        self, cutoff_date: datetime, unpaid_cancelled_only: bool = False, run_vacuum: bool = True
    ) -> dict:
        target_ids = self._get_maintenance_target_ids(cutoff_date, unpaid_cancelled_only)
        if not target_ids:
            return {
                "status": "success",
                "deleted_appointments": 0,
                "deleted_payments": 0,
                "deleted_notifications": 0,
                "deleted_webhooks": 0,
                "vacuum_executed": False,
                "message": "Nenhum agendamento encontrado para o período especificado.",
            }

        try:
            # 1. Purgar notification_deliveries vinculadas
            del_deliveries = (
                self.db.query(NotificationDelivery)
                .filter(NotificationDelivery.appointment_id.in_(target_ids))
                .delete(synchronize_session=False)
            )

            # 2. Purgar notification_log
            del_logs = (
                self.db.query(NotificationLog)
                .filter(NotificationLog.appointment_id.in_(target_ids))
                .delete(synchronize_session=False)
            )

            # 3. Purgar payments vinculados
            del_payments = (
                self.db.query(Payment)
                .filter(Payment.appointment_id.in_(target_ids))
                .delete(synchronize_session=False)
            )

            # 4. Purgar appointments
            del_appointments = (
                self.db.query(Appointment)
                .filter(Appointment.id.in_(target_ids))
                .delete(synchronize_session=False)
            )

            # 5. Purgar webhooks antigos
            del_webhooks = (
                self.db.query(WebhookEvent)
                .filter(WebhookEvent.received_at < cutoff_date)
                .delete(synchronize_session=False)
            )

            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise RuntimeError(f"Erro durante a limpeza de dados: {e!s}") from e

        vacuum_executed = False
        if run_vacuum:
            try:
                from sqlalchemy.engine import Engine

                bind = self.db.get_bind()
                dialect = bind.dialect.name if bind is not None else ""
                if dialect == "postgresql" and bind is not None:
                    engine = bind.engine if not isinstance(bind, Engine) else bind
                    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                        conn.execute(text("VACUUM ANALYZE"))
                    vacuum_executed = True
                elif dialect == "sqlite":
                    self.db.execute(text("VACUUM"))
                    self.db.commit()
                    vacuum_executed = True
            except Exception:
                vacuum_executed = False

        total_notifs = del_deliveries + del_logs
        return {
            "status": "success",
            "deleted_appointments": del_appointments,
            "deleted_payments": del_payments,
            "deleted_notifications": total_notifs,
            "deleted_webhooks": del_webhooks,
            "vacuum_executed": vacuum_executed,
            "message": f"Limpeza concluída com sucesso! {del_appointments} agendamentos, {del_payments} pagamentos e {total_notifs} registros de notificação foram expurgados.",
        }
