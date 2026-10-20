import logging

import httpx
from sqlalchemy.orm import Session

from app.models.company_settings import CompanySettings
from app.services.company_settings_service import CompanySettingsService

logger = logging.getLogger(__name__)


class AudioTranscriptionService:
    """
    Serviço de transcrição de áudios recebidos pelo WhatsApp usando Whisper
    (compatível com OpenAI e Groq - ultra rápido).
    """

    def __init__(self, db: Session):
        self.db = db
        self.company_settings: CompanySettings = CompanySettingsService(db).get_or_create_default()

    async def transcribe_audio_bytes(self, audio_data: bytes, filename: str = "audio.ogg") -> str | None:
        """Transcreve arquivo de áudio usando OpenAI ou Groq Whisper."""
        if not audio_data or len(audio_data) < 100:
            return None

        api_key = self.company_settings.ai_api_key
        if not api_key:
            logger.info("Transcrição de áudio ignorada: nenhuma chave de IA configurada.")
            return None

        provider = (self.company_settings.ai_provider or "openai").lower()

        if provider == "groq":
            endpoint = "https://api.groq.com/openai/v1/audio/transcriptions"
            model = "whisper-large-v3-turbo"
        else:
            endpoint = "https://api.openai.com/v1/audio/transcriptions"
            model = "whisper-1"

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
                    logger.info("Áudio transcrito com sucesso via %s: '%s'", provider, transcription)
                    return transcription
                else:
                    logger.warning("Falha na API Whisper (%s): HTTP %s - %s", provider, res.status_code, res.text)
                    return None
        except Exception as exc:
            logger.error("Erro ao chamar serviço de transcrição Whisper: %s", exc)
            return None

    async def download_and_transcribe(self, audio_url: str, auth_header: dict[str, str] | None = None) -> str | None:
        """Baixa o arquivo de áudio da URL e executa a transcrição."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(audio_url, headers=auth_header or {})
                if res.status_code == 200 and res.content:
                    return await self.transcribe_audio_bytes(res.content)
        except Exception as exc:
            logger.error("Erro ao baixar áudio do WhatsApp (%s): %s", audio_url, exc)
        return None
