import logging

from sqlalchemy.orm import Session

from app.models.company_settings import CompanySettings
from app.schemas.company_settings import CompanySettingsUpdate

logger = logging.getLogger(__name__)


class CompanySettingsService:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_default(self) -> CompanySettings:
        """Retorna a configuração da empresa (singleton/primeiro registro) ou cria com padrões."""
        settings = self.db.query(CompanySettings).first()
        if not settings:
            settings = CompanySettings(
                company_name="Minha Empresa",
                payment_required=True,
                default_gateway="asaas",
                gateway_credentials={},
                accepted_payment_methods=["pix"],
                commission_model="percentage",
                default_commission_rate=0.0,
                split_enabled=False,
                whatsapp_provider="evolution",
                assistant_name="Assistente",
                tone="casual",
                require_cpf=False,
                human_support_phone=None,
                human_support_name="Equipe de Atendimento",
                human_support_message=None,
                custom_settings={},
            )
            self.db.add(settings)
            self.db.commit()
            self.db.refresh(settings)
        return settings

    def update(self, update_data: CompanySettingsUpdate) -> CompanySettings:
        settings = self.get_or_create_default()
        fields_to_update = update_data.model_dump(exclude_unset=True)

        if "gateway_credentials" in fields_to_update:
            creds = dict(settings.gateway_credentials or {})
            creds.update(fields_to_update.pop("gateway_credentials") or {})
            settings.gateway_credentials = creds

        if "custom_settings" in fields_to_update:
            custom = dict(settings.custom_settings or {})
            custom.update(fields_to_update.pop("custom_settings") or {})
            settings.custom_settings = custom

        for field, value in fields_to_update.items():
            setattr(settings, field, value)

        self.db.commit()
        self.db.refresh(settings)
        return settings

    def is_payment_required(self) -> bool:
        settings = self.get_or_create_default()
        return bool(settings.payment_required)

    def get_active_gateway(self) -> str:
        settings = self.get_or_create_default()
        return settings.default_gateway or "asaas"
