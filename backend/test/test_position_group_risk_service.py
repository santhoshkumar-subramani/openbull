import pytest
from backend.services.position_group_risk_service import _calculate_live_pnl
from backend.services.market_data_cache import get_market_data_cache

@pytest.fixture(autouse=True)
def clear_market_data_cache():
    cache = get_market_data_cache()
    # Force a clear of the local validator/cache lock state
    with cache._lock:
        cache._cache.clear()

def test_calculate_live_pnl_ws_unhealthy():
    """When WS is unhealthy, it should strictly return the REST pnl."""
    pos = {
        "pnl": -50.0,
        "quantity": 50,
        "average_price": 100.0,
        "realized_pnl": 0.0,
        "lot_size": 1.0,
    }
    # No LTP is set, but even if it was, ws_healthy=False means fallback to REST
    result = _calculate_live_pnl(pos, "NIFTY", "NSE", ws_healthy=False)
    assert result == -50.0

def test_calculate_live_pnl_closed_position():
    """When quantity is 0 (closed position), it should strictly return the REST pnl (realized)."""
    pos = {
        "pnl": 1500.0,
        "quantity": 0,
        "average_price": 100.0,
        "realized_pnl": 1500.0,
        "lot_size": 1.0,
    }
    result = _calculate_live_pnl(pos, "NIFTY", "NSE", ws_healthy=True)
    assert result == 1500.0

def test_calculate_live_pnl_missing_ltp():
    """When WS is healthy but we have no LTP in cache, fallback to REST pnl."""
    pos = {
        "pnl": -20.0,
        "quantity": 50,
        "average_price": 100.0,
    }
    result = _calculate_live_pnl(pos, "NIFTY", "NSE", ws_healthy=True)
    assert result == -20.0

def test_calculate_live_pnl_long_position():
    """Test live unrealized PNL calculation for a BUY position."""
    # Add fake tick to cache
    cache = get_market_data_cache()
    cache.process_market_data({
        "symbol": "NIFTY",
        "exchange": "NSE",
        "mode": 1,
        "data": {"ltp": 105.0}
    })

    pos = {
        "pnl": 0.0, # REST PNL is stale
        "quantity": 50, # Long 1 lot
        "average_price": 100.0,
        "realized_pnl": 10.0, # Previously booked profit
        "lot_size": 1.0,
    }
    
    # Live unrealized = (105 - 100) * 50 * 1.0 = 250
    # Total = 250 + 10 (realized) = 260
    result = _calculate_live_pnl(pos, "NIFTY", "NSE", ws_healthy=True)
    assert result == 260.0

def test_calculate_live_pnl_short_position():
    """Test live unrealized PNL calculation for a SELL position."""
    # Add fake tick to cache
    cache = get_market_data_cache()
    cache.process_market_data({
        "symbol": "BANKNIFTY",
        "exchange": "NFO",
        "mode": 1,
        "data": {"ltp": 90.0}
    })

    pos = {
        "pnl": -100.0, # REST PNL is stale
        "quantity": -15, # Short 1 lot
        "average_price": 100.0,
        "realized_pnl": 0.0,
        "lot_size": 1.0,
    }
    
    # Live unrealized = (100 - 90) * |-15| * 1.0 = 150
    # Total = 150 + 0 = 150
    result = _calculate_live_pnl(pos, "BANKNIFTY", "NFO", ws_healthy=True)
    assert result == 150.0
