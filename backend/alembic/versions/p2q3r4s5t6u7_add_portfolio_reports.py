"""add portfolio reports

Revision ID: p2q3r4s5t6u7
Revises: o1p2q3r4s5t6
Create Date: 2026-07-02 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "p2q3r4s5t6u7"
down_revision: str | None = "o1p2q3r4s5t6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "portfolio_reports",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("portfolio_id", sa.BigInteger(), nullable=False),
        sa.Column("portfolio_version_id", sa.BigInteger(), nullable=False),
        sa.Column("report_type", sa.Text(), server_default="weekly", nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("generated_by", sa.Text(), server_default="template", nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column(
            "source_facts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "report_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "report_type IN ('weekly')",
            name="ck_portfolio_reports_report_type",
        ),
        sa.CheckConstraint(
            "generated_by IN ('template', 'openai_compatible', 'fallback')",
            name="ck_portfolio_reports_generated_by",
        ),
        sa.ForeignKeyConstraint(["portfolio_id"], ["model_portfolios.id"]),
        sa.ForeignKeyConstraint(
            ["portfolio_version_id"], ["model_portfolio_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "portfolio_id",
            "portfolio_version_id",
            "report_type",
            "period_start",
            "period_end",
            name="uq_portfolio_reports_period",
        ),
    )


def downgrade() -> None:
    op.drop_table("portfolio_reports")
