import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.whatsapp.transcription_service import (
    AudioTranscriptionService,
    _detect_audio_format,
)


def _make_service(settings_obj):
    service = AudioTranscriptionService.__new__(AudioTranscriptionService)
    service.db = MagicMock()
    service.company_settings = settings_obj
    return service


def test_detect_audio_format_variants():
    assert _detect_audio_format("voice.oga", "audio/ogg; codecs=opus") == ("ogg", "audio/ogg")
    assert _detect_audio_format("clip.mp3", None) == ("mp3", "audio/mpeg")
    assert _detect_audio_format("note.m4a", None) == ("m4a", "audio/mp4")
    assert _detect_audio_format("unknown.bin", None) == ("ogg", "audio/ogg")


def test_waha_local_url_rewrite_and_auth_header():
    settings_obj = SimpleNamespace(
        whatsapp_provider="waha",
        waha_base_url="http://waha:3000",
        waha_api_key="waha_secret_key",
        evolution_base_url=None,
        evolution_api_key=None,
        meta_access_token=None,
    )
    service = _make_service(settings_obj)

    normalized = service._normalize_media_url(
        "http://localhost:3000/api/files/default/audio_123.oga",
        provider="waha",
    )
    assert normalized == "http://waha:3000/api/files/default/audio_123.oga"
    assert "waha" in service._allowed_media_hosts()

    headers = service._resolve_download_headers(normalized, provider="waha")
    assert headers == {"X-Api-Key": "waha_secret_key"}


@pytest.mark.asyncio
async def test_openrouter_audio_transcription_uses_input_audio():
    settings_obj = SimpleNamespace(
        ai_provider="openrouter",
        ai_api_key="sk-or-v1-test",
        triage_api_key=None,
    )
    service = _make_service(settings_obj)
    raw_audio = b"OggS" + (b"\x00" * 150)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Quero agendar botox quinta à tarde"}}]
    }

    captured = {}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return mock_response

    with patch("app.services.whatsapp.transcription_service.httpx.AsyncClient", DummyClient):
        result = await service.transcribe_audio_bytes(
            raw_audio,
            filename="voice.oga",
            content_type="audio/ogg; codecs=opus",
        )

    assert result == "Quero agendar botox quinta à tarde"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-v1-test"
    audio_part = captured["json"]["messages"][0]["content"][1]
    assert audio_part["type"] == "input_audio"
    assert audio_part["input_audio"]["format"] == "ogg"
    assert audio_part["input_audio"]["data"] == base64.b64encode(raw_audio).decode("ascii")


@pytest.mark.asyncio
async def test_anthropic_provider_falls_back_to_triage_openrouter_key():
    settings_obj = SimpleNamespace(
        ai_provider="anthropic",
        ai_api_key="sk-ant-test",
        triage_api_key="sk-or-v1-triage",
    )
    service = _make_service(settings_obj)
    raw_audio = b"OggS" + (b"\x01" * 150)

    with (
        patch("app.services.whatsapp.transcription_service.app_settings") as mock_app_settings,
        patch.object(
            service,
            "_transcribe_with_openrouter",
            new=AsyncMock(return_value="Qual o valor da limpeza de pele?"),
        ) as mock_or,
    ):
        mock_app_settings.groq_api_key = ""
        mock_app_settings.openai_api_key = ""
        mock_app_settings.openrouter_api_key = ""
        mock_app_settings.gemini_api_key = ""

        result = await service.transcribe_audio_bytes(raw_audio, filename="audio.ogg")

    assert result == "Qual o valor da limpeza de pele?"
    mock_or.assert_awaited_once_with(
        api_key="sk-or-v1-triage",
        audio_data=raw_audio,
        audio_format="ogg",
    )


@pytest.mark.asyncio
async def test_gemini_direct_audio_transcription():
    settings_obj = SimpleNamespace(
        ai_provider="gemini",
        ai_api_key="AIza-test-key",
        triage_api_key=None,
    )
    service = _make_service(settings_obj)
    raw_audio = b"OggS" + (b"\x02" * 150)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Bom dia, tem horário hoje?"}]}}]
    }

    captured = {}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return mock_response

    with patch("app.services.whatsapp.transcription_service.httpx.AsyncClient", DummyClient):
        result = await service.transcribe_audio_bytes(raw_audio, filename="voice.ogg")

    assert result == "Bom dia, tem horário hoje?"
    assert "generativelanguage.googleapis.com" in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "AIza-test-key"
    inline_data = captured["json"]["contents"][0]["parts"][1]["inline_data"]
    assert inline_data["mime_type"] == "audio/ogg"
    assert inline_data["data"] == base64.b64encode(raw_audio).decode("ascii")


