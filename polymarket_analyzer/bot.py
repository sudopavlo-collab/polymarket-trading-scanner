"""
Polymarket Analyzer Bot
Главный модуль бота для мониторинга и уведомлений
"""
import logging
import asyncio
import signal
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

from .config import (
    ANALYZER_CONFIG,
    LOG_LEVEL,
    LOG_FORMAT
)
from .polymarket_client import PolymarketClient
from .telegram_notifier import TelegramNotifier
from .analyzer import EventAnalyzer, AnalysisResult

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("polymarket_analyzer.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


class PolymarketBot:
    """Главный класс бота"""
    
    def __init__(self):
        self.client: Optional[PolymarketClient] = None
        self.notifier: Optional[TelegramNotifier] = None
        self.analyzer: Optional[EventAnalyzer] = None
        self.running = False
        self._last_summary_time: Optional[datetime] = None
        self._summary_interval = timedelta(hours=6)  # Сводка каждые 6 часов
        self._notified_markets: set = set()
    
    async def start(self):
        """Запуск бота"""
        logger.info("Starting Polymarket Analyzer Bot...")
        
        # Инициализация компонентов
        self.client = PolymarketClient()
        await self.client.connect()
        
        self.notifier = TelegramNotifier()
        await self.notifier.connect()
        
        self.analyzer = EventAnalyzer(self.client, ANALYZER_CONFIG)
        
        self.running = True
        
        # Отправляем уведомление о запуске
        await self.notifier.notify_startup()
        
        logger.info("Bot started successfully")
        
        # Первый анализ
        await self._run_analysis_cycle()
        
        # Основной цикл
        await self._main_loop()
    
    async def stop(self):
        """Остановка бота"""
        logger.info("Stopping bot...")
        self.running = False
        
        if self.client:
            await self.client.close()
        
        if self.notifier:
            await self.notifier.send_message("🛑 <b>Polymarket Analyzer остановлен</b>")
            await self.notifier.close()
        
        logger.info("Bot stopped")
    
    async def _main_loop(self):
        """Основной цикл работы"""
        while self.running:
            try:
                await asyncio.sleep(ANALYZER_CONFIG.check_interval)
                await self._run_analysis_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                try:
                    await self.notifier.notify_error(f"Ошибка в главном цикле: {str(e)[:200]}")
                except:
                    pass
                await asyncio.sleep(30)  # Пауза перед повторной попыткой
    
    async def _run_analysis_cycle(self):
        """Один цикл анализа"""
        logger.info("Running analysis cycle...")
        
        try:
            # Выполняем анализ
            result = await self.analyzer.fetch_and_analyze()
            
            # Обрабатываем результаты
            await self._process_results(result)
            
            # Проверяем, нужна ли сводка
            await self._check_summary_needed(result)
            
        except Exception as e:
            logger.error(f"Analysis cycle error: {e}", exc_info=True)
            raise
    
    async def _process_results(self, result: AnalysisResult):
        """Обработка результатов анализа"""
        notifications_sent = 0
        max_notifications_per_cycle = 10  # Максимум уведомлений за цикл
        
        # 1. ПРИОРИТЕТ: Арбитражные возможности (сумма цен < 1.0)
        for market in result.arbitrage_markets:
            if notifications_sent >= max_notifications_per_cycle:
                break
            arb_key = f"arb_{market.id}"
            if arb_key not in self._notified_markets:
                profit_pct = market.arbitrage_profit * 100
                logger.info(f"🔥 ARBITRAGE: {market.question[:50]}... Profit: {profit_pct:.2f}%")
                await self.notifier.notify_arbitrage(market)
                self._notified_markets.add(arb_key)
                notifications_sent += 1
                await asyncio.sleep(1)  # Rate limiting
        
        # 2. Уведомления о срочных событиях с низкой суммой цен (< 24 часов)
        for market in result.urgent_markets:
            if notifications_sent >= max_notifications_per_cycle:
                break
            if market.id not in self._notified_markets:
                # Только если сумма цен < 0.99 (есть хоть какой-то спред)
                if market.total_price < 0.99:
                    logger.info(f"Sending urgent alert for: {market.question[:50]}...")
                    await self.notifier.notify_market(market)
                    self._notified_markets.add(market.id)
                    notifications_sent += 1
                    await asyncio.sleep(1)
        
        # Логируем статистику
        stats = self.analyzer.get_statistics()
        logger.info(f"Analysis stats: {stats}")
        logger.info(f"Arbitrage opportunities found: {len(result.arbitrage_markets)}")
        logger.info(f"Notifications sent this cycle: {notifications_sent}")
    
    async def _check_summary_needed(self, result: AnalysisResult):
        """Проверка необходимости отправки сводки"""
        now = datetime.now(timezone.utc)
        
        # Отправляем сводку каждые N часов
        if self._last_summary_time is None or \
           (now - self._last_summary_time) >= self._summary_interval:
            
            logger.info("Sending periodic summary...")
            
            # Формируем сводку
            summary_markets = result.low_price_markets[:20]  # Топ 20
            await self.notifier.notify_summary(summary_markets)
            
            self._last_summary_time = now
            
            # Очищаем кэш уведомлений (чтобы повторно уведомлять о старых)
            # Оставляем только те, что были уведомлены в последние 2 часа
            # (упрощенная логика - просто очищаем)
            if len(self._notified_markets) > 1000:
                self._notified_markets.clear()
    
    async def force_summary(self):
        """Принудительная отправка сводки"""
        if self.analyzer and self.notifier:
            result = await self.analyzer.fetch_and_analyze()
            await self.notifier.notify_summary(result.low_price_markets[:20])


async def run_bot():
    """Запуск бота с обработкой сигналов"""
    bot = PolymarketBot()
    
    # Обработка сигналов для graceful shutdown
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(bot.stop())
    
    # Регистрируем обработчики сигналов (только для Unix)
    if sys.platform != 'win32':
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)
    finally:
        await bot.stop()


def main():
    """Точка входа"""
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║         POLYMARKET EVENT ANALYZER BOT v1.0                ║
    ║                                                           ║
    ║  Мониторинг событий с низкой ценой (<$1.00)              ║
    ║  Уведомления в Telegram о заканчивающихся событиях       ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nБот остановлен пользователем")
    except Exception as e:
        print(f"\nОшибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
