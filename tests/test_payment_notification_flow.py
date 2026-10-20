import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.mcp.tools import handle_tool_call
from app.models.appointment import Appointment
from app.models.notification_delivery import NotificationDelivery
from app.models.notification_log import NotificationLog
from app.models.user import User
from app.services.notification_service import (
    NotificationService,
    build_payment_notification_payload,
    format_payment_confirmation_message,
)
from tests.seed import seed_appointment, seed_data, seed_payment


def test_format_payment_confirmation_message(db_session: Session):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    payment.amount_cents = 15000
    payment.billing_type = "pix"

    msg = format_payment_confirmation_message(appointment, payment)

    assert "R$ 150.00 (PIX)" in msg
    assert "confirmado com sucesso" in msg
    assert appointment.service.name in msg
    assert appointment.professional.name in msg
    assert f"#{appointment.id}" in msg


def test_build_payment_notification_payload(db_session: Session):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    payment.status = "confirmed"
    payment.amount_cents = 8000
    payment.billing_type = "pix"
    payment.asaas_payment_id = "pay_test_123"

    payload = build_payment_notification_payload(appointment, payment)

    assert payload["event"] == "payment.confirmed"
    assert payload["appointment_id"] == appointment.id
    assert payload["payment_id"] == payment.id
    assert payload["asaas_payment_id"] == "pay_test_123"
    assert payload["amount_cents"] == 8000
    assert payload["client"]["name"] == appointment.user.name
    assert payload["service"]["name"] == appointment.service.name
    assert payload["professional"]["name"] == appointment.professional.name
    assert "formatted_message" in payload


@pytest.mark.asyncio
async def test_notification_service_dispatch_to_webhook(db_session: Session):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    appointment.notified_at = None
    db_session.commit()

    service = NotificationService(db_session)

    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()

    with (
        patch.object(settings, "notification_webhook_url", "https://webhook.test/webhook/payment"),
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post,
    ):
        mock_post.return_value = mock_resp
        delivery = service.queue_payment_confirmed(appointment.id, payment.id)
        db_session.commit()
        dispatched = await service.dispatch_payment_confirmed(delivery.id)

        assert dispatched is True
        assert mock_post.called
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://webhook.test/webhook/payment"
        sent_json = call_args[1]["json"]
        assert sent_json["event"] == "payment.confirmed"
        assert sent_json["delivery_id"] == delivery.id

    db_session.refresh(appointment)
    assert appointment.notified_at is None

    log = (
        db_session.query(NotificationLog)
        .filter(
            NotificationLog.appointment_id == appointment.id,
            NotificationLog.type == "confirmation",
        )
        .first()
    )
    assert log is None


@pytest.mark.asyncio
async def test_notification_service_no_webhook_configured(db_session: Session):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    appointment.notified_at = None
    db_session.commit()

    service = NotificationService(db_session)

    with (
        patch.object(settings, "notification_webhook_url", ""),
        patch("app.services.whatsapp.gateway.WhatsAppGatewayService.send_text", new_callable=AsyncMock) as mock_send,
    ):
        mock_send.return_value.success = False
        mock_send.return_value.error = "WhatsApp não configurado"
        delivery = service.queue_payment_confirmed(appointment.id, payment.id)
        db_session.commit()
        dispatched = await service.dispatch_payment_confirmed(delivery.id)
        assert dispatched is False

    db_session.refresh(appointment)
    assert appointment.notified_at is None


