from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.company_settings import CompanySettings


def test_meta_webhook_verification_success(anonymous_client: TestClient, db_session: Session):
    settings = db_session.query(CompanySettings).first()
    if not settings:
        settings = CompanySettings(company_name="Teste")
        db_session.add(settings)
    settings.meta_verify_token = "meu_token_secreto"  # noqa: S105
    db_session.commit()

    response = anonymous_client.get(
        "/api/webhooks/whatsapp/meta",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "meu_token_secreto",
            "hub.challenge": "1158201444",
        },
    )

    assert response.status_code == 200
    assert response.text == "1158201444"


def test_meta_webhook_verification_forbidden_on_wrong_token(
    anonymous_client: TestClient, db_session: Session
):
    settings = db_session.query(CompanySettings).first()
    if not settings:
        settings = CompanySettings(company_name="Teste")
        db_session.add(settings)
    settings.meta_verify_token = "meu_token_secreto"  # noqa: S105
    db_session.commit()

    response = anonymous_client.get(
        "/api/webhooks/whatsapp/meta",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "token_errado",
            "hub.challenge": "1158201444",
        },
    )

    assert response.status_code == 403


def test_waha_webhook_post_triggers_seller_ai(anonymous_client: TestClient):
    payload = {
        "event": "message",
        "session": "default",
        "payload": {
            "id": "true_5585999998888@c.us_123",
            "from": "5585999998888@c.us",
            "fromMe": False,
            "body": "Quais os produtos disponíveis?",
            "_data": {"notifyName": "Rodrigo"},
        },
    }

    with patch("app.services.seller_ai_service.SellerAIService.handle_inbound_message", new_callable=AsyncMock) as mock_handle:
        response = anonymous_client.post("/api/webhooks/whatsapp/waha", json=payload)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        mock_handle.assert_called_once()
        inbound = mock_handle.call_args[0][0]
        assert inbound.sender_phone == "5585999998888"
        assert inbound.text == "Quais os produtos disponíveis?"


def test_evolution_webhook_post_triggers_seller_ai(anonymous_client: TestClient):
    payload = {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": "5585988887777@s.whatsapp.net",
                "fromMe": False,
                "id": "evo-123",
            },
            "pushName": "Ana",
            "message": {
                "conversation": "Quero comprar",
            },
        },
    }

    with patch("app.services.seller_ai_service.SellerAIService.handle_inbound_message", new_callable=AsyncMock) as mock_handle:
        response = anonymous_client.post("/api/webhooks/whatsapp/evolution", json=payload)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        mock_handle.assert_called_once()
        inbound = mock_handle.call_args[0][0]
        assert inbound.sender_phone == "5585988887777"
        assert inbound.text == "Quero comprar"
