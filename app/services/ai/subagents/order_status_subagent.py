import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.payment import Payment
from app.models.user import User
from app.services.ai.providers.base import ToolDefinition
from app.services.ai.subagents.base import BaseSubagent

logger = logging.getLogger(__name__)


class OrderStatusSubagent(BaseSubagent):
    """
    Subagente especialista em consultar o status de agendamentos e pedidos de produtos do cliente.
    """

    def __init__(self, db: Session, user: User | None = None):
        self.db = db
        self.user = user

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="consultar_meus_pedidos_e_agendamentos",
                description="Consulta os agendamentos de serviços e as compras de produtos recentes do cliente.",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if not self.user:
            return json.dumps({"erro": "Cliente não identificado."}, ensure_ascii=False)

        # 1. Agendamentos
        apts = (
            self.db.query(Appointment)
            .filter(Appointment.user_id == self.user.id)
            .order_by(Appointment.start_time.desc())
            .limit(5)
            .all()
        )
        apts_list = []
        for a in apts:
            apts_list.append({
                "id": a.id,
                "servico": a.service.name if a.service else "Serviço",
                "profissional": a.professional.name if a.professional else "Profissional",
                "horario": a.start_time.strftime("%d/%m/%Y às %H:%M") if a.start_time else "N/A",
                "status": a.status,
            })

        # 2. Pagamentos e Compras de produtos
        payments = (
            self.db.query(Payment)
            .filter(Payment.user_id == self.user.id)
            .order_by(Payment.created_at.desc())
            .limit(5)
            .all()
        )
        orders_list = []
        for p in payments:
            orders_list.append({
                "id": p.id,
                "descricao": p.description or f"Pedido #{p.id}",
                "valor": f"R$ {p.amount_cents / 100:.2f}",
                "status": p.status,
                "link_pagamento": p.invoice_url if p.status == "pending" else None,
            })

        return json.dumps({
            "status": "sucesso",
            "cliente": self.user.name,
            "agendamentos": apts_list,
            "pedidos": orders_list,
        }, ensure_ascii=False)
