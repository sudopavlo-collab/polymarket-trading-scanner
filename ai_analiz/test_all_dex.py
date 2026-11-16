"""
Тестирование токена на всех DEX: Jupiter, Raydium, Meteora, Birdeye
"""
import requests
from config import SOL_MINT
from decimal import Decimal

mint = 'ChxdFmEo52opKKX9DfD2eK8KVMZkiguD87CuZSNeU43C'
amount = 5000000  # 0.005 SOL

print("="*70)
print(f"Testing token: {mint}")
print(f"Swap amount: {amount / 1_000_000_000:.6f} SOL")
print("="*70)

# ============================================================================
# 1. JUPITER
# ============================================================================
print("\n1️⃣  JUPITER API")
print("-" * 70)
try:
    url = "https://public.jupiterapi.com/quote"
    params = {
        "inputMint": SOL_MINT,
        "outputMint": mint,
        "amount": str(amount),
        "slippageBps": "50"
    }
    resp = requests.get(url, params=params, timeout=10, verify=False)
    print(f"Status: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.json()
        if "data" in data and len(data["data"]) > 0:
            route = data["data"][0]
            out_amount = route.get("outAmount", 0)
            print(f"✅ FOUND on Jupiter!")
            print(f"   OutAmount: {out_amount}")
            print(f"   Link: https://jup.ag/swap/SOL-{mint}")
        elif "outAmount" in data:
            print(f"✅ FOUND on Jupiter!")
            print(f"   OutAmount: {data['outAmount']}")
            print(f"   Link: https://jup.ag/swap/SOL-{mint}")
        else:
            print(f"❌ Not found on Jupiter")
            print(f"   Response: {str(data)[:200]}")
    else:
        print(f"❌ Error: {resp.text[:200]}")
except Exception as e:
    print(f"❌ Exception: {e}")

# ============================================================================
# 2. RAYDIUM
# ============================================================================
print("\n2️⃣  RAYDIUM API")
print("-" * 70)
try:
    url = "https://api-v3.raydium.io/swap/quote"
    params = {
        "inputMint": SOL_MINT,
        "outputMint": mint,
        "amount": str(amount),
        "slippage": "0.5"
    }
    resp = requests.get(url, params=params, timeout=10)
    print(f"Status: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.json()
        if "data" in data and ("outputAmount" in data["data"] or "outAmount" in data["data"]):
            out_amount = data["data"].get("outputAmount") or data["data"].get("outAmount")
            print(f"✅ FOUND on Raydium!")
            print(f"   OutAmount: {out_amount}")
            print(f"   Link: https://raydium.io/swap/?inputMint={SOL_MINT}&outputMint={mint}")
        else:
            print(f"❌ Not found on Raydium")
            print(f"   Response: {str(data)[:200]}")
    else:
        print(f"❌ Error: {resp.text[:200]}")
except Exception as e:
    print(f"❌ Exception: {e}")

# ============================================================================
# 3. METEORA
# ============================================================================
print("\n3️⃣  METEORA API")
print("-" * 70)
try:
    # Meteora использует другой endpoint
    url = "https://quote-api.meteora.ag/quote"
    params = {
        "inputMint": SOL_MINT,
        "outputMint": mint,
        "amount": str(amount),
        "slippage": "0.5"
    }
    resp = requests.get(url, params=params, timeout=10)
    print(f"Status: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.json()
        if "outAmount" in data or "outputAmount" in data:
            out_amount = data.get("outAmount") or data.get("outputAmount")
            print(f"✅ FOUND on Meteora!")
            print(f"   OutAmount: {out_amount}")
            print(f"   Link: https://app.meteora.ag/pools")
        else:
            print(f"❌ Not found on Meteora")
            print(f"   Response: {str(data)[:200]}")
    else:
        print(f"❌ Error: {resp.text[:200]}")
except Exception as e:
    print(f"❌ Exception: {e}")

# ============================================================================
# 4. BIRDEYE (Token Info)
# ============================================================================
print("\n4️⃣  BIRDEYE (Token Info)")
print("-" * 70)
try:
    url = f"https://public-api.birdeye.so/defi/token_overview"
    params = {
        "address": mint
    }
    headers = {
        "X-API-KEY": "your-api-key-here",  # Birdeye требует API key
        "x-chain": "solana"
    }
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    print(f"Status: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.json()
        if data.get("success"):
            token_data = data.get("data", {})
            print(f"✅ Token info from Birdeye:")
            print(f"   Symbol: {token_data.get('symbol', 'N/A')}")
            print(f"   Name: {token_data.get('name', 'N/A')}")
            print(f"   Price: ${token_data.get('price', 0)}")
            print(f"   Liquidity: ${token_data.get('liquidity', 0):,.0f}")
            print(f"   Volume 24h: ${token_data.get('v24hUSD', 0):,.0f}")
        else:
            print(f"❌ Token not found on Birdeye")
    else:
        print(f"❌ Error (API key required): {resp.status_code}")
    
    print(f"   Link: https://birdeye.so/token/{mint}?chain=solana")
except Exception as e:
    print(f"❌ Exception: {e}")

# ============================================================================
# 5. DEXSCREENER (Alternative to Birdeye)
# ============================================================================
print("\n5️⃣  DEXSCREENER (Token Info)")
print("-" * 70)
try:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    resp = requests.get(url, timeout=10)
    print(f"Status: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.json()
        pairs = data.get("pairs", [])
        if pairs:
            print(f"✅ Found {len(pairs)} pairs on DexScreener:")
            for pair in pairs[:3]:  # Показываем первые 3
                dex = pair.get("dexId", "Unknown")
                price = pair.get("priceUsd", "0")
                liquidity = pair.get("liquidity", {}).get("usd", 0)
                volume = pair.get("volume", {}).get("h24", 0)
                print(f"   • {dex}: ${price} | Liq: ${liquidity:,.0f} | Vol24h: ${volume:,.0f}")
        else:
            print(f"❌ No pairs found on DexScreener")
    else:
        print(f"❌ Error: {resp.status_code}")
    
    print(f"   Link: https://dexscreener.com/solana/{mint}")
except Exception as e:
    print(f"❌ Exception: {e}")

print("\n" + "="*70)
print("Testing complete!")
print("="*70)
