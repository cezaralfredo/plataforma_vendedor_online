from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class PaymentCreate(BaseModel):
    appointment_id: int | None = None
    product_id: int | None = None
    user_id: int | None = None
    description: str | None = None
    amount_cents: int | None = None
    billing_type: str = "pix"

    @field_validator("billing_type")
    @classmethod
    def normalize_billing_type(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"pix", "boleto", "credit_card", "undefined"}:
            raise ValueError("Forma de pagamento inv\u00e1lida")
        return normalized


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    appointment_id: int | None = None
    product_id: int | None = None
    user_id: int | None = None
    description: str | None = None
    asaas_payment_id: str | None = None
    amount_cents: int
    billing_type: str
    status: str
    invoice_url: str | None = None
    received_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AsaasCustomerCreate(BaseModel):
    name: str
    cpfCnpj: str | None = None
    phone: str | None = None
    email: str | None = None


class AsaasPaymentCreate(BaseModel):
    customer: str
    billingType: str = "UNDEFINED"
    value: float
    dueDate: str
    description: str | None = None


class AsaasPaymentResponse(BaseModel):
    id: str
    invoiceUrl: str | None = None
    status: str
    value: float
    billingType: str
    dueDate: str


class AsaasCustomerResponse(BaseModel):
    id: str
    name: str
    cpfCnpj: str | None = None
    phone: str | None = None
    email: str | None = None


class AsaasWebhookEvent(BaseModel):
    event: str
    payment: dict | None = None
    subscription: dict | None = None
