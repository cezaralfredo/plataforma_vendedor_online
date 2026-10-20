import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.appointment import Appointment
from app.models.notification_delivery import NotificationDelivery
from app.models.notification_log import NotificationLog
from app.models.payment import Payment

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    """Normaliza valores do SQLite sem fuso para comparação segura em testes."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def format_payment_confirmation_message(appointment: Appointment, payment: Payment) -> str:
    """Gera mensagem formatada e humanizada de confirmação para envio via WhatsApp."""
    user_name = appointment.user.name if appointment.user else "Cliente"
    service_name = appointment.service.name if appointment.service else "Serviço"
    professional_name = appointment.professional.name if appointment.professional else "Profissional"
    date_str = appointment.start_time.strftime("%d/%m/%Y às %H:%M")
    amount = f"R$ {payment.amount_cents / 100:.2f}"
    billing = (payment.billing_type or "PIX").upper()

    return (
        f"Olá, {user_name}! 👋\n\n"
        f"✅ Seu pagamento de {amount} ({billing}) foi confirmado com sucesso!\n\n"
        f"📋 *Detalhes da sua Reserva:*\n"
        f"• *Serviço:* {service_name}\n"
        f"• *Profissional:* {professional_name}\n"
        f"• *Data e Horário:* {date_str}\n"
        f"• *Código da Reserva:* #{appointment.id}\n\n"
        f"Sua vaga está garantida na nossa agenda. Qualquer dúvida, é só responder por aqui!"
    )


def build_payment_notification_payload(appointment: Appointment, payment: Payment) -> dict[str, Any]:
    """Monta payload padronizado para notificação de pagamento."""
    user = appointment.user
    service = appointment.service
    prof = appointment.professional

    client_phone = user.phone if user else ""
    whatsapp = getattr(user, "whatsapp_number", None) or client_phone

    return {
        "event": "payment.confirmed",
        "timestamp": datetime.now(UTC).isoformat(),
        "appointment_id": appointment.id,
        "chatId": whatsapp,
        "whatsapp": whatsapp,
        "payment_id": payment.id,
        "asaas_payment_id": payment.asaas_payment_id,
        "status": appointment.status,
        "payment_status": payment.status,
        "amount_cents": payment.amount_cents,
        "billing_type": payment.billing_type,
        "client": {
            "id": user.id if user else None,
            "name": user.name if user else "Cliente",
            "phone": client_phone,
            "whatsapp_number": whatsapp,
        },
        "service": {
            "id": service.id if service else None,
            "name": service.name if service else "Serviço",
            "duration_minutes": getattr(service, "duration_minutes", None),
            "price_cents": getattr(service, "price_cents", None),
        },
        "professional": {
            "id": prof.id if prof else None,
            "name": prof.name if prof else "Profissional",
        },
        "appointment": {
            "id": appointment.id,
            "start_time": appointment.start_time.isoformat() if appointment.start_time else None,
            "end_time": appointment.end_time.isoformat() if appointment.end_time else None,
            "notes": appointment.notes,
        },
        "formatted_message": format_payment_confirmation_message(appointment, payment),
    }


class NotificationService:
    def __init__(self, db: Session):
        self.db = db

    def get_effective_webhook_url(self) -> str:
        """Retorna o webhook externo de notificações configurado ou string vazia para entrega direta via WhatsApp."""
        return getattr(settings, "notification_webhook_url", "")

    def queue_payment_confirmed(self, appointment_id: int, payment_id: int) -> NotificationDelivery:
        """Registra a confirmação em uma outbox idempotente na mesma transação do pagamento."""
        delivery = (
            self.db.query(NotificationDelivery)
            .filter(
                NotificationDelivery.payment_id == payment_id,
                NotificationDelivery.event == "payment.confirmed",
            )
            .first()
        )
        if delivery:
            return delivery

        delivery = NotificationDelivery(
            appointment_id=appointment_id,
            payment_id=payment_id,
            event="payment.confirmed",
            status="pending",
        )
        self.db.add(delivery)
        self.db.flush()
        return delivery

    def recover_unnotified_payment_confirmations(self, limit: int = 100) -> int:
        """Recupera confirmações já registradas antes de uma indisponibilidade."""
        appointments = (
            self.db.query(Appointment)
            .join(Payment, Payment.appointment_id == Appointment.id)
            .filter(
                Appointment.status == "confirmed",
                Appointment.notified_at.is_(None),
                Payment.status.in_(("received", "confirmed")),
            )
            .order_by(Payment.received_at, Payment.id)
            .limit(limit)
            .all()
        )
        created = 0
        for appointment in appointments:
            payment = (
                self.db.query(Payment)
                .filter(
                    Payment.appointment_id == appointment.id,
                    Payment.status.in_(("received", "confirmed")),
                )
                .order_by(Payment.received_at.desc(), Payment.id.desc())
                .first()
            )
            if not payment:
                continue
            existing = (
                self.db.query(NotificationDelivery)
                .filter(
                    NotificationDelivery.payment_id == payment.id,
                    NotificationDelivery.event == "payment.confirmed",
                )
                .first()
            )
            if existing:
                continue
            self.queue_payment_confirmed(appointment.id, payment.id)
            created += 1
        if created:
            self.db.commit()
        return created

    def get_delivery_payload(self, delivery: NotificationDelivery) -> dict[str, Any] | None:
        appointment = (
            self.db.query(Appointment)
            .options(
                joinedload(Appointment.user),
                joinedload(Appointment.service),
                joinedload(Appointment.professional),
            )
            .filter(Appointment.id == delivery.appointment_id)
            .first()
        )
        if not appointment:
            logger.warning("Appointment %s not found for notification delivery", delivery.appointment_id)
            return None

        payment = self.db.query(Payment).filter(Payment.id == delivery.payment_id).first()
        if not payment:
            logger.warning("Payment %s not found for notification delivery", delivery.payment_id)
            return None

        payload = build_payment_notification_payload(appointment, payment)
        payload["delivery_id"] = delivery.id
        payload["attempt"] = delivery.attempts
        return payload

    async def dispatch_payment_confirmed(self, delivery_id: int) -> bool:
        """Dispara a confirmação de pagamento para o webhook externo ou diretamente via WhatsApp."""
        delivery = self.db.get(NotificationDelivery, delivery_id)
        if not delivery or delivery.status == "sent":
            return False

        webhook_url = self.get_effective_webhook_url()
        if not webhook_url:
            # Envio direto pelo provedor WhatsApp ativo (WAHA, Evolution ou Meta)
            full_payload = self.get_delivery_payload(delivery)
            recipient_phone = (full_payload.get("chatId") or full_payload.get("whatsapp")) if full_payload else None
            if full_payload and recipient_phone:
                from app.services.whatsapp.gateway import WhatsAppGatewayService

                gateway = WhatsAppGatewayService(self.db)
                result = await gateway.send_text(
                    phone=recipient_phone,
                    text=full_payload.get("formatted_message", ""),
                )
                if result.success:
                    self.claim_delivery(delivery.id)
                    self.mark_delivery_sent(
                        delivery_id=delivery.id,
                        provider_message_id=result.message_id,
                    )
                    logger.info("Notificação de pagamento entregue via %s (msg_id: %s)", result.provider, result.message_id)
                    return True
                else:
                    self.defer_delivery(delivery.id, result.error or "Falha no envio direto pelo provedor WhatsApp")
                    return False

            logger.info(
                "No notification webhook or recipient phone configured. Delivery %s remains pending.",
                delivery.id,
            )
            return False

        # Somente o identificador interno é enviado ao gatilho externo, evitando expor dados do cliente em logs.
        payload = {"delivery_id": delivery.id, "event": delivery.event}
        headers = {"Content-Type": "application/json"}
        token = getattr(settings, "notification_webhook_token", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(webhook_url, json=payload, headers=headers)
                response.raise_for_status()

            logger.info(
                "Payment confirmation delivery %s accepted by %s",
                delivery.id,
                webhook_url,
            )
            return True
        except Exception as e:
            logger.warning(
                "Failed to wake notification workflow %s for delivery %s: %s",
                webhook_url,
                delivery.id,
                e,
            )
            return False

    def claim_delivery(self, delivery_id: int) -> NotificationDelivery | None:
        """Reserva uma entrega para um único worker e recupera leases abandonados."""
        delivery = (
            self.db.query(NotificationDelivery)
            .filter(NotificationDelivery.id == delivery_id)
            .with_for_update()
            .first()
        )
        if not delivery or delivery.status in {"sent", "failed"}:
            return None

        now = datetime.now(UTC)
        lease_expired = (
            delivery.status == "processing"
            and delivery.claimed_at is not None
            and _as_utc(delivery.claimed_at)
            <= now - timedelta(seconds=settings.notification_delivery_lease_seconds)
        )
        if delivery.status == "processing" and not lease_expired:
            return None
        if delivery.status == "pending" and _as_utc(delivery.next_attempt_at) > now:
            return None
        if delivery.attempts >= settings.notification_delivery_max_attempts:
            delivery.status = "failed"
            delivery.last_error = "Número máximo de tentativas atingido sem confirmação do provedor."
            self.db.commit()
            return None

        delivery.status = "processing"
        delivery.attempts += 1
        delivery.claimed_at = now
        delivery.last_error = None
        self.db.commit()
        return delivery

    def mark_delivery_sent(
        self, delivery_id: int, provider_message_id: str | None = None
    ) -> NotificationDelivery | None:
        delivery = self.db.get(NotificationDelivery, delivery_id)
        if not delivery:
            return None
        if delivery.status == "sent":
            return delivery
        if delivery.status != "processing":
            return None

        now = datetime.now(UTC)
        delivery.status = "sent"
        delivery.sent_at = now
        delivery.provider_message_id = provider_message_id
        delivery.last_error = None
        appointment = self.db.get(Appointment, delivery.appointment_id)
        if appointment:
            appointment.notified_at = now
            already_logged = (
                self.db.query(NotificationLog)
                .filter(
                    NotificationLog.appointment_id == appointment.id,
                    NotificationLog.type == "confirmation",
                )
                .first()
            )
            if not already_logged:
                self.db.add(
                    NotificationLog(
                        appointment_id=appointment.id,
                        type="confirmation",
                        sent_at=now,
                    )
                )
        self.db.commit()
        return delivery

    def defer_delivery(self, delivery_id: int, error: str) -> NotificationDelivery | None:
        delivery = self.db.get(NotificationDelivery, delivery_id)
        if not delivery or delivery.status == "sent":
            return None
        now = datetime.now(UTC)
        delivery.last_error = error[:1000]
        delivery.claimed_at = None
        if delivery.attempts >= settings.notification_delivery_max_attempts:
            delivery.status = "failed"
        else:
            delay_seconds = min(60 * (2 ** max(delivery.attempts - 1, 0)), 3600)
            delivery.status = "pending"
            delivery.next_attempt_at = now + timedelta(seconds=delay_seconds)
        self.db.commit()
        return delivery
