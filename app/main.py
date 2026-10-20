import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.admin.auth_service import bootstrap_admin_account
from app.admin.router import router as admin_router
from app.api.appointments import router as appointments_router
from app.api.availability import router as availability_router
from app.api.health import router as health_router
from app.api.notification_deliveries import router as notification_deliveries_router
from app.api.payments import router as payments_router
from app.api.professionals import router as professionals_router
from app.api.services import router as services_router
from app.api.triage import router as triage_router
from app.api.users import router as users_router
from app.api.webhooks import router as webhooks_router
from app.api.whatsapp_webhooks import router as whatsapp_webhooks_router
from app.config import Settings, settings
from app.database import SessionLocal
from app.mcp.router import router as mcp_router
from app.repositories.appointment_repo import AppointmentRepository
from app.security import require_api_key

logger = logging.getLogger(__name__)


async def _expiration_worker():
    """Background task to reliably expire pending reservations every 5 minutes."""
    while True:
        try:
            await asyncio.sleep(300)
            with SessionLocal() as db:
                repo = AppointmentRepository(db)
                expired = repo.expire_reservations(datetime.now(UTC))
                if expired > 0:
                    db.commit()
                    logger.info("Automatically expired %d pending reservation(s)", expired)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in reservation expiration worker")


def create_app(
    app_settings: Settings = settings,
    session_factory: Callable[[], Session] = SessionLocal,
) -> FastAPI:
    docs_url = "/docs" if app_settings.debug else None
    redoc_url = "/redoc" if app_settings.debug else None
    openapi_url = "/openapi.json" if app_settings.debug else None
    application = FastAPI(
        title=app_settings.app_name,
        debug=app_settings.debug,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )
    application.state.settings = app_settings
    application.mount(
        "/admin/static",
        StaticFiles(directory="app/admin/static"),
        name="admin-static",
    )
    application.add_middleware(
        SessionMiddleware,
        secret_key=app_settings.admin_session_secret,
        session_cookie="admin_session",
        max_age=8 * 60 * 60,
        https_only=not app_settings.debug,
        same_site="lax",
    )

    @application.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if not app_settings.debug:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @application.get("/", include_in_schema=False)
    async def root_redirect():
        return RedirectResponse("/admin", status_code=303)

    @application.get("/login", include_in_schema=False)
    async def login_redirect():
        return RedirectResponse("/admin/login", status_code=303)

    application.include_router(health_router)
    application.include_router(users_router)
    application.include_router(professionals_router)
    application.include_router(services_router)
    application.include_router(availability_router)
    application.include_router(appointments_router)
    application.include_router(payments_router)
    application.include_router(webhooks_router)
    application.include_router(notification_deliveries_router)
    application.include_router(triage_router)
    application.include_router(whatsapp_webhooks_router)
    application.include_router(mcp_router)
    application.include_router(admin_router)

    Instrumentator().instrument(application)

    @application.on_event("startup")
    async def startup_tasks() -> None:
        db = session_factory()
        try:
            bootstrap_admin_account(db, app_settings)
        finally:
            db.close()
        application.state.expiration_task = asyncio.create_task(_expiration_worker())

    @application.on_event("shutdown")
    async def shutdown_tasks() -> None:
        task = getattr(application.state, "expiration_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @application.get(
        "/metrics",
        include_in_schema=False,
        dependencies=[Depends(require_api_key)],
    )
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return application


app = create_app()
