import logging
import re
import unicodedata
import uuid
from typing import Any

from app.services.payment_gateways.base import (
    BasePaymentGateway,
    ChargeRequest,
    ChargeResult,
)

logger = logging.getLogger(__name__)


def _sanitize_string(text: str, max_len: int) -> str:
    """Remove acentos e caracteres especiais conforme padrão EMVCo do Banco Central."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ASCII", "ignore").decode("ASCII")
    cleaned = re.sub(r"[^A-Za-z0-9 ]", "", ascii_text).strip()
    return cleaned[:max_len]


def _crc16_ccitt(payload: str) -> str:
    """
    Calcula o checksum CRC16-CCITT (polinômio 0x1021, valor inicial 0xFFFF)
    obrigatório no padrão EMVCo BRCode do Banco Central do Brasil.
    """
    crc = 0xFFFF
    for char in payload.encode("utf-8"):
        crc ^= char << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def generate_brcode_pix(
    pix_key: str,
    merchant_name: str,
    merchant_city: str,
    amount: float | None = None,
    txid: str | None = None,
    description: str | None = None,
) -> str:
    """
    Gera a string oficial do PIX Copia e Cola (BRCode padrão EMVCo do BACEN)
    compatível com qualquer aplicativo bancário brasileiro (Nubank, Itaú, BB, etc.).
    """
    clean_name = _sanitize_string(merchant_name or "RECEBEDOR", 25) or "RECEBEDOR"
    clean_city = _sanitize_string(merchant_city or "BRASIL", 15) or "BRASIL"
    clean_key = pix_key.strip()

    # ID 00: Payload Format Indicator
    payload = "000201"

    # ID 26: Merchant Account Information (GUI + Chave + Descrição)
    gui = "0014br.gov.bcb.pix"
    key_field = f"01{len(clean_key):02d}{clean_key}"
    desc_field = ""
    if description:
        clean_desc = _sanitize_string(description, 25)
        if clean_desc:
            desc_field = f"02{len(clean_desc):02d}{clean_desc}"
    account_info = gui + key_field + desc_field
    payload += f"26{len(account_info):02d}{account_info}"

    # ID 52: Merchant Category Code
    payload += "52040000"

    # ID 53: Transaction Currency (986 = BRL)
    payload += "5303986"

    # ID 54: Transaction Amount
    if amount is not None and amount > 0:
        amount_str = f"{amount:.2f}"
        payload += f"54{len(amount_str):02d}{amount_str}"

    # ID 58: Country Code
    payload += "5802BR"

    # ID 59: Merchant Name
    payload += f"59{len(clean_name):02d}{clean_name}"

    # ID 60: Merchant City
    payload += f"60{len(clean_city):02d}{clean_city}"

    # ID 62: Additional Data Field (TXID)
    clean_txid = _sanitize_string(txid or "***", 25) or "***"
    ref_field = f"05{len(clean_txid):02d}{clean_txid}"
    payload += f"62{len(ref_field):02d}{ref_field}"

    # ID 63: CRC16 Checksum
    payload += "6304"
    crc = _crc16_ccitt(payload)
    return payload + crc


class DirectPixGateway(BasePaymentGateway):
    """
    Gateway de PIX Direto na conta bancária do cliente (Zero Taxas).
    Funciona para QUALQUER BANCO (Itaú, Bradesco, Santander, Nubank, Inter, BB, Caixa, etc.)
    através da geração do BRCode EMVCo oficial do Banco Central do Brasil.
    """

    def __init__(
        self,
        pix_key: str | None = None,
        merchant_name: str | None = None,
        merchant_city: str | None = None,
    ):
        self.pix_key = (pix_key or "").strip()
        self.merchant_name = (merchant_name or "Minha Loja").strip()
        self.merchant_city = (merchant_city or "Brasil").strip()

    async def create_charge(
        self,
        request_or_appointment_id: ChargeRequest | int,
        amount_cents: int | None = None,
        customer_info: dict[str, Any] | None = None,
        billing_type: str = "pix",
    ) -> ChargeResult:
        if isinstance(request_or_appointment_id, ChargeRequest):
            req = request_or_appointment_id
            amount = req.amount_cents / 100.0
            txid = req.order_ref or f"PED{uuid.uuid4().hex[:8].upper()}"
            desc = req.description
        else:
            amount = (amount_cents or 0) / 100.0
            txid = f"APPT{request_or_appointment_id}"
            desc = f"Agendamento #{request_or_appointment_id}"

        if not self.pix_key:
            raise ValueError("Chave PIX da empresa não configurada para o gateway DirectPix.")

        pix_copia_cola = generate_brcode_pix(
            pix_key=self.pix_key,
            merchant_name=self.merchant_name,
            merchant_city=self.merchant_city,
            amount=amount,
            txid=txid,
            description=desc,
        )

        return ChargeResult(
            gateway_payment_id=f"direct_pix:{txid}",
            status="pending",
            invoice_url=None,
            pix_copy_paste=pix_copia_cola,
            pix_qr_code_url=None,
            raw_response={
                "chave_pix": self.pix_key,
                "titular": self.merchant_name,
                "cidade": self.merchant_city,
                "txid": txid,
                "valor": amount,
            },
        )

    async def check_status(self, gateway_payment_id: str) -> dict[str, Any]:
        return {
            "gateway_payment_id": gateway_payment_id,
            "status": "pending",
            "message": "PIX direto: aguarda conciliação de comprovante ou webhook bancário",
        }

    async def refund(self, gateway_payment_id: str, reason: str | None = None) -> dict[str, Any]:
        return {
            "gateway_payment_id": gateway_payment_id,
            "status": "manual_refund_required",
            "refunded": False,
        }
