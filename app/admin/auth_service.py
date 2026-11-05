import base64
import binascii
import hashlib
import hmac
import os
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.admin_account import AdminAccount

HASH_VERSION = "scrypt-v1"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
SALT_BYTES = 16
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_DURATION = timedelta(minutes=15)


def hash_password(password: str) -> str:
    salt = os.urandom(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    salt_encoded = base64.b64encode(salt).decode("ascii")
    derived_encoded = base64.b64encode(derived).decode("ascii")
    return (
        f"{HASH_VERSION}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${SCRYPT_DKLEN}"
        f"${salt_encoded}${derived_encoded}"
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        (
            version,
            n_value,
            r_value,
            p_value,
            dklen_value,
            salt_encoded,
            expected_encoded,
        ) = encoded_hash.split("$")
        if version != HASH_VERSION:
            return False
        n = int(n_value)
        r = int(r_value)
        p = int(p_value)
        dklen = int(dklen_value)
        salt = base64.b64decode(salt_encoded.encode("ascii"), validate=True)
        expected = base64.b64decode(expected_encoded.encode("ascii"), validate=True)
        if not salt or len(expected) != dklen:
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen
        )
    except (ValueError, TypeError, binascii.Error):
        return False
    return hmac.compare_digest(derived, expected)


def bootstrap_admin_account(db: Session, settings: Settings) -> AdminAccount:
    account = db.scalar(select(AdminAccount).where(AdminAccount.id == 1))
    if account is not None:
        return account
    if not settings.admin_bootstrap_password:
        raise RuntimeError(
            "Admin account has not been initialized; configure the bootstrap password."
        )
    account = AdminAccount(
        id=1,
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_bootstrap_password),
    )
    try:
        with db.begin_nested():
            db.add(account)
            db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        account = db.scalar(select(AdminAccount).where(AdminAccount.id == 1))
        if account is not None:
            return account
        raise
    db.refresh(account)
    return account


def record_failed_login(account: AdminAccount, now: datetime) -> None:
    account.failed_login_count += 1
    if account.failed_login_count >= LOGIN_FAILURE_LIMIT:
        account.locked_until = now + LOGIN_LOCK_DURATION


def authenticate_admin(
    db: Session, username: str, password: str, now: datetime
) -> AdminAccount | None:
    account = db.scalar(
        select(AdminAccount)
        .where(AdminAccount.username == username)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        return None
    if account.locked_until is not None:
        locked_until = account.locked_until
        if locked_until.tzinfo is None and now.tzinfo is not None:
            locked_until = locked_until.replace(tzinfo=now.tzinfo)
        elif locked_until.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=locked_until.tzinfo)
        if locked_until > now:
            return None
    if not verify_password(password, account.password_hash):
        record_failed_login(account, now)
        db.commit()
        return None
    account.failed_login_count = 0
    account.locked_until = None  # type: ignore[assignment]
    db.commit()
    db.refresh(account)
    return account


def change_admin_password(db: Session, account: AdminAccount, new_password: str) -> None:
    db.refresh(account, with_for_update=True)
    account.password_hash = hash_password(new_password)
    account.auth_version += 1
    account.failed_login_count = 0
    account.locked_until = None  # type: ignore[assignment]
    db.commit()
    db.refresh(account)


def upsert_admin_account(db: Session, username: str, password: str) -> AdminAccount:
    """Cria ou atualiza as credenciais de acesso do administrador.

    Se o login já existir, atualiza o nome de usuário, calcula novo hash seguro com scrypt,
    incrementa auth_version (revogando instantaneamente qualquer sessão aberta anterior)
    e zera os contadores de falhas/bloqueios.
    Se não existir, cria a conta primária id=1.
    """
    if len(password) < 12:
        raise ValueError("A senha deve possuir pelo menos 12 caracteres.")
    if not username.strip():
        raise ValueError("O nome de usuário não pode estar em branco.")

    account = db.scalar(
        select(AdminAccount).where(AdminAccount.id == 1).with_for_update()
    )
    if account is not None:
        account.username = username.strip()
        account.password_hash = hash_password(password)
        account.auth_version += 1
        account.failed_login_count = 0
        account.locked_until = None  # type: ignore[assignment]
    else:
        account = AdminAccount(
            id=1,
            username=username.strip(),
            password_hash=hash_password(password),
        )
        db.add(account)
    db.commit()
    db.refresh(account)
    return account

