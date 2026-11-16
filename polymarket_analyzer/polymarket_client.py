"""
Polymarket API Client
Клиент для работы с Polymarket CLOB и Gamma API
"""
import hmac
import hashlib
import base64
import time
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

import aiohttp
import asyncio

from .config import (
    PolymarketCredentials,
    PRIMARY_CREDENTIALS,
    SECONDARY_CREDENTIALS,
    POLYMARKET_API_BASE,
    POLYMARKET_GAMMA_API
)

logger = logging.getLogger(__name__)


@dataclass
class Market:
    """Представление рынка Polymarket"""
    id: str
    question: str
    description: str
    end_date: Optional[datetime]
    outcomes: List[str]
    outcome_prices: List[float]
    volume: float
    liquidity: float
    active: bool
    closed: bool
    condition_id: str
    slug: str
    
    @property
    def min_price(self) -> float:
        """Минимальная цена среди исходов"""
        return min(self.outcome_prices) if self.outcome_prices else 0.0
    
    @property
    def max_price(self) -> float:
        """Максимальная цена среди исходов"""
        return max(self.outcome_prices) if self.outcome_prices else 0.0
    
    @property
    def total_price(self) -> float:
        """Сумма всех цен исходов (для арбитража должна быть < 1.0)"""
        return sum(self.outcome_prices) if self.outcome_prices else 0.0
    
    @property
    def arbitrage_profit(self) -> float:
        """Потенциальная прибыль от арбитража (если < 0, арбитраж невозможен)"""
        if not self.outcome_prices or len(self.outcome_prices) < 2:
            return -1.0
        # Если сумма цен < 1.0, можно купить все исходы и гарантированно получить $1
        # Прибыль = 1.0 - сумма цен
        return 1.0 - self.total_price
    
    @property
    def has_arbitrage(self) -> bool:
        """Есть ли возможность арбитража"""
        return self.arbitrage_profit > 0.001  # Минимум 0.1% прибыли
    
    @property
    def time_until_end(self) -> Optional[float]:
        """Время до окончания в часах"""
        if self.end_date:
            delta = self.end_date - datetime.now(timezone.utc)
            return delta.total_seconds() / 3600
        return None
    
    @property
    def is_ending_soon(self) -> bool:
        """Заканчивается ли событие в ближайшее время (< 7 дней)"""
        hours = self.time_until_end
        return hours is not None and 0 < hours < 168  # 7 дней


@dataclass
class Event:
    """Представление события Polymarket"""
    id: str
    title: str
    description: str
    end_date: Optional[datetime]
    markets: List[Market]
    slug: str
    active: bool
    closed: bool
    
    @property
    def min_market_price(self) -> float:
        """Минимальная цена среди всех рынков события"""
        prices = [m.min_price for m in self.markets if m.outcome_prices]
        return min(prices) if prices else 0.0


