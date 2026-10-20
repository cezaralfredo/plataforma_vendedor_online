import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.company_settings import CompanySettings
from app.services.company_settings_service import CompanySettingsService
from app.services.whatsapp.base import (
    BaseWhatsAppProvider,
    InboundMessage,
    SendMessageResult,
)
from app.services.whatsapp.evolution import EvolutionWhatsAppProvider
from app.services.whatsapp.meta import MetaWhatsAppProvider
from app.services.whatsapp.tracker import SentMessageTracker
from app.services.whatsapp.waha import WahaWhatsAppProvider

logger = logging.getLogger(__name__)


class WhatsAppGatewayService:
    def __init__(self, db: Session):
        self.db = db
        self.settings: CompanySettings = CompanySettingsService(db).get_or_create_default()

    def get_provider(self, provider_name: str | None = None) -> BaseWhatsAppProvider:
        name = (provider_name or self.settings.whatsapp_provider or "evolution").lower()

        if name == "waha":
            return WahaWhatsAppProvider(
                base_url=self.settings.waha_base_url,
                session=self.settings.waha_session,
                api_key=self.settings.waha_api_key,
            )
        elif name == "meta":
            return MetaWhatsAppProvider(
                phone_number_id=self.settings.meta_phone_number_id,
                access_token=self.settings.meta_access_token,
                waba_id=self.settings.meta_waba_id,
                verify_token=self.settings.meta_verify_token,
            )
        else:  # default to evolution
            return EvolutionWhatsAppProvider(
                base_url=self.settings.evolution_base_url,
                instance=self.settings.evolution_instance,
                api_key=self.settings.evolution_api_key,
            )

    async def send_text(
        self, phone: str, text: str, provider_name: str | None = None
    ) -> SendMessageResult:
        provider = self.get_provider(provider_name)
        result = await provider.send_text(phone, text)
        if result.success and result.message_id:
            SentMessageTracker.get_instance().record(result.message_id)
        return result

    async def send_image(
        self,
        phone: str,
        image_url: str,
        caption: str | None = None,
        provider_name: str | None = None,
    ) -> SendMessageResult:
        provider = self.get_provider(provider_name)
        result = await provider.send_image(phone, image_url, caption)
        if result.success and result.message_id:
            SentMessageTracker.get_instance().record(result.message_id)
        return result

    def parse_inbound(
        self, provider_name: str, payload: dict[str, Any]
    ) -> InboundMessage | None:
        provider = self.get_provider(provider_name)
        return provider.parse_inbound_webhook(payload)

    async def check_health(self) -> dict[str, Any]:
        provider = self.get_provider()
        return {
            "active_provider": provider.name,
            "details": await provider.check_health(),
        }
