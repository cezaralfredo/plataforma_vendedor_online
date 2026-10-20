import logging
from typing import Any

from app.services.asaas_client import AsaasClient
from app.services.payment_gateways.base import (
    BasePaymentGateway,
    ChargeRequest,
    ChargeResult,
)

logger = logging.getLogger(__name__)


class AsaasPaymentGateway(BasePaymentGateway):
    """Adapter para o Asaas implementando a interface unificada BasePaymentGateway."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.client = AsaasClient()
        if api_key:
            self.client.api_key = api_key
            self.client.headers["access_token"] = api_key
        if base_url:
            self.client.base_url = base_url.rstrip("/")

    async def create_charge(
        self,
        request_or_appointment_id: ChargeRequest | int,
        amount_cents: int | None = None,
        customer_info: dict[str, Any] | None = None,
        billing_type: str = "pix",
    ) -> ChargeResult:
        if isinstance(request_or_appointment_id, ChargeRequest):
            req = request_or_appointment_id
            customer_id = req.extra_params.get("customer_id")
            val = req.amount_cents / 100.0
            due_date = req.extra_params.get("due_date")
            desc = req.description
            ext_ref = req.order_ref or f"user:{req.user_id}"
            b_type = req.billing_type.upper()
        else:
            cust_info = customer_info or {}
            customer_id = cust_info.get("customer_id")
            val = (amount_cents or 0) / 100.0
            due_date = cust_info.get("due_date")
            desc = f"Agendamento #{request_or_appointment_id}"
            ext_ref = f"appointment:{request_or_appointment_id}"
            b_type = billing_type.upper()

        res = await self.client.create_payment(
            customer_id=customer_id,
            billing_type=b_type,
            value=val,
            due_date=due_date,
            description=desc,
            external_reference=ext_ref,
        )

        payment_id = res.get("id")
        pix_qr = None
        if b_type == "PIX" and payment_id:
            try:
                pix_qr = await self.client.get_pix_qr_code(payment_id)
            except Exception as exc:
                logger.debug("PIX QR code não disponível imediatamente: %s", exc)

        return ChargeResult(
            gateway_payment_id=payment_id,
            status="pending",
            invoice_url=res.get("invoiceUrl"),
            pix_copy_paste=pix_qr.get("payload") if pix_qr else None,
            pix_qr_code_url=pix_qr.get("encodedImage") if pix_qr else None,
            raw_response=res,
        )

    async def check_status(self, gateway_payment_id: str) -> dict[str, Any]:
        res = await self.client.get_payment(gateway_payment_id)
        return {
            "gateway_payment_id": gateway_payment_id,
            "status": res.get("status", "pending").lower(),
            "received_at": res.get("paymentDate"),
            "raw_response": res,
        }

    async def refund(self, gateway_payment_id: str, reason: str | None = None) -> dict[str, Any]:
        res = await self.client.refund_payment(gateway_payment_id, description=reason)
        return {
            "gateway_payment_id": gateway_payment_id,
            "status": "refunded",
            "raw_response": res,
        }
