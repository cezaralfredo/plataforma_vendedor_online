from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from app.models.company_settings import CompanySettings
from app.models.product import Product
from app.models.professional import Professional
from app.models.professional_service import ProfessionalService
from app.models.service import Service
from app.models.user import User
from app.services.ai.orchestrator import SalonAIOrchestrator
from app.services.ai.prompt_engine import SalonPromptEngine
from app.services.ai.subagents.checkout_subagent import CheckoutSubagent
from app.services.ai.subagents.service_subagent import ServiceSubagent


@pytest.fixture
def salon_db(db_session: Session):
    # Empresa / Salão
    settings = db_session.query(CompanySettings).first()
    if not settings:
        settings = CompanySettings(
            company_name="Espaço Beleza & Estética",
            assistant_name="Sofia",
            ai_provider="openai",
            ai_model="gpt-4o-mini",
            ai_api_key="sk-fake-key",
        )
        db_session.add(settings)

    # Cliente
    user = User(
        name="Juliana Costa",
        phone="5585988887777",
        whatsapp_number="5585988887777",
    )
    db_session.add(user)
    db_session.flush()

    # Serviço de Salão
    serv = Service(
        name="Corte & Hidratação",
        category="Cabelo",
        description="Corte bordado e hidratação profunda",
        duration_minutes=60,
        price_cents=12000,
    )
    db_session.add(serv)
    db_session.flush()

    # Profissional
    prof = Professional(
        name="Camila Hairstylist",
        phone="5585977776666",
        bio="Cabelos e Mechas",
        active=True,
    )
    db_session.add(prof)
    db_session.flush()

    from decimal import Decimal
    ps = ProfessionalService(
        professional_id=prof.id,
        service_id=serv.id,
        commission_percent=Decimal("15.00"),
        active=True,
    )
    db_session.add(ps)

    # Produto de Salão
    prod = Product(
        name="Óleo Reparador de Pontas 60ml",
        category="Finalizadores",
        description="Óleo com argan e macadâmia",
        price_cents=6500,
        stock_quantity=15,
        active=True,
    )
    db_session.add(prod)
    db_session.commit()

    return {
        "db": db_session,
        "user": user,
        "service": serv,
        "professional": prof,
        "product": prod,
        "settings": settings,
    }


def test_salon_prompt_engine(salon_db):
    settings = salon_db["settings"]
    user = salon_db["user"]

    prompt = SalonPromptEngine.build_system_prompt(settings, user)
    assert "Espaço Beleza & Estética" in prompt
    assert "Sofia" in prompt
    assert "Juliana Costa" in prompt
    assert "listar_servicos_salao" in prompt
    assert "consultar_catalogo" in prompt


@pytest.mark.asyncio
async def test_service_subagent_listar_servicos(salon_db):
    subagent = ServiceSubagent(salon_db["db"], user=salon_db["user"])
    res_json = await subagent.execute_tool("listar_servicos_salao", {"categoria": "Cabelo"})
    import json
    data = json.loads(res_json)
    assert data["status"] == "sucesso"
    assert data["total"] >= 1
    assert any("Corte" in s["nome"] for s in data["servicos"])


@pytest.mark.asyncio
async def test_service_subagent_listar_profissionais(salon_db):
    subagent = ServiceSubagent(salon_db["db"], user=salon_db["user"])
    res_json = await subagent.execute_tool("listar_profissionais_salao", {})
    import json
    data = json.loads(res_json)
    assert data["status"] == "sucesso"
    assert any("Camila" in p["nome"] for p in data["profissionais"])


@pytest.mark.asyncio
async def test_service_subagent_agendar_servico(salon_db):
    from datetime import time

    from app.models.availability import Availability

    start_time = (datetime.now(UTC) + timedelta(days=2)).replace(hour=14, minute=0, second=0, microsecond=0)
    avail = Availability(
        professional_id=salon_db["professional"].id,
        day_of_week=start_time.weekday(),
        start_time=time(8, 0),
        end_time=time(18, 0),
    )
    salon_db["db"].add(avail)
    salon_db["db"].commit()

    subagent = ServiceSubagent(salon_db["db"], user=salon_db["user"])

    res_json = await subagent.execute_tool(
        "agendar_servico",
        {
            "servico_id": salon_db["service"].id,
            "profissional_id": salon_db["professional"].id,
            "horario_inicio": start_time.isoformat(),
            "observacoes": "Prefere lavagem com água morna",
        },
    )
    import json
    data = json.loads(res_json)
    assert data["status"] == "sucesso"
    assert data["agendamento_id"] is not None
    assert data["status_reserva"] == "pending"


@pytest.mark.asyncio
async def test_checkout_subagent_gerar_pix_produto(salon_db):
    subagent = CheckoutSubagent(salon_db["db"], user=salon_db["user"])

    mock_asaas = AsyncMock()
    mock_asaas.create_payment.return_value = {
        "id": "pay_test_999",
        "invoiceUrl": "https://sandbox.asaas.com/i/test999",
    }
    mock_asaas.get_pix_qr_code.return_value = {
        "payload": "00020126580014br.gov.bcb.pix0136test-pix-code520400005303986540565.00",
    }
    subagent.payment_service.asaas = mock_asaas
    subagent.payment_service.ensure_asaas_customer = AsyncMock(return_value="cus_test_123")

    res_json = await subagent.execute_tool(
        "gerar_cobranca_pix_produto",
        {
            "produto_id": salon_db["product"].id,
            "quantidade": 1,
        },
    )
    import json
    data = json.loads(res_json)
    assert data["status"] == "sucesso"
    assert "Óleo Reparador" in data["produto"]
    assert "65.00" in data["valor_total_reais"]
    assert data["pix_copia_e_cola"] is not None


@pytest.mark.asyncio
async def test_salon_orchestrator_deterministic_fallback(salon_db):
    # Desativa API key para testar fallback determinístico
    salon_db["settings"].ai_api_key = ""
    orchestrator = SalonAIOrchestrator(
        db=salon_db["db"],
        user=salon_db["user"],
        company_settings=salon_db["settings"],
    )

    resp = await orchestrator.process_message("Gostaria de agendar um horário para amanhã", [])
    assert "Espaço Beleza & Estética" in resp
    assert "Cabelo" in resp or "horários" in resp
