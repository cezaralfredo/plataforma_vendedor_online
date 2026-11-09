from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.whatsapp.base import SendMessageResult
from tests.test_admin_login_flow import login, session_data


def test_whatsapp_connection_page_requires_auth(client: TestClient):
    res = client.get("/admin/whatsapp", follow_redirects=False)
    assert res.status_code in {303, 307, 401, 403}


def test_whatsapp_connection_page_renders_deskcomm_ui(client: TestClient, db_session: Session):
    login(client)

    res = client.get("/admin/whatsapp")
    assert res.status_code == 200
    assert "Conexão WhatsApp" in res.text
    assert "WAHA (WhatsApp HTTP API Interno)" in res.text
    assert "Escaneie o QR Code no seu WhatsApp" in res.text
    assert "Modo Coexistência Ativo" in res.text
    assert "Como Conectar seu Celular" in res.text
    assert "Infraestrutura Interna WAHA" in res.text
    assert "href=\"/admin/whatsapp\"" in res.text


def test_whatsapp_api_qr_status_endpoint(client: TestClient, db_session: Session):
    login(client)

    with patch("app.services.whatsapp.waha.WahaWhatsAppProvider.get_qr_status", new_callable=AsyncMock) as mock_qr:
        mock_qr.return_value = {
            "success": True,
            "status": "SCAN_QR_CODE",
            "me": None,
            "qr_code": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
            "session": "default",
            "engine": "NOWEB",
        }

        res = client.get("/admin/api/whatsapp/qr")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["status"] == "SCAN_QR_CODE"
        assert "data:image/png;base64" in data["qr_code"]


def test_whatsapp_test_message_endpoint_with_csrf(client: TestClient, db_session: Session):
    login(client)
    csrf = session_data(client)["csrf_token"]

    # 1. Sem CSRF -> 403
    res_no_csrf = client.post(
        "/admin/api/whatsapp/test-message",
        json={"phone": "5585999998888", "message": "Teste unitário"},
    )
    assert res_no_csrf.status_code == 403

    # 2. Com CSRF e envio bem-sucedido
    with patch("app.services.whatsapp.waha.WahaWhatsAppProvider.send_text", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = SendMessageResult(
            success=True,
            message_id="msg-test-123",
            provider="waha",
        )

        res = client.post(
            "/admin/api/whatsapp/test-message",
            headers={"X-CSRF-Token": csrf},
            json={"phone": "5585999998888", "message": "Teste unitário"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["message_id"] == "msg-test-123"
