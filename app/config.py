from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://agenda_user:agenda_pass@localhost:5432/agenda_atende"
    api_key: str = "dev-api-key-change-in-production"
    admin_api_key: str = "dev-admin-key-change-in-production"
    admin_username: str = "admin"
    admin_bootstrap_password: str = ""
    admin_session_secret: str = ""
    admin_recovery_key: str = ""
    app_name: str = "Agenda Atende"
    app_timezone: str = "America/Sao_Paulo"
    debug: bool = True

    asaas_api_key: str = ""
    asaas_base_url: str = "https://api-sandbox.asaas.com/v3"
    asaas_webhook_token: str = ""

    notification_webhook_url: str = ""
    notification_webhook_token: str = ""
    notification_delivery_lease_seconds: int = 300
    notification_delivery_max_attempts: int = 8

    # Triagem Ultrarrápida com TypeSafe Jev (OpenRouter Decisions API)
    openrouter_api_key: str = ""
    triage_enabled: bool = True
    triage_model: str = "typesafe/jev-1.13"
    triage_timeout_seconds: float = 10.0

    @model_validator(mode="after")
    def validate_production_secrets(self):
        if self.debug:
            return self

        invalid_values = {
            "",
            "dev-api-key-change-in-production",
            "dev-admin-key-change-in-production",
            "SUA_API_KEY_AQUI_64_CHARS",
            "SUA_ADMIN_KEY_AQUI_64_CHARS",
            "TOKEN_WEBHOOK_SEGURO_AQUI",
            "dev-admin-session-secret-change-in-production",
            "dev-admin-recovery-key-change-in-production",
            "SUA_CHAVE_DE_SESSAO_ADMIN_64_CHARS",
            "SUA_CHAVE_DE_RECUPERACAO_ADMIN_64_CHARS",
            "admin-bootstrap-password-change-me",
            "SUA_SENHA_DE_BOOTSTRAP_ADMIN",
        }
        secrets = {
            "API_KEY": self.api_key,
            "ADMIN_API_KEY": self.admin_api_key,
            "ASAAS_WEBHOOK_TOKEN": self.asaas_webhook_token,
        }
        invalid = [
            name
            for name, value in secrets.items()
            if value in invalid_values or len(value) < 32
        ]
        if invalid:
            raise ValueError(
                "Production requires secure values for: " + ", ".join(invalid)
            )
        if not self.admin_username.strip():
            raise ValueError("Production requires a non-empty ADMIN_USERNAME")
        if (
            self.admin_bootstrap_password
            and (
                len(self.admin_bootstrap_password) < 12
                or self.admin_bootstrap_password in invalid_values
            )
        ):
            raise ValueError(
                "Production requires ADMIN_BOOTSTRAP_PASSWORD to be at least 12 characters"
            )
        admin_secrets = {
            "ADMIN_SESSION_SECRET": self.admin_session_secret,
            "ADMIN_RECOVERY_KEY": self.admin_recovery_key,
        }
        invalid_admin_secrets = [
            name
            for name, value in admin_secrets.items()
            if value in invalid_values or len(value) < 32
        ]
        if invalid_admin_secrets:
            raise ValueError(
                "Production requires secure values for: "
                + ", ".join(invalid_admin_secrets)
            )
        return self

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
        "hide_input_in_errors": True,
    }


settings = Settings()
