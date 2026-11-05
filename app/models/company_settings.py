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

    # 5. Módulo de Contato & Suporte Humano (Transbordo)
    human_support_phone: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    human_support_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, default="Equipe de Atendimento"
    )
    human_support_message: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Configurações adicionais extensíveis
    custom_settings: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )

    # Propriedades de conveniência para PIX Direto / Bancos e Copia e Cola
    @property
    def pix_copy_paste(self) -> str | None:
        custom = self.custom_settings or {}
        creds = self.gateway_credentials or {}
        return custom.get("pix_copy_paste") or creds.get("pix_copy_paste")

    @pix_copy_paste.setter
    def pix_copy_paste(self, value: str | None) -> None:
        custom = dict(self.custom_settings or {})
        custom["pix_copy_paste"] = value
        self.custom_settings = custom
        creds = dict(self.gateway_credentials or {})
        creds["pix_copy_paste"] = value
        self.gateway_credentials = creds

    @property
    def pix_key(self) -> str | None:
        custom = self.custom_settings or {}
        creds = self.gateway_credentials or {}
        return custom.get("pix_key") or creds.get("pix_key")

    @pix_key.setter
    def pix_key(self, value: str | None) -> None:
        custom = dict(self.custom_settings or {})
        custom["pix_key"] = value
        self.custom_settings = custom
        creds = dict(self.gateway_credentials or {})
        creds["pix_key"] = value
        self.gateway_credentials = creds

    @property
    def pix_key_type(self) -> str | None:
        custom = self.custom_settings or {}
        creds = self.gateway_credentials or {}
        return custom.get("pix_key_type") or creds.get("pix_key_type") or "cnpj"

    @pix_key_type.setter
    def pix_key_type(self, value: str | None) -> None:
        custom = dict(self.custom_settings or {})
        custom["pix_key_type"] = value
        self.custom_settings = custom
        creds = dict(self.gateway_credentials or {})
        creds["pix_key_type"] = value
        self.gateway_credentials = creds

    @property
    def pix_merchant_name(self) -> str | None:
        custom = self.custom_settings or {}
        creds = self.gateway_credentials or {}
        return custom.get("pix_merchant_name") or creds.get("merchant_name") or self.company_name

    @pix_merchant_name.setter
    def pix_merchant_name(self, value: str | None) -> None:
        custom = dict(self.custom_settings or {})
        custom["pix_merchant_name"] = value
        self.custom_settings = custom
        creds = dict(self.gateway_credentials or {})
        creds["merchant_name"] = value
        self.gateway_credentials = creds

    @property
    def pix_merchant_city(self) -> str | None:
        custom = self.custom_settings or {}
        creds = self.gateway_credentials or {}
        return custom.get("pix_merchant_city") or creds.get("merchant_city") or "Brasil"

    @pix_merchant_city.setter
    def pix_merchant_city(self, value: str | None) -> None:
        custom = dict(self.custom_settings or {})
        custom["pix_merchant_city"] = value
        self.custom_settings = custom
        creds = dict(self.gateway_credentials or {})
        creds["merchant_city"] = value
        self.gateway_credentials = creds

    @property
    def pix_bank_name(self) -> str | None:
        custom = self.custom_settings or {}
        creds = self.gateway_credentials or {}
        return custom.get("pix_bank_name") or creds.get("bank_name")

    @pix_bank_name.setter
    def pix_bank_name(self, value: str | None) -> None:
        custom = dict(self.custom_settings or {})
        custom["pix_bank_name"] = value
        self.custom_settings = custom
        creds = dict(self.gateway_credentials or {})
        creds["bank_name"] = value
        self.gateway_credentials = creds

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
