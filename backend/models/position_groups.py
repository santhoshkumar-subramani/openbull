from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from backend.database import Base


class PositionGroup(Base):
    """A user-defined group for live positions (Strategy Manager)."""

    __tablename__ = "position_groups"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(200), nullable=False)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    mappings = relationship(
        "PositionGroupMapping",
        back_populates="group",
        cascade="all, delete-orphan",
    )


class PositionGroupMapping(Base):
    """Links a live position (identified by symbol, exchange, product) to a group."""

    __tablename__ = "position_group_mappings"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    group_id = Column(
        Integer,
        ForeignKey("position_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(20), nullable=False)
    product = Column(String(20), nullable=False)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    group = relationship("PositionGroup", back_populates="mappings")

    __table_args__ = (
        UniqueConstraint(
            "user_id", "symbol", "exchange", "product", name="uix_user_position_mapping"
        ),
    )
