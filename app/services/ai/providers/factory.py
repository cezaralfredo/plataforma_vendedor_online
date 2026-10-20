import logging

from app.config import settings
from app.models.company_settings import CompanySettings
from app.services.ai.providers.anthropic_client import AnthropicClient
from app.services.ai.providers.base import BaseLLMClient
from app.services.ai.providers.openai_client import OpenAICompatibleClient

logger = logging.getLogger(__name__)

# Modelos recomendados padrão por provedor
DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-20241022",
    "gemini": "gemini-1.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openai/gpt-4o-mini",
    "custom": "llama3.2",
}

# Endpoints padrão por provedor
DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "custom": "http://localhost:11434/v1",
}


def get_llm_client(company_settings: CompanySettings | None = None) -> BaseLLMClient | None:
    """
    Retorna a instância do cliente LLM de acordo com o provedor e credenciais
    configuradas pelo cliente (CompanySettings), com fallback para variáveis de ambiente.
    Retorna None caso nenhuma chave de API válida esteja configurada.
    """
    provider = "openai"
    custom_model: str | None = None
    custom_api_key: str | None = None
    custom_base_url: str | None = None

    if company_settings:
        provider = (company_settings.ai_provider or "openai").lower().strip()
        custom_model = (company_settings.ai_model or "").strip() or None
        custom_api_key = (company_settings.ai_api_key or "").strip() or None
        custom_base_url = (company_settings.ai_base_url or "").strip() or None

    # Resolve a API Key com prioridade: Configuração do Cliente -> Variável de Ambiente do Sistema
    api_key = custom_api_key
    if not api_key:
        if provider == "openai":
            api_key = getattr(settings, "openai_api_key", None)
        elif provider == "anthropic":
            api_key = getattr(settings, "anthropic_api_key", None)
        elif provider == "gemini":
            api_key = getattr(settings, "gemini_api_key", None)
        elif provider == "groq":
            api_key = getattr(settings, "groq_api_key", None)
        elif provider == "openrouter":
            api_key = getattr(settings, "openrouter_api_key", None)
        elif provider == "custom":
            api_key = "ollama-local"

    # Se ainda assim não houver chave (exceto custom que pode rodar sem auth local), não podemos instanciar
    if not api_key and provider != "custom":
        logger.info("Nenhuma chave de API configurada para o provedor de IA '%s'. Operando em fallback determinístico.", provider)
        return None

    model = custom_model or DEFAULT_MODELS.get(provider, "gpt-4o-mini")
    base_url = custom_base_url or DEFAULT_BASE_URLS.get(provider, "https://api.openai.com/v1")

    if provider == "anthropic":
        return AnthropicClient(
            api_key=api_key or "",
            model=model,
            base_url=base_url,
        )

    # OpenRouter, Groq, Gemini, OpenAI e Custom compartilham a especificação OpenAI
    custom_headers: dict[str, str] = {}
    if provider == "openrouter":
        custom_headers = {
            "HTTP-Referer": "https://agendatende.com.br",
            "X-Title": "Plataforma Vendedor IA",
        }

    return OpenAICompatibleClient(
        api_key=api_key or "local-key",
        model=model,
        base_url=base_url,
        custom_headers=custom_headers,
    )
