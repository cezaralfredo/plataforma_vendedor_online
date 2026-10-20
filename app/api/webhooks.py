import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.notification_log import NotificationLog
from app.models.payment import Payment
from app.models.webhook_event import WebhookEvent
from app.services.notification_service import NotificationService
from app.services.payment_state_service import apply_payment_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

STATUS_MAP = {
    "PAYMENT_RECEIVED": "received",
    "PAYMENT_CONFIRMED": "confirmed",
    "PAYMENT_OVERDUE": "overdue",
    "PAYMENT_REFUNDED": "refunded",
    "PAYMENT_CANCELLED": "cancelled",
}


def verify_webhook_signature(request: Request) -> bool:
    token = request.headers.get("asaas-access-token", "")
    expected = settings.asaas_webhook_token
    if not expected:
        return True
    return hmac.compare_digest(token, expected)


@router.post("/asaas")
async def asaas_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if not verify_webhook_signature(request):
        raise HTTPException(status_code=401, detail="Invalid signature")

    body = await request.json()
    canonical_body = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    event_id = body.get("id") or hashlib.sha256(canonical_body).hexdigest()
    db.add(WebhookEvent(provider_event_id=event_id))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "ignored", "reason": "duplicate"}

    event = body.get("event", "")
    payment_data = body.get("payment") or {}
    if not payment_data:
        db.commit()
        return {"status": "ignored", "reason": "no_payment_data"}

    asaas_payment_id = payment_data.get("id")
    payment = (
        db.query(Payment)
        .filter(Payment.asaas_payment_id == asaas_payment_id)
        .first()
    )
    if not payment:
        db.rollback()
        logger.warning(
            "Webhook received for unknown or in-flight payment %s (event %s). Rolling back event receipt to allow retry.",
            asaas_payment_id,
            event_id,
        )
        raise HTTPException(
            status_code=404,
            detail=f"Payment {asaas_payment_id} not yet available in local store; retry deferred",
        )

    new_status = STATUS_MAP.get(event)
    should_notify = False
    appointment_id = None
    delivery_id = None

    if new_status:
        apply_payment_state(payment, new_status, datetime.now(UTC))
        if new_status in {"received", "confirmed"}:
            if payment.appointment and payment.appointment.status == "confirmed":
                should_notify = True
                appointment_id = payment.appointment_id
                logger.info(
                    "Payment %s (%s) confirmed for appointment %s - pending customer notification",
                    payment.id,
                    payment.asaas_payment_id,
                    payment.appointment_id,
                )
                try:
                    db.add(
                        NotificationLog(
                            appointment_id=payment.appointment_id,
                            type="payment_received",
                        )
                    )
                except Exception as e:
                    logger.warning("Could not create NotificationLog for webhook: %s", e)
                # A outbox participa da mesma transação que a confirmação do Asaas.
                delivery_id = NotificationService(db).queue_payment_confirmed(
                    payment.appointment_id, payment.id
                ).id
            elif payment.product_id:
                logger.info("Payment %s (%s) confirmed for product %s", payment.id, payment.asaas_payment_id, payment.product_id)
                user = payment.user
                client_phone = getattr(user, "whatsapp_number", None) or getattr(user, "phone", None)
                if client_phone:
                    from app.services.notification_service import format_payment_confirmation_message
                    from app.services.whatsapp.gateway import WhatsAppGatewayService

                    gateway = WhatsAppGatewayService(db)
                    confirm_msg = format_payment_confirmation_message(None, payment)
                    background_tasks.add_task(gateway.send_text, client_phone, confirm_msg)
    db.commit()

    if should_notify and appointment_id and delivery_id:
        notification_service = NotificationService(db)
        await notification_service.dispatch_payment_confirmed(delivery_id)

    return {"status": "ok"}
