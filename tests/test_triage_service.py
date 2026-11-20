from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.config import settings as app_settings
from app.mcp.tools import handle_tool_call
from app.services.triage_service import HUMAN_SUPPORT_URL, TriageService


@pytest.fixture
def mock_openrouter_human_handoff():
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": {
            "intent": {
                "type": "choice",
                "choice": "falar_humano",
                "confidence": 1.0,
            },
            "is_human_handoff": {
                "type": "noul",
                "noul": 0.98,
            },
            "is_payment_claim": {
                "type": "noul",
                "noul": 0.01,
            },
            "frustration": {
                "type": "score",
                "score": 1.45,
            },
        },
        "usage": {
            "cost": 0.000023,
        },
    }


@pytest.fixture
def mock_openrouter_pix():
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": {
            "intent": {
                "type": "choice",
                "choice": "confirmar_pix",
                "confidence": 0.95,
            },
            "is_human_handoff": {
                "type": "noul",
                "noul": 0.05,
            },
            "is_payment_claim": {
                "type": "noul",
                "noul": 0.92,
            },
            "frustration": {
                "type": "score",
                "score": 0.1,
            },
        },
        "usage": {
            "cost": 0.000022,
        },
    }


@pytest.fixture
def mock_openrouter_booking():
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": {
            "intent": {
                "type": "choice",
                "choice": "novo_agendamento",
                "confidence": 0.99,
            },
            "is_human_handoff": {
                "type": "noul",
                "noul": 0.02,
            },
            "is_payment_claim": {
                "type": "noul",
                "noul": 0.01,
            },
            "frustration": {
                "type": "score",
                "score": 0.0,
            },
        },
        "usage": {
            "cost": 0.000023,
        },
    }


@pytest.mark.asyncio
async def test_triage_human_handoff_fast_path(mock_openrouter_human_handoff):
    settings = Settings(openrouter_api_key="test-key", triage_enabled=True)
    mock_client = AsyncMock()
    mock_client.post.return_value = httpx.Response(
        200,
        json=mock_openrouter_human_handoff,
        request=httpx.Request("POST", "https://openrouter.ai/api/alpha/decisions"),
    )

    service = TriageService(settings=settings, http_client=mock_client)
    result = await service.evaluate("Quero falar com uma pessoa urgente!")

    assert result.recommended_action == "fast_handoff_human"
    assert result.intent == "falar_humano"
    assert result.is_human_handoff >= 0.80
    assert result.frustration_level == "irritado"
    assert HUMAN_SUPPORT_URL in result.fast_response_text
    assert result.is_fallback is False


@pytest.mark.asyncio
async def test_triage_pix_payment_fast_path(mock_openrouter_pix):
    settings = Settings(openrouter_api_key="test-key", triage_enabled=True)
    mock_client = AsyncMock()
    mock_client.post.return_value = httpx.Response(
        200,
        json=mock_openrouter_pix,
        request=httpx.Request("POST", "https://openrouter.ai/api/alpha/decisions"),
    )

    service = TriageService(settings=settings, http_client=mock_client)
    result = await service.evaluate("Acabei de transferir o PIX, confirma aí")

    assert result.recommended_action == "fast_check_pix"
    assert result.intent == "confirmar_pix"
    assert result.is_payment_claim >= 0.75
    assert result.frustration_level == "calmo"
    assert "conferindo a confirmação do seu pagamento" in result.fast_response_text
    assert result.is_fallback is False


@pytest.mark.asyncio
async def test_triage_booking_routes_to_llm(mock_openrouter_booking):
    settings = Settings(openrouter_api_key="test-key", triage_enabled=True)
    mock_client = AsyncMock()
    mock_client.post.return_value = httpx.Response(
        200,
        json=mock_openrouter_booking,
        request=httpx.Request("POST", "https://openrouter.ai/api/alpha/decisions"),
    )

    service = TriageService(settings=settings, http_client=mock_client)
    result = await service.evaluate("Gostaria de agendar um corte amanhã às 15h com a Carla")

    assert result.recommended_action == "route_to_llm"
    assert result.intent == "novo_agendamento"
    assert result.fast_response_text is None
    assert result.is_fallback is False


@pytest.mark.asyncio
async def test_triage_disabled_or_no_key_fallback():
    settings = Settings(openrouter_api_key="", triage_enabled=False)
    service = TriageService(settings=settings)
    result = await service.evaluate("Qualquer mensagem aqui")

    assert result.is_fallback is True
    assert result.recommended_action == "route_to_llm"
    assert result.model_used == "fallback-local"


