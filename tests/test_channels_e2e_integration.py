from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.chat_message import ChatMessage
from app.models.company_settings import CompanySettings
from app.models.payment import Payment
from app.models.product import Product
from app.models.professional import Professional
from app.models.professional_service import ProfessionalService
from app.models.service import Service
from app.models.user import User
from app.services.ai.providers.base import LLMResponse, ToolCall
from app.services.notification_service import NotificationService
from app.services.seller_ai_service import SellerAIService
from app.services.whatsapp.base import InboundMessage, SendMessageResult


@pytest.fixture
def e2e_salon_db(db_session: Session):
    settings = db_session.query(CompanySettings).first()
    if not settings:
        settings = CompanySettings(
            company_name="Salão Estilo & Beleza",
            assistant_name="Sofia",
            ai_provider="openai",
            ai_model="gpt-4o-mini",
            ai_api_key="sk-test",
            whatsapp_provider="waha",
            coexistence_enabled=True,
            coexistence_pause_minutes=45,
        )
        db_session.add(settings)

    user = User(
        name="Beatriz Silva",
        phone="5585981112222",
        whatsapp_number="5585981112222",
    )
    db_session.add(user)
    db_session.flush()

    serv = Service(
        name="Manicure & Pedicure Spa",
        category="Unhas",
        description="Spa completo para mãos e pés",
        duration_minutes=45,
        price_cents=7500,
    )
    db_session.add(serv)
    db_session.flush()

    prof = Professional(
        name="Luciana Especialista",
        phone="5585994443333",
        bio="Especialista em Unhas e Esmaltação em Gel",
        active=True,
    )
    db_session.add(prof)
    db_session.flush()

    from decimal import Decimal

    ps = ProfessionalService(
        professional_id=prof.id,
        service_id=serv.id,
        commission_percent=Decimal("20.00"),
        active=True,
    )
    db_session.add(ps)

    from datetime import time

    from app.models.availability import Availability

    for dow in range(7):
        avail = Availability(
            professional_id=prof.id,
            day_of_week=dow,
            start_time=time(8, 0),
            end_time=time(19, 0),
        )
        db_session.add(avail)

    prod = Product(
        name="Kit Esmaltes & Hidratante de Cutículas",
        category="Unhas",
        description="Kit profissional para cuidados diários",
        price_cents=4990,
        stock_quantity=8,
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


@pytest.mark.asyncio
async def test_e2e_waha_inbound_appointment_booking(e2e_salon_db):
    db: Session = e2e_salon_db["db"]
    user: User = e2e_salon_db["user"]
    serv: Service = e2e_salon_db["service"]
    prof: Professional = e2e_salon_db["professional"]

    start_dt = (datetime.now(UTC) + timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)

    inbound = InboundMessage(
        message_id="waha-msg-001",
        sender_phone=user.phone,
        sender_name=user.name,
        text="Olá, quero agendar Manicure & Pedicure Spa para a próxima segunda às 10h com a Luciana",
        provider="waha",
    )

    seller_ai = SellerAIService(db)

    # 1º turno LLM: chama a ferramenta 'agendar_servico' do ServiceSubagent
    tool_call = ToolCall(
        id="call_agendar_1",
        name="agendar_servico",
        arguments={
            "servico_id": serv.id,
            "profissional_id": prof.id,
            "horario_inicio": start_dt.strftime("%Y-%m-%d %H:%M"),
            "observacoes": "Agendado via WhatsApp IA",
        },
    )
    first_llm_response = LLMResponse(content="", tool_calls=[tool_call])

    # 2º turno LLM: confirmação final humanizada
    second_llm_response = LLMResponse(
        content=(
            f"Perfeito, {user.name}! ✨ Seu horário para *{serv.name}* com a *{prof.name}* está pré-agendado para "
            f"{start_dt.strftime('%d/%m às %H:%M')}.\n\n"
            "Chave PIX gerada com sucesso! Copie o código abaixo para efetuar o pagamento. 💖"
        ),
        tool_calls=[],
    )

    with (
        patch("app.services.ai.providers.openai_client.OpenAICompatibleClient.generate_response", new_callable=AsyncMock) as mock_llm,
        patch("app.services.whatsapp.gateway.WhatsAppGatewayService.send_text", new_callable=AsyncMock) as mock_send,
    ):
        mock_llm.side_effect = [first_llm_response, second_llm_response]
        mock_send.return_value = SendMessageResult(success=True, message_id="out-waha-001", provider="waha")

        reply = await seller_ai.handle_inbound_message(inbound)

        assert "Seu horário para *Manicure & Pedicure Spa*" in reply
        assert mock_send.called
        assert mock_send.call_args[0][0] == user.phone

        # Verifica se o agendamento foi persistido no banco
        appointment = db.query(Appointment).filter(Appointment.user_id == user.id).first()
        assert appointment is not None
        assert appointment.service_id == serv.id
        assert appointment.professional_id == prof.id
        assert appointment.status == "pending"

        # Verifica histórico de mensagens salvas em chat_messages
        messages = db.query(ChatMessage).filter(ChatMessage.phone == user.phone).all()
        roles = [m.role for m in messages]
        assert "user" in roles
        assert "assistant" in roles


@pytest.mark.asyncio
async def test_e2e_evolution_inbound_product_purchase(e2e_salon_db):
    db: Session = e2e_salon_db["db"]
    user: User = e2e_salon_db["user"]
    prod: Product = e2e_salon_db["product"]

    user.asaas_customer_id = "cus_mock_evo_123"
    db.commit()

    inbound = InboundMessage(
        message_id="evo-msg-002",
        sender_phone=user.phone,
        sender_name=user.name,
        text="Quero comprar o Kit Esmaltes & Hidratante",
        provider="evolution",
    )

    seller_ai = SellerAIService(db)

    # 1º turno LLM: chama 'gerar_cobranca_pix_produto'
    tool_call = ToolCall(
        id="call_checkout_prod",
        name="gerar_cobranca_pix_produto",
        arguments={
            "produto_id": prod.id,
            "quantidade": 1,
        },
    )
    first_llm_response = LLMResponse(content="", tool_calls=[tool_call])

    # 2º turno LLM: resposta conversacional
    second_llm_response = LLMResponse(
        content=(
            f"Maravilha, {user.name}! 💅 Seu pedido de *{prod.name}* (R$ 49,90) foi gerado!\n\n"
            "Aqui está o código PIX Copia e Cola para pagamento."
        ),
        tool_calls=[],
    )

    with (
        patch("app.services.ai.providers.openai_client.OpenAICompatibleClient.generate_response", new_callable=AsyncMock) as mock_llm,
        patch("app.services.whatsapp.gateway.WhatsAppGatewayService.send_text", new_callable=AsyncMock) as mock_send,
        patch("app.services.asaas_client.AsaasClient.create_payment", new_callable=AsyncMock) as mock_create_pay,
        patch("app.services.asaas_client.AsaasClient.get_pix_qr_code", new_callable=AsyncMock) as mock_qr,
    ):
        mock_llm.side_effect = [first_llm_response, second_llm_response]
        mock_send.return_value = SendMessageResult(success=True, message_id="out-evo-002", provider="evolution")
        mock_create_pay.return_value = {
            "id": "pay_evo_mock_123",
            "invoiceUrl": "https://sandbox.asaas.com/i/mock_evo_123",
            "status": "PENDING",
            "value": 49.90,
        }
        mock_qr.return_value = {
            "encodedImage": "mock_img_base64",
            "payload": "00020126580014BR.GOV.BCB.PIX...",
            "expirationDate": "2026-10-25 23:59:59",
        }

        reply = await seller_ai.handle_inbound_message(inbound)

        assert "Kit Esmaltes & Hidratante" in reply
        assert mock_send.called

        # Verifica se o pagamento de produto foi criado no banco
        payment = db.query(Payment).filter(Payment.product_id == prod.id).first()
        assert payment is not None
        assert payment.user_id == user.id
        assert payment.appointment_id is None
        assert payment.amount_cents == 4990


@pytest.mark.asyncio
async def test_e2e_meta_inbound_human_support_and_silencing(e2e_salon_db):
    db: Session = e2e_salon_db["db"]
    user: User = e2e_salon_db["user"]

    inbound = InboundMessage(
        message_id="meta-msg-003",
        sender_phone=user.phone,
        sender_name=user.name,
        text="Preciso falar com uma pessoa urgente, por favor",
        provider="meta",
    )

    seller_ai = SellerAIService(db)

    tool_call = ToolCall(
        id="call_support_001",
        name="acionar_atendente_humano",
        arguments={"motivo": "Cliente solicitou atendimento urgente com humano"},
    )
    first_llm = LLMResponse(content="", tool_calls=[tool_call])
    second_llm = LLMResponse(
        content="Com certeza! Já notifiquei nossa equipe para assumir seu atendimento. 🤝\n\nCaso prefira falar diretamente, acesse o link: https://wa.me/5585994443333",
        tool_calls=[],
    )

    with (
        patch("app.services.ai.providers.openai_client.OpenAICompatibleClient.generate_response", new_callable=AsyncMock) as mock_llm,
        patch("app.services.whatsapp.gateway.WhatsAppGatewayService.send_text", new_callable=AsyncMock) as mock_send,
    ):
        mock_llm.side_effect = [first_llm, second_llm]
        mock_send.return_value = SendMessageResult(success=True, message_id="out-meta-003", provider="meta")

        reply = await seller_ai.handle_inbound_message(inbound)

        assert "notifiquei nossa equipe" in reply
        assert "wa.me/" in reply

        # Verifica se a IA foi silenciada / pausada
        db.refresh(user)
        paused_until = user.custom_fields.get("ai_paused_until")
        assert paused_until is not None

        # Próxima mensagem do usuário enquanto pausada deve ser ignorada pela IA
        inbound_followup = InboundMessage(
            message_id="meta-msg-004",
            sender_phone=user.phone,
            sender_name=user.name,
            text="Ainda estou aguardando",
            provider="meta",
        )
        silenced_reply = await seller_ai.handle_inbound_message(inbound_followup)
        assert silenced_reply == ""

        # Usuário enviando comando de retorno para o bot
        inbound_resume = InboundMessage(
            message_id="meta-msg-005",
            sender_phone=user.phone,
            sender_name=user.name,
            text="voltar para o bot",
            provider="meta",
        )
        with patch("app.services.ai.orchestrator.SalonAIOrchestrator.process_message", new_callable=AsyncMock) as mock_orch:
            mock_orch.return_value = "Olá novamente! Estou de volta para te ajudar. ✨"
            resumed_reply = await seller_ai.handle_inbound_message(inbound_resume)
            assert "Estou de volta" in resumed_reply

            db.refresh(user)
            assert "ai_paused_until" not in user.custom_fields


@pytest.mark.asyncio
async def test_e2e_coexistence_mode_mobile_attendant_interventions(e2e_salon_db):
    db: Session = e2e_salon_db["db"]
    user: User = e2e_salon_db["user"]

    seller_ai = SellerAIService(db)

    # Atendente humano responde do aplicativo móvel do WhatsApp Business
    inbound_human = InboundMessage(
        message_id="human-phone-001",
        sender_phone=user.phone,
        sender_name="Atendente Salão",
        text="Olá Beatriz! Aqui é a Fernanda da recepção, como posso te ajudar?",
        is_from_me=True,
        provider="waha",
    )

    with patch.object(seller_ai, "is_bot_message", return_value=False):
        reply = await seller_ai.handle_inbound_message(inbound_human)
        assert reply == ""  # Não responde a si mesmo

        # Verifica se a pausa automática de 45 minutos foi aplicada
        db.refresh(user)
        paused_until = user.custom_fields.get("ai_paused_until")
        assert paused_until is not None

        # Histórico gravado como human_agent
        msg = db.query(ChatMessage).filter(ChatMessage.phone == user.phone, ChatMessage.role == "human_agent").first()
        assert msg is not None
        assert "Fernanda da recepção" in msg.content

    # Atendente manda comando #ia para reativar o bot
    inbound_reactivate = InboundMessage(
        message_id="human-phone-002",
        sender_phone=user.phone,
        sender_name="Atendente Salão",
        text="#ia",
        is_from_me=True,
        provider="waha",
    )
    with patch.object(seller_ai, "is_bot_message", return_value=False):
        reply2 = await seller_ai.handle_inbound_message(inbound_reactivate)
        assert reply2 == ""

        db.refresh(user)
        assert "ai_paused_until" not in user.custom_fields


@pytest.mark.asyncio
async def test_e2e_whisper_audio_inbound_transcription(e2e_salon_db):
    db: Session = e2e_salon_db["db"]
    user: User = e2e_salon_db["user"]

    inbound_audio = InboundMessage(
        message_id="audio-msg-001",
        sender_phone=user.phone,
        sender_name=user.name,
        text="",
        media_type="audio",
        media_url="https://waha.test/audio/voz_cliente.ogg",
        provider="waha",
    )

    seller_ai = SellerAIService(db)

    with (
        patch(
            "app.services.whatsapp.transcription_service.AudioTranscriptionService.download_and_transcribe",
            new_callable=AsyncMock,
        ) as mock_transcribe,
        patch("app.services.ai.orchestrator.SalonAIOrchestrator.process_message", new_callable=AsyncMock) as mock_orch,
        patch("app.services.whatsapp.gateway.WhatsAppGatewayService.send_text", new_callable=AsyncMock) as mock_send,
    ):
        mock_transcribe.return_value = "Vocês têm horário para corte hoje à tarde?"
        mock_orch.return_value = "Temos sim! Temos disponibilidade às 15h e às 17h. Qual prefere? ✨"
        mock_send.return_value = SendMessageResult(success=True, message_id="out-001", provider="waha")

        reply = await seller_ai.handle_inbound_message(inbound_audio)

        assert "Temos sim!" in reply
        mock_transcribe.assert_called_once_with("https://waha.test/audio/voz_cliente.ogg")

        # Verifica se o texto transcrito foi gravado na tabela chat_messages
        msg = db.query(ChatMessage).filter(ChatMessage.phone == user.phone, ChatMessage.role == "user").first()
        assert msg is not None
        assert msg.content == "Vocês têm horário para corte hoje à tarde?"


@pytest.mark.asyncio
async def test_e2e_asaas_payment_webhook_dispatches_direct_whatsapp(e2e_salon_db):
    db: Session = e2e_salon_db["db"]
    user: User = e2e_salon_db["user"]
    serv: Service = e2e_salon_db["service"]
    prof: Professional = e2e_salon_db["professional"]

    # Cria agendamento e pagamento pendente
    start_time = datetime.now(UTC) + timedelta(days=2)
    end_time = start_time + timedelta(minutes=serv.duration_minutes)

    appt = Appointment(
        user_id=user.id,
        service_id=serv.id,
        professional_id=prof.id,
        start_time=start_time,
        end_time=end_time,
        status="awaiting_payment",
    )
    db.add(appt)
    db.flush()

    payment = Payment(
        appointment_id=appt.id,
        user_id=user.id,
        asaas_payment_id="pay_asaas_e2e_123",
        amount_cents=serv.price_cents,
        billing_type="pix",
        status="pending",
    )
    db.add(payment)
    db.commit()

    notification_service = NotificationService(db)

    with patch("app.services.whatsapp.gateway.WhatsAppGatewayService.send_text", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = SendMessageResult(success=True, message_id="wamid-confirm-001", provider="waha")

        # Registra na outbox e dispara
        delivery = notification_service.queue_payment_confirmed(appt.id, payment.id)
        db.commit()

        dispatched = await notification_service.dispatch_payment_confirmed(delivery.id)
        assert dispatched is True

        assert mock_send.called
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["phone"] == user.whatsapp_number
        assert "pagamento de R$ 75.00 (PIX) foi confirmado" in call_kwargs["text"]
        assert serv.name in call_kwargs["text"]
        assert prof.name in call_kwargs["text"]


def test_e2e_http_waha_webhook_endpoint(anonymous_client, e2e_salon_db):
    payload = {
        "event": "message",
        "payload": {
            "id": "waha-http-001",
            "from": "5585981112222@c.us",
            "body": "Quero agendar manicure",
            "_data": {"notifyName": "Beatriz"},
        },
    }
    with patch("app.services.seller_ai_service.SellerAIService.handle_inbound_message", new_callable=AsyncMock) as mock_handle:
        res = anonymous_client.post("/api/webhooks/whatsapp/waha", json=payload)
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}
        assert mock_handle.called
        inbound = mock_handle.call_args[0][0]
        assert inbound.text == "Quero agendar manicure"
        assert inbound.sender_phone == "5585981112222"
        assert inbound.provider == "waha"


def test_e2e_http_evolution_webhook_endpoint(anonymous_client, e2e_salon_db):
    payload = {
        "data": {
            "key": {
                "remoteJid": "5585981112222@s.whatsapp.net",
                "fromMe": False,
                "id": "evo-http-001",
            },
            "pushName": "Beatriz",
            "message": {
                "conversation": "Quero comprar kit de esmaltes",
            },
        },
    }
    with patch("app.services.seller_ai_service.SellerAIService.handle_inbound_message", new_callable=AsyncMock) as mock_handle:
        res = anonymous_client.post("/api/webhooks/whatsapp/evolution", json=payload)
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}
        assert mock_handle.called
        inbound = mock_handle.call_args[0][0]
        assert inbound.text == "Quero comprar kit de esmaltes"
        assert inbound.sender_phone == "5585981112222"
        assert inbound.provider == "evolution"


