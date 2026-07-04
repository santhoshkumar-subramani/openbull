import json
from backend.services.market_data_cache import get_market_data_cache

cache = get_market_data_cache()
keys = cache.get_all_keys()
print(f"Total keys: {len(keys)}")
for key in keys:
    if "SENSEX" in key or "VIX" in key:
        print(key)
