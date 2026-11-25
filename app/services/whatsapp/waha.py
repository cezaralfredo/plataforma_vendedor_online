import asyncio
import base64
import logging
import re
import time
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

    async def set_typing(self, phone: str, is_typing: bool = True) -> None:
        """Envia indicador best-effort de 'digitando...' via WAHA entre bolhas de resposta."""
        if not self.base_url:
            return
        chat_id = self._format_chat_id(phone)
        endpoint = "startTyping" if is_typing else "stopTyping"
        url = f"{self.base_url}/api/{endpoint}"
        payload = {"session": self.session, "chatId": chat_id}
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                await client.post(url, json=payload, headers=self._headers())
        except Exception as exc:
            logger.debug("WAHA %s ignorado: %s", endpoint, exc)

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

    _IGNORED_JID_SUFFIXES = ("@g.us", "@broadcast", "@newsletter")
    _IGNORED_MESSAGE_TYPES = frozenset({
        "protocol",
        "reaction",
        "revoked",
        "e2e_notification",
        "notification_template",
        "call_log",
        "ciphertext",
        "gp2",
        "broadcast_notification",
        "status",
        "system",
    })
    _MAX_INBOUND_AGE_SECONDS = 300  # 5 minutos (ignora histórico antigo de sincronização)

    def parse_inbound_webhook(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Processa webhook do WAHA (apenas conversas privadas 1:1 reais e recentes)."""
        event = payload.get("event")
        msg_data = payload.get("payload") or payload

        if event and event not in ("message", "message.any"):
            return None

        data_block = msg_data.get("_data") or {}
        key_block = data_block.get("key") or msg_data.get("key") or {}

        # 1. Bloqueia IMEDIATAMENTE grupos (@g.us), Status/Stories (status@broadcast),
        # listas de transmissão (@broadcast) e canais (@newsletter) ANTES de resolver remetente.
        raw_jids = [
            msg_data.get("from"),
            msg_data.get("to"),
            msg_data.get("chatId"),
            key_block.get("remoteJid"),
        ]
        for jid in raw_jids:
            jid_str = str(jid or "").strip().lower()
            if jid_str == "status@broadcast" or jid_str.endswith(self._IGNORED_JID_SUFFIXES):
                return None

        # 2. Ignora mensagens de protocolo, reações, chamadas, notificações de sistema
        raw_type = str(msg_data.get("type") or "").strip().lower()
        if raw_type in self._IGNORED_MESSAGE_TYPES:
            return None

        # 3. Ignora mensagens antigas (ex: sincronização de histórico ao conectar WAHA)
        raw_ts = msg_data.get("timestamp") or data_block.get("messageTimestamp")
        if raw_ts is not None:
            try:
                ts_val = float(raw_ts)
                if ts_val > 1e12:  # milissegundos -> segundos
                    ts_val = ts_val / 1000.0
                if ts_val > 1_000_000_000 and (time.time() - ts_val) > self._MAX_INBOUND_AGE_SECONDS:
                    logger.debug("Mensagem WAHA ignorada por ser antiga (timestamp=%s).", raw_ts)
                    return None
            except (TypeError, ValueError):
                pass

        is_from_me = bool(msg_data.get("fromMe", False) or key_block.get("fromMe", False))

        if is_from_me:
            # Em mensagens enviadas pelo app móvel/bot, o destinatário é o cliente
            target = msg_data.get("to") or msg_data.get("chatId") or msg_data.get("from") or ""
        else:
            # Prioriza identificador canônico de telefone de chats 1:1 (NUNCA participantAlt de grupos/status)
            candidate_pns = [
                key_block.get("remoteJidAlt"),
                data_block.get("senderPn"),
                data_block.get("pn"),
                msg_data.get("senderPn"),
                msg_data.get("pn"),
            ]
            canonical = None
            for cand in candidate_pns:
                cand_str = str(cand or "").strip()
                if (
                    cand_str
                    and not cand_str.lower().endswith(self._IGNORED_JID_SUFFIXES)
                    and ("@c.us" in cand_str or "@s.whatsapp.net" in cand_str)
                ):
                    canonical = cand_str
                    break

            target = canonical or msg_data.get("from") or msg_data.get("chatId") or ""

        target_str = str(target or "").strip()
        if not target_str or target_str.lower().endswith(self._IGNORED_JID_SUFFIXES):
            return None

        # Se for um LID (@lid), preserva a tag para roteamento e envio correto
        if target_str.endswith("@lid"):
            clean_digits = re.sub(r"\D", "", target_str)
            clean_phone = f"{clean_digits}@lid" if clean_digits else ""
        else:
            clean_phone = self._clean_phone(target_str)

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
            mimetype = media_info.get("mimetype") or data_block.get("mimetype") or ""
            if "audio" in mimetype or raw_type in ("ptt", "audio"):
                media_type = "audio"
                media_url = media_info.get("url")
                if not text:
                    text = "[Mensagem de áudio recebida]"

        # Só processa se houver texto real ou áudio para transcrever (evita disparar para figurinhas/fotos vazias)
        if not text:
            return None

        msg_id = str(msg_data.get("id") or "")
        sender_name = (
            "Atendente"
            if is_from_me
            else (
                data_block.get("pushName")
                or data_block.get("notifyName")
                or msg_data.get("pushName")
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

    def _expected_webhook_url(self) -> str:
        from app.config import settings as app_settings

        webhook_url = "http://ria360-api:8000/api/webhooks/whatsapp/waha"
        if app_settings.whatsapp_webhook_secret:
            webhook_url = f"{webhook_url}?token={app_settings.whatsapp_webhook_secret}"
        return webhook_url

    def _session_webhook_config(self) -> dict[str, Any]:
        return {
            "ignore": {
                "status": True,
                "groups": True,
                "channels": True,
                "broadcast": True,
            },
            "webhooks": [
                {
                    "url": self._expected_webhook_url(),
                    "events": ["message.any", "session.status"],
                }
            ],
        }

    async def _ensure_webhook_synced(
        self, client: httpx.AsyncClient, session_data: dict[str, Any]
    ) -> None:
        expected_url = self._expected_webhook_url()
        cfg = session_data.get("config") or {}
        webhooks = cfg.get("webhooks") or []
        urls = [w.get("url") for w in webhooks if isinstance(w, dict)]
        events_ok = (
            len(webhooks) == 1
            and isinstance(webhooks[0], dict)
            and set(webhooks[0].get("events") or []) == {"message.any", "session.status"}
        )
        ignore_cfg = cfg.get("ignore") or {}
        ignore_ok = all(bool(ignore_cfg.get(k)) for k in ("status", "groups", "channels", "broadcast"))
        if urls == [expected_url] and events_ok and ignore_ok:
            return
        try:
            await client.put(
                f"{self.base_url}/api/sessions/{self.session}",
                json={"config": self._session_webhook_config()},
                headers=self._headers(),
                timeout=5.0,
            )
            logger.info("Webhook e filtros (ignore status/groups/channels/broadcast) da sessão WAHA '%s' sincronizados.", self.session)
        except Exception as sync_exc:
            logger.debug("Falha ao sincronizar webhook da sessão WAHA: %s", sync_exc)

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
                    # Sessão não existe, cria e inicializa com webhook do RIA 360
                    await client.post(
                        f"{self.base_url}/api/sessions/",
                        json={
                            "name": self.session,
                            "start": True,
                            "config": self._session_webhook_config(),
                        },
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
                await self._ensure_webhook_synced(client, data)
                status = data.get("status") or "UNKNOWN"
                me = data.get("me")

                if status == "STOPPED":
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

                if status == "FAILED":
                    # Credenciais antigas/revogadas impedem reconexão: limpa auth e reinicia para liberar novo QR Code
                    logger.warning(
                        "Sessão WAHA '%s' em estado FAILED; executando logout e reinício limpo para gerar novo QR.",
                        self.session,
                    )
                    try:
                        await client.post(
                            f"{self.base_url}/api/sessions/{self.session}/logout",
                            headers=self._headers(),
                            timeout=5.0,
                        )
                    except Exception as logout_exc:
                        logger.debug("Tentativa de logout WAHA em FAILED ignorada: %s", logout_exc)

                    await client.post(
                        f"{self.base_url}/api/sessions/{self.session}/start",
                        headers=self._headers(),
                        timeout=5.0,
                    )
                    await asyncio.sleep(2.5)
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
                    # 1. Tenta obter o QR code em imagem binária
                    try:
                        qr_res = await client.get(
                            f"{self.base_url}/api/{self.session}/auth/qr",
                            headers=self._headers(),
                            timeout=5.0,
                        )
                        if qr_res.status_code == 200 and qr_res.content:
                            ct = qr_res.headers.get("content-type", "")
                            if "image" in ct or qr_res.content.startswith(b"\x89PNG") or qr_res.content.startswith(b"\xff\xd8"):
                                b64 = base64.b64encode(qr_res.content).decode("ascii")
                                qr_code = f"data:image/png;base64,{b64}"
                            else:
                                # Tenta parse JSON se o WAHA retornou payload
                                try:
                                    qr_json = qr_res.json()
                                    if isinstance(qr_json, dict):
                                        if qr_json.get("image"):
                                            img_val = qr_json["image"]
                                            qr_code = img_val if img_val.startswith("data:") else f"data:image/png;base64,{img_val}"
                                        elif qr_json.get("raw"):
                                            qr_code = qr_json["raw"]
                                except Exception as json_err:
                                    logger.debug("Falha ao analisar JSON do QR WAHA: %s", json_err)
                    except Exception as qr_err:
                        logger.warning("Falha ao obter imagem do QR Code: %s", qr_err)

                    # 2. Se não conseguiu via auth/qr, tenta via screenshot
                    if not qr_code:
                        try:
                            sc_res = await client.get(
                                f"{self.base_url}/api/screenshot?session={self.session}",
                                headers=self._headers(),
                                timeout=5.0,
                            )
                            if sc_res.status_code == 200 and sc_res.content and sc_res.content.startswith(b"\x89PNG"):
                                b64 = base64.b64encode(sc_res.content).decode("ascii")
                                qr_code = f"data:image/png;base64,{b64}"
                        except Exception as sc_err:
                            logger.debug("Tentativa de screenshot ignorada: %s", sc_err)

                return {
                    "success": True,
                    "status": status,
                    "me": me,
                    "qr_code": qr_code,
                    "session": self.session,
                    "engine": data.get("engine", "NOWEB"),
                }
        except Exception as exc:
            logger.error("Erro ao consultar QR status do WAHA: %s", exc)
            return {"success": False, "error": str(exc)}

    async def restart_session(self) -> dict[str, Any]:
        """Reinicia a sessão no WAHA, limpando credenciais quebradas (FAILED) e sincronizando webhook."""
        if not self.base_url:
            return {"success": False, "error": "URL base do WAHA não configurada."}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                current = await client.get(
                    f"{self.base_url}/api/sessions/{self.session}",
                    headers=self._headers(),
                    timeout=5.0,
                )
                if current.status_code == 404:
                    create_res = await client.post(
                        f"{self.base_url}/api/sessions/",
                        json={
                            "name": self.session,
                            "start": True,
                            "config": self._session_webhook_config(),
                        },
                        headers=self._headers(),
                        timeout=5.0,
                    )
                    return {"success": create_res.status_code in (200, 201)}

                current_status = (current.json().get("status") if current.status_code == 200 else "") or ""
                if current_status in ("FAILED", "STOPPED"):
                    try:
                        await client.post(
                            f"{self.base_url}/api/sessions/{self.session}/logout",
                            headers=self._headers(),
                            timeout=5.0,
                        )
                    except Exception as logout_exc:
                        logger.debug("Tentativa de logout WAHA ignorada: %s", logout_exc)
                else:
                    try:
                        await client.post(
                            f"{self.base_url}/api/sessions/{self.session}/stop",
                            headers=self._headers(),
                            timeout=5.0,
                        )
                    except Exception as stop_exc:
                        logger.debug("Tentativa de parar sessão WAHA ignorada: %s", stop_exc)

                if current.status_code == 200:
                    await self._ensure_webhook_synced(client, current.json())

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
