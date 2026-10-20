import json
import logging
from typing import Any

from app.models.company_settings import CompanySettings
from app.services.ai.providers.base import ToolDefinition
from app.services.ai.subagents.base import BaseSubagent

logger = logging.getLogger(__name__)


class SupportSubagent(BaseSubagent):
    """
    Subagente especialista em transbordo e encaminhamento para atendimento humano.
    Pausa a IA para o cliente e notifica ativamente o vendedor responsável no WhatsApp.
    """

    def __init__(
        self,
        company_settings: CompanySettings,
        db: Any = None,
        user: Any = None,
        gateway: Any = None,
    ):
        self.company_settings = company_settings
        self.db = db
        self.user = user
        self.gateway = gateway

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="acionar_atendente_humano",
                description="Aciona a equipe de suporte e notifica o vendedor responsável no WhatsApp para assumir o atendimento humano.",
                parameters={
                    "type": "object",
                    "properties": {
                        "motivo": {
                            "type": "string",
                            "description": "Motivo resumido pelo qual o cliente solicitou atendimento humano ou negociação especial.",
                        },
                    },
                },
            ),
        ]

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        from datetime import UTC, datetime, timedelta

        if tool_name != "acionar_atendente_humano":
            return json.dumps({"erro": f"Ferramenta desconhecida: {tool_name}"}, ensure_ascii=False)

        motivo = arguments.get("motivo") or "Solicitação direta de contato com vendedor"
        logger.info("Transbordo humano solicitado: motivo='%s'", motivo)

        # 1. Pausa o autoatendimento da IA para este cliente por 2 horas para não interferir
        if self.user and self.db:
            custom_fields = dict(self.user.custom_fields or {})
            paused_until = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
            custom_fields["ai_paused_until"] = paused_until
            self.user.custom_fields = custom_fields
            try:
                self.db.commit()
            except Exception as e:
                logger.warning("Falha ao registrar pausa da IA no usuário: %s", e)

        # 2. Localiza o vendedor responsável pelo cliente
        seller = None
        if self.user and self.db:
            custom_fields = self.user.custom_fields or {}
            seller_id = custom_fields.get("seller_id")
            if seller_id:
                from app.models.seller import Seller
                seller = self.db.query(Seller).filter(Seller.id == seller_id).first()

        # 3. Notificação ativa no WhatsApp do Vendedor Humano
        seller_notified = False
        client_phone = getattr(self.user, "whatsapp_number", None) or getattr(self.user, "phone", "")
        clean_client_phone = "".join(filter(str.isdigit, client_phone))
        if len(clean_client_phone) in (10, 11) and not clean_client_phone.startswith("55"):
            clean_client_phone = "55" + clean_client_phone

        seller_phone = seller.whatsapp_number if seller else None
        seller_name = seller.name if seller else None

        # Fallback para número da filial caso não haja vendedor com telefone
        if not seller_phone and self.user and self.db:
            custom_fields = self.user.custom_fields or {}
            branch_id = custom_fields.get("branch_id")
            if branch_id:
                from app.models.branch import Branch
                branch = self.db.query(Branch).filter(Branch.id == branch_id).first()
                if branch and branch.whatsapp_number:
                    seller_phone = branch.whatsapp_number
                    seller_name = f"Equipe {branch.name}"

        seller_name = seller_name or "Equipe de Atendimento"
        client_name = getattr(self.user, "name", "Cliente")
        addr_str = f"\n📍 *Endereço:* {self.user.full_address}" if (self.user and getattr(self.user, "full_address", None)) else ""

        if self.gateway and seller_phone:
            alert_text = (
                f"🔔 *Atendimento Humano Solicitado!*\n\n"
                f"Olá, *{seller_name}*! O cliente *{client_name}* solicitou atendimento humano no WhatsApp.\n\n"
                f"📱 *Telefone do Cliente:* {client_phone}"
                f"{addr_str}\n"
                f"💬 *Motivo:* {motivo}\n\n"
                f"👉 [Clique aqui para abrir a conversa](https://wa.me/{clean_client_phone})\n"
                f"A IA foi pausada temporariamente para você atender com exclusividade! 🤝"
            )
            try:
                res = await self.gateway.send_text(seller_phone, alert_text)
                seller_notified = getattr(res, "success", False)
                logger.info("Notificação de transbordo enviada ao vendedor %s: %s", seller_name, seller_notified)
            except Exception as e:
                logger.warning("Falha ao notificar vendedor via WhatsApp: %s", e)

        support_phone = seller_phone or "5585996277707"
        support_link = f"https://wa.me/{support_phone}"

        return json.dumps({
            "status": "sucesso",
            "vendedor_notificado": seller_notified,
            "vendedor_nome": seller_name,
            "link_suporte": support_link,
            "instrucoes": (
                f"Informe ao cliente com carinho que o vendedor(a) {seller_name} já foi acionado(a) "
                "no WhatsApp e assumirá a conversa em instantes para ajudá-lo(a)."
            ),
        }, ensure_ascii=False)
