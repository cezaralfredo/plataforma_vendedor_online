"""Add chat_messages, delivery fields to users, weight_kg to products, and product payment support.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # 1. Tabela chat_messages
    if "chat_messages" not in tables:
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("phone", sa.String(length=30), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("provider", sa.String(length=20), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_chat_messages_phone", "chat_messages", ["phone"])
        op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"])

    # 2. Produtos: peso em kg para cálculo de frete
    if "products" in tables:
        product_columns = {col["name"] for col in inspector.get_columns("products")}
        with op.batch_alter_table("products") as batch_op:
            if "weight_kg" not in product_columns:
                batch_op.add_column(sa.Column("weight_kg", sa.Float(), nullable=True))
                batch_op.create_check_constraint(
                    "check_product_weight",
                    "weight_kg IS NULL OR weight_kg >= 0",
                )

    # 3. Usuários: endereço e detalhes de entrega
    if "users" in tables:
        user_columns = {col["name"] for col in inspector.get_columns("users")}
        with op.batch_alter_table("users") as batch_op:
            if "address_number" not in user_columns:
                batch_op.add_column(sa.Column("address_number", sa.String(length=30), nullable=True))
            if "address_complement" not in user_columns:
                batch_op.add_column(sa.Column("address_complement", sa.String(length=100), nullable=True))
            if "neighborhood" not in user_columns:
                batch_op.add_column(sa.Column("neighborhood", sa.String(length=100), nullable=True))
            if "delivery_instructions" not in user_columns:
                batch_op.add_column(sa.Column("delivery_instructions", sa.Text(), nullable=True))
            if "preferred_bank" not in user_columns:
                batch_op.add_column(sa.Column("preferred_bank", sa.String(length=100), nullable=True))

    # 4. Pagamentos: suporte a pagamentos avulsos de produtos sem appointment_id obrigatório
    if "payments" in tables:
        payment_columns = {col["name"] for col in inspector.get_columns("payments")}
        with op.batch_alter_table("payments") as batch_op:
            if "appointment_id" in payment_columns:
                batch_op.alter_column("appointment_id", existing_type=sa.Integer(), nullable=True)
            if "user_id" not in payment_columns:
                batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_payments_user_id_users",
                    "users",
                    ["user_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
                batch_op.create_index("ix_payments_user_id", ["user_id"])
            if "product_id" not in payment_columns:
                batch_op.add_column(sa.Column("product_id", sa.Integer(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_payments_product_id_products",
                    "products",
                    ["product_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
                batch_op.create_index("ix_payments_product_id", ["product_id"])
            if "description" not in payment_columns:
                batch_op.add_column(sa.Column("description", sa.String(length=255), nullable=True))

    # 5. Configurações da Empresa: WAHA, Coexistência e IA Nativa Multi-Provider
    if "company_settings" in tables:
        settings_columns = {col["name"] for col in inspector.get_columns("company_settings")}
        with op.batch_alter_table("company_settings") as batch_op:
            if "coexistence_enabled" not in settings_columns:
                batch_op.add_column(
                    sa.Column("coexistence_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true"))
                )
            if "coexistence_pause_minutes" not in settings_columns:
                batch_op.add_column(
                    sa.Column("coexistence_pause_minutes", sa.Integer(), nullable=False, server_default=sa.text("45"))
                )
            if "evolution_base_url" not in settings_columns:
                batch_op.add_column(sa.Column("evolution_base_url", sa.String(length=255), nullable=True))
            if "evolution_api_key" not in settings_columns:
                batch_op.add_column(sa.Column("evolution_api_key", sa.Text(), nullable=True))
            if "waha_base_url" not in settings_columns:
                batch_op.add_column(sa.Column("waha_base_url", sa.String(length=255), nullable=True))
            if "waha_session" not in settings_columns:
                batch_op.add_column(
                    sa.Column("waha_session", sa.String(length=100), nullable=True, server_default=sa.text("'default'"))
                )
            if "waha_api_key" not in settings_columns:
                batch_op.add_column(sa.Column("waha_api_key", sa.Text(), nullable=True))
            if "meta_verify_token" not in settings_columns:
                batch_op.add_column(sa.Column("meta_verify_token", sa.String(length=100), nullable=True))
            if "ai_provider" not in settings_columns:
                batch_op.add_column(
                    sa.Column("ai_provider", sa.String(length=30), nullable=False, server_default=sa.text("'openai'"))
                )
            if "ai_model" not in settings_columns:
                batch_op.add_column(
                    sa.Column("ai_model", sa.String(length=100), nullable=False, server_default=sa.text("'gpt-4o-mini'"))
                )
            if "ai_api_key" not in settings_columns:
                batch_op.add_column(sa.Column("ai_api_key", sa.Text(), nullable=True))
            if "ai_base_url" not in settings_columns:
                batch_op.add_column(sa.Column("ai_base_url", sa.String(length=255), nullable=True))
            if "ai_temperature" not in settings_columns:
                batch_op.add_column(
                    sa.Column("ai_temperature", sa.Numeric(precision=3, scale=2), nullable=False, server_default=sa.text("0.30"))
                )
            if "ai_system_prompt" not in settings_columns:
                batch_op.add_column(sa.Column("ai_system_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "company_settings" in tables:
        settings_columns = {col["name"] for col in inspector.get_columns("company_settings")}
        with op.batch_alter_table("company_settings") as batch_op:
            for col in [
                "ai_system_prompt",
                "ai_temperature",
                "ai_base_url",
                "ai_api_key",
                "ai_model",
                "ai_provider",
                "meta_verify_token",
                "waha_api_key",
                "waha_session",
                "waha_base_url",
                "evolution_api_key",
                "evolution_base_url",
                "coexistence_pause_minutes",
                "coexistence_enabled",
            ]:
                if col in settings_columns:
                    batch_op.drop_column(col)

    if "payments" in tables:
        payment_columns = {col["name"] for col in inspector.get_columns("payments")}
        with op.batch_alter_table("payments") as batch_op:
            if "description" in payment_columns:
                batch_op.drop_column("description")
            if "product_id" in payment_columns:
                batch_op.drop_index("ix_payments_product_id")
                batch_op.drop_column("product_id")
            if "user_id" in payment_columns:
                batch_op.drop_index("ix_payments_user_id")
                batch_op.drop_column("user_id")

    if "users" in tables:
        user_columns = {col["name"] for col in inspector.get_columns("users")}
        with op.batch_alter_table("users") as batch_op:
            for col in [
                "preferred_bank",
                "delivery_instructions",
                "neighborhood",
                "address_complement",
                "address_number",
            ]:
                if col in user_columns:
                    batch_op.drop_column(col)

    if "products" in tables:
        product_columns = {col["name"] for col in inspector.get_columns("products")}
        with op.batch_alter_table("products") as batch_op:
            if "weight_kg" in product_columns:
                try:
                    batch_op.drop_constraint("check_product_weight", type_="check")
                except Exception:
                    pass
                batch_op.drop_column("weight_kg")

    if "chat_messages" in tables:
        op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
        op.drop_index("ix_chat_messages_phone", table_name="chat_messages")
        op.drop_table("chat_messages")
