import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.chat_message import ChatMessage
from app.models.company_settings import CompanySettings
from app.models.payment import Payment
from app.models.user import User
from app.schemas.user import UserCreate
from app.services.ai.orchestrator import SalonAIOrchestrator
from app.services.ai.subagents.support_subagent import SupportSubagent
from app.services.company_settings_service import CompanySettingsService
from app.services.payment_service import PaymentService
from app.services.product_service import ProductService
from app.services.triage_service import TriageService
from app.services.user_service import UserService
from app.services.whatsapp.base import InboundMessage
from app.services.whatsapp.gateway import WhatsAppGatewayService

logger = logging.getLogger(__name__)


class SellerAIService:
    """
    Motor Inteligente de Atendimento e Vendas para Salão de Beleza e Estética via WhatsApp.
    Recebe mensagens inbound dos canais (WAHA, Evolution ou Meta Cloud),
    identifica/cadastra o cliente, delega a conversação e tomada de decisão
    para o SalonAIOrchestrator (com os subagentes de serviços, agendamentos, catálogo
    de produtos e checkout PIX Asaas) e despacha a resposta de volta ao cliente.
    """

    def __init__(self, db: Session, triage_service: TriageService | None = None):
        self.db = db
        self.company_settings: CompanySettings = CompanySettingsService(db).get_or_create_default()
        self.gateway = WhatsAppGatewayService(db)
        self.user_service = UserService(db)
        self.payment_service = PaymentService(db)
        self.product_service = ProductService(db)
        self.triage_service = triage_service or TriageService()

    def _get_or_create_user(self, phone: str, name: str | None = None) -> User:
        user = self.user_service.find_by_phone(phone)
        if not user:
            clean_name = (name or "Cliente").strip() or "Cliente"
            try:
                user = self.user_service.create(
                    UserCreate(
                        name=clean_name,
                        phone=phone,
                        whatsapp_number=phone,
                    )
                )
            except Exception as exc:
                logger.warning("Erro ao cadastrar novo cliente via WhatsApp: %s", exc)
                user = self.user_service.find_by_phone(phone)
        return user

    def _record_chat(self, phone: str, role: str, content: str, provider: str | None = None) -> None:
        msg = ChatMessage(
            phone=phone,
            role=role,
            content=content,
            provider=provider,
            created_at=datetime.now(UTC),
        )
        self.db.add(msg)
        self.db.commit()

    def _get_recent_history(self, phone: str, limit: int = 8) -> list[ChatMessage]:
        return (
            self.db.query(ChatMessage)
            .filter(ChatMessage.phone == phone)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
            .all()
        )[::-1]

    def is_bot_message(self, message_id: str, phone: str, text: str) -> bool:
        """Verifica se a mensagem com fromMe: true foi disparada pela própria API ou por humano no WhatsApp Business."""
        from app.services.whatsapp.tracker import SentMessageTracker
        if SentMessageTracker.get_instance().is_bot_message(message_id):
            return True

        recent = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.phone == phone, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc())
            .first()
        )
        if recent and recent.content and text:
            if recent.content.strip() == text.strip():
                return True
        return False

    async def handle_inbound_message(self, inbound: InboundMessage) -> str:
        """Ponto de entrada único para qualquer mensagem recebida no WhatsApp."""
        phone = inbound.sender_phone
        text = inbound.text.strip()
        user = self._get_or_create_user(phone, inbound.sender_name)

        # Se for mensagem enviada do próprio número (fromMe: true / WhatsApp Business App)
        if inbound.is_from_me:
            # 1. Se for eco da mensagem que o próprio bot enviou via API, ignora
            if self.is_bot_message(inbound.message_id, phone, text):
                logger.debug("Mensagem %s ignorada: eco do envio do próprio bot para %s.", inbound.message_id, phone)
                return ""

            # 2. Intervenção de atendente humano pelo app WhatsApp Business (Modo Coexistência)
            self.company_settings = CompanySettingsService(self.db).get_or_create_default()
            if not self.company_settings.coexistence_enabled:
                logger.info("Coexistence desabilitada. Mensagem do atendente humano para %s ignorada.", phone)
                return ""

            lowered = text.lower()
            custom_fields = dict(user.custom_fields or {})

            # Comando do atendente para reativar IA: #ia, #bot, #ativar, #robo, #auto
            if any(cmd in lowered for cmd in ["#ia", "#bot", "#ativar", "#robo", "#auto"]):
                custom_fields.pop("ai_paused_until", None)
                user.custom_fields = custom_fields
                self.db.commit()
                self._record_chat(phone, "human_agent", text, inbound.provider)
                logger.info("Coexistence: Atendente reativou o bot via comando (%s) para o cliente %s.", text, phone)
                return ""

            # Comando do atendente para pausar por tempo longo: #pausa, #pause, #humano, #manual
            if any(cmd in lowered for cmd in ["#pausa", "#pause", "#humano", "#manual"]):
                paused_until = (datetime.now(UTC) + timedelta(hours=4)).isoformat()
                custom_fields["ai_paused_until"] = paused_until
                user.custom_fields = custom_fields
                self.db.commit()
                self._record_chat(phone, "human_agent", text, inbound.provider)
                logger.info("Coexistence: Atendente pausou a IA por 4 horas via comando (%s) para o cliente %s.", text, phone)
                return ""

            # Mensagem normal do atendente humano ao cliente
            pause_minutes = int(self.company_settings.coexistence_pause_minutes or 45)
            paused_until = (datetime.now(UTC) + timedelta(minutes=pause_minutes)).isoformat()
            custom_fields["ai_paused_until"] = paused_until
            user.custom_fields = custom_fields
            self.db.commit()

            # Registra no histórico como human_agent para que a IA tenha contexto quando reassumir
            self._record_chat(phone, "human_agent", text, inbound.provider)
            logger.info(
                "Coexistence: Atendente humano atendeu %s pelo WhatsApp Business. IA silenciada por %d minutos (até %s).",
                phone,
                pause_minutes,
                paused_until,
            )
            return ""

        # Se for mensagem de áudio, transcreve com Whisper
        if getattr(inbound, "media_type", None) == "audio" and inbound.media_url:
            try:
                from app.services.whatsapp.transcription_service import AudioTranscriptionService
                transcription = await AudioTranscriptionService(self.db).download_and_transcribe(inbound.media_url)
                if transcription:
                    text = transcription
                    logger.info("Áudio do WhatsApp (%s) transcrito com sucesso: '%s'", phone, text)
            except Exception as e:
                logger.warning("Falha ao tentar transcrever áudio recebido: %s", e)

        # Registra a mensagem do cliente no histórico
        self._record_chat(phone, "user", text, inbound.provider)

        # Verifica se o autoatendimento da IA está pausado para este cliente (transbordo humano ativo)
        custom_fields = dict(user.custom_fields or {})
        ai_paused_until_str = custom_fields.get("ai_paused_until")
        if ai_paused_until_str:
            lowered = text.lower()
            if any(w in lowered for w in ["voltar para o bot", "retomar ia", "falar com robô", "reiniciar ia", "menu"]):
                custom_fields.pop("ai_paused_until", None)
                user.custom_fields = custom_fields
                self.db.commit()
                logger.info("Atendimento por IA retomado para o cliente %s pelo comando do usuário.", phone)
            else:
                try:
                    paused_until = datetime.fromisoformat(ai_paused_until_str)
                    if paused_until.tzinfo is None:
                        paused_until = paused_until.replace(tzinfo=UTC)
                    if datetime.now(UTC) < paused_until:
                        logger.info("IA pausada para o telefone %s até %s (atendente humano em atendimento).", phone, paused_until)
                        return ""
                except Exception:
                    logger.debug("Falha ao analisar data de pausa de IA para %s", phone)

        # Triagem Rápida de Nível 1 com Jev / Regras
        triage_result = None
        try:
            triage_result = await self.triage_service.evaluate(text, phone=phone)
        except Exception as exc:
            logger.warning("Erro na avaliação de triagem com Jev: %s. Prosseguindo para o orquestrador.", exc)

        if triage_result and not triage_result.is_fallback:
            if triage_result.recommended_action == "fast_handoff_human":
                support_subagent = SupportSubagent(self.company_settings, db=self.db, user=user, gateway=self.gateway)
                res_tool = await support_subagent.execute_tool(
                    "acionar_atendente_humano",
                    {"motivo": f"Solicitado pelo cliente via Triagem (intenção: {triage_result.intent})"},
                )
                import json
                support_data = json.loads(res_tool)
                response_text = (
                    f"Com certeza, {user.name or 'Cliente'}! ✨ Já notifiquei nossa equipe de atendimento.\n\n"
                    f"Se preferir falar diretamente agora, basta clicar no link:\n"
                    f"👉 {support_data.get('link_suporte')}\n\n"
                    "Em instantes você será atendida(o)! 💖"
                )
                self._record_chat(phone, "assistant", response_text, inbound.provider)
                try:
                    await self.gateway.send_text(phone, response_text, provider_name=inbound.provider)
                except Exception as exc:
                    logger.error("Erro ao despachar fast_handoff_human: %s", exc)
                return response_text

            if triage_result.recommended_action == "fast_check_pix":
                recent_payment = (
                    self.db.query(Payment)
                    .filter(Payment.user_id == user.id)
                    .order_by(Payment.created_at.desc())
                    .first()
                )
                if recent_payment:
                    valor_str = f"R$ {recent_payment.amount_cents / 100:.2f}"
                    if recent_payment.status in ("received", "confirmed"):
                        response_text = (
                            f"🎉 Excelente notícia, {user.name or 'Cliente'}! Localizamos o seu pagamento de *{valor_str}* confirmado com sucesso!\n\n"
                            "Seu agendamento/pedido está 100% confirmado. Muito obrigado pela confiança! ✨💖"
                        )
                    else:
                        link_pix = f"\n👉 Link do pagamento: {recent_payment.invoice_url}" if recent_payment.invoice_url else ""
                        response_text = (
                            f"Olá, {user.name or 'Cliente'}! Estou acompanhando seu pagamento de *{valor_str}* no sistema.\n\n"
                            f"⏳ Assim que o banco confirmar a liquidação do PIX, seu agendamento/pedido será atualizado automaticamente aqui!{link_pix}"
                        )
                    self._record_chat(phone, "assistant", response_text, inbound.provider)
                    try:
                        await self.gateway.send_text(phone, response_text, provider_name=inbound.provider)
                    except Exception as exc:
                        logger.error("Erro ao despachar fast_check_pix: %s", exc)
                    return response_text

            if triage_result.recommended_action == "fast_greeting":
                history = self._get_recent_history(phone)
                if len(history) <= 1:
                    salon_name = self.company_settings.company_name or "Nosso Salão & Estética"
                    welcome = self.company_settings.welcome_message
                    if welcome and welcome.strip():
                        response_text = welcome.strip()
                    else:
                        response_text = (
                            f"Olá, {user.name or 'seja muito bem-vindo(a)'}! ✨\n\n"
                            f"Sou a consultora virtual do *{salon_name}*. Como posso te ajudar hoje?\n"
                            "• Agendamento de Serviços (Cabelo, Unhas, Estética)\n"
                            "• Linha de Produtos Profissionais\n"
                            "• Consultar Horários e Valores"
                        )
                    self._record_chat(phone, "assistant", response_text, inbound.provider)
                    try:
                        await self.gateway.send_text(phone, response_text, provider_name=inbound.provider)
                    except Exception as exc:
                        logger.error("Erro ao despachar fast_greeting: %s", exc)
                    return response_text

        history = self._get_recent_history(phone)
        self.company_settings = CompanySettingsService(self.db).get_or_create_default()

        # Instancia o SalonAIOrchestrator
        orchestrator = SalonAIOrchestrator(
            db=self.db,
            user=user,
            company_settings=self.company_settings,
            gateway=self.gateway,
        )

        response_text = await orchestrator.process_message(text, history)

        # Registra a resposta do assistente no histórico
        self._record_chat(phone, "assistant", response_text, inbound.provider)

        # Envia a mensagem de volta pelo canal WhatsApp do remetente
        try:
            await self.gateway.send_text(phone, response_text, provider_name=inbound.provider)
        except Exception as exc:
            logger.error("Erro ao despachar resposta via WhatsApp Gateway (%s): %s", inbound.provider, exc)

        return response_text
