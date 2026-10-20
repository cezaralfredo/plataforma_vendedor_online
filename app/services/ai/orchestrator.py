import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.chat_message import ChatMessage
from app.models.company_settings import CompanySettings
from app.models.user import User
from app.services.ai.prompt_engine import SalonPromptEngine
from app.services.ai.providers.base import (
    BaseLLMClient,
    LLMMessage,
    ToolCall,
    ToolDefinition,
)
from app.services.ai.providers.factory import get_llm_client
from app.services.ai.subagents.base import BaseSubagent
from app.services.ai.subagents.catalog_subagent import CatalogSubagent
from app.services.ai.subagents.checkout_subagent import CheckoutSubagent
from app.services.ai.subagents.order_status_subagent import OrderStatusSubagent
from app.services.ai.subagents.service_subagent import ServiceSubagent
from app.services.ai.subagents.support_subagent import SupportSubagent

logger = logging.getLogger(__name__)


class SalonAIOrchestrator:
    """
    Orquestrador central de inteligência artificial para Salão de Beleza e Estética.
    Coordena a conversa com o cliente, aciona o provedor de LLM configurado,
    executa as ferramentas dos subagentes e fornece fallback determinístico.
    """

    def __init__(
        self,
        db: Session,
        user: User,
        company_settings: CompanySettings,
        llm_client: BaseLLMClient | None = None,
        gateway: Any = None,
    ):
        self.db = db
        self.user = user
        self.company_settings = company_settings
        self.llm_client = llm_client if llm_client is not None else get_llm_client(company_settings)
        self.gateway = gateway

        # Subagentes especialistas
        self.service_subagent = ServiceSubagent(db, user=user)
        self.catalog_subagent = CatalogSubagent(db, user=user, gateway=gateway)
        self.checkout_subagent = CheckoutSubagent(db, user=user)
        self.order_status_subagent = OrderStatusSubagent(db, user=user)
        self.support_subagent = SupportSubagent(company_settings, db=db, user=user, gateway=gateway)

        self.subagents: list[BaseSubagent] = [
            self.service_subagent,
            self.catalog_subagent,
            self.checkout_subagent,
            self.order_status_subagent,
            self.support_subagent,
        ]

        self._tool_map: dict[str, BaseSubagent] = {}
        self._all_tools: list[ToolDefinition] = []
        self._register_subagents()

    def _register_subagents(self) -> None:
        for sa in self.subagents:
            for tool_def in sa.get_tools():
                self._tool_map[tool_def.name] = sa
                self._all_tools.append(tool_def)

    def _build_system_prompt(self) -> str:
        return SalonPromptEngine.build_system_prompt(
            company_settings=self.company_settings,
            user=self.user,
        )

    async def _execute_tool_call(self, tool_call: ToolCall) -> str:
        subagent = self._tool_map.get(tool_call.name)
        if not subagent:
            logger.warning("Ferramenta '%s' não encontrada em nenhum subagente.", tool_call.name)
            return json.dumps({"erro": f"Ferramenta '{tool_call.name}' não reconhecida."}, ensure_ascii=False)

        try:
            return await subagent.execute_tool(tool_call.name, tool_call.arguments)
        except Exception as exc:
            logger.exception("Falha na execução da ferramenta %s: %s", tool_call.name, exc)
            return json.dumps({"erro": str(exc)}, ensure_ascii=False)

    async def process_message(self, user_text: str, history: list[ChatMessage]) -> str:
        """
        Processa a mensagem do cliente executando o ciclo ReAct com o modelo de LLM.
        """
        # Se o provedor de LLM não estiver disponível ou sem API key
        if not self.llm_client or not getattr(self.company_settings, "ai_api_key", None):
            return self._build_deterministic_fallback(user_text)

        system_prompt = self._build_system_prompt()
        messages: list[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]

        for msg in history:
            role = "user" if msg.role == "user" else "assistant"
            messages.append(LLMMessage(role=role, content=msg.content or ""))

        # Adiciona a mensagem atual se não for duplicada
        if not history or history[-1].content != user_text:
            messages.append(LLMMessage(role="user", content=user_text))

        max_iterations = 5
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            try:
                response = await self.llm_client.chat(
                    messages=messages,
                    tools=self._all_tools,
                    temperature=float(self.company_settings.ai_temperature or 0.3),
                )
            except Exception as exc:
                logger.error("Erro na chamada ao modelo de IA (%s): %s", type(self.llm_client).__name__, exc)
                return self._build_deterministic_fallback(user_text)

            # Se não houver chamadas de ferramentas, retornamos o texto final
            if not response.tool_calls:
                return response.content or "Olá! Como posso te ajudar hoje com nossos serviços e produtos?"

            # Registra o turno do assistente contendo as chamadas de ferramentas
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
            )

            # Executa cada ferramenta chamada pelo LLM
            for tool_call in response.tool_calls:
                result_str = await self._execute_tool_call(tool_call)
                messages.append(
                    LLMMessage(
                        role="tool",
                        content=result_str,
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                    )
                )

        return "Aqui estão as informações solicitadas! Se precisar de mais algum horário ou produto, é só me avisar. ✨"

    def _build_deterministic_fallback(self, user_text: str) -> str:
        salon_name = self.company_settings.company_name or "Nosso Salão & Estética"
        lowered = user_text.lower()

        if any(w in lowered for w in ["horario", "horário", "agendar", "marcar", "vaga", "dia"]):
            return (
                f"Olá! ✨ No *{salon_name}*, oferecemos serviços completos de Cabelo, Manicure, Pedicure e Estética!\n\n"
                "Para consultar nossos horários livres e agendar, por favor nos informe qual serviço você deseja "
                "ou me diga um dia de sua preferência! 💇‍♀️💅"
            )

        if any(w in lowered for w in ["produto", "shampoo", "creme", "mascara", "máscara", "oleo", "óleo"]):
            return (
                f"Temos uma linha completa de produtos profissionais e homecare no *{salon_name}*! ✨\n\n"
                "Você pode consultar nossos itens ou nos dizer o que você está procurando para seus cuidados diários!"
            )

        return (
            f"Olá! Seja muito bem-vindo(a) ao *{salon_name}*! ✨\n\n"
            "Sou sua consultora virtual. Como posso te ajudar hoje?\n"
            "• Agendamento de Serviços (Cabelo, Unhas, Estética)\n"
            "• Linha de Produtos Profissionais\n"
            "• Informações sobre Horários e Preços"
        )


SalesOrchestrator = SalonAIOrchestrator
