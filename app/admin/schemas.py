import re
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AppointmentStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    AWAITING_PAYMENT = "awaiting_payment"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    RECEIVED = "received"
    OVERDUE = "overdue"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class AdminKPIs(BaseModel):
    appointments_today: int
    appointments_pending: int
    appointments_confirmed: int
    revenue_today_cents: int
    revenue_week_cents: int
    revenue_month_cents: int
    revenue_total_cents: int = 0
    pending_total_cents: int = 0
    payments_pending: int
    payments_overdue: int
    professionals_active: int
    professionals_total: int
    users_total: int


class AdminSystemComponentStatus(BaseModel):
    status: Literal["online", "connected", "unavailable"]


class AdminAsaasStatus(BaseModel):
    configured: bool
    mode: Literal["sandbox", "production"]


class AdminPaymentGatewayStatus(BaseModel):
    provider: str
    label: str
    configured: bool
    mode: str
    detail: str


class AdminMCPStatus(BaseModel):
    endpoint_enabled: bool


class AdminSystemStatus(BaseModel):
    api: AdminSystemComponentStatus
    database: AdminSystemComponentStatus
    asaas: AdminAsaasStatus
    payment_gateway: AdminPaymentGatewayStatus | None = None
    mcp: AdminMCPStatus


class AdminAppointment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    professional_id: int
    service_id: int
    start_time: datetime
    end_time: datetime
    status: str
    expires_at: datetime | None = None
    notes: str | None = None
    created_at: datetime

    # Related data
    client_name: str | None = None
    client_phone: str | None = None
    professional_name: str | None = None
    service_name: str | None = None
    service_price_cents: int | None = None
    payment_status: str | None = None
    payment_id: int | None = None


class AdminPayment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    appointment_id: int | None = None
    user_id: int | None = None
    product_id: int | None = None
    description: str | None = None
    asaas_payment_id: str | None = None
    amount_cents: int
    billing_type: str
    status: str
    invoice_url: str | None = None
    received_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # Related data
    client_name: str | None = None
    client_phone: str | None = None
    professional_name: str | None = None
    service_name: str | None = None
    product_name: str | None = None
    appointment_start: datetime | None = None


class AdminPaymentSummary(BaseModel):
    total_cents: int = 0
    received_cents: int = 0
    received_count: int = 0
    pending_cents: int = 0
    pending_count: int = 0
    overdue_cents: int = 0
    overdue_count: int = 0
    refunded_cents: int = 0
    refunded_count: int = 0
    cancelled_cents: int = 0
    cancelled_count: int = 0


class AdminPaymentsResponse(BaseModel):
    data: list[AdminPayment]
    total: int
    summary: AdminPaymentSummary
    page: int
    page_size: int
    total_pages: int


class AdminProfessional(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str | None = None
    email: str | None = None
    bio: str | None = None
    photo_url: str | None = None
    active: bool

    # Computed
    services_count: int = 0
    appointments_today: int = 0
    appointments_week: int = 0
    revenue_month_cents: int = 0


class AdminFilters(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    professional_id: int | None = None
    status: str | None = None
    search: str | None = None
    page: int = 1
    page_size: int = 20


class AdminClientCreate(BaseModel):
    name: str
    phone: str
    email: str | None = None
    whatsapp_number: str | None = None
    cpf_cnpj: str | None = None
    postal_code: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    delivery_instructions: str | None = None
    preferred_bank: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        cleaned = v.strip() if isinstance(v, str) else ""
        if not cleaned:
            raise ValueError("O nome do cliente é obrigatório.")
        return cleaned

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = v.strip() if isinstance(v, str) else ""
        if not cleaned or len(cleaned) < 8:
            raise ValueError("O telefone do cliente é obrigatório e deve ser válido.")
        return cleaned

    @field_validator("state")
    @classmethod
    def normalize_state(cls, v: str | None) -> str | None:
        if not v:
            return None
        cleaned = v.strip().upper()
        return cleaned[:2] if cleaned else None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(t).strip() for t in v if str(t).strip()]
        if isinstance(v, str):
            return [t.strip() for t in re.split(r"[,;|]", v) if t.strip()]
        return []


class AdminClientUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    whatsapp_number: str | None = None
    cpf_cnpj: str | None = None
    postal_code: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    delivery_instructions: str | None = None
    preferred_bank: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    tags: list[str] | str | None = None
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip() if isinstance(v, str) else ""
            if not cleaned:
                raise ValueError("O nome do cliente não pode ser vazio.")
            return cleaned
        return None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip() if isinstance(v, str) else ""
            if not cleaned or len(cleaned) < 8:
                raise ValueError("O telefone do cliente deve ser válido.")
            return cleaned
        return None

    @field_validator("state")
    @classmethod
    def normalize_state(cls, v: str | None) -> str | None:
        if not v:
            return None
        cleaned = v.strip().upper()
        return cleaned[:2] if cleaned else None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, v: Any) -> list[str] | None:
        if v is None:
            return None
        if isinstance(v, list):
            return [str(t).strip() for t in v if str(t).strip()]
        if isinstance(v, str):
            return [t.strip() for t in re.split(r"[,;|]", v) if t.strip()]
        return None


class AdminClientSummary(BaseModel):
    id: int
    name: str
    phone: str
    email: str | None = None
    whatsapp_number: str | None = None
    postal_code: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    delivery_instructions: str | None = None
    preferred_bank: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    active: bool
    created_at: datetime | None = None
    appointments_total: int
    payments_received_total: int
    last_appointment_at: datetime | None = None


class AdminClientPage(BaseModel):
    data: list[AdminClientSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminAppointmentCreate(BaseModel):
    user_id: int | None = None
    new_client: AdminClientCreate | None = None
    professional_id: int
    service_id: int
    start_time: datetime
    end_time: datetime
    notes: str | None = None

    @model_validator(mode="after")
    def validate_client_source(self):
        if (self.user_id is None) == (self.new_client is None):
            raise ValueError("Informe um cliente existente ou cadastre um novo cliente")
        return self


class AdminAppointmentUpdate(BaseModel):
    user_id: int | None = None
    professional_id: int | None = None
    service_id: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    notes: str | None = None


class AdminProfessionalOfferingUpsert(BaseModel):
    service_id: int
    commission_percent: Decimal = Decimal("10.00")


class AdminAvailabilityInput(BaseModel):
    day_of_week: int | None = None
    start_time: time | None = None
    end_time: time | None = None
    specific_date: date | None = None


class AdminServiceCatalogCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    price_cents: int = Field(ge=0)
    duration_minutes: int = Field(gt=0)


class AdminServiceCatalogUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    price_cents: int | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, gt=0)


class AppointmentAction(BaseModel):
    action: Literal["cancel", "confirm", "complete"]
    notes: str | None = None


class PaymentAction(BaseModel):
    action: Literal["refresh", "refund"]


class PaymentActionResult(BaseModel):
    message: str
    changed: bool
    payment: AdminPayment


class AdminMaintenancePreview(BaseModel):
    cutoff_date: str
    unpaid_cancelled_only: bool
    appointments_count: int
    completed_count: int
    cancelled_unpaid_count: int
    payments_count: int
    notifications_count: int
    estimated_kb_freed: float


class AdminMaintenancePurgeRequest(BaseModel):
    cutoff_date: str
    unpaid_cancelled_only: bool = False
    run_vacuum: bool = True
    confirmed: bool = True


class AdminMaintenancePurgeResult(BaseModel):
    status: str
    deleted_appointments: int
    deleted_payments: int
    deleted_notifications: int
    deleted_webhooks: int
    vacuum_executed: bool
    message: str
