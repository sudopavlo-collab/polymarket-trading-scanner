"""
Closing Events Bot
Ловим события за минуты до закрытия с ценой ~99%
Почти гарантированная прибыль на "вершках"!
"""
import asyncio
import logging
import sys
import json
from datetime import datetime, timezone, timedelta
from typing import Set, Dict, List, Optional
from dataclasses import dataclass

import aiohttp

from .config import LOG_LEVEL, LOG_FORMAT
from .telegram_notifier import TelegramNotifier

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("closing_bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


@dataclass
class ClosingMarket:
    """Рынок близкий к закрытию"""
    condition_id: str
    question: str
    outcomes: List[str]
    prices: List[float]
    end_date: datetime
    volume: float
    liquidity: float
    slug: str
    
    @property
    def minutes_until_close(self) -> float:
        """Минут до закрытия"""
        delta = self.end_date - datetime.now(timezone.utc)
        return delta.total_seconds() / 60
    
    @property
    def best_outcome_idx(self) -> int:
        """Индекс исхода с максимальной ценой"""
        if not self.prices:
            return 0
        return self.prices.index(max(self.prices))
    
    @property
    def best_price(self) -> float:
        """Лучшая цена (максимальная)"""
        return max(self.prices) if self.prices else 0.0
    
    @property
    def best_outcome(self) -> str:
        """Название лучшего исхода"""
        idx = self.best_outcome_idx
        return self.outcomes[idx] if idx < len(self.outcomes) else "Unknown"
    
    @property
    def expected_profit(self) -> float:
        """Ожидаемая прибыль (1.0 - цена)"""
        return 1.0 - self.best_price
    
    @property
    def expected_profit_pct(self) -> float:
        """Ожидаемая прибыль в %"""
        return self.expected_profit * 100


class ClosingEventsBot:
    """
    Бот для ловли событий перед закрытием
    
    Стратегия:
    1. Мониторим события которые закрываются в ближайшие X минут
    2. Ищем исходы с ценой > 95% (почти определённый победитель)
    3. Покупаем и ждём закрытия
    4. Получаем $1 за каждый выигрышный контракт
    
    Риски:
    - Событие может разрешиться неожиданно
    - Цена может упасть перед закрытием
    """
    
    def __init__(
        self,
        min_price: float = 0.95,      # Минимальная цена (95%)
        max_minutes: float = 5.0,      # Максимум минут до закрытия
        min_volume: float = 1000.0,    # Минимальный объём
        check_interval: float = 30.0   # Интервал проверки (сек)
    ):
        self.min_price = min_price
        self.max_minutes = max_minutes
        self.min_volume = min_volume
        self.check_interval = check_interval
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.notifier: Optional[TelegramNotifier] = None
        self.running = False
        
        # Кэш уведомлений
        self._notified: Set[str] = set()
        self._scan_count = 0
    
    async def connect(self):
        """Инициализация"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
        
        self.notifier = TelegramNotifier()
        await self.notifier.connect()
        
        self.running = True
        return True
    
    async def close(self):
        """Закрытие"""
        self.running = False
        if self.session and not self.session.closed:
            await self.session.close()
        if self.notifier:
            await self.notifier.close()
    
    async def _fetch_markets(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Получение рынков"""
        url = f"{GAMMA_API}/markets?active=true&closed=false&limit={limit}&offset={offset}"
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.error(f"Fetch error: {e}")
        return []
    
    def _parse_market(self, data: Dict) -> Optional[ClosingMarket]:
        """Парсинг рынка"""
        try:
            # Проверяем дату окончания
            end_date_str = data.get("endDate")
            if not end_date_str:
                return None
            
            # Парсим дату
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            
            # Проверяем что событие скоро закроется
            now = datetime.now(timezone.utc)
            minutes_until = (end_date - now).total_seconds() / 60
            
            if minutes_until < 0 or minutes_until > self.max_minutes:
                return None
            
            # Парсим цены
            prices_raw = data.get("outcomePrices", "[]")
            if isinstance(prices_raw, str):
                prices = [float(p) for p in json.loads(prices_raw)]
            else:
                prices = [float(p) for p in prices_raw] if prices_raw else []
            
            if not prices:
                return None
            
            # Проверяем максимальную цену
            max_price = max(prices)
            if max_price < self.min_price:
                return None
            
            # Парсим outcomes
            outcomes_raw = data.get("outcomes", "[]")
            if isinstance(outcomes_raw, str):
                outcomes = json.loads(outcomes_raw)
            else:
                outcomes = outcomes_raw
            
            # Проверяем объём
            volume = float(data.get("volume", 0) or 0)
            if volume < self.min_volume:
                return None
            
            return ClosingMarket(
                condition_id=data.get("conditionId", data.get("condition_id", "")),
                question=data.get("question", ""),
                outcomes=outcomes,
                prices=prices,
                end_date=end_date,
                volume=volume,
                liquidity=float(data.get("liquidity", 0) or 0),
                slug=data.get("slug", "")
            )
        except Exception as e:
            logger.debug(f"Parse error: {e}")
            return None
    
    async def scan_closing_markets(self) -> List[ClosingMarket]:
        """Сканирование рынков близких к закрытию"""
        closing_markets = []
        offset = 0
        limit = 100
        max_pages = 200
        
        for _ in range(max_pages):
            markets = await self._fetch_markets(limit, offset)
            if not markets:
                break
            
            for data in markets:
                market = self._parse_market(data)
                if market:
                    closing_markets.append(market)
            
            if len(markets) < limit:
                break
            
            offset += limit
            await asyncio.sleep(0.05)
        
        # Сортируем по времени до закрытия
        closing_markets.sort(key=lambda m: m.minutes_until_close)
        
        return closing_markets
    
    async def _notify_opportunity(self, market: ClosingMarket):
        """Отправка уведомления"""
        # Проверяем дубликаты
        key = f"{market.condition_id}"
        if key in self._notified:
            return
        
        minutes = market.minutes_until_close
        price = market.best_price
        profit_pct = market.expected_profit_pct
        
        # Уровень срочности
        if minutes < 1:
            urgency = "🚨🚨🚨 СРОЧНО! < 1 мин"
        elif minutes < 2:
            urgency = "🚨🚨 ОЧЕНЬ СРОЧНО!"
        elif minutes < 3:
            urgency = "🚨 СРОЧНО!"
        else:
            urgency = "⏰ Скоро закрытие"
        
        # Уровень уверенности
        if price >= 0.99:
            confidence = "💯 99%+ уверенность"
        elif price >= 0.97:
            confidence = "🔥 97%+ уверенность"
        elif price >= 0.95:
            confidence = "✅ 95%+ уверенность"
        else:
            confidence = f"📊 {price*100:.0f}% уверенность"
        
        url = f"https://polymarket.com/event/{market.slug}" if market.slug else ""
        
        # Формируем цены
        prices_str = ""
        for i, (outcome, price_val) in enumerate(zip(market.outcomes, market.prices)):
            marker = "👉 " if i == market.best_outcome_idx else "   "
            prices_str += f"\n{marker}{outcome}: ${price_val:.4f} ({price_val*100:.1f}%)"
        
        message = f"""
{urgency}

<b>🎯 {market.question}</b>

<b>{confidence}</b>

<b>📊 Цены:</b>{prices_str}

<b>⏱ До закрытия:</b> {minutes:.1f} мин
<b>💰 Ожидаемая прибыль:</b> {profit_pct:.2f}%
<b>📊 Объём:</b> ${market.volume:,.0f}

<b>🔗</b> {url}

<i>Купи "{market.best_outcome}" по ${market.best_price:.4f} → получи $1.00</i>
"""
        
        success = await self.notifier.send_message(message.strip())
        
        if success:
            self._notified.add(key)
            logger.info(f"Notified: {market.question[:40]}... ({minutes:.1f} min, {price*100:.1f}%)")
    
    async def run(self):
        """Основной цикл"""
        logger.info("Starting Closing Events Bot...")
        logger.info(f"Settings: min_price={self.min_price}, max_minutes={self.max_minutes}, min_volume={self.min_volume}")
        
        await self.notifier.send_message(
            "🎯 <b>Closing Events Bot запущен!</b>\n\n"
            f"⏱ Ищу события за {self.max_minutes} мин до закрытия\n"
            f"💰 Мин. цена: {self.min_price*100:.0f}%\n"
            f"📊 Мин. объём: ${self.min_volume:,.0f}\n"
            f"🔄 Проверка каждые {self.check_interval} сек"
        )
        
        while self.running:
            try:
                self._scan_count += 1
                
                closing = await self.scan_closing_markets()
                
                if closing:
                    logger.info(f"[Scan #{self._scan_count}] Found {len(closing)} closing markets!")
                    
                    for market in closing:
                        await self._notify_opportunity(market)
                else:
                    if self._scan_count % 10 == 0:
                        logger.info(f"[Scan #{self._scan_count}] No closing markets found")
                
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                await asyncio.sleep(10)


async def run_closing_bot(
    min_price: float = 0.95,
    max_minutes: float = 5.0,
    min_volume: float = 1000.0,
    check_interval: float = 30.0
):
    """Запуск бота"""
    bot = ClosingEventsBot(
        min_price=min_price,
        max_minutes=max_minutes,
        min_volume=min_volume,
        check_interval=check_interval
    )
    
    try:
        await bot.connect()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)
    finally:
        await bot.close()


