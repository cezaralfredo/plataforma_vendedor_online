from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, String, Text, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    whatsapp_number: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    cpf_cnpj: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    asaas_customer_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Rastreamento de importação e origem
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="direct", server_default="direct", index=True
    )
    external_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    import_batch_id: Mapped[str | None] = mapped_column(
        String(50), nullable=True, index=True
    )

    # Segmentação e CRM
    tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_fields: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )

    # Dados adicionais de cadastro e entrega
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address_complement: Mapped[str | None] = mapped_column(String(100), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(100), nullable=True)
    delivery_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_bank: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Consentimento
    opt_in_whatsapp: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    appointments = relationship("Appointment", back_populates="user")
    payments = relationship("Payment", back_populates="user")

    @property
    def full_address(self) -> str:
        """Retorna o endereço completo e formatado para a logística ou entrega."""
        parts = []
        if self.address:
            addr = self.address
            if self.address_number:
                addr += f", {self.address_number}"
            if self.address_complement:
                addr += f" - {self.address_complement}"
            parts.append(addr)
        if self.neighborhood:
            parts.append(f"Bairro: {self.neighborhood}")
        if self.city:
            loc = self.city
            if self.state:
                loc += f"/{self.state}"
            parts.append(loc)
        if self.postal_code:
            parts.append(f"CEP: {self.postal_code}")
        return " | ".join(parts) if parts else "Endereço não informado"

