from dataclasses import dataclass
from backend.utils.event_bus import Event

@dataclass
class BrokerOrderUpdateEvent(Event):
    broker: str
    auth_token: str
    user_id_hint: str
