import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDefinition:
    """Definição de uma ferramenta executável para os subagentes."""
    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai_dict(self) -> dict[str, Any]:
        """Formata para o schema de function calling padrão OpenAI."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_dict(self) -> dict[str, Any]:
        """Formata para o schema de tools da Anthropic Claude."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


@dataclass
class ToolCall:
    """Representa a chamada de ferramenta solicitada pelo LLM."""
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMMessage:
    """Mensagem unificada no histórico da conversa."""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class LLMResponse:
    """Resposta estruturada emitida pelo provedor de IA."""
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] | None = None


class BaseLLMClient(abc.ABC):
    """Contrato base para clientes de modelos de linguagem."""

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    @abc.abstractmethod
    async def generate_response(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 600,
    ) -> LLMResponse:
        """Gera resposta do modelo com suporte opcional a tool calling."""
        pass

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 600,
    ) -> LLMResponse:
        """Alias para generate_response."""
        return await self.generate_response(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
