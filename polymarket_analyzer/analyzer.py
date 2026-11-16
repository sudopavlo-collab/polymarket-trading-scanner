"""
Event Analyzer
Анализатор заканчивающихся событий с низкой ценой
"""
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from .config import ANALYZER_CONFIG, AnalyzerConfig
from .polymarket_client import PolymarketClient, Market, Event

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Результат анализа"""
    timestamp: datetime
    total_markets: int
    filtered_markets: int
    low_price_markets: List[Market]
    ending_soon_markets: List[Market]
    urgent_markets: List[Market]  # < 24 часов
    opportunities: List[Market]  # Лучшие возможности
    arbitrage_markets: List[Market]  # Арбитражные возможности (сумма цен < 1.0)
    
    @property
    def has_alerts(self) -> bool:
        return bool(self.urgent_markets or self.opportunities or self.arbitrage_markets)


@dataclass 
class MarketTracker:
    """Трекер для отслеживания изменений рынков"""
    market_id: str
    first_seen: datetime
    last_seen: datetime
    price_history: List[Tuple[datetime, float]] = field(default_factory=list)
    notified: bool = False
    
    def add_price(self, price: float):
        self.price_history.append((datetime.now(timezone.utc), price))
        self.last_seen = datetime.now(timezone.utc)
        # Храним только последние 100 записей
        if len(self.price_history) > 100:
            self.price_history = self.price_history[-100:]
    
    @property
    def price_change(self) -> Optional[float]:
        """Изменение цены с первого наблюдения"""
        if len(self.price_history) < 2:
            return None
        return self.price_history[-1][1] - self.price_history[0][1]


class EventAnalyzer:
    """Анализатор событий Polymarket"""
    
    def __init__(
        self,
        client: PolymarketClient,
        config: AnalyzerConfig = ANALYZER_CONFIG
    ):
        self.client = client
        self.config = config
        self.tracked_markets: Dict[str, MarketTracker] = {}
        self._last_analysis: Optional[AnalysisResult] = None
    
    async def fetch_and_analyze(self) -> AnalysisResult:
        """Получение и анализ всех активных рынков"""
        logger.info("Starting market analysis...")
        
        # Получаем все активные рынки
        all_markets = await self.client.get_all_active_markets(max_pages=20)
        
        if not all_markets:
            logger.warning("No markets fetched")
            return AnalysisResult(
                timestamp=datetime.now(timezone.utc),
                total_markets=0,
                filtered_markets=0,
                low_price_markets=[],
                ending_soon_markets=[],
                urgent_markets=[],
                opportunities=[],
                arbitrage_markets=[]
            )
        
        # Обновляем трекеры
        self._update_trackers(all_markets)
        
        # Фильтруем рынки
        filtered = self._filter_markets(all_markets)
        
        # Анализируем
        result = self._analyze_markets(filtered, len(all_markets))
        
        self._last_analysis = result
        
        logger.info(
            f"Analysis complete: {result.total_markets} total, "
            f"{result.filtered_markets} filtered, "
            f"{len(result.low_price_markets)} low price, "
            f"{len(result.urgent_markets)} urgent"
        )
        
        return result
    
    def _update_trackers(self, markets: List[Market]):
        """Обновление трекеров рынков"""
        now = datetime.now(timezone.utc)
        
        for market in markets:
            if market.id not in self.tracked_markets:
                self.tracked_markets[market.id] = MarketTracker(
                    market_id=market.id,
                    first_seen=now,
                    last_seen=now
                )
            
            tracker = self.tracked_markets[market.id]
            tracker.add_price(market.min_price)
    
    def _filter_markets(self, markets: List[Market]) -> List[Market]:
        """Фильтрация рынков по критериям"""
        filtered = []
        
        for market in markets:
            # Пропускаем закрытые
            if market.closed:
                continue
            
            # Пропускаем без цен
            if not market.outcome_prices:
                continue
            
            # Пропускаем если все цены 0
            if all(p == 0 for p in market.outcome_prices):
                continue
            
            # Фильтр по минимальному объему
            if market.volume < self.config.min_volume:
                continue
            
            # Фильтр по дате окончания (если есть дата)
            # Пропускаем только если дата слишком далеко
            if market.end_date:
                days_until = (market.end_date - datetime.now(timezone.utc)).days
                if days_until > self.config.days_until_end:
                    continue
                # Пропускаем уже завершенные
                if days_until < -1:
                    continue
            
            filtered.append(market)
        
        return filtered
    
    def _analyze_markets(
        self,
        markets: List[Market],
        total_count: int
    ) -> AnalysisResult:
        """Анализ отфильтрованных рынков"""
        low_price_markets = []
        ending_soon_markets = []
        urgent_markets = []
        opportunities = []
        arbitrage_markets = []
        
        for market in markets:
            min_price = market.min_price
            time_until_end = market.time_until_end
            total_price = market.total_price
            
            # ГЛАВНОЕ: Арбитражные возможности (сумма цен < 1.0)
            if market.has_arbitrage:
                arbitrage_markets.append(market)
            
            # Рынки с низкой суммой цен (< threshold)
            if total_price < self.config.price_threshold:
                low_price_markets.append(market)
                
                # Срочные (< 24 часов)
                if time_until_end is not None and time_until_end < 24:
                    urgent_markets.append(market)
                
                # Заканчивающиеся скоро (< 7 дней)
                if time_until_end is not None and time_until_end < 168:
                    ending_soon_markets.append(market)
        
        # Определяем лучшие возможности
        opportunities = self._find_opportunities(low_price_markets)
        
        # Сортируем арбитражные по прибыли (от большей к меньшей)
        arbitrage_markets = sorted(
            arbitrage_markets,
            key=lambda m: m.arbitrage_profit,
            reverse=True
        )
        
        return AnalysisResult(
            timestamp=datetime.now(timezone.utc),
            total_markets=total_count,
            filtered_markets=len(markets),
            low_price_markets=sorted(low_price_markets, key=lambda m: m.total_price),
            ending_soon_markets=sorted(
                ending_soon_markets,
                key=lambda m: m.time_until_end if m.time_until_end else float('inf')
            ),
            urgent_markets=sorted(
                urgent_markets,
                key=lambda m: m.time_until_end if m.time_until_end else float('inf')
            ),
            opportunities=opportunities,
            arbitrage_markets=arbitrage_markets
        )
    
    def _find_opportunities(self, markets: List[Market]) -> List[Market]:
        """Поиск лучших возможностей"""
        scored_markets = []
        
        for market in markets:
            score = self._calculate_opportunity_score(market)
            if score > 0:
                scored_markets.append((score, market))
        
        # Сортируем по score и возвращаем топ-10
        scored_markets.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored_markets[:10]]
    
    def _calculate_opportunity_score(self, market: Market) -> float:
        """Расчет score возможности"""
        score = 0.0
        
        min_price = market.min_price
        time_until_end = market.time_until_end
        
        # Бонус за очень низкую цену
        if min_price < 0.05:
            score += 50
        elif min_price < 0.1:
            score += 30
        elif min_price < 0.2:
            score += 20
        elif min_price < 0.5:
            score += 10
        
        # Бонус за скорое окончание
        if time_until_end is not None:
            if time_until_end < 6:
                score += 40
            elif time_until_end < 24:
                score += 30
            elif time_until_end < 72:
                score += 20
            elif time_until_end < 168:
                score += 10
        
        # Бонус за объем
        if market.volume > 100000:
            score += 20
        elif market.volume > 10000:
            score += 10
        elif market.volume > 1000:
            score += 5
        
        # Бонус за ликвидность
        if market.liquidity > 10000:
            score += 10
        elif market.liquidity > 1000:
            score += 5
        
        # Проверяем изменение цены
        tracker = self.tracked_markets.get(market.id)
        if tracker and tracker.price_change is not None:
            # Бонус если цена падает
            if tracker.price_change < -0.05:
                score += 15
            elif tracker.price_change < 0:
                score += 5
        
        return score
    
    def get_new_low_price_markets(self) -> List[Market]:
        """Получение новых рынков с низкой ценой (еще не уведомленных)"""
        if not self._last_analysis:
            return []
        
        new_markets = []
        for market in self._last_analysis.low_price_markets:
            tracker = self.tracked_markets.get(market.id)
            if tracker and not tracker.notified:
                new_markets.append(market)
        
        return new_markets
    
    def mark_as_notified(self, market_ids: List[str]):
        """Отметить рынки как уведомленные"""
        for market_id in market_ids:
            if market_id in self.tracked_markets:
                self.tracked_markets[market_id].notified = True
    
    def get_price_drops(self, threshold: float = -0.1) -> List[Tuple[Market, float]]:
        """Получение рынков с падением цены"""
        if not self._last_analysis:
            return []
        
        drops = []
        for market in self._last_analysis.low_price_markets:
            tracker = self.tracked_markets.get(market.id)
            if tracker and tracker.price_change is not None:
                if tracker.price_change < threshold:
                    drops.append((market, tracker.price_change))
        
        return sorted(drops, key=lambda x: x[1])
    
    def get_statistics(self) -> Dict:
        """Получение статистики анализа"""
        if not self._last_analysis:
            return {}
        
        result = self._last_analysis
        
        return {
            "timestamp": result.timestamp.isoformat(),
            "total_markets": result.total_markets,
            "filtered_markets": result.filtered_markets,
            "low_price_count": len(result.low_price_markets),
            "ending_soon_count": len(result.ending_soon_markets),
            "urgent_count": len(result.urgent_markets),
            "opportunities_count": len(result.opportunities),
            "tracked_markets": len(self.tracked_markets),
            "price_ranges": self._get_price_distribution(result.low_price_markets)
        }
    
    def _get_price_distribution(self, markets: List[Market]) -> Dict[str, int]:
        """Распределение по ценовым диапазонам"""
        distribution = {
            "0-5%": 0,
            "5-10%": 0,
            "10-20%": 0,
            "20-50%": 0,
            "50-100%": 0
        }
        
        for market in markets:
            price = market.min_price
            if price < 0.05:
                distribution["0-5%"] += 1
            elif price < 0.1:
                distribution["5-10%"] += 1
            elif price < 0.2:
                distribution["10-20%"] += 1
            elif price < 0.5:
                distribution["20-50%"] += 1
            else:
                distribution["50-100%"] += 1
        
        return distribution


async def test_analyzer():
    """Тестирование анализатора"""
    async with PolymarketClient() as client:
        analyzer = EventAnalyzer(client)
        
        print("Running analysis...")
        result = await analyzer.fetch_and_analyze()
        
        print(f"\n=== Analysis Results ===")
        print(f"Total markets: {result.total_markets}")
        print(f"Filtered: {result.filtered_markets}")
        print(f"Low price (<$1): {len(result.low_price_markets)}")
        print(f"Ending soon: {len(result.ending_soon_markets)}")
        print(f"Urgent (<24h): {len(result.urgent_markets)}")
        print(f"Opportunities: {len(result.opportunities)}")
        
        print("\n=== Top Opportunities ===")
        for i, market in enumerate(result.opportunities[:5], 1):
            print(f"{i}. {market.question[:60]}...")
            print(f"   Price: ${market.min_price:.4f} | Time: {market.time_until_end:.1f}h")
        
        print("\n=== Statistics ===")
        stats = analyzer.get_statistics()
        print(f"Price distribution: {stats.get('price_ranges', {})}")


if __name__ == "__main__":
    asyncio.run(test_analyzer())
