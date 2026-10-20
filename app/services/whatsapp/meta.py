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


class MetaWhatsAppProvider(BaseWhatsAppProvider):
    name: str = "meta"

    def __init__(
        self,
        phone_number_id: str | None,
        access_token: str | None,
        waba_id: str | None = None,
        verify_token: str | None = None,
        api_version: str = "v21.0",
        timeout: float = 15.0,
    ):
        self.phone_number_id = phone_number_id or ""
        self.access_token = access_token or ""
        self.waba_id = waba_id or ""
        self.verify_token = verify_token or ""
        self.api_version = api_version
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _clean_phone(phone: str) -> str:
        return re.sub(r"\D", "", phone)

    async def send_text(self, phone: str, text: str) -> SendMessageResult:
        if not self.phone_number_id or not self.access_token:
            return SendMessageResult(
                success=False,
                provider=self.name,
                error="Phone Number ID ou Access Token da Meta não configurado.",
            )

        clean = self._clean_phone(phone)
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers())
                if response.status_code in (200, 201):
                    data = response.json()
                    msg_id = data.get("messages", [{}])[0].get("id", "")
                    return SendMessageResult(
                        success=True,
                        message_id=str(msg_id),
                        provider=self.name,
                        raw_response=data,
                    )
                return SendMessageResult(
                    success=False,
                    provider=self.name,
                    error=f"Meta API retornou erro HTTP {response.status_code}: {response.text}",
                )
        except Exception as exc:
            logger.error("Erro ao enviar mensagem via Meta Cloud API: %s", exc)
            return SendMessageResult(
                success=False,
                provider=self.name,
                error=str(exc),
            )

    async def send_image(
        self, phone: str, image_url: str, caption: str | None = None
    ) -> SendMessageResult:
        if not self.phone_number_id or not self.access_token:
            return SendMessageResult(
                success=False,
                provider=self.name,
                error="Phone Number ID ou Access Token da Meta não configurado.",
            )

        clean = self._clean_phone(phone)
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        image_obj: dict[str, Any] = {"link": image_url}
        if caption:
            image_obj["caption"] = caption

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean,
            "type": "image",
            "image": image_obj,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers())
                if response.status_code in (200, 201):
                    data = response.json()
                    msg_id = data.get("messages", [{}])[0].get("id", "")
                    return SendMessageResult(
                        success=True,
                        message_id=str(msg_id),
                        provider=self.name,
                        raw_response=data,
                    )
                return SendMessageResult(
                    success=False,
                    provider=self.name,
                    error=f"Meta API retornou erro HTTP {response.status_code}: {response.text}",
                )
        except Exception as exc:
            logger.error("Erro ao enviar imagem via Meta Cloud API: %s", exc)
            return SendMessageResult(
                success=False,
                provider=self.name,
                error=str(exc),
            )

    def parse_inbound_webhook(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Processa webhook da Meta Cloud API Oficial."""
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})

        messages = value.get("messages")
        if not messages or not isinstance(messages, list):
            return None

        msg = messages[0]
        metadata = value.get("metadata", {})
        display_phone = self._clean_phone(str(metadata.get("display_phone_number") or ""))

        raw_from = self._clean_phone(str(msg.get("from") or ""))
        raw_to = self._clean_phone(str(msg.get("to") or msg.get("recipient_id") or ""))
        is_echo = bool(msg.get("is_echo", False))

        # No modo Coexistence da Meta, mensagens enviadas pelo WhatsApp Business App
        # podem vir com is_echo=True ou o campo "from" correspondente ao número da empresa
        if is_echo or (display_phone and raw_from == display_phone and raw_to):
            is_from_me = True
            sender_phone = raw_to or raw_from
            sender_name = "Atendente"
        else:
            is_from_me = False
            sender_phone = raw_from
            contacts = value.get("contacts", [{}])
            sender_name = contacts[0].get("profile", {}).get("name") if contacts else "Cliente"

        if not sender_phone:
            return None

        text = ""
        msg_type = msg.get("type")
        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
        elif msg_type == "interactive":
            interactive = msg.get("interactive", {})
            btn = interactive.get("button_reply") or interactive.get("list_reply") or {}
            text = btn.get("title") or btn.get("id") or ""
        elif msg_type == "image":
            text = msg.get("image", {}).get("caption", "")

        text = text.strip()
        if not text:
            return None

        msg_id = str(msg.get("id") or "")

        return InboundMessage(
            provider=self.name,
            sender_phone=sender_phone,
            sender_name=sender_name or "Cliente",
            text=text,
            message_id=msg_id,
            is_from_me=is_from_me,
            raw_payload=payload,
        )

    async def check_health(self) -> dict[str, Any]:
        if not self.phone_number_id or not self.access_token:
            return {"configured": False, "status": "not_configured"}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(
                    f"{self.base_url}/{self.phone_number_id}",
                    headers=self._headers(),
                )
                if res.status_code == 200:
                    data = res.json()
                    return {
                        "configured": True,
                        "status": "online",
                        "display_phone_number": data.get("display_phone_number"),
                        "verified_name": data.get("verified_name"),
                    }
                return {"configured": True, "status": "error", "http_code": res.status_code}
        except Exception as exc:
            return {"configured": True, "status": "unavailable", "detail": str(exc)}
