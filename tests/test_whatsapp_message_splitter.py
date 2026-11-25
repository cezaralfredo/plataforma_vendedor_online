from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.seller_ai_service import SellerAIService
from app.services.whatsapp.base import InboundMessage, SendMessageResult
from app.services.whatsapp.message_splitter import (
    compute_typing_delay,
    split_whatsapp_message,
)


def test_short_message_stays_single_bubble():
    msg = "Ótima escolha! Qual dia e período você prefere para o agendamento? 😊"
    assert split_whatsapp_message(msg) == [msg]


def test_pix_brcode_is_isolated_in_standalone_bubble_without_backticks():
    pix = (
        "00020126580014br.gov.bcb.pix013612345678-1234-1234-1234-1234567890ab"
        "52040000530398654071200.005802BR5915CLINICA MODELO6009FORTALEZA62140510APPT1234566304ABCD"
    )
    msg = (
        "Perfeito, Izabel! Seu agendamento de Botox com a Dra. Larissa foi pré-reservado para quinta-feira às 15:00.\n\n"
        f"Use o código PIX Copia e Cola abaixo:\n`{pix}`\n\n"
        "Assim que realizar o pagamento, me avise por aqui para agilizar a confirmação! 😊"
    )
    chunks = split_whatsapp_message(msg)
    assert len(chunks) in (3, 4)
    assert pix in chunks
    assert all("`" not in c for c in chunks)


def test_long_multi_paragraph_message_splits_into_at_most_4_bubbles():
    msg = (
        "Olá! Seja muito bem-vinda à Clínica Modelo! ✨\n\n"
        "Trabalhamos com diversos procedimentos estéticos faciais e corporais feitos com produtos de alta qualidade e acompanhamento personalizado.\n\n"
        "Nossos principais serviços são:\n\n"
        "- Botox (Toxina Botulínica): R$ 1.200,00 (60 min)\n"
        "- Preenchimento Labial: R$ 1.500,00 (60 min)\n"
        "- Limpeza de Pele Profunda: R$ 180,00 (60 min)\n"
        "- Bioestimulador de Colágeno: R$ 2.200,00 (60 min)\n\n"
        "Todos os atendimentos são realizados mediante agendamento prévio com a Dra. Larissa Almeida, de segunda a sexta.\n\n"
        "Qual desses procedimentos você gostaria de saber mais detalhes ou agendar?"
    )
    chunks = split_whatsapp_message(msg)
    assert 2 <= len(chunks) <= 4
    # A introdução terminada em ":" deve ficar colada à lista de serviços
    assert any("Nossos principais serviços são:\n- Botox" in c for c in chunks)
    # A pergunta final deve ficar na última bolha
    assert chunks[-1].endswith("agendar?")


def test_long_single_paragraph_splits_on_sentence_boundaries():
    msg = (
        "Olá! Tudo bem por aí? Ficamos muito felizes com o seu contato hoje na nossa clínica. "
        "O tratamento com toxina botulínica ajuda a suavizar linhas de expressão com resultado natural e duradouro. "
        "A aplicação leva cerca de trinta minutos e o retorno às atividades normais é praticamente imediato. "
        "Trabalhamos com horários flexíveis de segunda a sexta-feira no período da manhã e da tarde. "
        "Qual dia e horário ficam melhores para agendarmos a sua avaliação?"
    )
    chunks = split_whatsapp_message(msg)
    assert 2 <= len(chunks) <= 4
    assert " ".join(chunks) == msg


def test_compute_typing_delay_bounds():
    assert compute_typing_delay("") == 0.0
    assert compute_typing_delay("00020126580014br.gov.bcb.pix") == 0.6
    assert 0.7 <= compute_typing_delay("Oi!") <= 1.8
    assert compute_typing_delay("x" * 1000) == 1.8


@pytest.mark.asyncio
async def test_seller_ai_dispatches_long_reply_as_multiple_bubbles(db_session):
    from app.models.user import User

    user = User(name="Izabel Teste", phone="5585999990011", whatsapp_number="5585999990011")
    db_session.add(user)
    db_session.commit()

    inbound = InboundMessage(
        provider="waha",
        sender_phone=user.phone,
        sender_name=user.name,
        text="Quais serviços vocês oferecem?",
        message_id="msg-split-001",
    )

    long_reply = (
        "Olá, Izabel! Seja muito bem-vinda! ✨\n\n"
        "Temos diversos procedimentos estéticos faciais realizados com acompanhamento individualizado e materiais de primeira linha.\n\n"
        "Confira nossos principais procedimentos:\n"
        "- Botox: R$ 1.200,00\n"
        "- Preenchimento Labial: R$ 1.500,00\n"
        "- Limpeza de Pele: R$ 180,00\n\n"
        "Qual deles você gostaria de agendar hoje?"
    )

    seller = SellerAIService(db_session)
    seller.triage_service = MagicMock()
    seller.triage_service.evaluate = AsyncMock(return_value=None)

    with (
        patch("app.services.ai.orchestrator.SalonAIOrchestrator.process_message", new=AsyncMock(return_value=long_reply)),
        patch("app.services.whatsapp.gateway.WhatsAppGatewayService.send_text", new=AsyncMock(return_value=SendMessageResult(success=True, message_id="out-1", provider="waha"))) as mock_send,
        patch("app.services.whatsapp.gateway.WhatsAppGatewayService.set_typing", new=AsyncMock()) as mock_typing,
        patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
    ):
        returned = await seller.handle_inbound_message(inbound)

    assert returned == long_reply
    assert 2 <= mock_send.await_count <= 4
    assert mock_sleep.await_count == mock_send.await_count - 1
    assert mock_typing.await_count == 2 * (mock_send.await_count - 1)
