from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductBase(BaseModel):
    name: str = Field(..., max_length=255, description="Nome do produto")
    description: str | None = Field(default=None, description="Descrição detalhada do produto")
    category: str | None = Field(default=None, max_length=100, description="Categoria do produto")
    price_cents: int = Field(..., ge=0, description="Preço do produto em centavos")
    stock_quantity: int = Field(default=0, ge=0, description="Quantidade disponível em estoque")
    weight_kg: Decimal | None = Field(default=None, ge=0, description="Peso unitário em kg")
    sku: str | None = Field(default=None, max_length=50, description="Código de referência/SKU")
    image_url: str | None = Field(default=None, max_length=500, description="URL da foto do produto")
    active: bool = Field(default=True, description="Indica se o produto está visível para venda")


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    price_cents: int | None = Field(default=None, ge=0)
    stock_quantity: int | None = Field(default=None, ge=0)
    weight_kg: Decimal | None = Field(default=None, ge=0)
    sku: str | None = Field(default=None, max_length=50)
    image_url: str | None = Field(default=None, max_length=500)
    active: bool | None = None


class ProductResponse(ProductBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

