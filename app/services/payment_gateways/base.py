from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChargeRequest:
    """Dados unificados para emissão de cobrança em qualquer instituição bancária/gateway."""

    amount_cents: int
    user_id: int
    customer_name: str
    customer_phone: str | None = None
    customer_email: str | None = None
    customer_cpf: str | None = None
    description: str = "Pagamento"
    billing_type: str = "pix"
    order_ref: str | None = None
    appointment_id: int | None = None
    product_id: int | None = None
    extra_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChargeResult:
    """Resultado padronizado da emissão de cobrança."""

    gateway_payment_id: str
    status: str  # "pending", "confirmed", "received"
    invoice_url: str | None = None
    pix_copy_paste: str | None = None
    pix_qr_code_url: str | None = None
    qr_code_base64: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gateway_payment_id": self.gateway_payment_id,
            "status": self.status,
            "invoice_url": self.invoice_url,
            "pix_copy_paste": self.pix_copy_paste,
            "pix_qr_code_url": self.pix_qr_code_url,
            "qr_code_base64": self.qr_code_base64,
            "raw_response": self.raw_response,
        }


@dataclass
class RefundResult:
    """Resultado de estorno ou cancelamento de cobrança."""

    gateway_payment_id: str
    status: str
    refunded: bool
    raw_response: dict[str, Any] = field(default_factory=dict)


class BasePaymentGateway(ABC):
    """Contrato base para provedores bancários e gateways de pagamento."""

    @abstractmethod
    async def create_charge(
        self,
        request_or_appointment_id: ChargeRequest | int,
        amount_cents: int | None = None,
        customer_info: dict[str, Any] | None = None,
        billing_type: str = "pix",
    ) -> ChargeResult | dict[str, Any]:
        """
        Cria uma cobrança no banco ou gateway selecionado.
        Suporta tanto o novo formato tipado ChargeRequest quanto o formato legado por compatibilidade.
        """
        pass

    @abstractmethod
    async def check_status(self, gateway_payment_id: str) -> dict[str, Any]:
        """Consulta o status atual de uma cobrança no provedor."""
        pass

    @abstractmethod
    async def refund(self, gateway_payment_id: str, reason: str | None = None) -> dict[str, Any]:
        """Realiza estorno ou cancelamento da cobrança."""
        pass