def test_e2e_http_meta_webhook_challenge_and_inbound(anonymous_client, e2e_salon_db):
    db: Session = e2e_salon_db["db"]
    settings: CompanySettings = e2e_salon_db["settings"]
    settings.meta_verify_token = "secret_meta_token_123"  # noqa: S105
    db.commit()

    # 1. Verificação do Webhook Meta (GET challenge)
    res_get = anonymous_client.get(
        "/api/webhooks/whatsapp/meta",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "challenge_token_999",
            "hub.verify_token": "secret_meta_token_123",
        },
    )
    assert res_get.status_code == 200
    assert res_get.text == "challenge_token_999"

    # 2. Recebimento de Mensagem Meta (POST inbound)
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "5585994443333"},
                            "contacts": [{"profile": {"name": "Beatriz"}}],
                            "messages": [
                                {
                                    "id": "meta-http-001",
                                    "from": "5585981112222",
                                    "type": "text",
                                    "text": {"body": "Atendimento humano por favor"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    with patch("app.services.seller_ai_service.SellerAIService.handle_inbound_message", new_callable=AsyncMock) as mock_handle:
        res_post = anonymous_client.post("/api/webhooks/whatsapp/meta", json=payload)
        assert res_post.status_code == 200
        assert res_post.json() == {"status": "ok"}
        assert mock_handle.called
        inbound = mock_handle.call_args[0][0]
        assert inbound.text == "Atendimento humano por favor"
        assert inbound.sender_phone == "5585981112222"
        assert inbound.provider == "meta"

