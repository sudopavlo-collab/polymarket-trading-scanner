import asyncio
import time
import sys
sys.path.insert(0, '.')

from polymarket_analyzer.websocket_client import HybridMonitor

async def test_speed():
    monitor = HybridMonitor()
    await monitor.connect()
    
    print("Testing parallel scan speed...")
    
    start = time.time()
    opportunities = await monitor.scan_all_markets(threshold=0.999)
    elapsed = time.time() - start
    
    print(f"\n✅ Scanned all markets in {elapsed:.2f} seconds")
    print(f"Found {len(opportunities)} opportunities with sum < 99.9%")
    
    if opportunities:
        print("\nTop opportunities:")
        for opp in opportunities[:5]:
            u = opp["update"]
            print(f"  {u.question[:50]}... | Profit: {u.arbitrage_profit*100:.2f}%")
    
    await monitor.close()

asyncio.run(test_speed())
