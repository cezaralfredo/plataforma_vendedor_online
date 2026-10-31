import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.admin.auth_service import upsert_admin_account


def test_root_and_login_redirects(anonymous_client: TestClient):
    """Verifica se a rota raiz '/' e '/login' redirecionam corretamente para o painel seguro."""
    res_root = anonymous_client.get("/", follow_redirects=False)
    assert res_root.status_code == 303
    assert res_root.headers["location"] == "/admin"

    res_login = anonymous_client.get("/login", follow_redirects=False)
    assert res_login.status_code == 303
    assert res_login.headers["location"] == "/admin/login"


def test_security_headers_present(anonymous_client: TestClient):
    """Garante que cabeçalhos de segurança essenciais (HSTS, Anti-Clickjacking, MIME Sniffing) estão ativos."""
    response = anonymous_client.get("/admin/login")
    assert response.status_code == 200
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-XSS-Protection") == "1; mode=block"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_upsert_admin_account_creates_and_updates(db_session: Session):
    """Valida se criar ou atualizar o login administrativo preserva o singleton e zera bloqueios."""
    # Criação do primeiro acesso com usuário Anauê
    account = upsert_admin_account(
        db_session, username="admin_anaue", password="SenhaSegura@2026!Store"  # noqa: S106
    )
    assert account.id == 1
    assert account.username == "admin_anaue"
    assert account.failed_login_count == 0
    assert account.locked_until is None

    # Simula se já existir uma conta e for solicitada nova credencial de acesso
    v1 = account.auth_version
    updated_account = upsert_admin_account(
        db_session, username="anaue_master", password="NovaSenhaSegura#2026!Vendedor"  # noqa: S106
    )
    assert updated_account.id == 1
    assert updated_account.username == "anaue_master"
    assert updated_account.auth_version == v1 + 1  # Invalida tokens e sessões prévias


def test_upsert_admin_account_validates_strength(db_session: Session):
    """Impede senhas fracas ou nomes de usuário vazios."""
    with pytest.raises(ValueError, match="pelo menos 12 caracteres"):
        upsert_admin_account(db_session, username="admin", password="curta")  # noqa: S106

    with pytest.raises(ValueError, match="não pode estar em branco"):
        upsert_admin_account(db_session, username="  ", password="SenhaValida12345!")  # noqa: S106


def test_cli_create_or_reset_script(tmp_path):
    """Garante que o script scripts/create_or_reset_admin.py pode ser executado para definir novas credenciais."""
    db_file = tmp_path / "test_admin_cli.db"
    cmd = [
        sys.executable,
        "scripts/create_or_reset_admin.py",
        "--username",
        "admin_loja_anaue",
        "--password",
        "AnaueDesign#2026!Master",
        "--domain",
        "ria.anauedesign.com.br",
        "--database-url",
        f"sqlite:///{db_file.as_posix()}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)  # noqa: S603
    assert "ria.anauedesign.com.br" in proc.stdout
    assert "admin_loja_anaue" in proc.stdout
    assert "Credenciais de Acesso Administrativo Configuradas" in proc.stdout

