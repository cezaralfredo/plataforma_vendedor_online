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


class EvolutionWhatsAppProvider(BaseWhatsAppProvider):
    name: str = "evolution"

    def __init__(
        self,
        base_url: str | None,
        instance: str | None,
        api_key: str | None = None,
        timeout: float = 15.0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.instance = instance or "default"
        self.api_key = api_key or ""
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["apikey"] = self.api_key
        return headers

    @staticmethod
    def _clean_phone(phone: str) -> str:
        return re.sub(r"\D", "", phone)

    async def send_text(self, phone: str, text: str) -> SendMessageResult:
        if not self.base_url or not self.instance:
            return SendMessageResult(
                success=False,
                provider=self.name,
                error="URL base ou instância da Evolution API não configurada.",
            )

        clean = self._clean_phone(phone)
        url = f"{self.base_url}/message/sendText/{self.instance}"
        payload = {
            "number": clean,
            "text": text,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers())
                if response.status_code in (200, 201):
                    data = response.json() if response.text else {}
                    msg_id = data.get("key", {}).get("id") or data.get("id") or ""
                    return SendMessageResult(
                        success=True,
                        message_id=str(msg_id),
                        provider=self.name,
                        raw_response=data,
                    )
                return SendMessageResult(
                    success=False,
                    provider=self.name,
                    error=f"Evolution API retornou erro HTTP {response.status_code}: {response.text}",
                )
        except Exception as exc:
            logger.error("Erro ao enviar mensagem via Evolution API: %s", exc)
            return SendMessageResult(
                success=False,
                provider=self.name,
                error=str(exc),
            )

    async def send_image(
        self, phone: str, image_url: str, caption: str | None = None
    ) -> SendMessageResult:
        if not self.base_url or not self.instance:
            return SendMessageResult(
                success=False,
                provider=self.name,
                error="URL base ou instância da Evolution API não configurada.",
            )

        clean = self._clean_phone(phone)
        url = f"{self.base_url}/message/sendMedia/{self.instance}"
        payload = {
            "number": clean,
            "mediatype": "image",
            "media": image_url,
            "caption": caption or "",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers())
                if response.status_code in (200, 201):
                    data = response.json() if response.text else {}
                    return SendMessageResult(
                        success=True,
                        message_id=str(data.get("key", {}).get("id") or ""),
                        provider=self.name,
                        raw_response=data,
                    )
                return SendMessageResult(
                    success=False,
                    provider=self.name,
                    error=f"Evolution API retornou erro HTTP {response.status_code}: {response.text}",
                )
        except Exception as exc:
            logger.error("Erro ao enviar imagem via Evolution API: %s", exc)
            return SendMessageResult(
                success=False,
                provider=self.name,
                error=str(exc),
            )

    def parse_inbound_webhook(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Processa webhook da Evolution API (evento messages.upsert)."""
        body = payload.get("data") or payload
        key = body.get("key") or {}

        remote_jid = key.get("remoteJid") or ""
        # Ignora mensagens de grupos do WhatsApp
        if remote_jid.endswith("@g.us"):
            return None

        clean_phone = self._clean_phone(remote_jid)
        if not clean_phone:
            return None

        is_from_me = bool(key.get("fromMe", False))

        msg = body.get("message") or {}
        text = (
            msg.get("conversation")
            or msg.get("extendedTextMessage", {}).get("text")
            or msg.get("imageMessage", {}).get("caption")
            or msg.get("videoMessage", {}).get("caption")
            or ""
        ).strip()

        audio_msg = msg.get("audioMessage")
        media_type = "text"
        media_url = None

        if audio_msg:
            media_type = "audio"
            media_url = audio_msg.get("url")
            if not text:
                text = "[Mensagem de áudio recebida]"

        if not text and not audio_msg:
            return None

        msg_id = str(key.get("id") or "")
        sender_name = body.get("pushName") or ("Atendente" if is_from_me else "Cliente")

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
        if not self.base_url or not self.instance:
            return {"configured": False, "status": "not_configured"}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(
                    f"{self.base_url}/instance/connectionState/{self.instance}",
                    headers=self._headers(),
                )
                if res.status_code == 200:
                    data = res.json()
                    state = data.get("instance", {}).get("state") or data.get("state") or "online"
                    return {"configured": True, "status": state, "instance": self.instance}
                return {"configured": True, "status": "error", "http_code": res.status_code}
        except Exception as exc:
            return {"configured": True, "status": "unavailable", "detail": str(exc)}
