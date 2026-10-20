import json
import logging
from typing import Any

import httpx

from app.services.ai.providers.base import (
    BaseLLMClient,
    LLMMessage,
    LLMResponse,
    ToolCall,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(BaseLLMClient):
    """
    Cliente universal para APIs compatíveis com a especificação OpenAI.
    Suporta OpenAI Oficial, Groq Cloud, OpenRouter, Custom (Ollama, vLLM, LM Studio)
    e Google Gemini (via OpenAI-compatible endpoint).
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        custom_headers: dict[str, str] | None = None,
        timeout_seconds: float = 20.0,
    ):
        super().__init__(api_key=api_key, model=model, base_url=base_url)
        self.endpoint_url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        self.custom_headers = custom_headers or {}
        self.timeout_seconds = timeout_seconds

    def _format_messages(self, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                formatted.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id or "",
                    "content": msg.content or "",
                })
            elif msg.role == "assistant":
                payload: dict[str, Any] = {"role": "assistant", "content": msg.content}
                if msg.tool_calls:
                    payload["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                                if isinstance(tc.arguments, dict)
                                else str(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                formatted.append(payload)
            else:
                formatted.append({
                    "role": msg.role,
                    "content": msg.content or "",
                })
        return formatted

    async def generate_response(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 600,
    ) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.custom_headers,
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            payload["tools"] = [t.to_openai_dict() for t in tools]
            payload["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                res = await client.post(self.endpoint_url, headers=headers, json=payload)
                res.raise_for_status()
                data = res.json()

            choice = data["choices"][0]
            message_data = choice.get("message", {})
            content = message_data.get("content")

            tool_calls: list[ToolCall] = []
            raw_tool_calls = message_data.get("tool_calls") or []
            for tc in raw_tool_calls:
                func_data = tc.get("function", {})
                func_name = func_data.get("name", "")
                raw_args = func_data.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception as parse_err:
                    logger.warning("Falha ao parsear argumentos da ferramenta '%s': %s", func_name, parse_err)
                    args = {}

                tool_calls.append(
                    ToolCall(
                        id=tc.get("id", ""),
                        name=func_name,
                        arguments=args if isinstance(args, dict) else {},
                    )
                )

            return LLMResponse(content=content, tool_calls=tool_calls, raw=data)

        except httpx.HTTPStatusError as http_err:
            logger.error("Erro HTTP ao chamar LLM (%s): status=%s body=%s", self.endpoint_url, http_err.response.status_code, http_err.response.text)
            raise
        except Exception as exc:
            logger.error("Falha inesperada ao comunicar com LLM (%s): %s", self.endpoint_url, exc)
            raise
