"""
Polymarket WebSocket Client
Real-time мониторинг цен для арбитража
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Dict, List, Callable, Any
from dataclasses import dataclass

import aiohttp

from .config import POLYMARKET_API_BASE

logger = logging.getLogger(__name__)

# WebSocket endpoints
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_WS_URL = "wss://gamma-api.polymarket.com/ws"


@dataclass
class PriceUpdate:
    """Обновление цены"""
    token_id: str
    market_id: str
    asset_id: str
    price: float
    side: str  # 'buy' or 'sell'
    size: float
    timestamp: datetime
    
    @property
    def is_buy(self) -> bool:
        return self.side == 'buy'


@dataclass
class MarketUpdate:
    """Обновление рынка"""
    market_id: str
    question: str
    outcomes: List[str]
    prices: List[float]
    timestamp: datetime
    
    @property
    def total_price(self) -> float:
        return sum(self.prices) if self.prices else 0.0
    
    @property
    def arbitrage_profit(self) -> float:
        if len(self.prices) < 2:
            return -1.0
        return 1.0 - self.total_price
    
    @property
    def has_arbitrage(self) -> bool:
        return self.arbitrage_profit > 0.001


class PolymarketWebSocket:
    """WebSocket клиент для real-time мониторинга"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.running = False
        self._callbacks: List[Callable[[MarketUpdate], Any]] = []
        self._subscribed_markets: set = set()
        self._reconnect_delay = 1
        self._max_reconnect_delay = 60
        
    async def connect(self):
        """Подключение к WebSocket"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
        
        try:
            logger.info(f"Connecting to WebSocket: {WS_URL}")
            self.ws = await self.session.ws_connect(
                WS_URL,
                heartbeat=30,
                receive_timeout=60
            )
            self.running = True
            self._reconnect_delay = 1
            logger.info("WebSocket connected!")
            return True
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            return False
    
    async def close(self):
        """Закрытие соединения"""
        self.running = False
        if self.ws and not self.ws.closed:
            await self.ws.close()
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info("WebSocket closed")
    
    def on_update(self, callback: Callable[[MarketUpdate], Any]):
        """Регистрация callback для обновлений"""
        self._callbacks.append(callback)
    
    async def subscribe_market(self, token_id: str):
        """Подписка на обновления рынка"""
        if not self.ws or self.ws.closed:
            return False
        
        message = {
            "type": "subscribe",
            "channel": "market",
            "assets_ids": [token_id]
        }
        
        try:
            await self.ws.send_json(message)
            self._subscribed_markets.add(token_id)
            logger.debug(f"Subscribed to market: {token_id}")
            return True
        except Exception as e:
            logger.error(f"Subscribe failed: {e}")
            return False
    
    async def subscribe_all_markets(self, token_ids: List[str]):
        """Подписка на все рынки"""
        if not self.ws or self.ws.closed:
            return False
        
        # Подписываемся пачками по 100
        batch_size = 100
        for i in range(0, len(token_ids), batch_size):
            batch = token_ids[i:i+batch_size]
            message = {
                "type": "subscribe", 
                "channel": "market",
                "assets_ids": batch
            }
            try:
                await self.ws.send_json(message)
                self._subscribed_markets.update(batch)
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Batch subscribe failed: {e}")
        
        logger.info(f"Subscribed to {len(self._subscribed_markets)} markets")
        return True
    
    async def _handle_message(self, data: Dict):
        """Обработка входящего сообщения"""
        try:
            msg_type = data.get("type") or data.get("event_type")
            
            if msg_type == "book":
                # Обновление orderbook
                await self._handle_book_update(data)
            elif msg_type == "price_change":
                # Изменение цены
                await self._handle_price_change(data)
            elif msg_type == "trade":
                # Сделка
                await self._handle_trade(data)
            elif msg_type == "subscribed":
                logger.debug(f"Subscription confirmed")
            elif msg_type == "error":
                logger.error(f"WS Error: {data}")
            else:
                logger.debug(f"Unknown message type: {msg_type}")
                
        except Exception as e:
            logger.error(f"Message handling error: {e}")
    
    async def _handle_book_update(self, data: Dict):
        """Обработка обновления orderbook"""
        try:
            asset_id = data.get("asset_id", "")
            market = data.get("market", "")
            
            # Извлекаем лучшие цены
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            
            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 1.0
            
            # Создаем обновление
            update = MarketUpdate(
                market_id=market,
                question="",  # Будет заполнено из кэша
                outcomes=[],
                prices=[best_bid, 1.0 - best_bid],  # Примерная оценка
                timestamp=datetime.now(timezone.utc)
            )
            
            # Вызываем callbacks
            for callback in self._callbacks:
                try:
                    result = callback(update)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"Callback error: {e}")
                    
        except Exception as e:
            logger.error(f"Book update error: {e}")
    
    async def _handle_price_change(self, data: Dict):
        """Обработка изменения цены"""
        try:
            # Парсим данные
            prices = data.get("prices", [])
            outcomes = data.get("outcomes", [])
            market_id = data.get("market_id", data.get("condition_id", ""))
            
            if prices:
                update = MarketUpdate(
                    market_id=market_id,
                    question=data.get("question", ""),
                    outcomes=outcomes,
                    prices=[float(p) for p in prices],
                    timestamp=datetime.now(timezone.utc)
                )
                
                for callback in self._callbacks:
                    try:
                        result = callback(update)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"Callback error: {e}")
                        
        except Exception as e:
            logger.error(f"Price change error: {e}")
    
    async def _handle_trade(self, data: Dict):
        """Обработка сделки"""
        # Сделки могут влиять на цены
        pass
    
    async def listen(self):
        """Основной цикл прослушивания"""
        while self.running:
            try:
                if not self.ws or self.ws.closed:
                    logger.info("Reconnecting...")
                    connected = await self.connect()
                    if not connected:
                        await asyncio.sleep(self._reconnect_delay)
                        self._reconnect_delay = min(
                            self._reconnect_delay * 2,
                            self._max_reconnect_delay
                        )
                        continue
                
                async for msg in self.ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            await self._handle_message(data)
                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON: {msg.data[:100]}")
                    
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error(f"WS Error: {self.ws.exception()}")
                        break
                    
                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        logger.warning("WebSocket closed by server")
                        break
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Listen error: {e}")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    self._max_reconnect_delay
                )


class HybridMonitor:
    """
    Гибридный монитор: REST API для получения списка рынков + 
    быстрый polling для мониторинга цен
    """
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self._callbacks: List[Callable] = []
        self._markets_cache: Dict[str, Dict] = {}
        self._poll_interval = 2  # Секунды между проверками
        
    async def connect(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self.session = aiohttp.ClientSession(timeout=timeout)
        self.running = True
        return True
    
    async def close(self):
        self.running = False
        if self.session and not self.session.closed:
            await self.session.close()
    
    def on_arbitrage(self, callback: Callable):
        """Регистрация callback для арбитражных возможностей"""
        self._callbacks.append(callback)
    
    async def _fetch_markets_batch(self, offset: int = 0, limit: int = 100) -> List[Dict]:
        """Быстрое получение рынков"""
        url = f"https://gamma-api.polymarket.com/markets?active=true&closed=false&limit={limit}&offset={offset}"
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.error(f"Fetch error: {e}")
        return []
    
    async def _check_arbitrage(self, market: Dict, threshold: float = 0.999) -> Optional[MarketUpdate]:
        """Проверка рынка на арбитраж или near-arbitrage"""
        try:
            prices_str = market.get("outcomePrices")
            if not prices_str:
                return None
            
            if isinstance(prices_str, str):
                prices = json.loads(prices_str)
            else:
                prices = prices_str
            
            prices = [float(p) for p in prices]
            
            if len(prices) < 2:
                return None
            
            # Пропускаем если цены нулевые
            if all(p == 0 for p in prices):
                return None
            
            total = sum(prices)
            
            # Проверяем на арбитраж или near-arbitrage
            if total < threshold:  # Сумма меньше порога
                outcomes_str = market.get("outcomes", "[]")
                if isinstance(outcomes_str, str):
                    outcomes = json.loads(outcomes_str)
                else:
                    outcomes = outcomes_str
                
                return MarketUpdate(
                    market_id=str(market.get("id", "")),
                    question=market.get("question", ""),
                    outcomes=outcomes,
                    prices=prices,
                    timestamp=datetime.now(timezone.utc)
                )
        except Exception as e:
            logger.debug(f"Check error: {e}")
        
        return None
    
    async def _fetch_batch_parallel(self, offsets: List[int], limit: int = 100) -> List[Dict]:
        """Параллельное получение нескольких страниц"""
        tasks = []
        for offset in offsets:
            tasks.append(self._fetch_markets_batch(offset, limit))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_markets = []
        for result in results:
            if isinstance(result, list):
                all_markets.extend(result)
        
        return all_markets
    
    async def scan_all_markets(self, threshold: float = 0.999) -> List[Dict]:
        """
        Сканирование всех рынков на арбитраж
        
        Rate Limits (GAMMA /markets):
        - 125 requests / 10 sec = 12.5 req/sec
        
        Стратегия:
        - 10 параллельных запросов
        - Пауза 1 сек между пачками (10 req/sec < 12.5 limit)
        - 18 пачек = 180 запросов = ~18 сек на полное сканирование
        """
        arbitrage_opportunities = []
        limit = 100
        total_markets = 18000
        batch_size = 20  # 20 параллельных запросов за раз
        
        # Rate limit: 125 req / 10 sec = 12.5 req/sec
        # Мы делаем 20 req за ~2 сек (включая latency), потом ждём 0.5 сек
        # Итого: 20 req / 2.5 sec = 8 req/sec (безопасно, но быстрее из-за параллелизма)
        pause_between_batches = 0.5
        
        offsets = list(range(0, total_markets, limit))
        
        for i in range(0, len(offsets), batch_size):
            batch_offsets = offsets[i:i+batch_size]
            markets = await self._fetch_batch_parallel(batch_offsets, limit)
            
            if not markets:
                break
            
            for market in markets:
                update = await self._check_arbitrage(market, threshold)
                if update:
                    arbitrage_opportunities.append({
                        "update": update,
                        "volume": float(market.get("volume", 0) or 0),
                        "liquidity": float(market.get("liquidity", 0) or 0),
                        "slug": market.get("slug", ""),
                        "end_date": market.get("endDate")
                    })
            
            # Достигли конца?
            if len(markets) < len(batch_offsets) * limit * 0.5:
                break
            
            # Rate limiting пауза
            await asyncio.sleep(pause_between_batches)
        
        arbitrage_opportunities.sort(
            key=lambda x: x["update"].arbitrage_profit,
            reverse=True
        )
        
        return arbitrage_opportunities
    
    async def monitor_loop(self, threshold: float = 0.999):
        """
        Основной цикл мониторинга
        
        Полное сканирование ~18,000 рынков занимает ~18-20 сек
        (из-за rate limits: 10 req/sec)
        
        Цикл: сканирование → обработка → минимальная пауза → повтор
        """
        logger.info(f"Starting monitoring (threshold: {threshold})")
        logger.info(f"Rate limit safe: 10 req/sec, full scan takes ~18-20 sec")
        scan_count = 0
        
        while self.running:
            try:
                start = time.time()
                scan_count += 1
                
                logger.info(f"[Scan #{scan_count}] Starting full market scan...")
                
                opportunities = await self.scan_all_markets(threshold)
                
                elapsed = time.time() - start
                
                if opportunities:
                    logger.info(f"[Scan #{scan_count}] ✅ Found {len(opportunities)} opportunities in {elapsed:.1f}s")
                    
                    for opp in opportunities:
                        for callback in self._callbacks:
                            try:
                                result = callback(opp)
                                if asyncio.iscoroutine(result):
                                    await result
                            except Exception as e:
                                logger.error(f"Callback error: {e}")
                else:
                    logger.info(f"[Scan #{scan_count}] No opportunities (scanned in {elapsed:.1f}s)")
                
                # Минимальная пауза между полными сканами
                await asyncio.sleep(2)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                await asyncio.sleep(10)


async def test_hybrid_monitor():
    """Тест гибридного монитора"""
    monitor = HybridMonitor()
    await monitor.connect()
    
    def on_arb(opp):
        update = opp["update"]
        profit = update.arbitrage_profit * 100
        print(f"\n🔥 ARBITRAGE: {update.question[:50]}...")
        print(f"   Profit: {profit:.2f}%")
        print(f"   Prices: {update.prices}")
        print(f"   Volume: ${opp['volume']:,.0f}")
    
    monitor.on_arbitrage(on_arb)
    
    print("Scanning for arbitrage...")
    opportunities = await monitor.scan_all_markets()
    
    print(f"\nFound {len(opportunities)} opportunities")
    for opp in opportunities[:10]:
        on_arb(opp)
    
    await monitor.close()


if __name__ == "__main__":
    asyncio.run(test_hybrid_monitor())
