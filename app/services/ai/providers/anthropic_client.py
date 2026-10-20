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


class AnthropicClient(BaseLLMClient):
    """
    Cliente nativo para a Messages API da Anthropic Claude (Claude 3.5 Sonnet / Haiku / Opus).
    Implementa o formato de tools e tool_use da Anthropic.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-haiku-20241022",
        base_url: str | None = None,
        timeout_seconds: float = 20.0,
    ):
        super().__init__(api_key=api_key, model=model, base_url=base_url)
        self.endpoint_url = (base_url or "https://api.anthropic.com/v1").rstrip("/") + "/messages"
        self.timeout_seconds = timeout_seconds

    def _convert_messages(self, messages: list[LLMMessage]) -> tuple[str, list[dict[str, Any]]]:
        system_prompts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                if msg.content:
                    system_prompts.append(msg.content)
            elif msg.role == "tool":
                # Resposta de tool para o Claude deve ser role 'user' com type 'tool_result'
                anthropic_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id or "",
                            "content": msg.content or "",
                        }
                    ],
                })
            elif msg.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        })
                anthropic_messages.append({
                    "role": "assistant",
                    "content": blocks if blocks else [{"type": "text", "text": ""}],
                })
            else:
                anthropic_messages.append({
                    "role": "user",
                    "content": msg.content or "",
                })

        return "\n\n".join(system_prompts), anthropic_messages

    async def generate_response(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 600,
    ) -> LLMResponse:
        system_prompt, formatted_messages = self._convert_messages(messages)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if system_prompt:
            payload["system"] = system_prompt

        if tools:
            payload["tools"] = [t.to_anthropic_dict() for t in tools]

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                res = await client.post(self.endpoint_url, headers=headers, json=payload)
                res.raise_for_status()
                data = res.json()

            content_blocks = data.get("content", [])
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []

            for block in content_blocks:
                b_type = block.get("type")
                if b_type == "text":
                    text_parts.append(block.get("text", ""))
                elif b_type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            arguments=block.get("input", {}),
                        )
                    )

            final_content = "\n".join(text_parts).strip() if text_parts else None
            return LLMResponse(content=final_content, tool_calls=tool_calls, raw=data)

        except httpx.HTTPStatusError as http_err:
            logger.error("Erro HTTP Anthropic (%s): status=%s body=%s", self.endpoint_url, http_err.response.status_code, http_err.response.text)
            raise
        except Exception as exc:
            logger.error("Falha inesperada ao comunicar com Anthropic (%s): %s", self.endpoint_url, exc)
            raise
