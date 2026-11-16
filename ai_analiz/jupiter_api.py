"""
Jupiter API integration - получение котировок
"""
import requests
from decimal import Decimal
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from config import JUPITER_QUOTE_API, SOL_MINT, USDC_MINT
import logging

logger = logging.getLogger(__name__)

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def quote_sync(in_mint: str, out_mint: str, amount_atomic: int) -> Optional[dict]:
    """
    Получить котировку от Jupiter API v6 (синхронно)
    
    Args:
        in_mint: Входящий токен (mint address)
        out_mint: Выходящий токен (mint address)
        amount_atomic: Количество в atomic units
    
    Returns:
        dict или None если нет маршрута
    """
    from config import JUPITER_QUOTE_API
    
    params = {
        "inputMint": in_mint,
        "outputMint": out_mint,
        "amount": str(amount_atomic),
        "slippageBps": "50",  # 0.5% slippage
    }
    
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    
    try:
        resp = requests.get(JUPITER_QUOTE_API, params=params, headers=headers, timeout=10, verify=False)
        
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        
        # Проверяем разные форматы ответа
        # Формат 1: data[] массив (старый API)
        if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
            return data["data"][0]
        
        # Формат 2: прямой ответ (новый API)
        if "outAmount" in data and "inAmount" in data:
            return data
        
        return None
    
    except Exception as e:
        logger.debug(f"Jupiter API error: {e}")
        return None


def parse_route_amounts(route: dict, in_decimals: int = 9, out_decimals: int = 9) -> dict:
    """
    Парсинг amounts из route (универсальный для разных API версий)
    
    Returns:
        {
            "out_amount": Decimal,
            "in_amount": Decimal,
            "out_decimals": int,
            "in_decimals": int
        }
    """
    out_amount_raw = Decimal(route.get("outAmount", 0))
    in_amount_raw = Decimal(route.get("inAmount", 0))
    
    # Пытаемся получить decimals из ответа (Legacy API)
    if "outTokenDecimals" in route and "inTokenDecimals" in route:
        out_dec = route["outTokenDecimals"]
        in_dec = route["inTokenDecimals"]
    else:
        out_dec = out_decimals
        in_dec = in_decimals
    
    out_amount = out_amount_raw / Decimal(10 ** out_dec)
    in_amount = in_amount_raw / Decimal(10 ** in_dec)
    
    return {
        "out_amount": out_amount,
        "in_amount": in_amount,
        "out_decimals": out_dec,
        "in_decimals": in_dec,
    }


def get_sol_price_usd() -> Decimal:
    """Получить цену SOL в USDC"""
    try:
        route = quote_sync(
            SOL_MINT,
            USDC_MINT,
            10_000_000  # 0.01 SOL
        )
        
        if not route:
            return Decimal("0")
        
        parsed = parse_route_amounts(route, in_decimals=9, out_decimals=6)
        
        if parsed["in_amount"] == 0:
            return Decimal("0")
        
        price = parsed["out_amount"] / parsed["in_amount"]
        return price
    
    except Exception as e:
        logger.error(f"Error getting SOL price: {e}")
        return Decimal("0")


def check_can_sell(mint: str, amount_tokens: Decimal, decimals: int) -> bool:
    """Проверить можно ли продать токен обратно в SOL"""
    try:
        amount_atomic = int(amount_tokens * Decimal(10 ** decimals))
        route = quote_sync(mint, SOL_MINT, amount_atomic)
        return route is not None
    except Exception:
        return False