class PolymarketClient:
    """Асинхронный клиент Polymarket API"""
    
    def __init__(
        self,
        credentials: PolymarketCredentials = PRIMARY_CREDENTIALS,
        backup_credentials: PolymarketCredentials = SECONDARY_CREDENTIALS
    ):
        self.credentials = credentials
        self.backup_credentials = backup_credentials
        self.session: Optional[aiohttp.ClientSession] = None
        self._use_backup = False
        
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def connect(self):
        """Создание HTTP сессии"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self
    
    async def close(self):
        """Закрытие HTTP сессии"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    def _get_current_credentials(self) -> PolymarketCredentials:
        """Получение текущих credentials"""
        return self.backup_credentials if self._use_backup else self.credentials
    
    def _switch_credentials(self):
        """Переключение на backup credentials"""
        self._use_backup = not self._use_backup
        logger.info(f"Switched to {'backup' if self._use_backup else 'primary'} credentials")
    
    def _generate_signature(
        self,
        method: str,
        path: str,
        timestamp: str,
        body: str = ""
    ) -> str:
        """Генерация HMAC подписи для аутентификации"""
        creds = self._get_current_credentials()
        message = f"{timestamp}{method}{path}{body}"
        secret_decoded = base64.b64decode(creds.secret)
        signature = hmac.new(
            secret_decoded,
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        return base64.b64encode(signature).decode('utf-8')
    
    def _get_auth_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """Получение заголовков аутентификации"""
        creds = self._get_current_credentials()
        timestamp = str(int(time.time()))
        signature = self._generate_signature(method, path, timestamp, body)
        
        return {
            "POLY_API_KEY": creds.api_key,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_PASSPHRASE": creds.passphrase,
            "Content-Type": "application/json"
        }
    
    async def _request(
        self,
        method: str,
        url: str,
        path: str = "",
        data: Optional[Dict] = None,
        auth: bool = False,
        retry_on_fail: bool = True
    ) -> Optional[Dict]:
        """Выполнение HTTP запроса"""
        if self.session is None:
            await self.connect()
        
        headers = {}
        body = ""
        
        if data:
            body = json.dumps(data)
        
        if auth:
            headers = self._get_auth_headers(method, path, body)
        
        try:
            async with self.session.request(
                method,
                url,
                headers=headers,
                data=body if data else None
            ) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 401 and retry_on_fail:
                    logger.warning("Auth failed, switching credentials")
                    self._switch_credentials()
                    return await self._request(method, url, path, data, auth, False)
                else:
                    text = await response.text()
                    logger.error(f"Request failed: {response.status} - {text}")
                    return None
        except Exception as e:
            logger.error(f"Request error: {e}")
            if retry_on_fail:
                self._switch_credentials()
                return await self._request(method, url, path, data, auth, False)
            return None
    
    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False
    ) -> List[Market]:
        """Получение списка рынков через Gamma API"""
        params = []
        if active:
            params.append("active=true")
        # Важно: явно указываем closed=false чтобы не получать закрытые рынки
        params.append(f"closed={'true' if closed else 'false'}")
        params.append(f"limit={limit}")
        params.append(f"offset={offset}")
        
        url = f"{POLYMARKET_GAMMA_API}/markets?{'&'.join(params)}"
        
        response = await self._request("GET", url)
        
        if not response:
            return []
        
        markets = []
        for item in response:
            try:
                market = self._parse_market(item)
                if market:
                    markets.append(market)
            except Exception as e:
                logger.debug(f"Failed to parse market: {e}")
                continue
        
        return markets
    
    async def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False
    ) -> List[Event]:
        """Получение списка событий через Gamma API"""
        params = []
        if active:
            params.append("active=true")
        if closed:
            params.append("closed=true")
        params.append(f"limit={limit}")
        params.append(f"offset={offset}")
        
        url = f"{POLYMARKET_GAMMA_API}/events?{'&'.join(params)}"
        
        response = await self._request("GET", url)
        
        if not response:
            return []
        
        events = []
        for item in response:
            try:
                event = self._parse_event(item)
                if event:
                    events.append(event)
            except Exception as e:
                logger.debug(f"Failed to parse event: {e}")
                continue
        
        return events
    
    async def get_all_active_markets(self, max_pages: int = 10) -> List[Market]:
        """Получение всех активных рынков с пагинацией"""
        all_markets = []
        offset = 0
        limit = 100
        
        for _ in range(max_pages):
            markets = await self.get_markets(limit=limit, offset=offset, active=True)
            if not markets:
                break
            all_markets.extend(markets)
            if len(markets) < limit:
                break
            offset += limit
            await asyncio.sleep(0.5)  # Rate limiting
        
        logger.info(f"Fetched {len(all_markets)} active markets")
        return all_markets
    
    async def get_all_active_events(self, max_pages: int = 10) -> List[Event]:
        """Получение всех активных событий с пагинацией"""
        all_events = []
        offset = 0
        limit = 100
        
        for _ in range(max_pages):
            events = await self.get_events(limit=limit, offset=offset, active=True)
            if not events:
                break
            all_events.extend(events)
            if len(events) < limit:
                break
            offset += limit
            await asyncio.sleep(0.5)  # Rate limiting
        
        logger.info(f"Fetched {len(all_events)} active events")
        return all_events
    
    async def get_market_orderbook(self, token_id: str) -> Optional[Dict]:
        """Получение orderbook для рынка"""
        url = f"{POLYMARKET_API_BASE}/book?token_id={token_id}"
        return await self._request("GET", url)
    
    async def get_market_prices(self, token_id: str) -> Optional[Dict]:
        """Получение текущих цен для рынка"""
        url = f"{POLYMARKET_API_BASE}/price?token_id={token_id}&side=buy"
        return await self._request("GET", url)
    
    def _parse_market(self, data: Dict) -> Optional[Market]:
        """Парсинг данных рынка"""
        try:
            # Парсинг даты окончания
            end_date = None
            end_date_str = data.get("endDate") or data.get("end_date_iso")
            if end_date_str:
                try:
                    end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                except:
                    pass
            
            # Парсинг цен
            outcome_prices = []
            outcomes = []
            
            # Попытка получить цены из разных форматов
            if "outcomePrices" in data:
                try:
                    prices_str = data["outcomePrices"]
                    if isinstance(prices_str, str):
                        outcome_prices = [float(p) for p in json.loads(prices_str)]
                    elif isinstance(prices_str, list):
                        outcome_prices = [float(p) for p in prices_str]
                except:
                    pass
            
            if "outcomes" in data:
                try:
                    outcomes_str = data["outcomes"]
                    if isinstance(outcomes_str, str):
                        outcomes = json.loads(outcomes_str)
                    elif isinstance(outcomes_str, list):
                        outcomes = outcomes_str
                except:
                    pass
            
            # Альтернативный формат с tokens
            if not outcome_prices and "tokens" in data:
                tokens = data["tokens"]
                if isinstance(tokens, list):
                    for token in tokens:
                        if "price" in token:
                            outcome_prices.append(float(token["price"]))
                        if "outcome" in token:
                            outcomes.append(token["outcome"])
            
            return Market(
                id=str(data.get("id", "")),
                question=data.get("question", ""),
                description=data.get("description", ""),
                end_date=end_date,
                outcomes=outcomes,
                outcome_prices=outcome_prices,
                volume=float(data.get("volume", 0) or 0),
                liquidity=float(data.get("liquidity", 0) or 0),
                active=data.get("active", True),
                closed=data.get("closed", False),
                condition_id=data.get("conditionId", ""),
                slug=data.get("slug", "")
            )
        except Exception as e:
            logger.debug(f"Market parse error: {e}")
            return None
    
    def _parse_event(self, data: Dict) -> Optional[Event]:
        """Парсинг данных события"""
        try:
            # Парсинг даты окончания
            end_date = None
            end_date_str = data.get("endDate") or data.get("end_date_iso")
            if end_date_str:
                try:
                    end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                except:
                    pass
            
            # Парсинг рынков события
            markets = []
            markets_data = data.get("markets", [])
            if isinstance(markets_data, list):
                for m in markets_data:
                    market = self._parse_market(m)
                    if market:
                        markets.append(market)
            
            return Event(
                id=str(data.get("id", "")),
                title=data.get("title", ""),
                description=data.get("description", ""),
                end_date=end_date,
                markets=markets,
                slug=data.get("slug", ""),
                active=data.get("active", True),
                closed=data.get("closed", False)
            )
        except Exception as e:
            logger.debug(f"Event parse error: {e}")
            return None


async def test_client():
    """Тестирование клиента"""
    async with PolymarketClient() as client:
        print("Fetching markets...")
        markets = await client.get_markets(limit=10)
        print(f"Got {len(markets)} markets")
        
        for market in markets[:5]:
            print(f"\n{market.question}")
            print(f"  Prices: {market.outcome_prices}")
            print(f"  Min price: {market.min_price}")
            print(f"  End date: {market.end_date}")
            print(f"  Time until end: {market.time_until_end} hours")


if __name__ == "__main__":
    asyncio.run(test_client())
