from jupiter_api import quote_sync
from config import SOL_MINT
import requests

mint = '6SbMGoGbdawXLHcwzVTef5xyqjWFUo1YUJ6mDer4pump'

# Пробуем разные суммы
for amount in [1000000, 5000000, 10000000, 50000000]:  # 0.001, 0.005, 0.01, 0.05 SOL
    print(f'\nTrying {amount / 1_000_000_000:.6f} SOL ({amount} lamports):')
    
    # Прямой запрос
    url = "https://quote-api.jup.ag/v6/quote"
    params = {
        "inputMint": SOL_MINT,
        "outputMint": mint,
        "amount": str(amount),
        "slippageBps": "50"
    }
    
    try:
        resp = requests.get(url, params=params, timeout=5, verify=False)
        print(f'  Status: {resp.status_code}')
        if resp.status_code == 200:
            data = resp.json()
            if "outAmount" in data:
                print(f'  ✓ Route found! OutAmount: {data["outAmount"]}')
            else:
                print(f'  Response: {str(data)[:200]}')
        else:
            print(f'  Error: {resp.text[:200]}')
    except Exception as e:
        print(f'  Exception: {e}')
