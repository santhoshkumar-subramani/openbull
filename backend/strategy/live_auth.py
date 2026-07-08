"""Live-mode broker auth resolution for strategy runs.

Strategy module never persists broker auth tokens (plan Section 14.8). For
auto-triggered exits and cron-fired starts that run *without* a request
context, we fetch the user's current ``BrokerAuth.access_token`` fresh
from the DB on every call — mirrors :func:`backend.dependencies.get_broker_context`.

Returns ``None`` when the user has no active broker session — callers
treat that as "live not possible; refuse the action and log audit".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.auth import BrokerAuth
from backend.models.broker_config import BrokerConfig
from backend.security import decrypt_value

logger = logging.getLogger(__name__)


@dataclass
class LiveAuthContext:
    """What ``engine.start_run`` / dispatch needs to place live orders."""

    broker: str
    auth_token: str
    config: dict


async def resolve_live_auth(
    db: AsyncSession, *, user_id: int, broker: Optional[str] = None,
) -> Optional[LiveAuthContext]:
    """Fetch the user's active broker auth token + config.

    If ``broker`` is given, requires that specific broker's session. Else
    picks any non-revoked ``BrokerAuth`` for the user (matches the
    fallback behaviour in ``get_broker_context``).

    Returns ``None`` when no valid session exists — engine should refuse
    the live action.
    """
    resolved_broker = broker
    if resolved_broker is None:
        # Mirror request-path broker selection: prefer active broker config.
        cfg_rows = (await db.execute(
            select(BrokerConfig).where(
                BrokerConfig.user_id == user_id,
                BrokerConfig.is_active == True,  # noqa: E712
            )
        )).scalars().all()
        if len(cfg_rows) == 1:
            resolved_broker = cfg_rows[0].broker_name
        elif len(cfg_rows) > 1:
            logger.warning(
                "Live auth: multiple active broker configs for user=%d; refusing ambiguous selection",
                user_id,
            )
            return None

    stmt = select(BrokerAuth).where(
        BrokerAuth.user_id == user_id,
        BrokerAuth.is_revoked == False,  # noqa: E712
    )
    if resolved_broker:
        stmt = stmt.where(BrokerAuth.broker_name == resolved_broker)

    rows = (await db.execute(stmt)).scalars().all()
    if resolved_broker is None and len(rows) > 1:
        logger.warning(
            "Live auth: multiple active broker sessions for user=%d without selected broker",
            user_id,
        )
        return None
    row = rows[0] if rows else None
    if row is None:
        return None

    try:
        auth_token = decrypt_value(row.access_token)
    except Exception:
        logger.exception(
            "Live auth: failed to decrypt broker token for user=%d broker=%s",
            user_id, row.broker_name,
        )
        return None

    # Best-effort config fetch (API key + secret). Some brokers don't store
    # these; the live order path tolerates a missing key.
    cfg_row = (await db.execute(
        select(BrokerConfig).where(
            BrokerConfig.user_id == user_id,
            BrokerConfig.broker_name == row.broker_name,
        )
    )).scalar_one_or_none()
    config: dict = {}
    if cfg_row:
        try:
            config = {
                "api_key": decrypt_value(cfg_row.api_key) if cfg_row.api_key else None,
                "api_secret": decrypt_value(cfg_row.api_secret) if cfg_row.api_secret else None,
                "redirect_url": cfg_row.redirect_url,
            }
        except Exception:
            logger.exception(
                "Live auth: BrokerConfig decrypt failed for user=%d broker=%s",
                user_id, row.broker_name,
            )

    return LiveAuthContext(
        broker=row.broker_name, auth_token=auth_token, config=config,
    )
