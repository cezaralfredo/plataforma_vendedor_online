import logging
import uuid
from typing import Any

import httpx

from app.services.payment_gateways.base import (
    BasePaymentGateway,
    ChargeRequest,
    ChargeResult,
)

logger = logging.getLogger(__name__)

MP_API_BASE = "https://api.mercadopago.com/v1"


class MercadoPagoGateway(BasePaymentGateway):
    """
    Gateway oficial para Mercado Pago.
    Permite emissão de cobranças PIX dinâmicas com QR Code,
    código copia-e-cola e link direto de checkout.
    """

    def __init__(self, access_token: str | None = None, public_key: str | None = None):
        self.access_token = access_token or ""
        self.public_key = public_key or ""

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": str(uuid.uuid4()),
        }

    async def create_charge(
        self,
        request_or_appointment_id: ChargeRequest | int,
        amount_cents: int | None = None,
        customer_info: dict[str, Any] | None = None,
        billing_type: str = "pix",
    ) -> ChargeResult:
        if isinstance(request_or_appointment_id, ChargeRequest):
            req = request_or_appointment_id
            val = req.amount_cents / 100.0
            desc = req.description
            ext_ref = req.order_ref or f"order:{req.user_id}"
            email = req.customer_email or f"cliente_{req.user_id}@vendedor.ia"
            name = req.customer_name
            cpf = req.customer_cpf
        else:
            cust = customer_info or {}
            val = (amount_cents or 0) / 100.0
            desc = f"Agendamento #{request_or_appointment_id}"
            ext_ref = f"appointment:{request_or_appointment_id}"
            email = cust.get("email") or "cliente@vendedor.ia"
            name = cust.get("name") or "Cliente"
            cpf = cust.get("cpf")

        payload: dict[str, Any] = {
            "transaction_amount": val,
            "description": desc,
            "payment_method_id": "pix",
            "external_reference": ext_ref,
            "payer": {
                "email": email,
                "first_name": name,
            },
        }

        if cpf:
            clean_cpf = "".join(filter(str.isdigit, str(cpf)))
            if clean_cpf:
                payload["payer"]["identification"] = {
                    "type": "CPF" if len(clean_cpf) <= 11 else "CNPJ",
                    "number": clean_cpf,
                }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    f"{MP_API_BASE}/payments",
                    headers=self._get_headers(),
                    json=payload,
                )
                res.raise_for_status()
                data = res.json()

            payment_id = str(data.get("id"))
            poi = data.get("point_of_interaction", {})
            t_data = poi.get("transaction_data", {})

            pix_copy_paste = t_data.get("qr_code")
            qr_code_base64 = t_data.get("qr_code_base64")
            ticket_url = t_data.get("ticket_url")

            status_map = {
                "approved": "received",
                "in_process": "pending",
                "pending": "pending",
                "rejected": "cancelled",
                "refunded": "refunded",
            }
            mapped_status = status_map.get(data.get("status"), "pending")

            return ChargeResult(
                gateway_payment_id=payment_id,
                status=mapped_status,
                invoice_url=ticket_url,
                pix_copy_paste=pix_copy_paste,
                pix_qr_code_url=ticket_url,
                qr_code_base64=qr_code_base64,
                raw_response=data,
            )

        except Exception as exc:
            logger.error("Erro na API do Mercado Pago ao criar cobrança PIX: %s", exc)
            raise

    async def check_status(self, gateway_payment_id: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    f"{MP_API_BASE}/payments/{gateway_payment_id}",
                    headers=self._get_headers(),
                )
                res.raise_for_status()
                data = res.json()

            raw_status = data.get("status", "pending")
            status_map = {
                "approved": "received",
                "in_process": "pending",
                "pending": "pending",
                "rejected": "cancelled",
                "refunded": "refunded",
            }
            return {
                "gateway_payment_id": str(data.get("id")),
                "status": status_map.get(raw_status, "pending"),
                "received_at": data.get("date_approved"),
                "raw_response": data,
            }
        except Exception as exc:
            logger.error("Erro ao consultar status no Mercado Pago (%s): %s", gateway_payment_id, exc)
            return {"gateway_payment_id": gateway_payment_id, "status": "pending", "error": str(exc)}

    async def refund(self, gateway_payment_id: str, reason: str | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    f"{MP_API_BASE}/payments/{gateway_payment_id}/refunds",
                    headers=self._get_headers(),
                )
                res.raise_for_status()
                data = res.json()

            return {
                "gateway_payment_id": gateway_payment_id,
                "status": "refunded",
                "refunded": True,
                "raw_response": data,
            }
        except Exception as exc:
            logger.error("Erro ao estornar pagamento no Mercado Pago: %s", exc)
            raise
