import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models.company_settings import CompanySettings
from app.services.company_settings_service import CompanySettingsService

logger = logging.getLogger(__name__)

MAX_AUDIO_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


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
    Serviço de transcrição de áudios recebidos pelo WhatsApp usando Whisper
    (compatível com OpenAI e Groq - ultra rápido).
    """

    def __init__(self, db: Session):
        self.db = db
        self.company_settings: CompanySettings = CompanySettingsService(db).get_or_create_default()

    def _allowed_media_hosts(self) -> set[str]:
        hosts: set[str] = set()
        for raw_url in (
            getattr(self.company_settings, "waha_base_url", None),
            getattr(self.company_settings, "evolution_api_url", None),
            app_settings.waha_base_url,
        ):
            if raw_url:
                parsed = urlparse(str(raw_url))
                if parsed.hostname:
                    hosts.add(parsed.hostname.lower())
        return hosts

    async def transcribe_audio_bytes(self, audio_data: bytes, filename: str = "audio.ogg") -> str | None:
        """Transcreve arquivo de áudio usando OpenAI ou Groq Whisper."""
        if not audio_data or len(audio_data) < 100:
            return None

        provider = (self.company_settings.ai_provider or "openai").lower().strip()
        api_key: str | None = None

        if provider == "groq":
            api_key = self.company_settings.ai_api_key or app_settings.groq_api_key or None
            endpoint = "https://api.groq.com/openai/v1/audio/transcriptions"
            model = "whisper-large-v3-turbo"
        elif provider == "openai":
            api_key = self.company_settings.ai_api_key or app_settings.openai_api_key or None
            endpoint = "https://api.openai.com/v1/audio/transcriptions"
            model = "whisper-1"
        elif app_settings.groq_api_key:
            provider = "groq"
            api_key = app_settings.groq_api_key
            endpoint = "https://api.groq.com/openai/v1/audio/transcriptions"
            model = "whisper-large-v3-turbo"
        elif app_settings.openai_api_key:
            provider = "openai"
            api_key = app_settings.openai_api_key
            endpoint = "https://api.openai.com/v1/audio/transcriptions"
            model = "whisper-1"
        else:
            logger.info(
                "Transcrição de áudio ignorada: provedor '%s' não suporta Whisper e não há chave OpenAI/Groq configurada.",
                provider,
            )
            return None

        if not api_key:
            logger.info("Transcrição de áudio ignorada: nenhuma chave de IA configurada.")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        files = {
            "file": (filename, audio_data, "audio/ogg"),
        }
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
                        "Áudio transcrito com sucesso via %s (%d caracteres).",
                        provider,
                        len(transcription),
                    )
                    return transcription
                else:
                    logger.warning("Falha na API Whisper (%s): HTTP %s", provider, res.status_code)
                    return None
        except Exception as exc:
            logger.error("Erro ao chamar serviço de transcrição Whisper: %s", exc)
            return None

    async def download_and_transcribe(self, audio_url: str, auth_header: dict[str, str] | None = None) -> str | None:
        """Baixa o arquivo de áudio da URL validada (anti-SSRF e limite de tamanho) e executa a transcrição."""
        if not _is_safe_audio_url(audio_url, self._allowed_media_hosts()):
            logger.warning("Download de áudio bloqueado por validação de segurança (URL não permitida).")
            return None

        try:
            chunks: list[bytes] = []
            total_bytes = 0
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
                async with client.stream("GET", audio_url, headers=auth_header or {}) as res:
                    if res.status_code != 200:
                        return None
                    async for chunk in res.aiter_bytes(chunk_size=65536):
                        total_bytes += len(chunk)
                        if total_bytes > MAX_AUDIO_DOWNLOAD_BYTES:
                            logger.warning("Download de áudio abortado: arquivo excede limite de %d bytes.", MAX_AUDIO_DOWNLOAD_BYTES)
                            return None
                        chunks.append(chunk)
            if chunks:
                return await self.transcribe_audio_bytes(b"".join(chunks))
        except Exception as exc:
            logger.error("Erro ao baixar áudio do WhatsApp: %s", exc)
        return None
