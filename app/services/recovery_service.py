import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.company_settings import CompanySettings
from app.models.payment import Payment
from app.models.user import User
from app.services.whatsapp.gateway import WhatsAppGatewayService

logger = logging.getLogger(__name__)


class SalesRecoveryService:
    """
    Serviço inteligente de recuperação de agendamentos e vendas no WhatsApp.
    Identifica agendamentos ou pagamentos de produtos pendentes após um período
    de inatividade e dispara uma mensagem amigável de follow-up.
    """

    def __init__(self, db: Session):
        self.db = db
        self.gateway = WhatsAppGatewayService(db)

    def find_pending_leads(self, minutes_idle: int = 45, max_age_hours: int = 24) -> list[User]:
        now = datetime.now(UTC)
        idle_threshold = now - timedelta(minutes=minutes_idle)
        max_age_threshold = now - timedelta(hours=max_age_hours)

        # 1. Usuários com agendamentos pendentes
        pending_appointment_user_ids = [
            r[0]
            for r in self.db.query(Appointment.user_id)
            .filter(
                Appointment.status == "pending",
                Appointment.created_at <= idle_threshold,
                Appointment.created_at >= max_age_threshold,
            )
            .distinct()
            .all()
        ]

        # 2. Usuários com cobranças pendentes
        pending_payment_user_ids = [
            r[0]
            for r in self.db.query(Payment.user_id)
            .filter(
                Payment.status == "pending",
                Payment.created_at <= idle_threshold,
                Payment.created_at >= max_age_threshold,
                Payment.user_id.isnot(None),
            )
            .distinct()
            .all()
        ]

        candidate_ids = set(pending_appointment_user_ids + pending_payment_user_ids)
        if not candidate_ids:
            return []

        users = self.db.query(User).filter(User.id.in_(candidate_ids), User.active.is_(True)).all()
        eligible_users = []

        for u in users:
            custom = u.custom_fields or {}
            last_followup = custom.get("last_followup_at")
            if last_followup:
                try:
                    dt = datetime.fromisoformat(last_followup)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    if now - dt < timedelta(hours=20):
                        continue
                except Exception as exc:
                    logger.debug("Falha ao analisar last_followup_at: %s", exc)

            phone = u.whatsapp_number or u.phone
            if phone:
                eligible_users.append(u)

        return eligible_users

    def _get_salon_name(self) -> str:
        settings = self.db.query(CompanySettings).first()
        if settings and settings.company_name and settings.company_name.strip():
            return settings.company_name.strip()
        return "Nosso Salão & Estética"

    async def send_followup_to_user(self, user: User, custom_text: str | None = None) -> dict[str, Any]:
        phone = user.whatsapp_number or user.phone
        if not phone:
            return {"success": False, "error": "Cliente sem número de telefone"}

        salon_name = self._get_salon_name()

        if custom_text and custom_text.strip():
            msg_text = custom_text.strip()
        else:
            # Verifica se tem agendamento pendente recente
            recent_apt = (
                self.db.query(Appointment)
                .filter(Appointment.user_id == user.id, Appointment.status == "pending")
                .order_by(Appointment.created_at.desc())
                .first()
            )
            if recent_apt and recent_apt.service:
                msg_text = (
                    f"Olá, *{user.name}*! Tudo bem? ✨\n\n"
                    f"Passando para saber se você gostaria de confirmar o seu horário para *{recent_apt.service.name}* "
                    f"no *{salon_name}*!\n\n"
                    "Ainda temos vagas especiais para você. Se quiser manter ou alterar o horário, basta me responder aqui! 💖💇‍♀️"
                )
            else:
                msg_text = (
                    f"Olá, *{user.name}*! Tudo bem? ✨\n\n"
                    f"Passando para saber se ficou alguma dúvida sobre nossos serviços e produtos no *{salon_name}*!\n\n"
                    "Qualquer coisa, estou à disposição para te ajudar por aqui! 💖"
                )

        result = await self.gateway.send_text(phone, msg_text)

        if result.success:
            custom = dict(user.custom_fields or {})
            custom["last_followup_at"] = datetime.now(UTC).isoformat()
            user.custom_fields = custom
            self.db.commit()
            return {"success": True, "message_id": result.message_id}
        else:
            return {"success": False, "error": result.error}
