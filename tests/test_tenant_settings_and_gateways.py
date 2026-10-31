from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from app.schemas.appointment import AppointmentCreate
from app.schemas.company_settings import CompanySettingsUpdate
from app.services.appointment_service import AppointmentService
from app.services.company_settings_service import CompanySettingsService
from app.services.payment_gateways import (
    AsaasPaymentGateway,
    DirectPixGateway,
    ManualPaymentGateway,
    get_payment_gateway,
)
from tests.seed import seed_data


def test_company_settings_get_or_create_default(db_session: Session):
    service = CompanySettingsService(db_session)
    settings = service.get_or_create_default()

    assert settings.id is not None
    assert settings.company_name == "Minha Empresa"
    assert settings.payment_required is True
    assert settings.default_gateway == "asaas"
    assert settings.whatsapp_provider == "evolution"
    assert settings.tone == "casual"
    assert settings.commission_model == "percentage"


def test_company_settings_update(db_session: Session):
    service = CompanySettingsService(db_session)
    updated = service.update(
        CompanySettingsUpdate(
            company_name="Barbearia VIP",
            payment_required=False,
            default_gateway="manual",
            whatsapp_provider="meta",
            meta_phone_number_id="1029384756",
            tone="formal",
        )
    )

    assert updated.company_name == "Barbearia VIP"
    assert updated.payment_required is False
    assert updated.default_gateway == "manual"
    assert updated.whatsapp_provider == "meta"
    assert updated.meta_phone_number_id == "1029384756"
    assert updated.tone == "formal"


def test_company_settings_waha_configuration(db_session: Session):
    service = CompanySettingsService(db_session)
    updated = service.update(
        CompanySettingsUpdate(
            whatsapp_provider="waha",
            waha_base_url="http://localhost:3000",
            waha_session="minha_loja",
            waha_api_key="token-waha-123",
        )
    )

    assert updated.whatsapp_provider == "waha"
    assert updated.waha_base_url == "http://localhost:3000"
    assert updated.waha_session == "minha_loja"
    assert updated.waha_api_key == "token-waha-123"


def test_payment_gateway_factory():
    asaas_gw = get_payment_gateway("asaas", {"api_key": "test-key"})
    assert isinstance(asaas_gw, AsaasPaymentGateway)

    manual_gw = get_payment_gateway("manual")
    assert isinstance(manual_gw, ManualPaymentGateway)

    counter_gw = get_payment_gateway("in_person")
    assert isinstance(counter_gw, ManualPaymentGateway)


@pytest.mark.asyncio
async def test_manual_payment_gateway_charge_and_status():
    gateway = ManualPaymentGateway()
    res = await gateway.create_charge(
        appointment_id=42,
        amount_cents=5000,
        customer_info={"name": "Cliente Teste"},
    )

    assert res["status"] == "confirmed"
    assert res["gateway_payment_id"] == "manual:42"
    assert res["invoice_url"] is None

    status_res = await gateway.check_status("manual:42")
    assert status_res["status"] == "confirmed"


def test_appointment_creation_when_payment_is_mandatory(db_session: Session):
    entities = seed_data(db_session)
    settings_service = CompanySettingsService(db_session)
    settings_service.update(CompanySettingsUpdate(payment_required=True))

    service = AppointmentService(db_session)
    appointment = service.create(
        AppointmentCreate(
            user_id=entities["user"].id,
            professional_id=entities["professional"].id,
            service_id=entities["service"].id,
            start_time=datetime(2026, 7, 30, 10, 0, 0),
            end_time=datetime(2026, 7, 30, 11, 0, 0),
        )
    )

    assert appointment.status == "pending"
    assert appointment.expires_at is not None


def test_appointment_creation_when_payment_is_optional(db_session: Session):
    entities = seed_data(db_session)
    settings_service = CompanySettingsService(db_session)
    settings_service.update(CompanySettingsUpdate(payment_required=False))

    service = AppointmentService(db_session)
    appointment = service.create(
        AppointmentCreate(
            user_id=entities["user"].id,
            professional_id=entities["professional"].id,
            service_id=entities["service"].id,
            start_time=datetime(2026, 7, 30, 14, 0, 0),
            end_time=datetime(2026, 7, 30, 15, 0, 0),
        )
    )

    assert appointment.status == "confirmed"
    assert appointment.expires_at is None


def test_company_settings_pix_and_bank_modalities_update(db_session: Session):
    service = CompanySettingsService(db_session)
    updated = service.update(
        CompanySettingsUpdate(
            default_gateway="inter",
            accepted_payment_methods=["pix", "credit_card", "debit_card", "boleto", "in_person", "cash"],
            pix_copy_paste="00020126330014br.gov.bcb.pix0111123456789005204000053039865802BR5912Anaue Design6009Sao Paulo62070503***630493E2",
            pix_key="12345678900",
            pix_key_type="cpf",
            pix_merchant_name="Anaue Design",
            pix_bank_name="Banco Inter",
            pix_merchant_city="Sao Paulo",
        )
    )

    assert updated.default_gateway == "inter"
    assert "boleto" in updated.accepted_payment_methods
    assert "cash" in updated.accepted_payment_methods
    assert updated.pix_copy_paste.startswith("00020126")
    assert updated.pix_key == "12345678900"
    assert updated.pix_bank_name == "Banco Inter"
    assert updated.pix_merchant_name == "Anaue Design"


@pytest.mark.asyncio
async def test_bank_gateways_factory_and_charge_creation():
    # Test Banco Inter
    gw_inter = get_payment_gateway(
        "inter",
        {
            "pix_key": "12.345.678/0001-90",
            "pix_merchant_name": "Studio Inter",
            "pix_merchant_city": "Belo Horizonte",
            "pix_bank_name": "Banco Inter",
        },
    )
    assert isinstance(gw_inter, DirectPixGateway)
    charge = await gw_inter.create_charge(101, amount_cents=10000)
    assert charge.status == "pending"
    assert charge.pix_copy_paste is not None
    assert "000201" in charge.pix_copy_paste
    assert charge.raw_response["banco"] == "Banco Inter"

    # Test Nubank com PIX Copia e Cola estático
    static_code = "00020126330014br.gov.bcb.pix0111123456789005204000053039865802BR5912Nubank Teste6009Sao Paulo62070503***6304"
    gw_nu = get_payment_gateway(
        "nubank",
        {
            "pix_copy_paste": static_code,
            "bank_name": "Nubank",
        },
    )
    assert isinstance(gw_nu, DirectPixGateway)
    charge_nu = await gw_nu.create_charge(102, amount_cents=5000)
    assert charge_nu.pix_copy_paste == static_code