def main():
    """Точка входа"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Closing Events Bot")
    parser.add_argument("--min-price", "-p", type=float, default=0.95,
                        help="Мин. цена исхода (default: 0.95 = 95%%)")
    parser.add_argument("--max-minutes", "-m", type=float, default=5.0,
                        help="Макс. минут до закрытия (default: 5)")
    parser.add_argument("--min-volume", "-v", type=float, default=1000.0,
                        help="Мин. объём (default: 1000)")
    parser.add_argument("--interval", "-i", type=float, default=30.0,
                        help="Интервал проверки в сек (default: 30)")
    
    args = parser.parse_args()
    
    print(f"""
    ╔═══════════════════════════════════════════════════════════╗
    ║       🎯 CLOSING EVENTS BOT 🎯                           ║
    ╠═══════════════════════════════════════════════════════════╣
    ║  Ловим "вершки" перед закрытием!                         ║
    ║                                                           ║
    ║  ⏱ До закрытия: < {args.max_minutes:.0f} мин                                ║
    ║  💰 Мин. цена: {args.min_price*100:.0f}%                                     ║
    ║  📊 Мин. объём: ${args.min_volume:,.0f}                                ║
    ║  🔄 Проверка: каждые {args.interval:.0f} сек                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    try:
        asyncio.run(run_closing_bot(
            min_price=args.min_price,
            max_minutes=args.max_minutes,
            min_volume=args.min_volume,
            check_interval=args.interval
        ))
    except KeyboardInterrupt:
        print("\nБот остановлен")


if __name__ == "__main__":
    main()
