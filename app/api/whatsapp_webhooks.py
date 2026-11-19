import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.config import settings as default_settings
from app.database import SessionLocal, get_db
from app.services.company_settings_service import CompanySettingsService
from app.services.seller_ai_service import SellerAIService
from app.services.whatsapp.base import InboundMessage
from app.services.whatsapp.gateway import WhatsAppGatewayService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/webhooks/whatsapp",
    tags=["whatsapp-webhooks"],
)


def _get_app_settings(request: Request):
    return getattr(request.app.state, "settings", default_settings)


def _verify_meta_signature(request: Request, raw_body: bytes) -> None:
    app_settings = _get_app_settings(request)
    app_secret = getattr(app_settings, "meta_app_secret", "") or ""
    if not app_secret:
        if not app_settings.debug:
            logger.error("Meta webhook rejeitado: META_APP_SECRET não configurado em produção.")
            raise HTTPException(status_code=401, detail="Webhook signature not configured")
        return

    signature_header = request.headers.get("x-hub-signature-256", "")
    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Invalid signature")

    provided = signature_header[7:]
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid signature")


def _verify_webhook_secret(request: Request) -> None:
    app_settings = _get_app_settings(request)
    expected = getattr(app_settings, "whatsapp_webhook_secret", "") or ""
    if not expected:
        if not app_settings.debug:
            logger.error("WhatsApp webhook rejeitado: WHATSAPP_WEBHOOK_SECRET não configurado em produção.")
            raise HTTPException(status_code=401, detail="Webhook secret not configured")
        return

    provided = (
        request.headers.get("x-webhook-secret")
        or request.headers.get("x-api-key")
        or request.headers.get("apikey")
        or request.query_params.get("token")
        or ""
    )
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


async def _parse_json_body(raw_body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object")
    return parsed


async def process_inbound_in_own_session(
    inbound: InboundMessage, session_factory: Callable[[], Session] = SessionLocal
) -> None:
    """Processa a mensagem em background com sessão de banco própria.

    A sessão injetada na rota é fechada assim que a resposta HTTP é enviada,
    por isso a tarefa em background não pode reutilizá-la.
    """
    db = session_factory()
    try:
        await SellerAIService(db).handle_inbound_message(inbound)
    except Exception:
        db.rollback()
        logger.exception("Falha ao processar mensagem inbound do WhatsApp (%s).", inbound.provider)
    finally:
        db.close()


def _dispatch(
    request: Request,
    provider: str,
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session,
) -> None:
    inbound = WhatsAppGatewayService(db).parse_inbound(provider, payload)
    if inbound:
        factory = getattr(request.app.state, "session_factory", SessionLocal)
        background_tasks.add_task(process_inbound_in_own_session, inbound, factory)


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
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Webhook para mensagens recebidas da Meta Cloud API Oficial."""
    raw_body = await request.body()
    _verify_meta_signature(request, raw_body)
    payload = await _parse_json_body(raw_body)
    _dispatch(request, "meta", payload, background_tasks, db)
    return {"status": "ok"}


@router.post("/waha")
async def handle_waha_webhook(
    request: Request,
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Webhook para mensagens recebidas do WAHA (WhatsApp HTTP API)."""
    _verify_webhook_secret(request)
    _dispatch(request, "waha", payload, background_tasks, db)
    return {"status": "ok"}


@router.post("/evolution")
async def handle_evolution_webhook(
    request: Request,
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Webhook para mensagens recebidas da Evolution API."""
    _verify_webhook_secret(request)
    _dispatch(request, "evolution", payload, background_tasks, db)
    return {"status": "ok"}


@router.post("/inbound")
async def handle_generic_inbound_webhook(
    request: Request,
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Webhook genérico que utiliza o provedor ativo configurado na empresa."""
    _verify_webhook_secret(request)
    gateway = WhatsAppGatewayService(db)
    active_provider = gateway.settings.whatsapp_provider or "evolution"
    _dispatch(request, active_provider, payload, background_tasks, db)
    return {"status": "ok", "provider": active_provider}
