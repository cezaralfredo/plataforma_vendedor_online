import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.admin_account import AdminAccount

# The production container exposes Uvicorn's error channel.  Use the same
# logger so rejection diagnostics are visible in the operational log stream.
logger = logging.getLogger("uvicorn.error")


def require_api_key(request: Request) -> None:
    supplied = request.headers.get("Authorization", "")
    app_settings = getattr(request.app.state, "settings", settings)
    expected = f"Bearer {app_settings.api_key}"
    if not hmac.compare_digest(supplied, expected):
        reason = "missing" if not supplied else "invalid"
        # This digest supports correlation of repeated invalid credentials
        # without writing the credential itself to logs.
        credential_id = "-" if not supplied else hashlib.sha256(supplied.encode()).hexdigest()[:12]
        logger.warning(
            "API authorization rejected path=%s reason=%s credential_id=%s client=%s user_agent=%s",
            request.url.path,
            reason,
            credential_id,
            request.client.host if request.client else "unknown",
            request.headers.get("user-agent", "unknown"),
        )
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


@dataclass(frozen=True)
class AdminContext:
    method: Literal["technical_key", "session"]
    account: AdminAccount | None = None
    csrf_token: str | None = None


def resolve_admin_context(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    x_admin_key: Annotated[str | None, Header()] = None,
) -> AdminContext | None:
    if x_admin_key is not None:
        expected = request.app.state.settings.admin_api_key
        if not hmac.compare_digest(x_admin_key.encode(), expected.encode()):
            raise HTTPException(status_code=403, detail="Admin access denied")
        return AdminContext(method="technical_key")

    session = request.session
    account_id = session.get("admin_account_id")
    auth_version = session.get("auth_version")
    csrf_token = session.get("csrf_token")
    if (
        type(account_id) is not int
        or account_id != 1
        or type(auth_version) is not int
        or not isinstance(csrf_token, str)
        or not csrf_token
    ):
        return None

    # Refresh even if another consumer has already loaded this identity.
    account = db.get(AdminAccount, account_id, populate_existing=True)
    if account is None or account.auth_version != auth_version:
        request.session.clear()
        return None
    return AdminContext(method="session", account=account, csrf_token=csrf_token)


def require_admin(
    request: Request,
    context: Annotated[AdminContext | None, Depends(resolve_admin_context)],
) -> AdminContext:
    if context is not None:
        request.state.admin_context = context
        return context

    response_class = getattr(request.scope.get("route"), "response_class", None)
    if (
        request.method == "GET"
        and isinstance(response_class, type)
        and issubclass(response_class, HTMLResponse)
    ):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    raise HTTPException(
        status_code=401,
        detail="Admin authentication required",
    )


def require_csrf_token(request: Request, context: AdminContext) -> None:
    if context.method == "technical_key":
        return
    supplied = request.headers.get("X-CSRF-Token", "")
    expected = context.csrf_token
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Admin access denied")


def require_admin_mutation(
    request: Request,
    context: Annotated[AdminContext, Depends(require_admin)],
) -> AdminContext:
    require_csrf_token(request, context)
    return context
