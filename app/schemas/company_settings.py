from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FinancialSettingsSchema(BaseModel):
    payment_required: bool = Field(
        default=True,
        description="Se verdadeiro, exige pagamento antecipado para confirmar a reserva. Se falso, confirma na hora.",
    )
    default_gateway: str = Field(
        default="asaas",
        description="Gateway padrão ('asaas', 'mercadopago', 'stripe', 'direct_pix', 'inter', 'nubank', 'itau', 'bradesco', 'santander', 'manual', 'none')",
    )
    gateway_credentials: dict[str, Any] = Field(
        default_factory=dict,
        description="Credenciais e tokens de acesso específicos do gateway",
    )
    accepted_payment_methods: list[str] = Field(
        default_factory=lambda: ["pix"],
        description="Métodos aceitos: pix, credit_card, debit_card, boleto, in_person, cash, payment_link, bank_transfer",
    )
    pix_copy_paste: str | None = Field(
        default=None,
        description="Código PIX Copia e Cola estático ou gerado pelo banco",
    )
    pix_key: str | None = Field(
        default=None,
        description="Chave PIX da empresa (CPF, CNPJ, Celular, E-mail ou Aleatória)",
    )
    pix_key_type: str | None = Field(
        default="cnpj",
        description="Tipo da chave PIX ('cnpj', 'cpf', 'phone', 'email', 'random')",
    )
    pix_merchant_name: str | None = Field(
        default=None,
        description="Nome do beneficiário / titular da conta PIX",
    )
    pix_bank_name: str | None = Field(
        default=None,
        description="Banco / Instituição Financeira do titular do PIX",
    )
    pix_merchant_city: str | None = Field(
        default="Brasil",
        description="Cidade do beneficiário para padrão BRCode BACEN",
    )


class CommissionSettingsSchema(BaseModel):
    commission_model: str = Field(
        default="percentage",
        description="Modelo de comissão ('percentage', 'fixed_value', 'split_gateway', 'manual_settlement', 'none')",
    )
    default_commission_rate: Decimal = Field(
        default=Decimal("0.0"),
        description="Taxa percentual ou valor fixo padrão de comissão",
    )
    split_enabled: bool = Field(
        default=False,
        description="Se o gateway realiza split financeiro automático no ato da cobrança",
    )


class ChannelSettingsSchema(BaseModel):
    whatsapp_provider: str = Field(
        default="evolution",
        description="Provedor de WhatsApp ('evolution', 'meta' ou 'waha')",
    )
    # Modo Coexistência (WhatsApp Business App móvel + IA simultâneos)
    coexistence_enabled: bool = Field(
        default=True,
        description="Se o modo Coexistence (WhatsApp Business App + IA) está ativo",
    )
    coexistence_pause_minutes: int = Field(
        default=45,
        description="Minutos de silêncio da IA após atendimento humano no WhatsApp Business",
    )
    # Evolution
    evolution_base_url: str | None = Field(
        default=None,
        description="URL base do servidor Evolution API",
    )
    evolution_instance: str | None = Field(
        default=None,
        description="Nome da instância na Evolution API",
    )
    evolution_api_key: str | None = Field(
        default=None,
        description="API Key da Evolution API",
    )
    # WAHA
    waha_base_url: str | None = Field(
        default=None,
        description="URL base do servidor WAHA (WhatsApp HTTP API)",
    )
    waha_session: str | None = Field(
        default="default",
        description="Nome da sessão no WAHA",
    )
    waha_api_key: str | None = Field(
        default=None,
        description="API Key de autenticação no WAHA",
    )
    # Meta
    meta_phone_number_id: str | None = Field(
        default=None,
        description="ID do telefone na Meta Cloud API",
    )
    meta_access_token: str | None = Field(
        default=None,
        description="Token de acesso WABA na Meta",
    )
    meta_waba_id: str | None = Field(
        default=None,
        description="ID da conta WhatsApp Business (WABA)",
    )
    meta_verify_token: str | None = Field(
        default=None,
        description="Token de verificação do Webhook da Meta",
    )


class AIAttendanceSettingsSchema(BaseModel):
    company_name: str = Field(
        default="Minha Empresa",
        description="Nome da empresa apresentado no atendimento",
    )
    assistant_name: str = Field(
        default="Assistente",
        description="Nome da persona do assistente virtual",
    )
    tone: str = Field(
        default="casual",
        description="Tom da linguagem ('casual', 'formal', 'executive')",
    )
    require_cpf: bool = Field(
        default=False,
        description="Se exige CPF para criar o cadastro do cliente",
    )
    welcome_message: str | None = Field(
        default=None,
        description="Mensagem de boas-vindas customizada",
    )
    ai_provider: str = Field(
        default="openai",
        description="Provedor de IA ('openai', 'anthropic', 'gemini', 'groq', 'openrouter', 'custom')",
    )
    ai_model: str = Field(
        default="gpt-4o-mini",
        description="Modelo de IA selecionado",
    )
    ai_api_key: str | None = Field(
        default=None,
        description="Chave de API do provedor de IA",
    )
    ai_base_url: str | None = Field(
        default=None,
        description="URL base opcional para provedores customizados ou Ollama/vLLM",
    )
    ai_temperature: Decimal = Field(
        default=Decimal("0.3"),
        description="Criatividade/temperatura da IA (0.0 a 1.0)",
    )
    ai_system_prompt: str | None = Field(
        default=None,
        description="Prompt de sistema customizado para o assistente",
    )
    human_support_phone: str | None = Field(
        default=None,
        description="Número de WhatsApp do suporte/atendimento humano (55XXXXXXXXXXX)",
    )
    human_support_name: str | None = Field(
        default="Equipe de Atendimento",
        description="Nome da equipe ou pessoa responsável pelo atendimento humano",
    )
    human_support_message: str | None = Field(
        default=None,
        description="Mensagem personalizada exibida ao transferir para atendimento humano",
    )


