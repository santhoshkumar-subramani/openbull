import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from backend.config import get_settings
from backend.strategy import repo

async def main():
    engine = create_async_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)
    
    async with session_factory() as db:
        orders = await repo.list_orders_for_run(db, user_id=1, run_id=7)
        strategy = await repo.get_strategy(db, user_id=1, strategy_id=3)
        print("Orders fetched:", len(orders))
        
        aggs = {}
        for o in orders:
            if (o.status or "").lower() != "complete":
                continue
            fill_qty = int(o.filled_qty or o.qty or 0)
            fill_price = float(o.avg_fill_price or 0)
            if fill_qty <= 0 or fill_price <= 0:
                continue
            key = (o.symbol, o.exchange, strategy.product)
            a = aggs.setdefault(key, {
                "symbol": o.symbol,
                "exchange": o.exchange,
                "product": strategy.product,
                "buy_qty": 0,
                "buy_value": 0.0,
                "sell_qty": 0,
                "sell_value": 0.0,
            })
            if (o.action or "").upper() == "BUY":
                a["buy_qty"] += fill_qty
                a["buy_value"] += fill_qty * fill_price
            else:
                a["sell_qty"] += fill_qty
                a["sell_value"] += fill_qty * fill_price
                
        print("Aggregations:", aggs)
        
asyncio.run(main())
