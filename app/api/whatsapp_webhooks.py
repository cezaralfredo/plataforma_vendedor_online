import hmac
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.company_settings_service import CompanySettingsService
from app.services.seller_ai_service import SellerAIService
from app.services.whatsapp.gateway import WhatsAppGatewayService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/webhooks/whatsapp",
    tags=["whatsapp-webhooks"],
)


@router.get("/meta")
async def verify_meta_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    db: Session = Depends(get_db),
):
    """Verificação do Webhook da Meta Cloud API (GET challenge)."""
    settings = CompanySettingsService(db).get_or_create_default()
    expected_token = settings.meta_verify_token

    if hub_mode == "subscribe":
        if not expected_token or not hmac.compare_digest(hub_verify_token or "", expected_token):
            logger.warning("Falha na verificação da Meta: verify_token inválido.")
            raise HTTPException(status_code=403, detail="Verification token mismatch")
        logger.info("Webhook da Meta verificado com sucesso.")
        return Response(content=hub_challenge or "", media_type="text/plain")

    raise HTTPException(status_code=400, detail="Invalid hub.mode")


@router.post("/meta")
async def handle_meta_webhook(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Webhook para mensagens recebidas da Meta Cloud API Oficial."""
    gateway = WhatsAppGatewayService(db)
    inbound = gateway.parse_inbound("meta", payload)

    if inbound:
        seller_ai = SellerAIService(db)
        background_tasks.add_task(seller_ai.handle_inbound_message, inbound)

    return {"status": "ok"}


@router.post("/waha")
async def handle_waha_webhook(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Webhook para mensagens recebidas do WAHA (WhatsApp HTTP API)."""
    gateway = WhatsAppGatewayService(db)
    inbound = gateway.parse_inbound("waha", payload)

    if inbound:
        seller_ai = SellerAIService(db)
        background_tasks.add_task(seller_ai.handle_inbound_message, inbound)

    return {"status": "ok"}


@router.post("/evolution")
async def handle_evolution_webhook(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Webhook para mensagens recebidas da Evolution API."""
    gateway = WhatsAppGatewayService(db)
    inbound = gateway.parse_inbound("evolution", payload)

    if inbound:
        seller_ai = SellerAIService(db)
        background_tasks.add_task(seller_ai.handle_inbound_message, inbound)

    return {"status": "ok"}


@router.post("/inbound")
async def handle_generic_inbound_webhook(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Webhook genérico que utiliza o provedor ativo configurado na empresa."""
    gateway = WhatsAppGatewayService(db)
    active_provider = gateway.settings.whatsapp_provider or "evolution"
    inbound = gateway.parse_inbound(active_provider, payload)

    if inbound:
        seller_ai = SellerAIService(db)
        background_tasks.add_task(seller_ai.handle_inbound_message, inbound)

    return {"status": "ok", "provider": active_provider}