def test_asaas_webhook_dispatches_notification(client: TestClient, db_session: Session):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    appointment.status = "awaiting_payment"
    appointment.notified_at = None
    payment.status = "pending"
    db_session.commit()

    with patch(
        "app.services.notification_service.NotificationService.dispatch_payment_confirmed",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        mock_dispatch.return_value = True

        response = client.post(
            "/webhooks/asaas",
            json={
                "id": "evt_test_notif_dispatch",
                "event": "PAYMENT_RECEIVED",
                "payment": {"id": payment.asaas_payment_id},
            },
            headers={"asaas-access-token": settings.asaas_webhook_token},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        db_session.refresh(appointment)
        assert appointment.status == "confirmed"

        assert mock_dispatch.called
        delivery = db_session.query(NotificationDelivery).one()
        assert mock_dispatch.call_args[0][0] == delivery.id


def test_mcp_tool_verificar_status_pagamento_confirmed(db_session: Session):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    appointment.status = "confirmed"
    appointment.notified_at = None
    payment.status = "confirmed"
    payment.amount_cents = 12000
    payment.billing_type = "pix"
    db_session.commit()

    result = asyncio.run(
        handle_tool_call("verificar_status_pagamento", {"appointment_id": appointment.id}, db_session)
    )
    text = result["content"][0]["text"]

    assert "Pagamento CONFIRMADO com sucesso" in text
    assert f"#{appointment.id}" in text
    assert "R$ 120.00 (PIX)" in text
    assert appointment.service.name in text

    db_session.refresh(appointment)
    assert appointment.notified_at is None

    delivery = db_session.query(NotificationDelivery).one()
    assert delivery.status == "pending"


def test_delivery_only_marks_appointment_after_provider_acknowledgement(db_session: Session):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    appointment.status = "confirmed"
    db_session.commit()

    service = NotificationService(db_session)
    delivery = service.queue_payment_confirmed(appointment.id, payment.id)
    db_session.commit()

    claimed = service.claim_delivery(delivery.id)
    assert claimed is not None
    db_session.refresh(appointment)
    assert appointment.notified_at is None

    sent = service.mark_delivery_sent(delivery.id, "wamid.test.123")
    assert sent is not None
    assert sent.status == "sent"
    assert sent.provider_message_id == "wamid.test.123"

    db_session.refresh(appointment)
    assert appointment.notified_at is not None
    assert (
        db_session.query(NotificationLog)
        .filter(
            NotificationLog.appointment_id == appointment.id,
            NotificationLog.type == "confirmation",
        )
        .count()
        == 1
    )


def test_delivery_failure_remains_pending_for_retry(db_session: Session):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    appointment.status = "confirmed"
    db_session.commit()

    service = NotificationService(db_session)
    delivery = service.queue_payment_confirmed(appointment.id, payment.id)
    db_session.commit()
    assert service.claim_delivery(delivery.id) is not None

    deferred = service.defer_delivery(delivery.id, "Evolution API indisponível")
    assert deferred is not None
    assert deferred.status == "pending"
    assert deferred.attempts == 1
    assert deferred.last_error == "Evolution API indisponível"

    db_session.refresh(appointment)
    assert appointment.notified_at is None


def test_internal_delivery_claim_and_acknowledgement_api(
    client: TestClient, db_session: Session
):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    appointment.status = "confirmed"
    db_session.commit()

    delivery = NotificationService(db_session).queue_payment_confirmed(
        appointment.id, payment.id
    )
    db_session.commit()

    claim = client.post(f"/internal/notification-deliveries/{delivery.id}/claim")
    assert claim.status_code == 200
    claimed = claim.json()
    assert claimed["delivery"]["status"] == "processing"
    assert claimed["payload"]["delivery_id"] == delivery.id
    assert claimed["payload"]["chatId"] == appointment.user.phone

    acknowledgement = client.post(
        f"/internal/notification-deliveries/{delivery.id}/sent",
        json={"provider_message_id": "wamid.api.123"},
    )
    assert acknowledgement.status_code == 200
    assert acknowledgement.json()["delivery"]["status"] == "sent"

    db_session.refresh(appointment)
    assert appointment.notified_at is not None


def test_due_delivery_list_recovers_preexisting_unnotified_confirmation(
    client: TestClient, db_session: Session
):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    appointment.status = "confirmed"
    payment.status = "confirmed"
    db_session.commit()

    response = client.get("/internal/notification-deliveries")
    assert response.status_code == 200
    data = response.json()
    assert data["recovered"] == 1
    assert [delivery["payment_id"] for delivery in data["deliveries"]] == [payment.id]


def test_mcp_tool_verificar_status_pagamento_pending(db_session: Session):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    appointment.status = "awaiting_payment"
    payment.status = "pending"
    payment.amount_cents = 5000
    payment.invoice_url = "https://sandbox.asaas.com/i/test_invoice"
    db_session.commit()

    with patch(
        "app.services.payment_service.PaymentService.check_payment_status",
        new_callable=AsyncMock,
    ) as mock_check:
        mock_check.return_value = "pending"
        result = asyncio.run(
            handle_tool_call("verificar_status_pagamento", {"appointment_id": appointment.id}, db_session)
        )

    text = result["content"][0]["text"]
    assert "Pagamento PENDENTE" in text
    assert "https://sandbox.asaas.com/i/test_invoice" in text
    assert "R$ 50.00" in text


def test_mcp_tool_meus_agendamentos_rich_format(db_session: Session):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    payment = seed_payment(db_session, appointment)
    appointment.status = "confirmed"
    payment.status = "confirmed"
    db_session.commit()

    result = asyncio.run(
        handle_tool_call("meus_agendamentos", {"user_id": appointment.user_id}, db_session)
    )
    text = result["content"][0]["text"]

    assert f"#{appointment.id}" in text
    assert appointment.service.name in text
    assert appointment.professional.name in text
    assert "Pagamento: Confirmado" in text

    # Test via phone
    result_phone = asyncio.run(
        handle_tool_call("meus_agendamentos", {"phone": entities["user"].phone}, db_session)
    )
    text_phone = result_phone["content"][0]["text"]
    assert f"#{appointment.id}" in text_phone

    # Test via name
    result_name = asyncio.run(
        handle_tool_call("meus_agendamentos", {"name": "João Silva"}, db_session)
    )
    text_name = result_name["content"][0]["text"]
    assert f"#{appointment.id}" in text_name

    # Test buscar_cliente_por_telefone via name
    result_find_name = asyncio.run(
        handle_tool_call("buscar_cliente_por_telefone", {"phone": "João Silva"}, db_session)
    )
    assert "João Silva" in result_find_name["content"][0]["text"]

    # Test without valid user
    result_none = asyncio.run(
        handle_tool_call("meus_agendamentos", {"phone": "11000000000"}, db_session)
    )
    assert "Não foi possível localizar o cliente" in result_none["content"][0]["text"]


def test_mcp_brazilian_phone_ninth_digit_and_cpf_and_tokenized_name(db_session: Session):
    entities = seed_data(db_session)
    # Cadastra usuário exatamente como no caso real do Rafael:
    # Telefone com 12 dígitos (55 85 8643-8493, sem o nono dígito 9), nome "Rafael", CPF "01516443306"
    user = User(
        name="Rafael",
        phone="558586438493",
        whatsapp_number="558586438493",
        cpf_cnpj="01516443306",
    )
    db_session.add(user)
    db_session.flush()

    appt = Appointment(
        user_id=user.id,
        professional_id=entities["professional"].id,
        service_id=entities["service"].id,
        start_time=datetime(2026, 9, 22, 12, 0),
        end_time=datetime(2026, 9, 22, 12, 45),
        status="confirmed",
    )
    db_session.add(appt)
    db_session.commit()

    # 1. Busca por telefone COM o nono dígito (85 9 8643-8493) quando cadastrado sem o 9
    res_phone_9 = asyncio.run(
        handle_tool_call("buscar_cliente_por_telefone", {"phone": "85986438493"}, db_session)
    )
    assert "Rafael" in res_phone_9["content"][0]["text"]

    # 2. Busca por CPF diretamente
    res_cpf = asyncio.run(
        handle_tool_call("buscar_cliente_por_telefone", {"cpf_cnpj": "01516443306"}, db_session)
    )
    assert "Rafael" in res_cpf["content"][0]["text"]

    # 3. Busca por nome composto ("Antonio Rafael") quando no banco está só "Rafael"
    res_name = asyncio.run(
        handle_tool_call("buscar_cliente_por_telefone", {"name": "Antonio Rafael"}, db_session)
    )
    assert "Rafael" in res_name["content"][0]["text"]

    # 4. meus_agendamentos com o telefone com nono dígito
    res_appts_phone = asyncio.run(
        handle_tool_call("meus_agendamentos", {"phone": "85986438493"}, db_session)
    )
    assert f"#{appt.id}" in res_appts_phone["content"][0]["text"]

    # 5. meus_agendamentos com CPF
    res_appts_cpf = asyncio.run(
        handle_tool_call("meus_agendamentos", {"cpf_cnpj": "01516443306"}, db_session)
    )
    assert f"#{appt.id}" in res_appts_cpf["content"][0]["text"]

    # 6. meus_agendamentos com nome composto
    res_appts_name = asyncio.run(
        handle_tool_call("meus_agendamentos", {"name": "Antonio Rafael"}, db_session)
    )
    assert f"#{appt.id}" in res_appts_name["content"][0]["text"]


