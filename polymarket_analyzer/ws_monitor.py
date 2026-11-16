"""
Polymarket WebSocket Monitor
Real-time подписка на ВСЕ рынки через WebSocket
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Dict, List, Callable, Set
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API = "https://gamma-api.polymarket.com"


@dataclass
class MarketInfo:
    """Информация о рынке"""
    condition_id: str
    question: str
    outcomes: List[str]
    token_ids: List[str]  # asset_ids для подписки
    slug: str
    volume: float = 0.0
    liquidity: float = 0.0
    end_date: Optional[str] = None
    
    # Текущие цены (обновляются через WS)
    prices: List[float] = field(default_factory=list)
    best_bids: List[float] = field(default_factory=list)
    best_asks: List[float] = field(default_factory=list)
    
    @property
    def total_price(self) -> float:
        """Сумма лучших ask цен (для покупки обоих исходов)"""
        if self.best_asks and len(self.best_asks) >= 2:
            return sum(self.best_asks)
        return sum(self.prices) if self.prices else 1.0
    
    @property
    def arbitrage_profit(self) -> float:
        return 1.0 - self.total_price
    
    @property
    def has_arbitrage(self) -> bool:
        return self.arbitrage_profit > 0.001


class WebSocketMonitor:
    """
    WebSocket монитор для real-time арбитража
    
    Стратегия:
    1. Загружаем все рынки через REST API (один раз)
    2. Подписываемся на все token_ids через WebSocket
    3. Получаем мгновенные обновления цен
    4. Проверяем на арбитраж при каждом обновлении
    """
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.running = False
        
        # Кэш рынков: token_id -> MarketInfo
        self._markets: Dict[str, MarketInfo] = {}
        self._token_to_market: Dict[str, str] = {}  # token_id -> condition_id
        self._token_to_outcome_idx: Dict[str, int] = {}  # token_id -> outcome index
        
        # Callbacks
        self._on_arbitrage_callbacks: List[Callable] = []
        
        # Stats
        self._updates_received = 0
        self._arbitrage_found = 0
    
    async def connect(self):
        """Инициализация сессии"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
        self.running = True
        return True
    
    async def close(self):
        """Закрытие соединений"""
        self.running = False
        if self.ws and not self.ws.closed:
            await self.ws.close()
        if self.session and not self.session.closed:
            await self.session.close()
    
    def on_arbitrage(self, callback: Callable):
        """Регистрация callback для арбитража"""
        self._on_arbitrage_callbacks.append(callback)
    
    async def _fetch_all_markets(self) -> List[Dict]:
        """Загрузка всех активных рынков"""
        all_markets = []
        offset = 0
        limit = 100
        
        logger.info("Fetching all markets from GAMMA API...")
        
        while True:
            url = f"{GAMMA_API}/markets?active=true&closed=false&limit={limit}&offset={offset}"
            try:
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        if not markets:
                            break
                        all_markets.extend(markets)
                        if len(markets) < limit:
                            break
                        offset += limit
                        await asyncio.sleep(0.1)
                    else:
                        break
            except Exception as e:
                logger.error(f"Fetch error: {e}")
                break
        
        logger.info(f"Fetched {len(all_markets)} markets")
        return all_markets
    
    def _parse_market(self, market: Dict) -> Optional[MarketInfo]:
        """Парсинг рынка в MarketInfo"""
        try:
            condition_id = market.get("conditionId") or market.get("condition_id", "")
            if not condition_id:
                return None
            
            # Парсим tokens
            tokens = market.get("tokens", [])
            if not tokens or len(tokens) < 2:
                # Пробуем clobTokenIds
                clob_tokens = market.get("clobTokenIds")
                if clob_tokens:
                    if isinstance(clob_tokens, str):
                        tokens = json.loads(clob_tokens)
                    else:
                        tokens = clob_tokens
            
            if not tokens or len(tokens) < 2:
                return None
            
            # Извлекаем token_ids
            if isinstance(tokens[0], dict):
                token_ids = [t.get("token_id", "") for t in tokens]
            else:
                token_ids = tokens
            
            # Парсим outcomes
            outcomes_raw = market.get("outcomes", "[]")
            if isinstance(outcomes_raw, str):
                outcomes = json.loads(outcomes_raw)
            else:
                outcomes = outcomes_raw
            
            # Парсим prices
            prices_raw = market.get("outcomePrices", "[]")
            if isinstance(prices_raw, str):
                prices = [float(p) for p in json.loads(prices_raw)]
            else:
                prices = [float(p) for p in prices_raw] if prices_raw else []
            
            return MarketInfo(
                condition_id=condition_id,
                question=market.get("question", ""),
                outcomes=outcomes,
                token_ids=token_ids,
                slug=market.get("slug", ""),
                volume=float(market.get("volume", 0) or 0),
                liquidity=float(market.get("liquidity", 0) or 0),
                end_date=market.get("endDate"),
                prices=prices
            )
        except Exception as e:
            logger.debug(f"Parse error: {e}")
            return None
    
    async def load_markets(self):
        """Загрузка и индексация всех рынков"""
        raw_markets = await self._fetch_all_markets()
        
        for raw in raw_markets:
            market = self._parse_market(raw)
            if market and len(market.token_ids) >= 2:
                self._markets[market.condition_id] = market
                
                # Индексируем token_ids
                for idx, token_id in enumerate(market.token_ids):
                    self._token_to_market[token_id] = market.condition_id
                    self._token_to_outcome_idx[token_id] = idx
        
        logger.info(f"Indexed {len(self._markets)} markets with {len(self._token_to_market)} tokens")
        return len(self._markets)
    
    async def _connect_websocket(self):
        """Подключение к WebSocket"""
        try:
            logger.info(f"Connecting to WebSocket: {WS_URL}")
            self.ws = await self.session.ws_connect(
                WS_URL,
                heartbeat=30,
                receive_timeout=120
            )
            logger.info("WebSocket connected!")
            return True
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            return False
    
    async def _subscribe_to_markets(self):
        """Подписка на все token_ids"""
        all_tokens = list(self._token_to_market.keys())
        
        if not all_tokens:
            logger.error("No tokens to subscribe!")
            return False
        
        # Подписываемся пачками по 100 токенов
        batch_size = 100
        total_batches = (len(all_tokens) + batch_size - 1) // batch_size
        
        logger.info(f"Subscribing to {len(all_tokens)} tokens in {total_batches} batches...")
        
        for i in range(0, len(all_tokens), batch_size):
            batch = all_tokens[i:i+batch_size]
            
            message = {
                "assets_ids": batch,
                "type": "market"
            }
            
            try:
                await self.ws.send_json(message)
                await asyncio.sleep(0.1)  # Небольшая пауза между пачками
            except Exception as e:
                logger.error(f"Subscribe error: {e}")
                return False
        
        logger.info(f"Subscribed to all {len(all_tokens)} tokens!")
        return True
    
    async def _handle_message(self, data):
        """Обработка сообщения от WebSocket"""
        # Может прийти список сообщений
        if isinstance(data, list):
            for item in data:
                await self._handle_single_message(item)
        else:
            await self._handle_single_message(data)
    
    async def _handle_single_message(self, data: Dict):
        """Обработка одного сообщения"""
        if not isinstance(data, dict):
            return
            
        self._updates_received += 1
        
        event_type = data.get("event_type")
        asset_id = data.get("asset_id", "")
        
        if event_type == "book":
            await self._handle_book_update(data, asset_id)
        elif event_type == "price_change":
            await self._handle_price_change(data, asset_id)
        elif event_type == "last_trade_price":
            await self._handle_last_trade(data, asset_id)
    
    async def _handle_book_update(self, data: Dict, asset_id: str):
        """Обработка обновления orderbook"""
        condition_id = self._token_to_market.get(asset_id)
        if not condition_id:
            return
        
        market = self._markets.get(condition_id)
        if not market:
            return
        
        outcome_idx = self._token_to_outcome_idx.get(asset_id, 0)
        
        # Извлекаем лучшие цены
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        
        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 1.0
        
        # Обновляем цены в маркете
        while len(market.best_bids) <= outcome_idx:
            market.best_bids.append(0.0)
        while len(market.best_asks) <= outcome_idx:
            market.best_asks.append(1.0)
        
        market.best_bids[outcome_idx] = best_bid
        market.best_asks[outcome_idx] = best_ask
        
        # Проверяем на арбитраж
        await self._check_arbitrage(market)
    
    async def _handle_price_change(self, data: Dict, asset_id: str):
        """Обработка изменения цены"""
        condition_id = self._token_to_market.get(asset_id)
        if not condition_id:
            return
        
        market = self._markets.get(condition_id)
        if not market:
            return
        
        outcome_idx = self._token_to_outcome_idx.get(asset_id, 0)
        
        # Обновляем цену
        price = data.get("price")
        if price is not None:
            while len(market.prices) <= outcome_idx:
                market.prices.append(0.5)
            market.prices[outcome_idx] = float(price)
            
            await self._check_arbitrage(market)
    
    async def _handle_last_trade(self, data: Dict, asset_id: str):
        """Обработка последней сделки"""
        # Можно использовать для дополнительной логики
        pass
    
    async def _check_arbitrage(self, market: MarketInfo):
        """Проверка рынка на арбитраж"""
        if not market.has_arbitrage:
            return
        
        self._arbitrage_found += 1
        
        # Вызываем callbacks
        for callback in self._on_arbitrage_callbacks:
            try:
                result = callback(market)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    async def _ping_loop(self):
        """Отправка PING для поддержания соединения"""
        while self.running and self.ws and not self.ws.closed:
            try:
                await self.ws.send_str("PING")
                await asyncio.sleep(30)
            except Exception:
                break
    
    async def run(self):
        """Основной цикл"""
        # 1. Загружаем рынки
        await self.load_markets()
        
        if not self._markets:
            logger.error("No markets loaded!")
            return
        
        # 2. Подключаемся к WebSocket
        if not await self._connect_websocket():
            return
        
        # 3. Подписываемся на все токены
        if not await self._subscribe_to_markets():
            return
        
        # 4. Запускаем ping в фоне
        ping_task = asyncio.create_task(self._ping_loop())
        
        # 5. Слушаем сообщения
        logger.info("Listening for real-time updates...")
        last_stats_time = time.time()
        
        try:
            async for msg in self.ws:
                if not self.running:
                    break
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    if msg.data == "PONG":
                        continue
                    
                    try:
                        data = json.loads(msg.data)
                        await self._handle_message(data)
                    except json.JSONDecodeError:
                        pass
                
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    logger.warning(f"WebSocket closed: {msg.type}")
                    break
                
                # Статистика каждые 30 сек
                if time.time() - last_stats_time > 30:
                    logger.info(f"Stats: {self._updates_received} updates, {self._arbitrage_found} arbitrage found")
                    last_stats_time = time.time()
                    
        except asyncio.CancelledError:
            pass
        finally:
            ping_task.cancel()


async def test_ws_monitor():
    """Тест WebSocket монитора"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    monitor = WebSocketMonitor()
    await monitor.connect()
    
    def on_arb(market: MarketInfo):
        profit = market.arbitrage_profit * 100
        print(f"\n🔥 ARBITRAGE: {market.question[:50]}...")
        print(f"   Profit: {profit:.2f}%")
        print(f"   Prices: {market.prices}")
        print(f"   Best asks: {market.best_asks}")
    
    monitor.on_arbitrage(on_arb)
    
    print("Starting WebSocket monitor...")
    print("Press Ctrl+C to stop\n")
    
    try:
        await monitor.run()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await monitor.close()


if __name__ == "__main__":
    asyncio.run(test_ws_monitor())
