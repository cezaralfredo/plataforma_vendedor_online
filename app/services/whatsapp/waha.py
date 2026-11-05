import asyncio
import base64
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
        if str(phone).endswith("@lid"):
            return str(phone)
        digits = re.sub(r"\D", "", str(phone))
        return digits

    def _format_chat_id(self, phone: str) -> str:
        raw = str(phone or "").strip()
        if "@" in raw:
            return raw
        clean = self._clean_phone(raw)
        # Identifica se é um LID do WhatsApp (>= 14 dígitos sem DDI 55 padrão)
        if len(clean) >= 14 and not clean.startswith("55"):
            return f"{clean}@lid"
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
        """Processa webhook do WAHA (evento 'message' ou 'message.any')."""
        event = payload.get("event")
        msg_data = payload.get("payload") or payload

        if event and event not in ("message", "message.any"):
            return None

        is_from_me = bool(msg_data.get("fromMe", False))
        data_block = msg_data.get("_data") or {}
        key_block = data_block.get("key") or msg_data.get("key") or {}

        if is_from_me:
            # Em mensagens enviadas pelo app móvel/bot, o destinatário é o cliente
            target = msg_data.get("to") or msg_data.get("chatId") or msg_data.get("from") or ""
        else:
            # 1. Prioriza identificador canônico de telefone se disponível (remoteJidAlt / participantAlt / senderPn)
            candidate_pns = [
                key_block.get("remoteJidAlt"),
                key_block.get("participantAlt"),
                data_block.get("senderPn"),
                data_block.get("pn"),
                msg_data.get("senderPn"),
                msg_data.get("pn"),
            ]
            canonical = None
            for cand in candidate_pns:
                if cand and ("@c.us" in str(cand) or "@s.whatsapp.net" in str(cand)):
                    canonical = str(cand)
                    break

            target = canonical or msg_data.get("from") or msg_data.get("chatId") or ""

        # Ignora mensagens de grupos do WhatsApp
        if str(target).endswith("@g.us"):
            return None

        # Se for um LID (@lid), preserva a tag para roteamento e envio correto
        if str(target).endswith("@lid"):
            clean_digits = re.sub(r"\D", "", str(target))
            clean_phone = f"{clean_digits}@lid"
        else:
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

    async def get_qr_status(self) -> dict[str, Any]:
        """Consulta status da sessão e retorna o QR Code em Base64 se aguardando pareamento."""
        if not self.base_url:
            return {"success": False, "error": "URL base do WAHA não configurada."}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    f"{self.base_url}/api/sessions/{self.session}",
                    headers=self._headers(),
                )

                if res.status_code == 404:
                    # Sessão não existe, cria e inicializa
                    await client.post(
                        f"{self.base_url}/api/sessions/",
                        json={"name": self.session},
                        headers=self._headers(),
                    )
                    await client.post(
                        f"{self.base_url}/api/sessions/{self.session}/start",
                        headers=self._headers(),
                    )
                    await asyncio.sleep(1.5)
                    res = await client.get(
                        f"{self.base_url}/api/sessions/{self.session}",
                        headers=self._headers(),
                    )

                if res.status_code != 200:
                    return {"success": False, "error": f"Erro do WAHA HTTP {res.status_code}"}

                data = res.json()
                status = data.get("status") or "UNKNOWN"
                me = data.get("me")

                if status in ("STOPPED", "FAILED"):
                    try:
                        await client.post(
                            f"{self.base_url}/api/sessions/{self.session}/stop",
                            headers=self._headers(),
                            timeout=5.0,
                        )
                    except Exception:
                        pass

                    await client.post(
                        f"{self.base_url}/api/sessions/{self.session}/start",
                        headers=self._headers(),
                        timeout=5.0,
                    )
                    await asyncio.sleep(2.0)
                    res = await client.get(
                        f"{self.base_url}/api/sessions/{self.session}",
                        headers=self._headers(),
                    )
                    if res.status_code == 200:
                        data = res.json()
                        status = data.get("status") or "UNKNOWN"
                        me = data.get("me")

                qr_code = None
                if status == "SCAN_QR_CODE":
                    try:
                        qr_res = await client.get(
                            f"{self.base_url}/api/{self.session}/auth/qr",
                            headers=self._headers(),
                            timeout=5.0,
                        )
                        if qr_res.status_code == 200 and qr_res.content:
                            b64 = base64.b64encode(qr_res.content).decode("ascii")
                            qr_code = f"data:image/png;base64,{b64}"
                    except Exception as qr_err:
                        logger.warning("Falha ao obter imagem do QR Code: %s", qr_err)

                return {
                    "success": True,
                    "status": status,
                    "me": me,
                    "qr_code": qr_code,
                }
        except Exception as exc:
            logger.error("Erro ao consultar QR status do WAHA: %s", exc)
            return {"success": False, "error": str(exc)}

    async def restart_session(self) -> dict[str, Any]:
        """Reinicia a sessão no WAHA para forçar novo QR Code."""
        if not self.base_url:
            return {"success": False, "error": "URL base do WAHA não configurada."}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                try:
                    await client.post(
                        f"{self.base_url}/api/sessions/{self.session}/stop",
                        headers=self._headers(),
                        timeout=5.0,
                    )
                except Exception:
                    pass

                res = await client.post(
                    f"{self.base_url}/api/sessions/{self.session}/start",
                    headers=self._headers(),
                    timeout=5.0,
                )
                return {"success": res.status_code in (200, 201)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def logout_session(self) -> dict[str, Any]:
        """Desconecta a conta do WhatsApp no WAHA."""
        if not self.base_url:
            return {"success": False, "error": "URL base do WAHA não configurada."}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    f"{self.base_url}/api/sessions/{self.session}/logout",
                    headers=self._headers(),
                )
                return {"success": res.status_code in (200, 201, 204)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
