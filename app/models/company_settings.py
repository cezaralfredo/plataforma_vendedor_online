from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String, Text, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CompanySettings(Base):
    __tablename__ = "company_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Minha Empresa"
    )

    # 1. Módulo Financeiro
    payment_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    default_gateway: Mapped[str] = mapped_column(
        String(30), nullable=False, default="asaas"
    )
    gateway_credentials: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    accepted_payment_methods: Mapped[list] = mapped_column(
        JSON, nullable=False, default=lambda: ["pix"]
    )

    # 2. Módulo de Comissões
    commission_model: Mapped[str] = mapped_column(
        String(30), nullable=False, default="percentage"
    )
    default_commission_rate: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=0.0
    )
    split_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # 3. Módulo WhatsApp & Canais
    whatsapp_provider: Mapped[str] = mapped_column(
        String(20), nullable=False, default="evolution"
    )
    # Modo Coexistência (WhatsApp Business App móvel + IA simultâneos)
    coexistence_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    coexistence_pause_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=45, server_default="45"
    )
    # Evolution API
    evolution_base_url: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    evolution_instance: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    evolution_api_key: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    # WAHA (WhatsApp HTTP API)
    waha_base_url: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    waha_session: Mapped[str | None] = mapped_column(
        String(100), nullable=True, default="default"
    )
    waha_api_key: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    # Meta Cloud API Oficial
    meta_phone_number_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    meta_access_token: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    meta_waba_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    meta_verify_token: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    # 4. Módulo IA & Atendimento
    assistant_name: Mapped[str] = mapped_column(
        String(50), nullable=False, default="Assistente"
    )
    tone: Mapped[str] = mapped_column(
        String(20), nullable=False, default="casual"
    )
    require_cpf: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    welcome_message: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    # Provedor LLM configurável pelo cliente
    ai_provider: Mapped[str] = mapped_column(
        String(30), nullable=False, default="openai"
    )
    ai_model: Mapped[str] = mapped_column(
        String(100), nullable=False, default="gpt-4o-mini"
    )
    ai_api_key: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    ai_base_url: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    ai_temperature: Mapped[float] = mapped_column(
        Numeric(3, 2), nullable=False, default=0.3
    )
    ai_system_prompt: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Configurações adicionais extensíveis
    custom_settings: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