class CompanySettingsCreate(BaseModel):
    company_name: str = "Minha Empresa"
    payment_required: bool = True
    default_gateway: str = "asaas"
    gateway_credentials: dict[str, Any] = Field(default_factory=dict)
    accepted_payment_methods: list[str] = Field(default_factory=lambda: ["pix"])
    pix_copy_paste: str | None = None
    pix_key: str | None = None
    pix_key_type: str | None = "cnpj"
    pix_merchant_name: str | None = None
    pix_bank_name: str | None = None
    pix_merchant_city: str | None = "Brasil"
    commission_model: str = "percentage"
    default_commission_rate: Decimal = Decimal("0.0")
    split_enabled: bool = False
    whatsapp_provider: str = "evolution"
    coexistence_enabled: bool = True
    coexistence_pause_minutes: int = 45
    evolution_base_url: str | None = None
    evolution_instance: str | None = None
    evolution_api_key: str | None = None
    waha_base_url: str | None = None
    waha_session: str | None = "default"
    waha_api_key: str | None = None
    meta_phone_number_id: str | None = None
    meta_access_token: str | None = None
    meta_waba_id: str | None = None
    meta_verify_token: str | None = None
    assistant_name: str = "Assistente"
    tone: str = "casual"
    require_cpf: bool = False
    welcome_message: str | None = None
    ai_provider: str = "openai"
    ai_model: str = "gpt-4o-mini"
    ai_api_key: str | None = None
    ai_base_url: str | None = None
    ai_temperature: Decimal = Decimal("0.3")
    ai_system_prompt: str | None = None
    human_support_phone: str | None = None
    human_support_name: str | None = "Equipe de Atendimento"
    human_support_message: str | None = None
    custom_settings: dict[str, Any] = Field(default_factory=dict)


class CompanySettingsUpdate(BaseModel):
    company_name: str | None = None
    payment_required: bool | None = None
    default_gateway: str | None = None
    gateway_credentials: dict[str, Any] | None = None
    accepted_payment_methods: list[str] | None = None
    pix_copy_paste: str | None = None
    pix_key: str | None = None
    pix_key_type: str | None = None
    pix_merchant_name: str | None = None
    pix_bank_name: str | None = None
    pix_merchant_city: str | None = None
    commission_model: str | None = None
    default_commission_rate: Decimal | None = None
    split_enabled: bool | None = None
    whatsapp_provider: str | None = None
    coexistence_enabled: bool | None = None
    coexistence_pause_minutes: int | None = None
    evolution_base_url: str | None = None
    evolution_instance: str | None = None
    evolution_api_key: str | None = None
    waha_base_url: str | None = None
    waha_session: str | None = None
    waha_api_key: str | None = None
    meta_phone_number_id: str | None = None
    meta_access_token: str | None = None
    meta_waba_id: str | None = None
    meta_verify_token: str | None = None
    assistant_name: str | None = None
    tone: str | None = None
    require_cpf: bool | None = None
    welcome_message: str | None = None
    ai_provider: str | None = None
    ai_model: str | None = None
    ai_api_key: str | None = None
    ai_base_url: str | None = None
    ai_temperature: Decimal | None = None
    ai_system_prompt: str | None = None
    human_support_phone: str | None = None
    human_support_name: str | None = None
    human_support_message: str | None = None
    custom_settings: dict[str, Any] | None = None


class CompanySettingsResponse(BaseModel):
    id: int
    company_name: str
    payment_required: bool
    default_gateway: str
    gateway_credentials: dict[str, Any]
    accepted_payment_methods: list[str]
    pix_copy_paste: str | None = None
    pix_key: str | None = None
    pix_key_type: str | None = None
    pix_merchant_name: str | None = None
    pix_bank_name: str | None = None
    pix_merchant_city: str | None = None
    commission_model: str
    default_commission_rate: Decimal
    split_enabled: bool
    whatsapp_provider: str
    coexistence_enabled: bool = True
    coexistence_pause_minutes: int = 45
    evolution_base_url: str | None = None
    evolution_instance: str | None = None
    evolution_api_key: str | None = None
    waha_base_url: str | None = None
    waha_session: str | None = None
    waha_api_key: str | None = None
    meta_phone_number_id: str | None = None
    meta_access_token: str | None = None
    meta_waba_id: str | None = None
    meta_verify_token: str | None = None
    assistant_name: str
    tone: str
    require_cpf: bool
    welcome_message: str | None = None
    ai_provider: str = "openai"
    ai_model: str = "gpt-4o-mini"
    ai_api_key: str | None = None
    ai_base_url: str | None = None
    ai_temperature: Decimal = Decimal("0.3")
    ai_system_prompt: str | None = None
    human_support_phone: str | None = None
    human_support_name: str | None = "Equipe de Atendimento"
    human_support_message: str | None = None
    custom_settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
