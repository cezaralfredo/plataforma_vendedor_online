from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.admin.service import AdminService
from app.config import settings
from app.models.payment import Payment
from app.services.ai.prompt_engine import SalonPromptEngine
from app.services.company_settings_service import CompanySettingsService
from app.services.payment_gateways.efipay_gateway import EfiPayGateway
from app.services.payment_gateways.mercadopago_gateway import MercadoPagoGateway
from app.services.payment_service import PaymentService


def test_system_status_reflects_configured_gateway_not_hardcoded_asaas(db_session):
    db = db_session
    cs_service = CompanySettingsService(db)
    cs = cs_service.get_or_create_default()

    # 1. PIX Direto / Nubank
    cs.default_gateway = "nubank"
    cs.gateway_credentials = {"pix_key": "contato@clinica.com.br", "bank_name": "Nubank"}
    db.commit()

    status_nu = AdminService(db).get_system_status(settings)
    assert status_nu["payment_gateway"]["provider"] == "nubank"
    assert "Nubank" in status_nu["payment_gateway"]["label"]
    assert status_nu["payment_gateway"]["configured"] is True

    # 2. Mercado Pago sem token -> não configurado
    cs.default_gateway = "mercadopago"
    cs.gateway_credentials = {}
    db.commit()

    status_mp_off = AdminService(db).get_system_status(settings)
    assert status_mp_off["payment_gateway"]["provider"] == "mercadopago"
    assert status_mp_off["payment_gateway"]["configured"] is False

    # 3. Mercado Pago com token -> configurado
    cs.gateway_credentials = {"access_token": "APP_USR-123456"}
    db.commit()

    status_mp_on = AdminService(db).get_system_status(settings)
    assert status_mp_on["payment_gateway"]["provider"] == "mercadopago"
    assert status_mp_on["payment_gateway"]["configured"] is True

    # 4. Asaas configurado pelo painel (mesmo sem ASAAS_API_KEY no .env)
    cs.default_gateway = "asaas"
    cs.gateway_credentials = {
        "api_key": "$aact_panel_test_key",
        "base_url": "https://api.asaas.com/v3",
    }
    db.commit()

    status_asaas = AdminService(db).get_system_status(settings)
    assert status_asaas["payment_gateway"]["provider"] == "asaas"
    assert status_asaas["payment_gateway"]["configured"] is True
    assert status_asaas["payment_gateway"]["mode"] == "production"


@pytest.mark.asyncio
async def test_mercadopago_and_efipay_prefix_ids_and_strip_on_status_check():
    mp = MercadoPagoGateway(access_token="APP_USR-test")  # noqa: S106
    mock_post_resp = MagicMock()
    mock_post_resp.raise_for_status = MagicMock()
    mock_post_resp.json.return_value = {
        "id": 987654321,
        "status": "pending",
        "point_of_interaction": {
            "transaction_data": {"qr_code": "000201mp", "ticket_url": "https://mp.test/pay/1"}
        },
    }
    mock_get_resp = MagicMock()
    mock_get_resp.raise_for_status = MagicMock()
    mock_get_resp.json.return_value = {"id": 987654321, "status": "approved", "date_approved": "2026-11-25T12:00:00Z"}

    requested_urls: list[str] = []

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            requested_urls.append(url)
            return mock_post_resp

        async def get(self, url, headers=None):
            requested_urls.append(url)
            return mock_get_resp

    with patch("app.services.payment_gateways.mercadopago_gateway.httpx.AsyncClient", DummyClient):
        charge = await mp.create_charge(1, amount_cents=5000)
        assert charge.gateway_payment_id == "mp:987654321"

        st = await mp.check_status("mp:987654321")
        assert st["status"] == "received"
        assert requested_urls[-1].endswith("/payments/987654321")

    efi = EfiPayGateway(client_id="cid", client_secret="csec", pix_key="chave@efi.com")  # noqa: S106
    efi_urls: list[str] = []

    class DummyEfiClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            efi_urls.append(url)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"txid": "tx12345", "status": "CONCLUIDA"}
            return resp

    with (
        patch.object(efi, "_get_access_token", new=AsyncMock(return_value="tok")),
        patch("app.services.payment_gateways.efipay_gateway.httpx.AsyncClient", DummyEfiClient),
    ):
        efi_st = await efi.check_status("efi:tx12345")
        assert efi_st["status"] == "received"
        assert efi_urls[-1].endswith("/v2/cob/tx12345")


@pytest.mark.asyncio
async def test_payment_service_routes_check_and_refund_to_origin_gateway(db_session):
    db = db_session
    cs = CompanySettingsService(db).get_or_create_default()
    cs.default_gateway = "mercadopago"
    cs.gateway_credentials = {"access_token": "APP_USR-route-test"}
    db.commit()

    payment = Payment(
        asaas_payment_id="mp:555111",
        amount_cents=10000,
        billing_type="pix",
        status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    svc = PaymentService(db)
    svc.asaas = MagicMock()
    svc.asaas.get_payment = AsyncMock(side_effect=AssertionError("Não deveria chamar Asaas para pagamento mp:"))
    svc.asaas.refund_payment = AsyncMock(side_effect=AssertionError("Não deveria chamar Asaas para estorno mp:"))

    fake_mp = MagicMock()
    fake_mp.check_status = AsyncMock(return_value={"status": "received"})
    fake_mp.refund = AsyncMock(return_value={"status": "refunded", "refunded": True})

    with patch("app.services.payment_gateways.get_payment_gateway", return_value=fake_mp):
        new_status = await svc.check_payment_status(payment)
        assert new_status == "received"
        assert payment.status == "received"

        refunded = await svc.refund(payment.id)
        assert refunded.status == "refunded"
        fake_mp.refund.assert_awaited_once_with("mp:555111")


def test_prompt_engine_includes_active_gateway_and_accepted_methods(db_session):
    db = db_session
    cs = CompanySettingsService(db).get_or_create_default()
    cs.default_gateway = "nubank"
    cs.payment_required = True
    cs.accepted_payment_methods = ["pix", "credit_card", "cash"]
    db.commit()

    prompt = SalonPromptEngine.build_system_prompt(cs)
    assert "nubank" in prompt
    assert "PIX" in prompt
    assert "Cartão de Crédito" in prompt
    assert "Dinheiro" in prompt
