import logging
import re
from typing import Any

import httpx

from app.services.whatsapp.base import (
    BaseWhatsAppProvider,
    InboundMessage,
    SendMessageResult,
)

logger = logging.getLogger(__name__)


class WahaWhatsAppProvider(BaseWhatsAppProvider):
    name: str = "waha"

    def __init__(
        self,
        base_url: str | None,
        session: str | None = "default",
        api_key: str | None = None,
        timeout: float = 15.0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.session = session or "default"
        self.api_key = api_key or ""
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    @staticmethod
    def _clean_phone(phone: str) -> str:
        digits = re.sub(r"\D", "", phone)
        return digits

    def _format_chat_id(self, phone: str) -> str:
        clean = self._clean_phone(phone)
        return f"{clean}@c.us"

    async def send_text(self, phone: str, text: str) -> SendMessageResult:
        if not self.base_url:
            return SendMessageResult(
                success=False,
                provider=self.name,
                error="URL base do WAHA não configurada.",
            )

        chat_id = self._format_chat_id(phone)
        url = f"{self.base_url}/api/sendText"
        payload = {
            "session": self.session,
            "chatId": chat_id,
            "text": text,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers())
                if response.status_code in (200, 201):
                    data = response.json() if response.text else {}
                    msg_id = data.get("id") or data.get("messageId") or ""
                    return SendMessageResult(
                        success=True,
                        message_id=str(msg_id),
                        provider=self.name,
                        raw_response=data,
                    )
                return SendMessageResult(
                    success=False,
                    provider=self.name,
                    error=f"WAHA retornou erro HTTP {response.status_code}: {response.text}",
                )
        except Exception as exc:
            logger.error("Erro ao enviar mensagem via WAHA: %s", exc)
            return SendMessageResult(
                success=False,
                provider=self.name,
                error=str(exc),
            )

    async def send_image(
        self, phone: str, image_url: str, caption: str | None = None
    ) -> SendMessageResult:
        if not self.base_url:
            return SendMessageResult(
                success=False,
                provider=self.name,
                error="URL base do WAHA não configurada.",
            )

        chat_id = self._format_chat_id(phone)
        url = f"{self.base_url}/api/sendImage"
        payload = {
            "session": self.session,
            "chatId": chat_id,
            "file": {"url": image_url},
            "caption": caption or "",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers())
                if response.status_code in (200, 201):
                    data = response.json() if response.text else {}
                    return SendMessageResult(
                        success=True,
                        message_id=str(data.get("id") or ""),
                        provider=self.name,
                        raw_response=data,
                    )
                return SendMessageResult(
                    success=False,
                    provider=self.name,
                    error=f"WAHA retornou erro HTTP {response.status_code}: {response.text}",
                )
        except Exception as exc:
            logger.error("Erro ao enviar imagem via WAHA: %s", exc)
            return SendMessageResult(
                success=False,
                provider=self.name,
                error=str(exc),
            )

    def parse_inbound_webhook(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Processa webhook do WAHA (evento 'message')."""
        event = payload.get("event")
        msg_data = payload.get("payload") or payload

        if event and event != "message":
            return None

        is_from_me = bool(msg_data.get("fromMe", False))

        if is_from_me:
            # Em mensagens enviadas pelo app móvel/bot, o destinatário é o cliente
            target = msg_data.get("to") or msg_data.get("chatId") or msg_data.get("from") or ""
        else:
            target = msg_data.get("from") or ""

        # Ignora mensagens de grupos do WhatsApp
        if str(target).endswith("@g.us"):
            return None

        clean_phone = self._clean_phone(target)
        if not clean_phone:
            return None

        text = msg_data.get("body") or ""
        if not text and isinstance(msg_data.get("caption"), str):
            text = msg_data["caption"]

        text = text.strip()

        media_type = "text"
        media_url = None
        has_media = msg_data.get("hasMedia") is True
        media_info = msg_data.get("media") or {}
        if has_media:
            mimetype = media_info.get("mimetype") or msg_data.get("_data", {}).get("mimetype") or ""
            if "audio" in mimetype or msg_data.get("type") in ("ptt", "audio"):
                media_type = "audio"
                media_url = media_info.get("url")
                if not text:
                    text = "[Mensagem de áudio recebida]"

        if not text and not has_media:
            return None

        msg_id = str(msg_data.get("id") or "")
        data_block = msg_data.get("_data") or {}
        sender_name = (
            "Atendente"
            if is_from_me
            else (
                data_block.get("notifyName")
                or msg_data.get("notifyName")
                or "Cliente"
            )
        )

        return InboundMessage(
            provider=self.name,
            sender_phone=clean_phone,
            sender_name=sender_name,
            text=text,
            message_id=msg_id,
            media_type=media_type,
            media_url=media_url,
            is_from_me=is_from_me,
            raw_payload=payload,
        )


    async def check_health(self) -> dict[str, Any]:
        if not self.base_url:
            return {"configured": False, "status": "not_configured"}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(
                    f"{self.base_url}/api/sessions/{self.session}",
                    headers=self._headers(),
                )
                if res.status_code == 200:
                    data = res.json()
                    status = data.get("status") or "online"
                    return {"configured": True, "status": status, "session": self.session}
                return {
                    "configured": True,
                    "status": "error",
                    "http_code": res.status_code,
                }
        except Exception as exc:
            return {"configured": True, "status": "unavailable", "detail": str(exc)}
