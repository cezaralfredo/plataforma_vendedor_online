"""add human support fields to company_settings

Revision ID: f1b2c3d4e5f6
Revises: e9f0a1b2c3d4
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1b2c3d4e5f6"
down_revision: str | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text("""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'company_settings') THEN
                    CREATE TABLE IF NOT EXISTS company_settings_new (LIKE company_settings INCLUDING ALL);
                    INSERT INTO company_settings_new SELECT * FROM company_settings ON CONFLICT DO NOTHING;
                    ALTER TABLE company_settings_new ADD COLUMN IF NOT EXISTS human_support_phone VARCHAR(20);
                    ALTER TABLE company_settings_new ADD COLUMN IF NOT EXISTS human_support_name VARCHAR(100) DEFAULT 'Equipe de Atendimento';
                    ALTER TABLE company_settings_new ADD COLUMN IF NOT EXISTS human_support_message TEXT;
                    DROP TABLE company_settings CASCADE;
                    ALTER TABLE company_settings_new RENAME TO company_settings;
                END IF;
            END $$;
        """)
        )
    else:
        inspector = sa.inspect(bind)
        tables = set(inspector.get_table_names())
        if "company_settings" in tables:
            settings_columns = {col["name"] for col in inspector.get_columns("company_settings")}
            with op.batch_alter_table("company_settings") as batch_op:
                if "human_support_phone" not in settings_columns:
                    batch_op.add_column(sa.Column("human_support_phone", sa.String(length=20), nullable=True))
                if "human_support_name" not in settings_columns:
                    batch_op.add_column(
                        sa.Column(
                            "human_support_name",
                            sa.String(length=100),
                            nullable=True,
                            server_default=sa.text("'Equipe de Atendimento'"),
                        )
                    )
                if "human_support_message" not in settings_columns:
                    batch_op.add_column(sa.Column("human_support_message", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "company_settings" in tables:
        settings_columns = {col["name"] for col in inspector.get_columns("company_settings")}
        with op.batch_alter_table("company_settings") as batch_op:
            for col in ["human_support_message", "human_support_name", "human_support_phone"]:
                if col in settings_columns:
                    batch_op.drop_column(col)
