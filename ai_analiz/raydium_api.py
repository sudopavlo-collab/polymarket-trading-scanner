"""
Raydium API integration - для токенов которых нет на Jupiter
"""
import requests
from decimal import Decimal
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Raydium API endpoints
RAYDIUM_QUOTE_API = "https://api-v3.raydium.io/swap/quote"
RAYDIUM_COMPUTE_API = "https://api-v3.raydium.io/swap/compute"


def get_raydium_quote(in_mint: str, out_mint: str, amount_atomic: int) -> Optional[dict]:
    """
    Получить котировку от Raydium API
    
    Args:
        in_mint: Входящий токен (mint address)
        out_mint: Выходящий токен (mint address)
        amount_atomic: Количество в atomic units
    
    Returns:
        dict или None если нет маршрута
    """
    # Пробуем GET запрос (как Jupiter)
    params = {
        "inputMint": in_mint,
        "outputMint": out_mint,
        "amount": str(amount_atomic),
        "slippage": "0.5",
    }
    
    headers = {
        "Accept": "application/json",
    }
    
    try:
        # Пробуем quote endpoint
        resp = requests.get(RAYDIUM_QUOTE_API, params=params, headers=headers, timeout=10)
        
        if resp.status_code != 200:
            logger.debug(f"Raydium quote API error: {resp.status_code} - {resp.text[:100]}")
            return None
        
        data = resp.json()
        
        # Raydium v3 возвращает data с outputAmount
        if "data" in data:
            route_data = data["data"]
            if "outputAmount" in route_data or "outAmount" in route_data:
                # Конвертируем в формат совместимый с Jupiter
                return {
                    "outAmount": route_data.get("outputAmount") or route_data.get("outAmount"),
                    "inAmount": route_data.get("inputAmount") or route_data.get("inAmount") or str(amount_atomic),
                    "source": "raydium"
                }
        
        # Прямой формат
        if "outputAmount" in data or "outAmount" in data:
            return {
                "outAmount": data.get("outputAmount") or data.get("outAmount"),
                "inAmount": data.get("inputAmount") or data.get("inAmount") or str(amount_atomic),
                "source": "raydium"
            }
        
        logger.debug(f"Raydium unexpected response: {str(data)[:200]}")
        return None
    
    except Exception as e:
        logger.debug(f"Raydium API exception: {e}")
        return None


def quote_with_fallback(in_mint: str, out_mint: str, amount_atomic: int) -> Optional[dict]:
    """
    Попытаться получить котировку сначала от Jupiter, потом от Raydium
    
    Returns:
        dict с полями outAmount, inAmount и source (jupiter/raydium)
    """
    from jupiter_api import quote_sync
    
    # Сначала пробуем Jupiter (быстрее и надёжнее)
    route = quote_sync(in_mint, out_mint, amount_atomic)
    if route:
        route["source"] = "jupiter"
        return route
    
    # Если Jupiter не нашёл, пробуем Raydium
    logger.debug(f"Jupiter failed, trying Raydium...")
    route = get_raydium_quote(in_mint, out_mint, amount_atomic)
    if route:
        logger.info(f"✓ Found route on Raydium!")
        return route
    
    return None
