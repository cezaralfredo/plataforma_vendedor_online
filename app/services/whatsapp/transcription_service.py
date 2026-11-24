import base64
import ipaddress
import logging
import socket
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models.company_settings import CompanySettings
from app.services.company_settings_service import CompanySettingsService
from app.services.whatsapp.base import InboundMessage

logger = logging.getLogger(__name__)

MAX_AUDIO_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

DEFAULT_OPENROUTER_AUDIO_MODEL = "google/gemini-2.5-flash"
DEFAULT_GEMINI_AUDIO_MODEL = "gemini-2.5-flash"

_TRANSCRIPTION_PROMPT = (
    "Transcreva fielmente em português o conteúdo falado neste áudio. "
    "Retorne APENAS o texto transcrito, sem comentários, explicações, prefixos ou aspas."
)

_LOCAL_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

_MIME_TO_FORMAT: dict[str, tuple[str, str]] = {
    "audio/ogg": ("ogg", "audio/ogg"),
    "audio/opus": ("ogg", "audio/ogg"),
    "application/ogg": ("ogg", "audio/ogg"),
    "audio/mpeg": ("mp3", "audio/mpeg"),
    "audio/mp3": ("mp3", "audio/mpeg"),
    "audio/wav": ("wav", "audio/wav"),
    "audio/x-wav": ("wav", "audio/wav"),
    "audio/mp4": ("m4a", "audio/mp4"),
    "audio/m4a": ("m4a", "audio/mp4"),
    "audio/x-m4a": ("m4a", "audio/mp4"),
    "audio/aac": ("aac", "audio/aac"),
    "audio/webm": ("webm", "audio/webm"),
    "audio/flac": ("flac", "audio/flac"),
}

_EXT_TO_FORMAT: dict[str, tuple[str, str]] = {
    ".ogg": ("ogg", "audio/ogg"),
    ".oga": ("ogg", "audio/ogg"),
    ".opus": ("ogg", "audio/ogg"),
    ".mp3": ("mp3", "audio/mpeg"),
    ".wav": ("wav", "audio/wav"),
    ".m4a": ("m4a", "audio/mp4"),
    ".mp4": ("m4a", "audio/mp4"),
    ".aac": ("aac", "audio/aac"),
    ".webm": ("webm", "audio/webm"),
    ".flac": ("flac", "audio/flac"),
}


def _detect_audio_format(
    filename: str = "audio.ogg", content_type: str | None = None
) -> tuple[str, str]:
    """Retorna (format_short, mime_type) compatíveis com Whisper, OpenRouter e Gemini."""
    if content_type:
        base_ct = content_type.split(";", 1)[0].strip().lower()
        if base_ct in _MIME_TO_FORMAT:
            return _MIME_TO_FORMAT[base_ct]

    lower_name = (filename or "").lower().split("?", 1)[0]
    for ext, pair in _EXT_TO_FORMAT.items():
        if lower_name.endswith(ext):
            return pair

    return ("ogg", "audio/ogg")


