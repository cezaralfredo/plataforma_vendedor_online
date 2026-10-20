from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.company_settings import CompanySettings
from app.services.whatsapp.evolution import EvolutionWhatsAppProvider
from app.services.whatsapp.gateway import WhatsAppGatewayService
from app.services.whatsapp.meta import MetaWhatsAppProvider
from app.services.whatsapp.waha import WahaWhatsAppProvider


def test_waha_parse_inbound_webhook():
    provider = WahaWhatsAppProvider(base_url="http://waha:3000")

    payload = {
        "event": "message",
        "session": "default",
        "payload": {
            "id": "true_5585999998888@c.us_3EB012345",
            "from": "5585999998888@c.us",
            "fromMe": False,
            "body": "Olá, quanto custa o produto?",
            "_data": {
                "notifyName": "Carlos Silva",
            },
        },
    }

    inbound = provider.parse_inbound_webhook(payload)
    assert inbound is not None
    assert inbound.provider == "waha"
    assert inbound.sender_phone == "5585999998888"
    assert inbound.sender_name == "Carlos Silva"
    assert inbound.text == "Olá, quanto custa o produto?"
    assert inbound.message_id == "true_5585999998888@c.us_3EB012345"


def test_waha_parses_from_me_messages_for_coexistence():
    provider = WahaWhatsAppProvider(base_url="http://waha:3000")
    payload = {
        "event": "message",
        "session": "default",
        "payload": {
            "id": "false_5585999998888@c.us_3EB012345",
            "to": "5585999998888@c.us",
            "fromMe": True,
            "body": "Mensagem enviada pelo atendente humano",
        },
    }
    inbound = provider.parse_inbound_webhook(payload)
    assert inbound is not None
    assert inbound.is_from_me is True
    assert inbound.sender_phone == "5585999998888"
    assert inbound.sender_name == "Atendente"
    assert inbound.text == "Mensagem enviada pelo atendente humano"


@pytest.mark.asyncio
async def test_waha_send_text_success():
    provider = WahaWhatsAppProvider(base_url="http://waha:3000", api_key="secret-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"id": "msg-1234"}'
    mock_resp.json.return_value = {"id": "msg-1234"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        result = await provider.send_text("5585999998888", "Olá!")

        assert result.success is True
        assert result.message_id == "msg-1234"
        assert result.provider == "waha"

        call_args = mock_post.call_args
        assert "http://waha:3000/api/sendText" in call_args[0]
        assert call_args[1]["json"]["chatId"] == "5585999998888@c.us"
        assert call_args[1]["headers"]["X-Api-Key"] == "secret-key"


def test_evolution_parse_inbound_webhook():
    provider = EvolutionWhatsAppProvider(base_url="http://evolution:8080", instance="default")

    payload = {
        "event": "messages.upsert",
        "instance": "default",
        "data": {
            "key": {
                "remoteJid": "5585988887777@s.whatsapp.net",
                "fromMe": False,
                "id": "evo-msg-999",
            },
            "pushName": "Mariana",
            "message": {
                "conversation": "Gostaria de ver o catálogo",
            },
        },
    }

    inbound = provider.parse_inbound_webhook(payload)
    assert inbound is not None
    assert inbound.provider == "evolution"
    assert inbound.sender_phone == "5585988887777"
    assert inbound.sender_name == "Mariana"
    assert inbound.text == "Gostaria de ver o catálogo"


@pytest.mark.asyncio
async def test_evolution_send_text_success():
    provider = EvolutionWhatsAppProvider(
        base_url="http://evolution:8080", instance="loja1", api_key="evo-key"
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"key": {"id": "msg-evo-1"}}'
    mock_resp.json.return_value = {"key": {"id": "msg-evo-1"}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        result = await provider.send_text("5585988887777", "Olá, bem-vindo!")

        assert result.success is True
        assert result.message_id == "msg-evo-1"
        assert result.provider == "evolution"


def test_meta_parse_inbound_webhook():
    provider = MetaWhatsAppProvider(phone_number_id="12345", access_token="token")  # noqa: S106

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "12345"},
                            "contacts": [{"profile": {"name": "Fernanda"}}],
                            "messages": [
                                {
                                    "from": "5585977776666",
                                    "id": "wamid.ABCDEF123",
                                    "type": "text",
                                    "text": {"body": "Quero comprar o produto #1"},
                                }
                            ],
                        }
                    }
                ],
            }
        ],
    }

    inbound = provider.parse_inbound_webhook(payload)
    assert inbound is not None
    assert inbound.provider == "meta"
    assert inbound.sender_phone == "5585977776666"
    assert inbound.sender_name == "Fernanda"
    assert inbound.text == "Quero comprar o produto #1"
    assert inbound.message_id == "wamid.ABCDEF123"


def test_gateway_service_selects_active_provider(db_session):
    settings = db_session.query(CompanySettings).first()
    if not settings:
        settings = CompanySettings(company_name="Teste")
        db_session.add(settings)

    settings.whatsapp_provider = "waha"
    settings.waha_base_url = "http://waha-local:3000"
    settings.waha_session = "vendas"
    db_session.commit()

    gateway = WhatsAppGatewayService(db_session)
    provider = gateway.get_provider()
    assert isinstance(provider, WahaWhatsAppProvider)
    assert provider.name == "waha"
    assert provider.base_url == "http://waha-local:3000"
    assert provider.session == "vendas"

    settings.whatsapp_provider = "meta"
    settings.meta_phone_number_id = "meta-100"
    settings.meta_access_token = "meta-token-xyz"  # noqa: S105
    db_session.commit()

    gateway2 = WhatsAppGatewayService(db_session)
    provider2 = gateway2.get_provider()
    assert isinstance(provider2, MetaWhatsAppProvider)
    assert provider2.phone_number_id == "meta-100"
