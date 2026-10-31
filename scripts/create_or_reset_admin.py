#!/usr/bin/env python3
"""Script para criar ou redefinir credenciais administrativas com alta segurança.

Pode ser executado diretamente:
    python scripts/create_or_reset_admin.py --username admin_anaue --password "SuaSenhaSegura123!"
ou gerar senha automática:
    python scripts/create_or_reset_admin.py --username admin_anaue --generate
"""

import argparse
import os
import secrets
import string
import sys

# Adiciona a raiz do projeto ao sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal
from app.admin.auth_service import upsert_admin_account


def generate_secure_password(length: int = 20) -> str:
    """Gera uma senha forte com alta entropia contendo letras, dígitos e símbolos."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    # Garante pelo menos 1 maiúscula, 1 minúscula, 1 dígito e 1 símbolo
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        has_upper = any(c.isupper() for c in pwd)
        has_lower = any(c.islower() for c in pwd)
        has_digit = any(c.isdigit() for c in pwd)
        has_punct = any(c in "!@#$%^&*" for c in pwd)
        if has_upper and has_lower and has_digit and has_punct:
            return pwd


def main():
    parser = argparse.ArgumentParser(
        description="Cria ou redefine o login e senha de acesso administrativo para loja.anauedesign.com.br"
    )
    parser.add_argument(
        "--username",
        type=str,
        default=os.getenv("ADMIN_USERNAME", "admin_anaue"),
        help="Nome de usuário administrativo (padrão: admin_anaue)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Senha de acesso (mínimo 12 caracteres). Se omitido e --generate usado, gera automaticamente.",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Gera uma senha aleatória criptograficamente segura com 20 caracteres.",
    )
    parser.add_argument(
        "--domain",
        type=str,
        default=os.getenv("DOMAIN", "ria.anauedesign.com.br"),
        help="Domínio da plataforma (padrão: ria.anauedesign.com.br)",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=os.getenv("DATABASE_URL"),
        help="URL do banco de dados (padrão: lê de DATABASE_URL ou configuração local)",
    )

    args = parser.parse_args()

    username = args.username.strip()
    if not username:
        print("Erro: O nome de usuário não pode estar em branco.", file=sys.stderr)
        sys.exit(1)

    password = args.password
    if not password:
        if args.generate:
            password = generate_secure_password()
        else:
            # Se não forneceu senha e não passou --generate, gera automaticamente por padrão
            print("[INFO] Nenhuma senha informada; gerando senha segura automaticamente...")
            password = generate_secure_password()

    if len(password) < 12:
        print("Erro: A senha precisa ter pelo menos 12 caracteres.", file=sys.stderr)
        sys.exit(1)

    domain = args.domain.strip()

    if args.database_url:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine_kwargs = {}
        if args.database_url.startswith("sqlite"):
            from sqlalchemy.pool import StaticPool
            engine_kwargs = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        engine = create_engine(args.database_url, **engine_kwargs)
        from app.database import Base
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    else:
        from app.database import SessionLocal
        session_factory = SessionLocal

    try:
        with session_factory() as db:
            account = upsert_admin_account(db, username=username, password=password)

            print("\n" + "=" * 68)
            print("  ANAUÊ DESIGN - VENDEDOR ONLINE (ria.anauedesign.com.br)")
            print("  Credenciais de Acesso Administrativo Configuradas")
            print("=" * 68)
            print(f"  Domínio:            https://{domain}")
            print(f"  URL de Login:       https://{domain}/admin/login")
            print(f"  Usuário:            {account.username}")
            print(f"  Senha de Acesso:    {password}")
            print("  Algoritmo:          scrypt-v1 (N=16384, r=8, p=1, dklen=64)")
            print(f"  Versão de Auth:     {account.auth_version} (sessões antigas invalidadas)")
            print("  Status da Conta:    Ativa, sem bloqueios (falhas resetadas para 0)")
            print("=" * 68)
            print("  IMPORTANTE: Guarde esta senha em local seguro (ex: cofre de senhas).")
            print("=" * 68 + "\n")
    except Exception as exc:
        print(f"Erro ao atualizar credenciais no banco de dados: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
