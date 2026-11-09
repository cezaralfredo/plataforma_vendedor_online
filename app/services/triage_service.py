import logging
from typing import Any, Literal

import httpx

from app.config import Settings
from app.config import settings as default_settings
from app.schemas.triage import TriageActionType, TriageResult

logger = logging.getLogger(__name__)

OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
HUMAN_SUPPORT_URL = "https://wa.me/5585996277707"


class TriageService:
    """
    Serviço de Triagem de Nível 1 com TypeSafe Jev (OpenRouter Decisions API).
    Avalia a mensagem em paralelo através de perguntas atômicas estruturadas (Choice, Noul, Score).
    Implementa resiliência com fail-open e fallback automático para o Hermes LLM.
    """

    def __init__(self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None):
        self.settings = settings or default_settings
        self._client = http_client

    async def evaluate(self, message: str, phone: str | None = None) -> TriageResult:
        """
        Avalia o texto da mensagem e determina a intenção, probabilidade de transbordo humano,
        afirmação de pagamento, grau de frustração e a ação recomendada.
        """
        trimmed = (message or "").strip()
        if not trimmed:
            return self._build_fallback(trimmed, reason="empty_message")

        if not self.settings.triage_enabled or not self.settings.openrouter_api_key:
            return self._build_fallback(trimmed, reason="triage_disabled_or_no_key")

        payload = self._build_payload(trimmed)
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://plataforma-atende-agenda.local",
            "X-Title": "Plataforma Atende Agenda - Triage",
        }

        try:
            if self._client:
                response = await self._client.post(
                    OPENROUTER_DECISIONS_URL,
                    headers=headers,
                    json=payload,
                    timeout=self.settings.triage_timeout_seconds,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        OPENROUTER_DECISIONS_URL,
                        headers=headers,
                        json=payload,
                        timeout=self.settings.triage_timeout_seconds,
                    )

            if response.status_code != 200:
                logger.warning(
                    "Triage API returned status %d: %s. Using fallback.",
                    response.status_code,
                    response.text[:300],
                )
                return self._build_fallback(trimmed, reason=f"http_{response.status_code}")

            data = response.json()
            return self._parse_decisions(trimmed, data)

        except Exception as exc:
            logger.warning("Falha na chamada de triagem com Jev: %s. Aplicando fallback seguro.", exc)
            return self._build_fallback(trimmed, reason="exception_fallback")

    def _build_payload(self, message: str) -> dict[str, Any]:
        return {
            "model": self.settings.triage_model,
            "state": message,
            "questions": {
                "intent": {
                    "type": "choice",
                    "instructions": "Qual é a intenção principal do usuário nesta mensagem?",
                    "criteria": {
                        "falar_humano": "O usuário quer falar com um atendente humano, pessoa real, atendente ou suporte",
                        "confirmar_pix": "O usuário afirma que fez o pagamento, enviou o comprovante ou concluiu o PIX",
                        "novo_agendamento": "O usuário quer agendar, marcar um novo horário ou perguntar sobre agendamento de serviço",
                        "reagendamento": "O usuário quer mudar, trocar ou adiar o horário de um agendamento",
                        "cancelamento": "O usuário quer cancelar uma reserva ou horário marcado",
                        "consulta_agenda": "O usuário quer saber se tem horário marcado ou consultar seus agendamentos",
                        "saudacao": "O usuário apenas cumprimentou ou agradeceu (olá, bom dia, obrigado, etc.)",
                        "duvida": "O usuário tem dúvidas gerais sobre serviços, localização, endereço ou preços",
                    },
                },
                "is_human_handoff": {
                    "type": "noul",
                    "instructions": "O usuário está solicitando falar com um atendente humano, suporte ou pessoa real?",
                },
                "is_payment_claim": {
                    "type": "noul",
                    "instructions": "O usuário está afirmando que realizou a transferência, pagamento ou PIX?",
                },
                "frustration": {
                    "type": "score",
                    "instructions": "Avalie o nível de insatisfação, impaciência ou irritação do usuário",
                    "criteria": [
                        "Usuário calmo, educado e paciente",
                        "Usuário impaciente ou com pressa",
                        "Usuário muito irritado, reclamando ou furioso",
                    ],
                },
            },
        }

    def _parse_decisions(self, message: str, data: dict[str, Any]) -> TriageResult:
        answers = data.get("answers", {})

        intent_obj = answers.get("intent", {})
        intent = intent_obj.get("choice", "duvida")
        intent_conf = float(intent_obj.get("confidence", 1.0))

        human_obj = answers.get("is_human_handoff", {})
        is_human = float(human_obj.get("noul", 0.0))

        payment_obj = answers.get("is_payment_claim", {})
        is_payment = float(payment_obj.get("noul", 0.0))

        frust_obj = answers.get("frustration", {})
        frust_score = float(frust_obj.get("score", 0.0))

        frust_level: Literal["calmo", "impaciente", "irritado"]
        if frust_score < 0.6:
            frust_level = "calmo"
        elif frust_score < 1.3:
            frust_level = "impaciente"
        else:
            frust_level = "irritado"

        usage = data.get("usage", {})
        cost = usage.get("cost")
        model = data.get("model", self.settings.triage_model)

        # Regras de Decisão Determinística da Ação Recomendada:
        action: TriageActionType = "route_to_llm"
        fast_response: str | None = None

        if is_human >= 0.80 or intent == "falar_humano":
            action = "fast_handoff_human"
            fast_response = (
                f"Com certeza! Para falar diretamente com o nosso atendimento humano / administrador, "
                f"basta clicar no link: {HUMAN_SUPPORT_URL} . Qualquer dúvida, continuo por aqui à disposição!"
            )
        elif is_payment >= 0.75 or intent == "confirmar_pix":
            action = "fast_check_pix"
            fast_response = (
                "Obrigado por avisar! Estou conferindo a confirmação do seu pagamento no sistema agora mesmo. "
                "Um instante, por favor..."
            )
        elif intent == "saudacao" and intent_conf >= 0.85 and len(message.split()) <= 4:
            action = "fast_greeting"
            fast_response = "Olá! Seja muito bem-vindo à nossa plataforma de agendamento. Como posso ajudar você hoje?"
        else:
            action = "route_to_llm"
            fast_response = None

        return TriageResult(
            intent=intent,
            intent_confidence=round(intent_conf, 2),
            is_human_handoff=round(is_human, 2),
            is_payment_claim=round(is_payment, 2),
            frustration_score=round(frust_score, 2),
            frustration_level=frust_level,
            recommended_action=action,
            fast_response_text=fast_response,
            model_used=model,
            cost_usd=cost,
            is_fallback=False,
        )

    def _build_fallback(self, message: str, reason: str = "fallback") -> TriageResult:
        logger.debug("Triage fallback ativado (motivo: %s)", reason)
        return TriageResult(
            intent="duvida",
            intent_confidence=0.5,
            is_human_handoff=0.0,
            is_payment_claim=0.0,
            frustration_score=0.0,
            frustration_level="calmo",
            recommended_action="route_to_llm",
            fast_response_text=None,
            model_used="fallback-local",
            cost_usd=0.0,
            is_fallback=True,
        )