def _is_safe_audio_url(audio_url: str, allowed_hosts: set[str]) -> bool:
    try:
        parsed = urlparse(audio_url)
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False

    hostname = parsed.hostname.lower()
    if hostname in allowed_hosts:
        return True

    if parsed.scheme != "https":
        return False

    try:
        infos = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False

    for info in infos:
        ip_str = info[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return False

    return True


class AudioTranscriptionService:
    """
    Serviço de transcrição de áudios recebidos pelo WhatsApp com suporte a múltiplos provedores:
    - OpenAI / Groq (Whisper)
    - OpenRouter (multimodal input_audio via Gemini Flash)
    - Google Gemini API direta (inline_data)
    - Fallback automático caso o provedor principal (ex.: Anthropic/Ollama) não suporte áudio.
    """

    def __init__(self, db: Session):
        self.db = db
        self.company_settings: CompanySettings = CompanySettingsService(db).get_or_create_default()

    def _effective_waha_base_url(self) -> str:
        return str(
            getattr(self.company_settings, "waha_base_url", None)
            or app_settings.waha_base_url
            or ""
        ).rstrip("/")

    def _effective_waha_api_key(self) -> str:
        return str(
            getattr(self.company_settings, "waha_api_key", None)
            or app_settings.waha_api_key
            or ""
        ).strip()

    def _effective_evolution_base_url(self) -> str:
        return str(
            getattr(self.company_settings, "evolution_base_url", None) or ""
        ).rstrip("/")

    def _allowed_media_hosts(self) -> set[str]:
        hosts: set[str] = set()
        for raw_url in (
            self._effective_waha_base_url(),
            self._effective_evolution_base_url(),
            app_settings.waha_base_url,
        ):
            if raw_url:
                parsed = urlparse(str(raw_url))
                if parsed.hostname:
                    hosts.add(parsed.hostname.lower())
        return hosts

    def _normalize_media_url(self, audio_url: str, provider: str | None = None) -> str:
        """
        Reescreve URLs locais retornadas pelo container WAHA (ex.: http://localhost:3000/api/files/...)
        para o host interno configurado em waha_base_url (ex.: http://waha:3000/api/files/...).
        """
        raw = (audio_url or "").strip()
        if not raw:
            return raw

        waha_base = self._effective_waha_base_url()
        if not waha_base:
            return raw

        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        is_waha_local = host in _LOCAL_LOOPBACK_HOSTS and (
            (provider or "").lower() == "waha" or parsed.path.startswith("/api/files/")
        )
        if not is_waha_local:
            return raw

        target = urlparse(waha_base)
        if not target.scheme or not target.netloc:
            return raw

        base_path = target.path.rstrip("/")
        new_path = f"{base_path}{parsed.path}" if base_path else parsed.path
        rewritten = urlunparse(
            (target.scheme, target.netloc, new_path, parsed.params, parsed.query, "")
        )
        logger.debug("URL de mídia WAHA normalizada para host interno: %s", rewritten)
        return rewritten

    def _resolve_download_headers(
        self,
        audio_url: str,
        provider: str | None = None,
        explicit_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Monta cabeçalhos de autenticação adequados para baixar mídia do provedor WhatsApp."""
        headers: dict[str, str] = dict(explicit_headers or {})
        parsed = urlparse(audio_url)
        host = (parsed.hostname or "").lower()

        waha_base = self._effective_waha_base_url()
        waha_host = (urlparse(waha_base).hostname or "").lower() if waha_base else ""
        evo_base = self._effective_evolution_base_url()
        evo_host = (urlparse(evo_base).hostname or "").lower() if evo_base else ""

        prov = (provider or getattr(self.company_settings, "whatsapp_provider", "") or "").lower()

        if (prov == "waha" or (waha_host and host == waha_host)) and "X-Api-Key" not in headers:
            waha_key = self._effective_waha_api_key()
            if waha_key:
                headers["X-Api-Key"] = waha_key
        elif (prov == "evolution" or (evo_host and host == evo_host)) and "apikey" not in headers:
            evo_key = str(getattr(self.company_settings, "evolution_api_key", None) or "").strip()
            if evo_key:
                headers["apikey"] = evo_key
        elif prov == "meta" and "Authorization" not in headers:
            meta_token = str(getattr(self.company_settings, "meta_access_token", None) or "").strip()
            if meta_token:
                headers["Authorization"] = f"Bearer {meta_token}"

        return headers

    async def _transcribe_with_whisper(
        self,
        provider: str,
        api_key: str,
        endpoint: str,
        model: str,
        audio_data: bytes,
        filename: str,
        mime_type: str,
    ) -> str | None:
        headers = {"Authorization": f"Bearer {api_key}"}
        files = {"file": (filename, audio_data, mime_type)}
        data = {
            "model": model,
            "language": "pt",
            "temperature": "0.0",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(endpoint, headers=headers, files=files, data=data)
                if res.status_code == 200:
                    result = res.json()
                    transcription = (result.get("text") or "").strip()
                    logger.info(
                        "Áudio transcrito com sucesso via Whisper (%s, %d caracteres).",
                        provider,
                        len(transcription),
                    )
                    return transcription or None
                logger.warning(
                    "Falha na API Whisper (%s): HTTP %s", provider, res.status_code
                )
        except Exception as exc:
            logger.error("Erro ao chamar serviço de transcrição Whisper (%s): %s", provider, exc)
        return None

    async def _transcribe_with_openrouter(
        self,
        api_key: str,
        audio_data: bytes,
        audio_format: str,
    ) -> str | None:
        b64_audio = base64.b64encode(audio_data).decode("ascii")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": app_settings.app_base_url or f"https://{app_settings.domain}",
            "X-Title": app_settings.app_name or "RIA 360",
        }
        payload = {
            "model": DEFAULT_OPENROUTER_AUDIO_MODEL,
            "temperature": 0.0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _TRANSCRIPTION_PROMPT},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": b64_audio,
                                "format": audio_format,
                            },
                        },
                    ],
                }
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if res.status_code == 200:
                    data = res.json()
                    choices = data.get("choices") or []
                    if choices:
                        content = choices[0].get("message", {}).get("content") or ""
                        if isinstance(content, list):
                            content = " ".join(
                                str(part.get("text") or "")
                                for part in content
                                if isinstance(part, dict)
                            )
                        transcription = str(content).strip()
                        logger.info(
                            "Áudio transcrito com sucesso via OpenRouter (%s, %d caracteres).",
                            DEFAULT_OPENROUTER_AUDIO_MODEL,
                            len(transcription),
                        )
                        return transcription or None
                logger.warning(
                    "Falha na transcrição via OpenRouter: HTTP %s", res.status_code
                )
        except Exception as exc:
            logger.error("Erro ao chamar transcrição multimodal via OpenRouter: %s", exc)
        return None

    async def _transcribe_with_gemini(
        self,
        api_key: str,
        audio_data: bytes,
        mime_type: str,
    ) -> str | None:
        b64_audio = base64.b64encode(audio_data).decode("ascii")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{DEFAULT_GEMINI_AUDIO_MODEL}:generateContent"
        )
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": _TRANSCRIPTION_PROMPT},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64_audio,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.0},
        }
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    candidates = data.get("candidates") or []
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts") or []
                        text = " ".join(
                            str(p.get("text") or "") for p in parts if isinstance(p, dict)
                        ).strip()
                        logger.info(
                            "Áudio transcrito com sucesso via Gemini (%s, %d caracteres).",
                            DEFAULT_GEMINI_AUDIO_MODEL,
                            len(text),
                        )
                        return text or None
                logger.warning("Falha na transcrição via Gemini: HTTP %s", res.status_code)
        except Exception as exc:
            logger.error("Erro ao chamar transcrição via Gemini: %s", exc)
        return None

    def _build_provider_candidates(self) -> list[tuple[str, str]]:
        """
        Retorna lista ordenada de (provedor, api_key) para tentativa de transcrição,
        priorizando o provedor configurado no painel e usando fallbacks disponíveis.
        """
        primary = (self.company_settings.ai_provider or "openai").lower().strip()
        panel_key = (self.company_settings.ai_api_key or "").strip()
        triage_key = (getattr(self.company_settings, "triage_api_key", None) or "").strip()

        candidates: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def _add(prov: str, key: str | None) -> None:
            clean_key = (key or "").strip()
            if clean_key and (prov, clean_key) not in seen:
                seen.add((prov, clean_key))
                candidates.append((prov, clean_key))

        # 1. Provedor principal configurado no painel
        if primary == "groq":
            _add("groq", panel_key or app_settings.groq_api_key)
        elif primary == "openai":
            _add("openai", panel_key or app_settings.openai_api_key)
        elif primary == "openrouter":
            _add("openrouter", panel_key or triage_key or app_settings.openrouter_api_key)
        elif primary == "gemini":
            _add("gemini", panel_key or app_settings.gemini_api_key)

        # 2. Fallbacks automáticos (útil para Anthropic, DeepSeek, Ollama ou falha temporária)
        _add("groq", app_settings.groq_api_key)
        _add("openai", app_settings.openai_api_key)
        _add("openrouter", triage_key or app_settings.openrouter_api_key)
        _add("gemini", app_settings.gemini_api_key)

        return candidates

    async def transcribe_audio_bytes(
        self,
        audio_data: bytes,
        filename: str = "audio.ogg",
        content_type: str | None = None,
    ) -> str | None:
        """Transcreve arquivo de áudio usando Whisper (OpenAI/Groq), OpenRouter ou Gemini."""
        if not audio_data or len(audio_data) < 100:
            return None

        audio_format, mime_type = _detect_audio_format(filename, content_type)
        candidates = self._build_provider_candidates()

        if not candidates:
            logger.info(
                "Transcrição de áudio ignorada: provedor '%s' não suporta áudio nativamente "
                "e não há chave OpenAI, Groq, OpenRouter ou Gemini configurada.",
                (self.company_settings.ai_provider or "").lower(),
            )
            return None

        for provider, api_key in candidates:
            if provider == "groq":
                result = await self._transcribe_with_whisper(
                    provider="groq",
                    api_key=api_key,
                    endpoint="https://api.groq.com/openai/v1/audio/transcriptions",
                    model="whisper-large-v3-turbo",
                    audio_data=audio_data,
                    filename=filename,
                    mime_type=mime_type,
                )
            elif provider == "openai":
                result = await self._transcribe_with_whisper(
                    provider="openai",
                    api_key=api_key,
                    endpoint="https://api.openai.com/openai/v1/audio/transcriptions",
                    model="whisper-1",
                    audio_data=audio_data,
                    filename=filename,
                    mime_type=mime_type,
                )
            elif provider == "openrouter":
                result = await self._transcribe_with_openrouter(
                    api_key=api_key,
                    audio_data=audio_data,
                    audio_format=audio_format,
                )
            elif provider == "gemini":
                result = await self._transcribe_with_gemini(
                    api_key=api_key,
                    audio_data=audio_data,
                    mime_type=mime_type,
                )
            else:
                result = None

            if result:
                return result

        return None

    async def download_and_transcribe(
        self,
        audio_url: str,
        auth_header: dict[str, str] | None = None,
        provider: str | None = None,
    ) -> str | None:
        """Baixa o arquivo de áudio da URL validada (anti-SSRF e limite de tamanho) e executa a transcrição."""
        normalized_url = self._normalize_media_url(audio_url, provider=provider)
        if not _is_safe_audio_url(normalized_url, self._allowed_media_hosts()):
            logger.warning(
                "Download de áudio bloqueado por validação de segurança (URL não permitida)."
            )
            return None

        headers = self._resolve_download_headers(
            normalized_url,
            provider=provider,
            explicit_headers=auth_header,
        )

        try:
            chunks: list[bytes] = []
            total_bytes = 0
            content_type: str | None = None
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
                async with client.stream("GET", normalized_url, headers=headers) as res:
                    if res.status_code != 200:
                        logger.warning(
                            "Falha ao baixar áudio do WhatsApp: HTTP %s (%s)",
                            res.status_code,
                            urlparse(normalized_url).path,
                        )
                        return None
                    content_type = res.headers.get("content-type")
                    async for chunk in res.aiter_bytes(chunk_size=65536):
                        total_bytes += len(chunk)
                        if total_bytes > MAX_AUDIO_DOWNLOAD_BYTES:
                            logger.warning(
                                "Download de áudio abortado: arquivo excede limite de %d bytes.",
                                MAX_AUDIO_DOWNLOAD_BYTES,
                            )
                            return None
                        chunks.append(chunk)
            if chunks:
                path_part = urlparse(normalized_url).path
                filename = path_part.rsplit("/", 1)[-1] if "/" in path_part else "audio.ogg"
                if "." not in filename:
                    filename = "audio.ogg"
                return await self.transcribe_audio_bytes(
                    b"".join(chunks),
                    filename=filename,
                    content_type=content_type,
                )
        except Exception as exc:
            logger.error("Erro ao baixar áudio do WhatsApp: %s", exc)
        return None

    def _decode_base64_audio(self, raw_b64: str) -> bytes | None:
        cleaned = (raw_b64 or "").strip()
        if not cleaned:
            return None
        if "," in cleaned and cleaned.lower().startswith("data:"):
            cleaned = cleaned.split(",", 1)[1].strip()
        try:
            decoded = base64.b64decode(cleaned, validate=False)
        except Exception as exc:
            logger.warning("Falha ao decodificar áudio em base64: %s", exc)
            return None
        if len(decoded) > MAX_AUDIO_DOWNLOAD_BYTES:
            logger.warning(
                "Áudio em base64 ignorado: excede limite de %d bytes.",
                MAX_AUDIO_DOWNLOAD_BYTES,
            )
            return None
        return decoded

    async def _fetch_evolution_decrypted_audio(
        self, inbound: InboundMessage
    ) -> tuple[bytes, str | None] | None:
        evo_base = self._effective_evolution_base_url()
        evo_instance = str(getattr(self.company_settings, "evolution_instance", None) or "").strip()
        evo_key = str(getattr(self.company_settings, "evolution_api_key", None) or "").strip()
        if not evo_base or not evo_instance:
            return None

        raw_payload = inbound.raw_payload or {}
        body = raw_payload.get("data") or raw_payload
        key = body.get("key") if isinstance(body, dict) else None
        if not isinstance(key, dict) or not key.get("id"):
            if not inbound.message_id:
                return None
            key = {"id": inbound.message_id}

        url = f"{evo_base}/chat/getBase64FromMediaMessage/{evo_instance}"
        headers = {"Content-Type": "application/json"}
        if evo_key:
            headers["apikey"] = evo_key

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(
                    url,
                    headers=headers,
                    json={"message": {"key": key}, "convertToMp4": False},
                )
                if res.status_code in (200, 201):
                    data = res.json() if res.text else {}
                    b64 = data.get("base64")
                    if isinstance(b64, str) and b64.strip():
                        decoded = self._decode_base64_audio(b64)
                        if decoded:
                            mimetype = data.get("mimetype") or inbound.media_mimetype
                            return decoded, str(mimetype) if mimetype else None
                else:
                    logger.warning(
                        "Evolution getBase64FromMediaMessage retornou HTTP %s", res.status_code
                    )
        except Exception as exc:
            logger.error("Erro ao obter áudio descriptografado na Evolution API: %s", exc)
        return None

    async def _resolve_meta_media_url(self, media_id: str) -> tuple[str | None, str | None]:
        meta_token = str(getattr(self.company_settings, "meta_access_token", None) or "").strip()
        if not meta_token or not media_id:
            return None, None

        url = f"https://graph.facebook.com/v21.0/{media_id}"
        headers = {"Authorization": f"Bearer {meta_token}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    media_url = data.get("url")
                    mime_type = data.get("mime_type")
                    return (
                        str(media_url) if media_url else None,
                        str(mime_type) if mime_type else None,
                    )
                logger.warning(
                    "Meta Graph API retornou HTTP %s ao resolver media_id=%s",
                    res.status_code,
                    media_id,
                )
        except Exception as exc:
            logger.error("Erro ao resolver URL de mídia na Meta Cloud API: %s", exc)
        return None, None

    async def transcribe_inbound_audio(self, inbound: InboundMessage) -> str | None:
        """
        Orquestra a obtenção e transcrição de áudio para qualquer canal WhatsApp suportado:
        - Base64 direto no webhook (ex.: Evolution com WEBHOOK_BASE64=true)
        - Meta Cloud API (resolve audio.id via Graph API + download autenticado)
        - Evolution API (descriptografa via getBase64FromMediaMessage, com fallback para URL)
        - WAHA / genérico (download autenticado com normalização de URL)
        """
        if getattr(inbound, "media_type", None) != "audio":
            return None

        # 1. Base64 já enviado no webhook (Evolution WEBHOOK_BASE64=true)
        raw_b64 = getattr(inbound, "media_base64", None)
        if raw_b64:
            decoded = self._decode_base64_audio(raw_b64)
            if decoded:
                return await self.transcribe_audio_bytes(
                    decoded,
                    filename="audio.ogg",
                    content_type=getattr(inbound, "media_mimetype", None),
                )

        provider = (inbound.provider or "").lower()

        # 2. Meta Cloud API Oficial (resolve media_id -> URL temporária -> download com Bearer)
        if provider == "meta" and getattr(inbound, "media_id", None):
            resolved_url, _resolved_mime = await self._resolve_meta_media_url(str(inbound.media_id))
            if resolved_url:
                meta_token = str(
                    getattr(self.company_settings, "meta_access_token", None) or ""
                ).strip()
                auth = {"Authorization": f"Bearer {meta_token}"} if meta_token else None
                return await self.download_and_transcribe(
                    resolved_url,
                    auth_header=auth,
                    provider="meta",
                )
            return None

        # 3. Evolution API (obtém áudio descriptografado via endpoint oficial)
        if provider == "evolution":
            decrypted = await self._fetch_evolution_decrypted_audio(inbound)
            if decrypted:
                audio_bytes, mime_type = decrypted
                return await self.transcribe_audio_bytes(
                    audio_bytes,
                    filename="audio.ogg",
                    content_type=mime_type or getattr(inbound, "media_mimetype", None),
                )
            # Fallback para URL direta apenas se não for arquivo criptografado .enc do CDN WhatsApp
            if inbound.media_url and ".enc" not in urlparse(inbound.media_url).path.lower():
                return await self.download_and_transcribe(
                    inbound.media_url,
                    provider="evolution",
                )
            return None

        # 4. WAHA ou outro provedor com media_url direta
        if inbound.media_url:
            return await self.download_and_transcribe(
                inbound.media_url,
                provider=inbound.provider,
            )

        return None