@pytest.mark.asyncio
async def test_meta_audio_webhook_parse_and_graph_resolution():
    from app.services.whatsapp.meta import MetaWhatsAppProvider

    provider = MetaWhatsAppProvider(
        phone_number_id="123456789",
        access_token="EAA_meta_token",  # noqa: S106
    )
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"display_phone_number": "5511999990000"},
                            "contacts": [{"profile": {"name": "Maria Clara"}}],
                            "messages": [
                                {
                                    "id": "wamid.audio.001",
                                    "from": "5511988887777",
                                    "type": "audio",
                                    "audio": {
                                        "id": "media_998877",
                                        "mime_type": "audio/ogg; codecs=opus",
                                        "voice": True,
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }

    inbound = provider.parse_inbound_webhook(payload)
    assert inbound is not None
    assert inbound.provider == "meta"
    assert inbound.media_type == "audio"
    assert inbound.media_id == "media_998877"
    assert inbound.media_mimetype == "audio/ogg; codecs=opus"

    settings_obj = SimpleNamespace(
        meta_access_token="EAA_meta_token",  # noqa: S106
        waha_base_url=None,
        evolution_base_url=None,
    )
    service = _make_service(settings_obj)

    with (
        patch.object(
            service,
            "_resolve_meta_media_url",
            new=AsyncMock(
                return_value=("https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=998877", "audio/ogg")
            ),
        ) as mock_resolve,
        patch.object(
            service,
            "download_and_transcribe",
            new=AsyncMock(return_value="Quero marcar consulta amanhã"),
        ) as mock_dl,
    ):
        text = await service.transcribe_inbound_audio(inbound)

    assert text == "Quero marcar consulta amanhã"
    mock_resolve.assert_awaited_once_with("media_998877")
    mock_dl.assert_awaited_once_with(
        "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=998877",
        auth_header={"Authorization": "Bearer EAA_meta_token"},
        provider="meta",
    )


@pytest.mark.asyncio
async def test_evolution_audio_inline_base64_and_decrypt_endpoint():
    from app.services.whatsapp.evolution import EvolutionWhatsAppProvider

    evo = EvolutionWhatsAppProvider(
        base_url="http://evolution:8080",
        instance="clinica",
        api_key="evo_secret",
    )
    raw_audio = b"OggS" + (b"\x03" * 150)
    b64_audio = base64.b64encode(raw_audio).decode("ascii")

    # 1. Caso com base64 inline (WEBHOOK_BASE64=true)
    payload_b64 = {
        "data": {
            "key": {"id": "evo-msg-1", "remoteJid": "5511977776666@s.whatsapp.net", "fromMe": False},
            "pushName": "Carlos",
            "message": {
                "audioMessage": {"url": "https://mmg.whatsapp.net/v/t62.enc", "mimetype": "audio/ogg; codecs=opus"},
                "base64": b64_audio,
            },
        }
    }
    inbound_b64 = evo.parse_inbound_webhook(payload_b64)
    assert inbound_b64 is not None
    assert inbound_b64.media_type == "audio"
    assert inbound_b64.media_base64 == b64_audio

    settings_obj = SimpleNamespace(
        evolution_base_url="http://evolution:8080",
        evolution_instance="clinica",
        evolution_api_key="evo_secret",
        waha_base_url=None,
    )
    service = _make_service(settings_obj)

    with patch.object(
        service,
        "transcribe_audio_bytes",
        new=AsyncMock(return_value="Qual o endereço da clínica?"),
    ) as mock_tx:
        result = await service.transcribe_inbound_audio(inbound_b64)

    assert result == "Qual o endereço da clínica?"
    mock_tx.assert_awaited_once_with(
        raw_audio,
        filename="audio.ogg",
        content_type="audio/ogg; codecs=opus",
    )

    # 2. Caso sem base64 inline -> chama /chat/getBase64FromMediaMessage/{instance}
    payload_enc = {
        "data": {
            "key": {"id": "evo-msg-2", "remoteJid": "5511977776666@s.whatsapp.net", "fromMe": False},
            "pushName": "Carlos",
            "message": {
                "audioMessage": {"url": "https://mmg.whatsapp.net/v/t62.enc", "mimetype": "audio/ogg"},
            },
        }
    }
    inbound_enc = evo.parse_inbound_webhook(payload_enc)
    assert inbound_enc is not None
    assert inbound_enc.media_base64 is None

    mock_evo_resp = MagicMock()
    mock_evo_resp.status_code = 200
    mock_evo_resp.text = '{"ok": true}'
    mock_evo_resp.json.return_value = {"base64": b64_audio, "mimetype": "audio/ogg"}

    captured = {}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return mock_evo_resp

    with (
        patch("app.services.whatsapp.transcription_service.httpx.AsyncClient", DummyClient),
        patch.object(
            service,
            "transcribe_audio_bytes",
            new=AsyncMock(return_value="Tem horário sexta às 14h?"),
        ) as mock_tx2,
    ):
        result2 = await service.transcribe_inbound_audio(inbound_enc)

    assert result2 == "Tem horário sexta às 14h?"
    assert captured["url"] == "http://evolution:8080/chat/getBase64FromMediaMessage/clinica"
    assert captured["headers"]["apikey"] == "evo_secret"
    assert captured["json"]["message"]["key"]["id"] == "evo-msg-2"
    mock_tx2.assert_awaited_once()
