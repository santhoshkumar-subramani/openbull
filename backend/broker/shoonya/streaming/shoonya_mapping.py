"""
Shoonya WebSocket exchange code mapping.

Shoonya subscriptions use pipe-delimited exchange|token strings, e.g. "NSE|26000".
Index instruments are served on NSE/BSE (not NSE_INDEX/BSE_INDEX).
"""


class ShoonyaExchangeMapper:
    """Maps OpenBull exchange names to Shoonya WebSocket exchange codes and back."""

    # OpenBull exchange → Shoonya WS exchange code
    _TO_SHOONYA: dict[str, str] = {
        "NSE": "NSE",
        "BSE": "BSE",
        "NFO": "NFO",
        "CDS": "CDS",
        "MCX": "MCX",
        "BFO": "BFO",
        "NSE_INDEX": "NSE",   # Index instruments are served on NSE feed
        "BSE_INDEX": "BSE",   # Index instruments are served on BSE feed
    }

    # Shoonya WS exchange code → OpenBull exchange (used when parsing ticks)
    _FROM_SHOONYA: dict[str, str] = {
        "NSE": "NSE",
        "BSE": "BSE",
        "NFO": "NFO",
        "CDS": "CDS",
        "MCX": "MCX",
        "BFO": "BFO",
    }

    @classmethod
    def to_shoonya(cls, exchange: str) -> str:
        return cls._TO_SHOONYA.get(exchange, exchange)

    @classmethod
    def from_shoonya(cls, exchange: str) -> str:
        return cls._FROM_SHOONYA.get(exchange, exchange)

    @classmethod
    def make_key(cls, exchange: str, token: str) -> str:
        """Return 'EXCH|token' string for subscription payloads."""
        return f"{cls.to_shoonya(exchange)}|{token}"