@pytest.mark.asyncio
async def test_triage_network_error_fallback():
    settings = Settings(openrouter_api_key="test-key", triage_enabled=True)
    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception("Conexão expirada com OpenRouter")

    service = TriageService(settings=settings, http_client=mock_client)
    result = await service.evaluate("Mensagem durante queda de rede")

    assert result.is_fallback is True
    assert result.recommended_action == "route_to_llm"
    assert result.model_used == "fallback-local"


def test_triage_endpoint_fastapi(anonymous_client: TestClient):
    with patch("app.services.triage_service.TriageService.evaluate") as mock_eval:
        from app.schemas.triage import TriageResult
        mock_eval.return_value = TriageResult(
            intent="falar_humano",
            intent_confidence=1.0,
            is_human_handoff=0.99,
            is_payment_claim=0.01,
            frustration_score=1.5,
            frustration_level="irritado",
            recommended_action="fast_handoff_human",
            fast_response_text="Com certeza! Fale aqui: https://wa.me/5585996277707",
            model_used="typesafe/jev-1.13",
            cost_usd=0.00002,
            is_fallback=False,
        )

        response = anonymous_client.post(
            "/api/triage",
            headers={"Authorization": f"Bearer {app_settings.api_key}"},
            json={"message": "Preciso de um atendente humano agora", "phone": "5585999999999"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "falar_humano"
        assert data["recommended_action"] == "fast_handoff_human"
        assert "5585996277707" in data["fast_response_text"]


@pytest.mark.asyncio
async def test_triage_mcp_tool_execution():
    with patch("app.services.triage_service.TriageService.evaluate") as mock_eval:
        from app.schemas.triage import TriageResult
        mock_eval.return_value = TriageResult(
            intent="falar_humano",
            intent_confidence=1.0,
            is_human_handoff=0.99,
            is_payment_claim=0.01,
            frustration_score=1.5,
            frustration_level="irritado",
            recommended_action="fast_handoff_human",
            fast_response_text="Link: https://wa.me/5585996277707",
            model_used="typesafe/jev-1.13",
            cost_usd=0.00002,
            is_fallback=False,
        )

        res = await handle_tool_call(
            name="triar_mensagem",
            arguments={"message": "Atendente humano por favor"},
            db=None,
        )

        assert "content" in res
        text = res["content"][0]["text"]
        assert "Triagem Concluída" in text
        assert "falar_humano" in text
        assert "fast_handoff_human" in text


@pytest.mark.asyncio
async def test_triage_uses_panel_triage_key_and_openrouter_fallback():
    from app.models.company_settings import CompanySettings

    env_settings = Settings(openrouter_api_key="", triage_enabled=True)

    # 1. Chave explícita do painel para o Jev tem prioridade
    cs_explicit = CompanySettings(
        company_name="Salão",
        ai_provider="openai",
        ai_api_key="sk-openai-123",
        triage_enabled=True,
        triage_api_key="sk-or-v1-panel-jev",
        triage_model="typesafe/jev-1.13",
    )
    svc_explicit = TriageService(settings=env_settings, company_settings=cs_explicit)
    enabled, key, model = svc_explicit._resolve_triage_config()
    assert enabled is True
    assert key == "sk-or-v1-panel-jev"
    assert model == "typesafe/jev-1.13"

    # 2. Se triage_api_key estiver vazia e ai_provider == openrouter, reutiliza ai_api_key
    cs_reuse = CompanySettings(
        company_name="Salão",
        ai_provider="openrouter",
        ai_api_key="sk-or-v1-shared",
        triage_enabled=True,
        triage_api_key="",
        triage_model="typesafe/jev-1.13",
    )
    svc_reuse = TriageService(settings=env_settings, company_settings=cs_reuse)
    enabled, key, _ = svc_reuse._resolve_triage_config()
    assert enabled is True
    assert key == "sk-or-v1-shared"

    # 3. Se triage_enabled=False no painel, respeita desativação
    cs_disabled = CompanySettings(
        company_name="Salão",
        ai_provider="openrouter",
        ai_api_key="sk-or-v1-shared",
        triage_enabled=False,
        triage_api_key="sk-or-v1-panel-jev",
    )
    svc_disabled = TriageService(settings=env_settings, company_settings=cs_disabled)
    result = await svc_disabled.evaluate("Quero falar com um atendente")
    assert result.is_fallback is True
