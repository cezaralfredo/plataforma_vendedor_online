from datetime import datetime

from app.models.payment import Payment


def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False
    comparable_now = now
    if expires_at.tzinfo is None and now.tzinfo is not None:
        comparable_now = now.replace(tzinfo=None)
    elif expires_at.tzinfo is not None and now.tzinfo is None:
        comparable_now = now.replace(tzinfo=expires_at.tzinfo)
    return expires_at <= comparable_now


def apply_payment_state(
    payment: Payment,
    new_status: str,
    now: datetime,
) -> None:
    payment.status = new_status
    payment.updated_at = now
    appointment = payment.appointment

    if new_status in {"received", "confirmed"}:
        if payment.received_at is None:
            payment.received_at = now
        if (
            appointment is not None
            and appointment.status in {"pending", "awaiting_payment"}
            and not _is_expired(appointment.expires_at, now)
        ):
            appointment.status = "confirmed"
        return

    if new_status in {"overdue", "cancelled"}:
        if appointment is not None and appointment.status in {"pending", "awaiting_payment"}:
            appointment.status = "cancelled"
        return

    if new_status == "refunded" and appointment is not None and appointment.status != "completed":
        appointment.status = "cancelled"
