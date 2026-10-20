from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    provider: str
    sender_phone: str  # E.164, ex: "5585999998888"
    sender_name: str = "Cliente"
    text: str
    message_id: str
    media_type: str | None = "text"
    media_url: str | None = None
    is_from_me: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_payload: dict[str, Any] = Field(default_factory=dict)



class SendMessageResult(BaseModel):
    success: bool
    message_id: str | None = None
    error: str | None = None
    provider: str
    raw_response: dict[str, Any] | None = None


class BaseWhatsAppProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def send_text(self, phone: str, text: str) -> SendMessageResult:
        """Envia mensagem de texto simples para um número de WhatsApp."""
        pass

    @abstractmethod
    async def send_image(
        self, phone: str, image_url: str, caption: str | None = None
    ) -> SendMessageResult:
        """Envia uma imagem com legenda opcional."""
        pass

    @abstractmethod
    def parse_inbound_webhook(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Normaliza payload recebido do webhook do provedor para InboundMessage."""
        pass

    @abstractmethod
    async def check_health(self) -> dict[str, Any]:
        """Verifica a conectividade e status da sessão ou credenciais."""
        pass
