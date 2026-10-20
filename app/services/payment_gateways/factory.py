from typing import Any

from app.services.payment_gateways.asaas_gateway import AsaasPaymentGateway
from app.services.payment_gateways.base import BasePaymentGateway
from app.services.payment_gateways.direct_pix_gateway import DirectPixGateway
from app.services.payment_gateways.efipay_gateway import EfiPayGateway
from app.services.payment_gateways.manual_gateway import ManualPaymentGateway
from app.services.payment_gateways.mercadopago_gateway import MercadoPagoGateway


def get_payment_gateway(
    provider: str = "asaas",
    credentials: dict[str, Any] | None = None,
) -> BasePaymentGateway:
    """
    Retorna a instância do gateway de pagamento ou banco configurado pelo cliente.
    Suporta Asaas, Mercado Pago, EFI Pay (Gerencianet), PIX Direto (qualquer banco) e Manual.
    """
    provider_normalized = (provider or "asaas").lower().strip()
    creds = credentials or {}

    if provider_normalized == "asaas":
        return AsaasPaymentGateway(
            api_key=creds.get("api_key") or creds.get("asaas_api_key"),
            base_url=creds.get("base_url") or creds.get("asaas_base_url"),
        )
    elif provider_normalized in {"mercadopago", "mercado_pago"}:
        return MercadoPagoGateway(
            access_token=creds.get("access_token"),
            public_key=creds.get("public_key"),
        )
    elif provider_normalized in {"efipay", "efi", "gerencianet"}:
        return EfiPayGateway(
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
            pix_key=creds.get("pix_key"),
            sandbox=bool(creds.get("sandbox")),
        )
    elif provider_normalized in {"pix_direct", "direct_pix", "banco_direto", "chave_pix"}:
        return DirectPixGateway(
            pix_key=creds.get("pix_key"),
            merchant_name=creds.get("merchant_name"),
            merchant_city=creds.get("merchant_city"),
        )
    elif provider_normalized in {"manual", "none", "in_person", "balcao"}:
        return ManualPaymentGateway()
    else:
        return ManualPaymentGateway()
