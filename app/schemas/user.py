from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    name: str
    phone: str
    email: str | None = None
    whatsapp_number: str | None = None
    cpf_cnpj: str | None = None
    source: str = "direct"
    external_id: str | None = None
    import_batch_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    birth_date: date | None = None
    gender: str | None = None
    postal_code: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    delivery_instructions: str | None = None
    preferred_bank: str | None = None
    opt_in_whatsapp: bool = True


class UserUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    whatsapp_number: str | None = None
    cpf_cnpj: str | None = None
    asaas_customer_id: str | None = None
    active: bool | None = None
    source: str | None = None
    external_id: str | None = None
    import_batch_id: str | None = None
    tags: list[str] | None = None
    notes: str | None = None
    custom_fields: dict[str, Any] | None = None
    birth_date: date | None = None
    gender: str | None = None
    postal_code: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    delivery_instructions: str | None = None
    preferred_bank: str | None = None
    opt_in_whatsapp: bool | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str
    email: str | None = None
    whatsapp_number: str | None = None
    cpf_cnpj: str | None = None
    asaas_customer_id: str | None = None
    active: bool = True
    created_at: datetime | None = None
    source: str = "direct"
    external_id: str | None = None
    import_batch_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    birth_date: date | None = None
    gender: str | None = None
    postal_code: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    delivery_instructions: str | None = None
    preferred_bank: str | None = None
    opt_in_whatsapp: bool = True


class UserImportItem(BaseModel):
    name: str
    phone: str
    email: str | None = None
    whatsapp_number: str | None = None
    cpf_cnpj: str | None = None
    source: str | None = None
    external_id: str | None = None
    tags: list[str] | str | None = None
    notes: str | None = None
    custom_fields: dict[str, Any] | None = None
    birth_date: date | str | None = None
    gender: str | None = None
    postal_code: str | None = None
    city: str | None = None
    state: str | None = None
    address: str | None = None
    address_number: str | None = None
    address_complement: str | None = None
    neighborhood: str | None = None
    delivery_instructions: str | None = None
    preferred_bank: str | None = None
    opt_in_whatsapp: bool | None = True


class UserImportRequest(BaseModel):
    source: str = "import_csv"
    import_batch_id: str | None = None
    deduplication_strategy: Literal["update", "skip", "error"] = "update"
    items: list[UserImportItem]


class UserImportResultItem(BaseModel):
    index: int
    phone: str | None = None
    name: str | None = None
    status: Literal["created", "updated", "skipped", "error"]
    user_id: int | None = None
    message: str | None = None


class UserImportSummary(BaseModel):
    total: int
    created: int
    updated: int
    skipped: int
    errors: int
    import_batch_id: str
    details: list[UserImportResultItem]
