from dataclasses import dataclass, field
from typing import Any, Dict
from backend.utils.event_bus import Event

@dataclass
class PositionOpenedEvent(Event):
    topic: str = "position.opened"
    user_id: int = 0
    position_data: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PositionClosedEvent(Event):
    topic: str = "position.closed"
    user_id: int = 0
    position_data: Dict[str, Any] = field(default_factory=dict)
