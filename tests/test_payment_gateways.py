from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models.company_settings import CompanySettings
from app.models.product import Product
from app.models.user import User
from app.services.payment_gateways import (
    AsaasPaymentGateway,
    ChargeRequest,
    ChargeResult,
    DirectPixGateway,
    EfiPayGateway,
    ManualPaymentGateway,
    MercadoPagoGateway,
    generate_brcode_pix,
    get_payment_gateway,
)
from app.services.payment_service import PaymentService


def test_payment_gateway_factory():
    """Valida a seleção correta de instâncias de gateways bancários."""
    # Asaas
    asaas = get_payment_gateway("asaas", {"api_key": "test-key", "base_url": "https://api.asaas.com"})
    assert isinstance(asaas, AsaasPaymentGateway)
    assert asaas.client.api_key == "test-key"

    # Mercado Pago
    mp = get_payment_gateway("mercadopago", {"access_token": "TEST-123456", "public_key": "TEST-PUB"})
    assert isinstance(mp, MercadoPagoGateway)
    assert mp.access_token == "TEST-123456"  # noqa: S105

    # EFI Pay
    efi = get_payment_gateway("efipay", {"client_id": "cid", "client_secret": "csec", "pix_key": "123456"})
    assert isinstance(efi, EfiPayGateway)
    assert efi.client_id == "cid"
    assert efi.pix_key == "123456"

    # Direct PIX
    pix_dir = get_payment_gateway(
        "pix_direct",
        {"pix_key": "contato@loja.com.br", "merchant_name": "Loja VIP", "merchant_city": "Fortaleza"},
    )
    assert isinstance(pix_dir, DirectPixGateway)
    assert pix_dir.pix_key == "contato@loja.com.br"
    assert pix_dir.merchant_name == "Loja VIP"

    # Manual
    manual = get_payment_gateway("manual")
    assert isinstance(manual, ManualPaymentGateway)


def test_direct_pix_brcode_generator():
    """Valida o gerador de BRCode PIX padrão EMVCo oficial do Banco Central do Brasil."""
    brcode = generate_brcode_pix(
        pix_key="12345678000195",
        merchant_name="MINHA LOJA",
        merchant_city="SAO PAULO",
        amount=150.50,
        txid="PED12345",
        description="Pedido 12345",
    )

    # 1. Deve iniciar com o indicador de payload EMVCo "000201"
    assert brcode.startswith("000201")

    # 2. Deve conter a GUI oficial do Banco Central
    assert "br.gov.bcb.pix" in brcode

    # 3. Deve conter a chave PIX
    assert "12345678000195" in brcode

    # 4. Deve conter a moeda brasileira (986)
    assert "5303986" in brcode

    # 5. Deve conter o valor exato formatado
    assert "5406150.50" in brcode

    # 6. Deve conter o país BR
    assert "5802BR" in brcode

    # 7. Deve conter o nome e cidade sanitizados
    assert "MINHA LOJA" in brcode
    assert "SAO PAULO" in brcode

    # 8. Deve terminar com o campo CRC16 "6304" seguido de 4 caracteres hexadecimais
    assert "6304" in brcode[-8:]
    crc_hex = brcode[-4:]
    assert len(crc_hex) == 4
    assert all(c in "0123456789ABCDEF" for c in crc_hex)


@pytest.mark.asyncio
async def test_direct_pix_gateway_create_charge():
    """Testa a criação de cobrança no gateway de PIX direto."""
    gateway = DirectPixGateway(
        pix_key="loja@empresa.com",
        merchant_name="Empresa Teste",
        merchant_city="Recife",
    )

    req = ChargeRequest(
        amount_cents=8990,
        user_id=1,
        customer_name="João Silva",
        description="Kit Cuidados",
        order_ref="PED-888",
    )

    res = await gateway.create_charge(req)

    assert isinstance(res, ChargeResult)
    assert res.status == "pending"
    assert "direct_pix:PED-888" in res.gateway_payment_id
    assert res.pix_copy_paste.startswith("000201")
    assert "89.90" in res.pix_copy_paste


