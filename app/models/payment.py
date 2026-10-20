from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'received', 'confirmed', 'overdue', 'refunded', 'cancelled')",
            name="check_payment_status",
        ),
        CheckConstraint("amount_cents >= 0", name="check_payment_amount"),
        CheckConstraint(
            "billing_type IN ('pix', 'boleto', 'credit_card', 'undefined')",
            name="check_billing_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    asaas_payment_id: Mapped[str] = mapped_column(String(50), nullable=True, unique=True, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    billing_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pix"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )
    invoice_url: Mapped[str] = mapped_column(String(500), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    appointment = relationship("Appointment", back_populates="payments")
    user = relationship("User", back_populates="payments")
    product = relationship("Product", back_populates="payments")

