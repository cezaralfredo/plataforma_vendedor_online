from datetime import UTC, datetime
from typing import Any

from app.services.payment_gateways.base import (
    BasePaymentGateway,
    ChargeRequest,
    ChargeResult,
)


class ManualPaymentGateway(BasePaymentGateway):
    """Gateway de pagamento local / manual no balcão (sem cobrança online externa)."""

    async def create_charge(
        self,
        request: ChargeRequest | None = None,
        appointment_id: int | None = None,
        amount_cents: int | None = None,
        customer_info: dict[str, Any] | None = None,
        billing_type: str = "in_person",
        **kwargs: Any,
    ) -> ChargeResult:
        if isinstance(request, ChargeRequest):
            ref = f"manual:{request.order_ref or f'order:{request.user_id}'}"
        elif appointment_id is not None:
            ref = f"manual:{appointment_id}"
        else:
            ref = "manual:unknown"

        return ChargeResult(
            gateway_payment_id=ref,
            status="confirmed",
            invoice_url=None,
            pix_copy_paste=None,
            pix_qr_code_url=None,
            raw_response={"message": "Pagamento no balcão / presencial"},
        )

    async def check_status(self, gateway_payment_id: str) -> dict[str, Any]:
        return {
            "gateway_payment_id": gateway_payment_id,
            "status": "confirmed",
            "received_at": datetime.now(UTC),
        }

    async def refund(self, gateway_payment_id: str, reason: str | None = None) -> dict[str, Any]:
        return {
            "gateway_payment_id": gateway_payment_id,
            "status": "cancelled",
            "refunded": True,
        }