@pytest.mark.asyncio
async def test_mercadopago_gateway_create_charge():
    """Testa o gateway do Mercado Pago emitindo PIX dinâmico com QR Code."""
    mock_response = httpx.Response(
        201,
        json={
            "id": 987654321,
            "status": "pending",
            "point_of_interaction": {
                "transaction_data": {
                    "qr_code": "00020126580014br.gov.bcb.pix...mercadopago",
                    "qr_code_base64": "iVBORw0KGgoAAAANSUhEUgAA...",
                    "ticket_url": "https://www.mercadopago.com.br/payments/987654321/ticket",
                }
            },
        },
        request=httpx.Request("POST", "https://api.mercadopago.com/v1/payments"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        gateway = MercadoPagoGateway(access_token="TEST-TOKEN-MP")  # noqa: S106
        req = ChargeRequest(
            amount_cents=5000,
            user_id=2,
            customer_name="Camila Santos",
            customer_email="camila@teste.com",
            customer_cpf="11122233344",
            description="Produto de Beleza",
            order_ref="order:2",
        )

        res = await gateway.create_charge(req)

        assert mock_post.called
        assert res.gateway_payment_id == "987654321"
        assert res.status == "pending"
        assert "mercadopago" in res.pix_copy_paste
        assert "ticket" in res.invoice_url


@pytest.mark.asyncio
async def test_efipay_gateway_create_charge():
    """Testa o gateway da EFI Pay com token OAuth2 e emissão da cobrança PIX."""
    token_response = httpx.Response(
        200,
        json={"access_token": "token-efi-valido", "expires_in": 3600},
        request=httpx.Request("POST", "https://pix.gerencianet.com.br/v1/oauth/token"),
    )
    cob_response = httpx.Response(
        201,
        json={
            "txid": "txid_efi_998877",
            "status": "ATIVA",
            "pixCopiaECola": "00020126580014br.gov.bcb.pix...efipay",
            "loc": {"id": 4321},
        },
        request=httpx.Request("POST", "https://pix.gerencianet.com.br/v2/cob"),
    )
    qr_response = httpx.Response(
        200,
        json={"qrcode": "00020126580014br.gov.bcb.pix...efipay", "imagemQrcode": "base64..."},
        request=httpx.Request("GET", "https://pix.gerencianet.com.br/v2/loc/4321/qrcode"),
    )

    with (
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post,
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
    ):
        mock_post.side_effect = [token_response, cob_response]
        mock_get.return_value = qr_response

        gateway = EfiPayGateway(client_id="cid", client_secret="csec", pix_key="minha-chave-pix")  # noqa: S106
        req = ChargeRequest(
            amount_cents=7500,
            user_id=3,
            customer_name="Felipe Costa",
            customer_cpf="22233344455",
            description="Kit Salão",
        )

        res = await gateway.create_charge(req)

        assert mock_post.call_count == 2
        assert res.gateway_payment_id == "txid_efi_998877"
        assert res.status == "pending"
        assert "efipay" in res.pix_copy_paste


@pytest.mark.asyncio
async def test_payment_service_multi_gateway_integration(db_session: Session):
    """
    Testa se o PaymentService respeita dinamicamente a instituição financeira
    configurada pelo cliente em CompanySettings (ex: PIX Direto em qualquer banco).
    """
    user = User(name="Ana Paula", phone="5585999991111")
    prod = Product(
        name="Kit Hidratação Intensiva",
        price_cents=35000,
        weight_kg=Decimal("0.180"),
        stock_quantity=10,
        active=True,
    )
    db_session.add(user)
    db_session.add(prod)

    # Configura o salão para usar PIX Direto em conta bancária própria (Zero Taxas)
    settings = db_session.query(CompanySettings).first()
    if not settings:
        settings = CompanySettings(company_name="Salão Bella")
        db_session.add(settings)
    settings.default_gateway = "pix_direct"
    settings.gateway_credentials = {
        "pix_key": "financeiro@salamaster.com.br",
        "merchant_name": "SALAO BELLA LTDA",
        "merchant_city": "FORTALEZA",
    }
    db_session.commit()

    service = PaymentService(db_session)
    payment = await service.create_direct_charge(
        user_id=user.id,
        amount_cents=prod.price_cents,
        product_id=prod.id,
        description=f"Pedido: {prod.name}",
        billing_type="pix",
    )

    assert payment.id is not None
    assert payment.amount_cents == 35000
    assert payment.status == "pending"
    assert "direct_pix:" in payment.asaas_payment_id
    # O link de pagamento/copia e cola deve conter o BRCode oficial do Banco Central
    assert payment.invoice_url.startswith("000201")
    assert "br.gov.bcb.pix" in payment.invoice_url
    assert "350.00" in payment.invoice_url
