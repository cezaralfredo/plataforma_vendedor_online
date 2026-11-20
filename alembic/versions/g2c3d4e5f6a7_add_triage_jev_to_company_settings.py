"""add triage jev fields to company_settings

Revision ID: g2c3d4e5f6a7
Revises: f1b2c3d4e5f6
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "g2c3d4e5f6a7"
down_revision: str | None = "f1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    target_table = "company_settings"

    if target_table in tables:
        settings_columns = {col["name"] for col in inspector.get_columns(target_table)}
        with op.batch_alter_table(target_table) as batch_op:
            if "triage_enabled" not in settings_columns:
                batch_op.add_column(
                    sa.Column(
                        "triage_enabled",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.text("true"),
                    )
                )
            if "triage_api_key" not in settings_columns:
                batch_op.add_column(sa.Column("triage_api_key", sa.Text(), nullable=True))
            if "triage_model" not in settings_columns:
                batch_op.add_column(
                    sa.Column(
                        "triage_model",
                        sa.String(length=100),
                        nullable=False,
                        server_default=sa.text("'typesafe/jev-1.13'"),
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    target_table = "company_settings"

    if target_table in tables:
        settings_columns = {col["name"] for col in inspector.get_columns(target_table)}
        with op.batch_alter_table(target_table) as batch_op:
            for col in ["triage_model", "triage_api_key", "triage_enabled"]:
                if col in settings_columns:
                    batch_op.drop_column(col)
