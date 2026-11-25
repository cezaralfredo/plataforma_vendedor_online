
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.models.appointment import Appointment
from app.models.notification_log import NotificationLog
from app.models.payment import Payment
from app.models.user import User
from tests.seed import seed_appointment, seed_data, seed_payment


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Key": settings.admin_api_key}


def test_admin_can_create_client_and_appointment(
    anonymous_client: TestClient, db_session: Session
):
    entities = seed_data(db_session)

    response = anonymous_client.post(
        "/admin/api/appointments",
        headers=_admin_headers(),
        json={
            "new_client": {
                "name": "Cliente Teste",
                "phone": "+5511977777777",
                "email": "cliente@example.com",
            },
            "professional_id": entities["professional"].id,
            "service_id": entities["service"].id,
            "start_time": "2026-07-30T09:00:00-03:00",
            "end_time": "2026-07-30T10:00:00-03:00",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    assert response.json()["client_name"] == "Cliente Teste"


def test_appointments_page_exposes_admin_creation_flow(
    anonymous_client: TestClient, db_session: Session
):
    seed_data(db_session)

    response = anonymous_client.get(
        "/admin/appointments", headers=_admin_headers()
    )

    assert response.status_code == 200
    assert "Novo agendamento" in response.text
    assert "Cadastrar novo cliente" in response.text
    assert "Excluir" in response.text


def test_appointments_page_only_offers_active_clients(
    anonymous_client: TestClient, db_session: Session
):
    entities = seed_data(db_session)
    archived = User(name="Cliente Arquivado", phone="+5511900000000", active=False)
    db_session.add(archived)
    db_session.commit()

    response = anonymous_client.get("/admin/appointments", headers=_admin_headers())

    assert response.status_code == 200
    assert '"phone": "' + entities["user"].phone + '"' not in response.text
    assert "masked_phone" in response.text
    assert "Cliente Arquivado" not in response.text


def test_admin_cannot_create_appointment_for_archived_client(
    anonymous_client: TestClient, db_session: Session
):
    entities = seed_data(db_session)
    entities["user"].active = False
    db_session.commit()

    response = anonymous_client.post(
        "/admin/api/appointments",
        headers=_admin_headers(),
        json={
            "user_id": entities["user"].id,
            "professional_id": entities["professional"].id,
            "service_id": entities["service"].id,
            "start_time": "2026-07-30T09:00:00-03:00",
            "end_time": "2026-07-30T10:00:00-03:00",
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Cliente arquivado não pode receber novos agendamentos."}


def test_complete_action_returns_domain_response_instead_of_500(
    anonymous_client: TestClient, db_session: Session
):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    appointment.status = "confirmed"
    db_session.commit()

    response = anonymous_client.post(
        f"/admin/appointments/{appointment.id}/action",
        headers=_admin_headers(),
        json={"action": "complete", "notes": "Serviço realizado"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_admin_can_update_pending_appointment_notes(
    anonymous_client: TestClient, db_session: Session
):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    appointment.status = "pending"
    db_session.commit()

    response = anonymous_client.put(
        f"/admin/api/appointments/{appointment.id}",
        headers=_admin_headers(),
        json={"notes": "Cliente pediu confirmação por WhatsApp"},
    )

    assert response.status_code == 200
    assert response.json()["notes"] == "Cliente pediu confirmação por WhatsApp"


def test_admin_cannot_delete_appointment_with_payment(
    anonymous_client: TestClient, db_session: Session
):
    entities = seed_data(db_session)
    appointment = seed_appointment(db_session, entities)
    seed_payment(db_session, appointment)

    response = anonymous_client.delete(
        f"/admin/api/appointments/{appointment.id}", headers=_admin_headers()
    )

    assert response.status_code == 409
    assert "pagamento" in response.json()["detail"].lower()


@pytest.mark.parametrize("status", ["pending", "overdue", "cancelled"])
def test_admin_can_delete_cancelled_appointment_with_unpaid_payment(
    anonymous_client: TestClient, db_session: Session, status: str
):
    appointment = seed_appointment(db_session, seed_data(db_session))
    payment = seed_payment(db_session, appointment)
    appointment.status = "cancelled"
    payment.status = status
    db_session.add(NotificationLog(appointment_id=appointment.id, type="payment_overdue"))
    db_session.commit()
    appointment_id, payment_id = appointment.id, payment.id

    response = anonymous_client.delete(
        f"/admin/api/appointments/{appointment_id}", headers=_admin_headers()
    )

    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(Appointment, appointment_id) is None
    assert db_session.get(Payment, payment_id) is None
    assert db_session.query(NotificationLog).count() == 0


@pytest.mark.parametrize("status", ["received", "confirmed", "refunded"])
def test_admin_can_delete_cancelled_appointment_preserving_paid_history(
    anonymous_client: TestClient, db_session: Session, status: str
):
    appointment = seed_appointment(db_session, seed_data(db_session))
    payment = seed_payment(db_session, appointment)
    appointment.status = "cancelled"
    payment.status = status
    db_session.commit()
    appointment_id, payment_id = appointment.id, payment.id

    response = anonymous_client.delete(
        f"/admin/api/appointments/{appointment_id}", headers=_admin_headers()
    )
    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(Appointment, appointment_id) is None
    preserved = db_session.get(Payment, payment_id)
    assert preserved is not None
    assert preserved.appointment_id is None


def test_admin_can_delete_cancelled_appointment_with_overdue_receipt_preserving_payment(
    anonymous_client: TestClient, db_session: Session
):
    appointment = seed_appointment(db_session, seed_data(db_session))
    payment = seed_payment(db_session, appointment)
    appointment.status = "cancelled"
    payment.status = "overdue"
    payment.received_at = datetime.now()
    db_session.commit()
    appointment_id, payment_id = appointment.id, payment.id

    response = anonymous_client.delete(
        f"/admin/api/appointments/{appointment_id}", headers=_admin_headers()
    )
    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(Appointment, appointment_id) is None
    preserved = db_session.get(Payment, payment_id)
    assert preserved is not None
    assert preserved.appointment_id is None


@pytest.mark.parametrize("appointment_status", ["confirmed", "completed"])
def test_admin_preserves_confirmed_or_completed_with_overdue_charge(
    anonymous_client: TestClient, db_session: Session, appointment_status: str
):
    appointment = seed_appointment(db_session, seed_data(db_session))
    payment = seed_payment(db_session, appointment)
    appointment.status = appointment_status
    payment.status = "overdue"
    db_session.commit()
    response = anonymous_client.delete(
        f"/admin/api/appointments/{appointment.id}", headers=_admin_headers()
    )
    assert response.status_code == 409
    assert db_session.get(Payment, payment.id) is not None


def test_admin_cancelled_appointment_unlinks_paid_and_removes_unpaid_payments(
    anonymous_client: TestClient, db_session: Session
):
    appointment = seed_appointment(db_session, seed_data(db_session))
    unpaid = seed_payment(db_session, appointment)
    appointment.status = "cancelled"
    unpaid.status = "overdue"
    paid = Payment(
        appointment_id=appointment.id,
        amount_cents=5000,
        billing_type="pix",
        status="received",
    )
    db_session.add(paid)
    db_session.commit()
    appointment_id, unpaid_id, paid_id = appointment.id, unpaid.id, paid.id

    response = anonymous_client.delete(
        f"/admin/api/appointments/{appointment_id}", headers=_admin_headers()
    )
    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(Appointment, appointment_id) is None
    assert db_session.get(Payment, unpaid_id) is None
    preserved_paid = db_session.get(Payment, paid_id)
    assert preserved_paid is not None
    assert preserved_paid.appointment_id is None
