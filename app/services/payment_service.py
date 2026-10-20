import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, joinedload

from app.models.appointment import Appointment
from app.models.payment import Payment
from app.repositories import UserRepository
from app.repositories.appointment_repo import AppointmentRepository
from app.repositories.payment_repo import PaymentRepository
from app.services.asaas_client import (
    AsaasClient,
    AsaasIntegrationError,
    AsaasNotFoundError,
    AsaasUncertainResultError,
)
from app.services.payment_state_service import apply_payment_state
from app.utils.sanitizers import clean_digits

logger = logging.getLogger(__name__)

ASAAS_STATUS_MAP = {
    "PENDING": "pending",
    "RECEIVED": "received",
    "CONFIRMED": "confirmed",
    "OVERDUE": "overdue",
    "REFUNDED": "refunded",
    "CANCELLED": "cancelled",
}


class AsaasReconciliationError(AsaasIntegrationError):
    """Remote records cannot be mapped safely to one local entity."""


class PaymentService:
    def __init__(self, db: Session):
        self.db = db
        self.asaas = AsaasClient()
        self.user_repo = UserRepository(db)
        self.appointment_repo = AppointmentRepository(db)
        self.payment_repo = PaymentRepository(db)

    async def ensure_asaas_customer(self, user_id: int) -> str:
        user = self.user_repo.get(user_id)
        if not user:
            raise ValueError("Usuário não encontrado")

        user_cpf = clean_digits(getattr(user, "cpf_cnpj", None))

        if user.asaas_customer_id:
            return user.asaas_customer_id

        external_reference = f"user:{user.id}"
        matches = await self.asaas.list_customers(external_reference)
        customer = self._single_remote_match(matches, external_reference)
        if customer is None:
            try:
                customer = await self.asaas.create_customer(
                    name=user.name,
                    phone=clean_digits(user.phone),
                    email=user.email,
                    cpf_cnpj=user_cpf,
                    external_reference=external_reference,
                )
            except AsaasUncertainResultError:
                matches = await self.asaas.list_customers(external_reference)
                customer = self._single_remote_match(matches, external_reference)
                if customer is None:
                    raise

        customer_id = customer.get("id")
        if not customer_id:
            raise AsaasReconciliationError(
                f"Asaas customer {external_reference} has no id"
            )
        user.asaas_customer_id = customer_id
        if user_cpf and not customer.get("cpfCnpj"):
            try:
                await self.asaas.update_customer(customer_id, cpf_cnpj=user_cpf)
            except Exception as e:
                logger.warning("Could not sync CPF to Asaas customer %s: %s", customer_id, e)
        self.db.flush()
        return customer_id

    @staticmethod
    def _single_remote_match(
        matches: list[dict],
        external_reference: str,
    ) -> dict | None:
        if len(matches) > 1:
            raise AsaasReconciliationError(
                f"Asaas returned multiple records for {external_reference}"
            )
        return matches[0] if matches else None

    @staticmethod
    def _appointment_price_cents(appointment: Appointment) -> int:
        snapshot = getattr(appointment, "service_price_cents", None)
        if snapshot is not None and snapshot >= 500:
            return snapshot
        if not appointment.service:
            raise ValueError("Agendamento sem serviço")
        return appointment.service.price_cents

    @staticmethod
    def _payment_from_asaas(
        appointment: Appointment,
        payload: dict,
        billing_type: str,
    ) -> Payment:
        payment_id = payload.get("id")
        if not payment_id:
            raise AsaasReconciliationError("Asaas payment has no id")
        remote_status = payload.get("status")
        return Payment(
            appointment_id=appointment.id,
            asaas_payment_id=payment_id,
            amount_cents=PaymentService._appointment_price_cents(appointment),
            billing_type=billing_type,
            status=(
                ASAAS_STATUS_MAP.get(remote_status, "pending")
                if isinstance(remote_status, str)
                else "pending"
            ),
            invoice_url=payload.get("invoiceUrl"),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    def _locked_appointment_query(self, appointment_id: int):
        return (
            self.db.query(Appointment)
            .options(
                joinedload(Appointment.service),
                joinedload(Appointment.professional),
                joinedload(Appointment.user),
            )
            .filter(Appointment.id == appointment_id)
            .with_for_update(of=Appointment)
        )

    async def create_charge(
        self,
        appointment_id: int,
        billing_type: str = "undefined",
        amount_cents: int | None = None,
    ) -> Payment:
        self.appointment_repo.expire_reservations(datetime.now(UTC))
        self.db.flush()

        # Fast path: check for existing active payment locally
        existing = self.payment_repo.list_by_appointment(appointment_id)
        active_payment = next(
            (payment for payment in existing if payment.status in {"pending", "received", "confirmed"}),
            None,
        )
        if active_payment:
            return active_payment

        # Pre-check appointment without holding pessimistic row locks during external network calls
        appointment = (
            self.db.query(Appointment)
            .options(
                joinedload(Appointment.service),
                joinedload(Appointment.professional),
                joinedload(Appointment.user),
            )
            .filter(Appointment.id == appointment_id)
            .first()
        )

        if not appointment:
            raise ValueError("Agendamento não encontrado")

        if appointment.status in {"cancelled", "completed"}:
            raise ValueError("Cannot charge a cancelled or completed appointment")

        billing_type = billing_type.lower()
        asaas_billing_type = billing_type.upper()
        if asaas_billing_type not in {"PIX", "BOLETO", "CREDIT_CARD", "UNDEFINED"}:
            raise ValueError("Invalid billing type")

        value_cents = self._appointment_price_cents(appointment)
        if hasattr(appointment, "service_price_cents") and appointment.service_price_cents != value_cents:
            appointment.service_price_cents = value_cents
            self.db.flush()
        if amount_cents is not None and amount_cents != value_cents:
            raise ValueError("Charge amount must match the service price")

        from app.services.company_settings_service import CompanySettingsService
        from app.services.payment_gateways import ChargeRequest, get_payment_gateway

        company_settings = CompanySettingsService(self.db).get_or_create_default()
        active_gateway = (company_settings.default_gateway or "asaas").lower().strip()

        if active_gateway != "asaas":
            gateway = get_payment_gateway(active_gateway, company_settings.gateway_credentials)
            user = appointment.user
            charge_desc = (
                f"{appointment.service.name} - {appointment.professional.name}"
                if (appointment.service and appointment.professional)
                else f"Agendamento #{appointment.id}"
            )
            req = ChargeRequest(
                amount_cents=value_cents,
                user_id=appointment.user_id,
                customer_name=user.name if user else "Cliente",
                customer_phone=getattr(user, "phone", None),
                customer_email=getattr(user, "email", None),
                customer_cpf=getattr(user, "cpf_cnpj", None),
                description=charge_desc,
                billing_type=billing_type,
                order_ref=f"appt:{appointment.id}",
                appointment_id=appointment.id,
            )
            result = await gateway.create_charge(req)

            locked_appointment = self._locked_appointment_query(appointment_id).first()
            if not locked_appointment:
                raise ValueError("Agendamento não encontrado")

            payment = Payment(
                appointment_id=appointment.id,
                user_id=appointment.user_id,
                asaas_payment_id=result.gateway_payment_id,
                amount_cents=value_cents,
                billing_type=billing_type,
                status=result.status,
                invoice_url=result.invoice_url or result.pix_copy_paste,
            )
            self.db.add(payment)
            locked_appointment.status = (
                "confirmed"
                if payment.status in {"received", "confirmed"}
                else "awaiting_payment"
            )
            if locked_appointment.status == "awaiting_payment":
                locked_appointment.expires_at = datetime.now(UTC) + timedelta(hours=24)
            self.db.commit()
            self.db.refresh(payment)
            return payment

        external_reference = f"appointment:{appointment.id}"
        matches = await self.asaas.list_payments(
            external_reference=external_reference
        )
        remote_payment = self._single_remote_match(matches, external_reference)
        if remote_payment is None:
            customer_id = await self.ensure_asaas_customer(appointment.user_id)
            due_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                remote_payment = await self.asaas.create_payment(
                    customer_id=customer_id,
                    value=value_cents / 100.0,
                    due_date=due_date,
                    description=(
                        f"{appointment.service.name} - {appointment.professional.name}"
                    ),
                    billing_type=asaas_billing_type,
                    external_reference=external_reference,
                )
            except AsaasUncertainResultError:
                matches = await self.asaas.list_payments(
                    external_reference=external_reference
                )
                remote_payment = self._single_remote_match(
                    matches, external_reference
                )
                if remote_payment is None:
                    raise

        # Now acquire row lock to commit the payment record locally in milliseconds
        locked_appointment = self._locked_appointment_query(appointment_id).first()
        if not locked_appointment:
            raise ValueError("Agendamento não encontrado")

        # Double check if someone created a payment concurrently
        existing_now = self.payment_repo.list_by_appointment(appointment_id)
        concurrent_payment = next(
            (p for p in existing_now if p.status in {"pending", "received", "confirmed"}),
            None,
        )
        if concurrent_payment:
            return concurrent_payment

        payment = self._payment_from_asaas(
            locked_appointment,
            remote_payment,
            billing_type,
        )
        self.db.add(payment)
        locked_appointment.status = (
            "confirmed"
            if payment.status in {"received", "confirmed"}
            else "awaiting_payment"
        )
        if locked_appointment.status == "awaiting_payment":
            locked_appointment.expires_at = datetime.now(UTC) + timedelta(hours=24)
        self.db.commit()
        self.db.refresh(payment)
        return payment

    async def check_payment_status(self, payment: Payment) -> str:
        if not payment.asaas_payment_id:
            return payment.status

        try:
            resp = await self.asaas.get_payment(payment.asaas_payment_id)
            is_deleted = resp.get("deleted") is True
            asaas_status = resp.get("status", "")
        except AsaasNotFoundError:
            resp = None
            is_deleted = True
            asaas_status = "CANCELLED"

        if is_deleted:
            new_status = "cancelled"
        else:
            new_status = ASAAS_STATUS_MAP.get(asaas_status, payment.status)

        if new_status != payment.status:
            apply_payment_state(payment, new_status, datetime.now(UTC))
            if (
                new_status in {"received", "confirmed"}
                and payment.appointment.status == "confirmed"
            ):
                # A reconciliação também precisa alimentar a mesma outbox do webhook.
                from app.services.notification_service import NotificationService

                NotificationService(self.db).queue_payment_confirmed(
                    payment.appointment_id, payment.id
                )
            self.db.commit()

        # Se a fatura foi excluída/cancelada, verificar se o cliente associado também foi excluído no Asaas
        if (
            is_deleted
            and payment.appointment
            and payment.appointment.user
            and payment.appointment.user.asaas_customer_id
        ):
            user = payment.appointment.user
            try:
                cust_resp = await self.asaas.get_customer(user.asaas_customer_id)
                if cust_resp.get("deleted") is True:
                    user.asaas_customer_id = None
                    self.db.commit()
            except AsaasNotFoundError:
                user.asaas_customer_id = None
                self.db.commit()
            except Exception:
                logger.warning(
                    "Não foi possível verificar status do cliente %s no Asaas",
                    user.id,
                )

        return new_status

    async def refresh(self, payment_id: int) -> Payment:
        payment = self.db.query(Payment).filter(Payment.id == payment_id).first()
        if not payment:
            raise ValueError("Pagamento não encontrado")
        await self.check_payment_status(payment)
        self.db.refresh(payment)
        return payment

    async def refund(self, payment_id: int) -> Payment:
        payment = self.db.query(Payment).filter(Payment.id == payment_id).first()
        if not payment:
            raise ValueError("Pagamento não encontrado")
        if payment.status not in {"received", "confirmed"}:
            raise ValueError(
                "Só é possível estornar pagamentos recebidos/confirmados"
            )
        if not payment.asaas_payment_id:
            raise ValueError("Pagamento sem identificador do Asaas")

        await self.asaas.refund_payment(payment.asaas_payment_id)
        apply_payment_state(payment, "refunded", datetime.now(UTC))
        self.db.commit()
        self.db.refresh(payment)
        return payment

    async def verify_recent_payments(self) -> list[Payment]:
        pending_payments = (
            self.db.query(Payment)
            .filter(Payment.status.in_(["pending", "awaiting_payment"]))
            .filter(Payment.asaas_payment_id.isnot(None))
            .all()
        )

        updated: list[Payment] = []
        for payment in pending_payments:
            try:
                old_status = payment.status
                await self.check_payment_status(payment)
                if payment.status != old_status:
                    updated.append(payment)
            except Exception:
                logger.exception(
                    "Recent payment verification failed payment_id=%s appointment_id=%s",
                    payment.id,
                    payment.appointment_id,
                )
                continue

        return updated

    def get_payment_by_appointment(self, appointment_id: int) -> Payment | None:
        return self.db.query(Payment).filter(Payment.appointment_id == appointment_id).first()

    def list_unnotified_confirmed(self) -> list[Appointment]:
        """Retorna agendamentos confirmados cujo cliente ainda não foi notificado."""
        return (
            self.db.query(Appointment)
            .options(
                joinedload(Appointment.user),
                joinedload(Appointment.service),
                joinedload(Appointment.professional),
                joinedload(Appointment.payments),
            )
            .filter(
                Appointment.status == "confirmed",
                Appointment.notified_at.is_(None),
            )
            .order_by(Appointment.created_at.desc())
            .all()
        )

    async def create_direct_charge(
        self,
        user_id: int,
        amount_cents: int,
        product_id: int | None = None,
        description: str | None = None,
        billing_type: str = "undefined",
    ) -> Payment:
        if amount_cents <= 0:
            raise ValueError("O valor da cobrança deve ser maior que zero")

        billing_type = billing_type.lower()
        asaas_billing_type = billing_type.upper()
        if asaas_billing_type not in {"PIX", "BOLETO", "CREDIT_CARD", "UNDEFINED"}:
            raise ValueError("Invalid billing type")

        charge_desc = description or "Pagamento de produto"
        if product_id and not description:
            from app.models.product import Product

            prod = self.db.query(Product).filter(Product.id == product_id).first()
            if prod:
                charge_desc = f"Produto: {prod.name}"

        from app.services.company_settings_service import CompanySettingsService
        from app.services.payment_gateways import ChargeRequest, get_payment_gateway

        company_settings = CompanySettingsService(self.db).get_or_create_default()
        active_gateway = (company_settings.default_gateway or "asaas").lower().strip()

        if active_gateway != "asaas":
            gateway = get_payment_gateway(active_gateway, company_settings.gateway_credentials)
            user = self.user_repo.get(user_id)
            req = ChargeRequest(
                amount_cents=amount_cents,
                user_id=user_id,
                customer_name=user.name if user else "Cliente",
                customer_phone=getattr(user, "phone", None),
                customer_email=getattr(user, "email", None),
                customer_cpf=getattr(user, "cpf_cnpj", None),
                description=charge_desc,
                billing_type=billing_type,
                order_ref=f"prod:{product_id}:user:{user_id}",
                product_id=product_id,
            )
            result = await gateway.create_charge(req)
            payment = Payment(
                user_id=user_id,
                product_id=product_id,
                appointment_id=None,
                description=charge_desc,
                asaas_payment_id=result.gateway_payment_id,
                amount_cents=amount_cents,
                billing_type=billing_type,
                status=result.status,
                invoice_url=result.invoice_url or result.pix_copy_paste,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.db.add(payment)
            self.db.commit()
            self.db.refresh(payment)
            return payment

        # Fluxo Asaas padrão
        customer_id = await self.ensure_asaas_customer(user_id)
        due_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        remote_payment = await self.asaas.create_payment(
            customer_id=customer_id,
            value=amount_cents / 100.0,
            due_date=due_date,
            description=charge_desc,
            billing_type=asaas_billing_type,
        )

        payment_id = remote_payment.get("id")
        remote_status = remote_payment.get("status")
        payment = Payment(
            user_id=user_id,
            product_id=product_id,
            appointment_id=None,
            description=charge_desc,
            asaas_payment_id=payment_id,
            amount_cents=amount_cents,
            billing_type=billing_type,
            status=ASAAS_STATUS_MAP.get(remote_status, "pending") if isinstance(remote_status, str) else "pending",
            invoice_url=remote_payment.get("invoiceUrl"),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.db.add(payment)
        self.db.commit()
        self.db.refresh(payment)
        return payment

    create_charge_for_appointment = create_charge

