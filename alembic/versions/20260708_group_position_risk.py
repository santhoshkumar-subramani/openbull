"""Group-position risk controls and runtime state.

Adds optional stop-loss / profit-booking configuration and runtime monitor
state columns to ``position_groups``.

Revision ID: 20260708_group_position_risk
Revises: c1c4fbe2a9dc
Create Date: 2026-07-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260708_group_position_risk"
down_revision: Union[str, None] = "c1c4fbe2a9dc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _table_exists("position_groups"):
        return

    if not _column_exists("position_groups", "stop_loss_enabled"):
        op.add_column(
            "position_groups",
            sa.Column("stop_loss_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if not _column_exists("position_groups", "stop_loss_mtm"):
        op.add_column("position_groups", sa.Column("stop_loss_mtm", sa.Numeric(18, 2), nullable=True))
    if not _column_exists("position_groups", "profit_target_enabled"):
        op.add_column(
            "position_groups",
            sa.Column("profit_target_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if not _column_exists("position_groups", "profit_target_mtm"):
        op.add_column("position_groups", sa.Column("profit_target_mtm", sa.Numeric(18, 2), nullable=True))

    if not _column_exists("position_groups", "risk_status"):
        op.add_column(
            "position_groups",
            sa.Column("risk_status", sa.String(length=30), nullable=False, server_default=sa.text("'idle'")),
        )
    if not _column_exists("position_groups", "risk_last_mtm"):
        op.add_column("position_groups", sa.Column("risk_last_mtm", sa.Numeric(18, 2), nullable=True))
    if not _column_exists("position_groups", "risk_last_trigger_reason"):
        op.add_column("position_groups", sa.Column("risk_last_trigger_reason", sa.String(length=40), nullable=True))
    if not _column_exists("position_groups", "risk_last_triggered_at"):
        op.add_column("position_groups", sa.Column("risk_last_triggered_at", sa.DateTime(timezone=True), nullable=True))
    if not _column_exists("position_groups", "risk_last_error"):
        op.add_column("position_groups", sa.Column("risk_last_error", sa.Text(), nullable=True))
    if not _column_exists("position_groups", "risk_retry_count"):
        op.add_column(
            "position_groups",
            sa.Column("risk_retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    if not _column_exists("position_groups", "risk_pending_symbols"):
        op.add_column(
            "position_groups",
            sa.Column("risk_pending_symbols", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
    if not _column_exists("position_groups", "risk_force_close_requested"):
        op.add_column(
            "position_groups",
            sa.Column(
                "risk_force_close_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    if not _table_exists("position_groups"):
        return

    for column in [
        "risk_force_close_requested",
        "risk_pending_symbols",
        "risk_retry_count",
        "risk_last_error",
        "risk_last_triggered_at",
        "risk_last_trigger_reason",
        "risk_last_mtm",
        "risk_status",
        "profit_target_mtm",
        "profit_target_enabled",
        "stop_loss_mtm",
        "stop_loss_enabled",
    ]:
        if _column_exists("position_groups", column):
            op.drop_column("position_groups", column)
