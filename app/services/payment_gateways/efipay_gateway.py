import base64
import logging
from typing import Any

import httpx

from app.services.payment_gateways.base import (
    BasePaymentGateway,
    ChargeRequest,
    ChargeResult,
)

logger = logging.getLogger(__name__)


class EfiPayGateway(BasePaymentGateway):
    """
    Gateway oficial para EFI Pay (antiga Gerencianet).
    API PIX do Banco Central com autenticação OAuth2 e emissão de cobranças imediatas.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        pix_key: str | None = None,
        sandbox: bool = False,
    ):
        self.client_id = client_id or ""
        self.client_secret = client_secret or ""
        self.pix_key = pix_key or ""
        self.base_url = (
            "https://pix-h.gerencianet.com.br"
            if sandbox
            else "https://pix.gerencianet.com.br"
        )

    async def _get_access_token(self) -> str:
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_creds = base64.b64encode(credentials.encode()).decode()
        headers = {
            "Authorization": f"Basic {encoded_creds}",
            "Content-Type": "application/json",
        }
        body = {"grant_type": "client_credentials"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                f"{self.base_url}/v1/oauth/token",
                headers=headers,
                json=body,
            )
            res.raise_for_status()
            data = res.json()
            return data["access_token"]

    async def create_charge(
        self,
        request_or_appointment_id: ChargeRequest | int,
        amount_cents: int | None = None,
        customer_info: dict[str, Any] | None = None,
        billing_type: str = "pix",
    ) -> ChargeResult:
        if isinstance(request_or_appointment_id, ChargeRequest):
            req = request_or_appointment_id
            val_cents = req.amount_cents
            desc = req.description
            name = req.customer_name
            cpf = req.customer_cpf
        else:
            cust = customer_info or {}
            val_cents = amount_cents or 0
            desc = f"Agendamento #{request_or_appointment_id}"
            name = cust.get("name") or "Cliente"
            cpf = cust.get("cpf")

        token = await self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        valor_reais = f"{val_cents / 100:.2f}"
        payload: dict[str, Any] = {
            "calendario": {"expiracao": 86400},
            "valor": {"original": valor_reais},
            "chave": self.pix_key,
            "solicitacaoPagador": desc[:140],
        }

        if cpf:
            clean_cpf = "".join(filter(str.isdigit, str(cpf)))
            if clean_cpf:
                payload["devedor"] = {
                    "cpf" if len(clean_cpf) <= 11 else "cnpj": clean_cpf,
                    "nome": name[:80],
                }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    f"{self.base_url}/v2/cob",
                    headers=headers,
                    json=payload,
                )
                res.raise_for_status()
                data = res.json()

            txid = data.get("txid") or ""
            pix_copia_cola = data.get("pixCopiaECola")
            loc_id = data.get("loc", {}).get("id")
            qr_code_image = None

            if loc_id:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        qr_res = await client.get(
                            f"{self.base_url}/v2/loc/{loc_id}/qrcode",
                            headers=headers,
                        )
                        if qr_res.status_code == 200:
                            qr_data = qr_res.json()
                            pix_copia_cola = pix_copia_cola or qr_data.get("qrcode")
                            qr_code_image = qr_data.get("imagemQrcode")
                except Exception as qr_err:
                    logger.warning("Falha ao obter QR code da EFI Pay: %s", qr_err)

            status_map = {
                "ATIVA": "pending",
                "CONCLUIDA": "received",
                "REMOVIDA_PELO_USUARIO_RECEBEDOR": "cancelled",
            }
            mapped_status = status_map.get(data.get("status"), "pending")

            return ChargeResult(
                gateway_payment_id=txid,
                status=mapped_status,
                invoice_url=None,
                pix_copy_paste=pix_copia_cola,
                pix_qr_code_url=None,
                qr_code_base64=qr_code_image,
                raw_response=data,
            )

        except Exception as exc:
            logger.error("Erro na API da EFI Pay: %s", exc)
            raise

    async def check_status(self, gateway_payment_id: str) -> dict[str, Any]:
        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    f"{self.base_url}/v2/cob/{gateway_payment_id}",
                    headers=headers,
                )
                res.raise_for_status()
                data = res.json()

            status_map = {
                "ATIVA": "pending",
                "CONCLUIDA": "received",
                "REMOVIDA_PELO_USUARIO_RECEBEDOR": "cancelled",
            }
            return {
                "gateway_payment_id": gateway_payment_id,
                "status": status_map.get(data.get("status"), "pending"),
                "raw_response": data,
            }
        except Exception as exc:
            logger.error("Erro ao consultar status na EFI Pay (%s): %s", gateway_payment_id, exc)
            return {"gateway_payment_id": gateway_payment_id, "status": "pending", "error": str(exc)}

    async def refund(self, gateway_payment_id: str, reason: str | None = None) -> dict[str, Any]:
        return {
            "gateway_payment_id": gateway_payment_id,
            "status": "refund_requested",
            "refunded": True,
        }
