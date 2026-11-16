from raydium_api import get_raydium_quote, quote_with_fallback
from config import SOL_MINT

mint = '6SbMGoGbdawXLHcwzVTef5xyqjWFUo1YUJ6mDer4pump'

print("Testing Raydium API...")
print(f"Token: {mint}")
print()

# Тест 1: Только Raydium
print("1. Direct Raydium API:")
route = get_raydium_quote(SOL_MINT, mint, 5000000)  # 0.005 SOL
if route:
    print(f"   ✓ Route found!")
    print(f"   OutAmount: {route.get('outAmount')}")
    print(f"   InAmount: {route.get('inAmount')}")
else:
    print(f"   ✗ No route")

print()

# Тест 2: С fallback
print("2. Jupiter + Raydium fallback:")
route = quote_with_fallback(SOL_MINT, mint, 5000000)
if route:
    print(f"   ✓ Route found on {route.get('source', 'unknown')}!")
    print(f"   OutAmount: {route.get('outAmount')}")
else:
    print(f"   ✗ No route on both")
