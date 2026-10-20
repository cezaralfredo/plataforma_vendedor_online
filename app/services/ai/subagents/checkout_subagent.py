import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.appointment import Appointment
from app.models.payment import Payment
from app.models.product import Product
from app.models.user import User
from app.services.ai.providers.base import ToolDefinition
from app.services.ai.subagents.base import BaseSubagent
from app.services.payment_service import PaymentService
from app.services.product_service import ProductService

logger = logging.getLogger(__name__)


class CheckoutSubagent(BaseSubagent):
    """
    Subagente especialista em Checkout, Cobranças PIX via Asaas e
    Verificação de Pagamentos para Serviços de Salão e Produtos de Beleza.
    """

    def __init__(
        self,
        db: Session,
        user: User | None = None,
        payment_service: PaymentService | None = None,
        product_service: ProductService | None = None,
    ):
        self.db = db
        self.user = user
        self.payment_service = payment_service or PaymentService(db)
        self.product_service = product_service or ProductService(db)

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="gerar_cobranca_pix_agendamento",
                description="Gera uma cobrança PIX no Asaas para confirmação de um agendamento de salão/estética.",
                parameters={
                    "type": "object",
                    "properties": {
                        "agendamento_id": {
                            "type": "integer",
                            "description": "ID numérico do agendamento.",
                        },
                    },
                    "required": ["agendamento_id"],
                },
            ),
            ToolDefinition(
                name="gerar_cobranca_pix_produto",
                description="Gera uma cobrança PIX no Asaas para a compra de um produto de salão/estética.",
                parameters={
                    "type": "object",
                    "properties": {
                        "produto_id": {
                            "type": "integer",
                            "description": "ID numérico do produto de salão a ser comprado.",
                        },
                        "quantidade": {
                            "type": "integer",
                            "description": "Quantidade de unidades desejadas (padrão: 1).",
                        },
                    },
                    "required": ["produto_id"],
                },
            ),
            ToolDefinition(
                name="verificar_status_pagamento",
                description="Verifica se o pagamento de um agendamento ou produto foi confirmado no Asaas.",
                parameters={
                    "type": "object",
                    "properties": {
                        "agendamento_id": {
                            "type": "integer",
                            "description": "ID do agendamento para verificar pagamento (opcional).",
                        },
                        "pagamento_id": {
                            "type": "integer",
                            "description": "ID do pagamento para verificação direta (opcional).",
                        },
                    },
                },
            ),
        ]

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        try:
            if tool_name == "gerar_cobranca_pix_agendamento":
                apt_id = int(arguments["agendamento_id"])
                apt = self.db.get(Appointment, apt_id)
                if not apt:
                    return json.dumps({"erro": f"Agendamento #{apt_id} não encontrado."}, ensure_ascii=False)

                payment = await self.payment_service.create_charge(
                    appointment_id=apt_id,
                    billing_type="PIX",
                )

                pix_code = None
                if payment.invoice_url and payment.invoice_url.startswith("000201"):
                    pix_code = payment.invoice_url
                elif payment.asaas_payment_id and not str(payment.asaas_payment_id).startswith("direct_pix:"):
                    try:
                        pix_data = await self.payment_service.asaas.get_pix_qr_code(payment.asaas_payment_id)
                        pix_code = pix_data.get("payload") if pix_data else None
                    except Exception as e:
                        logger.warning("Falha ao obter QR Code PIX: %s", e)

                pix_code = pix_code or (payment.invoice_url if (payment.invoice_url and payment.invoice_url.startswith("000201")) else None)

                return json.dumps({
                    "status": "sucesso",
                    "agendamento_id": apt_id,
                    "pagamento_id": payment.id,
                    "valor_reais": f"R$ {payment.amount_cents / 100:.2f}",
                    "link_pagamento": payment.invoice_url,
                    "pix_copia_e_cola": pix_code,
                    "instrucoes": "Copie o código PIX acima ou use o link de pagamento para concluir.",
                }, ensure_ascii=False)

            elif tool_name == "gerar_cobranca_pix_produto":
                if not self.user:
                    return json.dumps({"erro": "Cliente não identificado para gerar cobrança."}, ensure_ascii=False)

                prod_id = int(arguments["produto_id"])
                qtd = int(arguments.get("quantidade") or 1)
                product = self.db.get(Product, prod_id)
                if not product or not product.active:
                    return json.dumps({"erro": f"Produto #{prod_id} não encontrado ou indisponível."}, ensure_ascii=False)

                total_cents = product.price_cents * qtd

                payment = await self.payment_service.create_direct_charge(
                    user_id=self.user.id,
                    amount_cents=total_cents,
                    product_id=product.id,
                    description=f"Compra: {qtd}x {product.name}",
                    billing_type="pix",
                )

                pix_code = None
                if payment.invoice_url and payment.invoice_url.startswith("000201"):
                    pix_code = payment.invoice_url
                elif payment.asaas_payment_id and not str(payment.asaas_payment_id).startswith("direct_pix:"):
                    try:
                        pix_data = await self.payment_service.asaas.get_pix_qr_code(payment.asaas_payment_id)
                        pix_code = pix_data.get("payload") if pix_data else None
                    except Exception as e:
                        logger.warning("Falha ao obter QR Code PIX para produto: %s", e)

                pix_code = pix_code or (payment.invoice_url if (payment.invoice_url and payment.invoice_url.startswith("000201")) else None)

                return json.dumps({
                    "status": "sucesso",
                    "produto": product.name,
                    "quantidade": qtd,
                    "pagamento_id": payment.id,
                    "valor_total_reais": f"R$ {total_cents / 100:.2f}",
                    "link_pagamento": payment.invoice_url,
                    "pix_copia_e_cola": pix_code,
                    "instrucoes": "Pagamento PIX gerado. O produto será separado assim que confirmado!",
                }, ensure_ascii=False)

            elif tool_name == "verificar_status_pagamento":
                apt_id = arguments.get("agendamento_id")
                pay_id = arguments.get("pagamento_id")

                payment = None
                if pay_id:
                    payment = self.db.get(Payment, int(pay_id))
                elif apt_id:
                    payment = (
                        self.db.query(Payment)
                        .filter(Payment.appointment_id == int(apt_id))
                        .order_by(Payment.id.desc())
                        .first()
                    )
                elif self.user:
                    payment = (
                        self.db.query(Payment)
                        .filter(Payment.user_id == self.user.id)
                        .order_by(Payment.id.desc())
                        .first()
                    )

                if not payment:
                    return json.dumps({"erro": "Nenhum pagamento localizado para verificação."}, ensure_ascii=False)

                if payment.status == "pending" and payment.asaas_payment_id:
                    try:
                        await self.payment_service.check_payment_status(payment)
                    except Exception as e:
                        logger.warning("Erro ao verificar status no Asaas: %s", e)

                confirmado = payment.status in ("received", "confirmed")

                return json.dumps({
                    "status": "sucesso",
                    "pagamento_id": payment.id,
                    "confirmado": confirmado,
                    "status_pagamento": payment.status,
                    "valor_reais": f"R$ {payment.amount_cents / 100:.2f}",
                    "link_pagamento": payment.invoice_url if not confirmado else None,
                }, ensure_ascii=False)

            return json.dumps({"erro": f"Ferramenta desconhecida: {tool_name}"}, ensure_ascii=False)

        except Exception as exc:
            logger.exception("Erro no CheckoutSubagent: %s", exc)
            return json.dumps({"erro": f"Erro interno ao processar cobrança: {exc!s}"}, ensure_ascii=False)
