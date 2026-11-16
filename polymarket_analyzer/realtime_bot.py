"""
Real-time Arbitrage Bot
Быстрый мониторинг арбитражных возможностей
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Set, Dict

from .config import LOG_LEVEL, LOG_FORMAT
from .telegram_notifier import TelegramNotifier
from .websocket_client import HybridMonitor, MarketUpdate

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("arbitrage_bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


class RealtimeArbitrageBot:
    """Real-time бот для арбитража"""
    
    def __init__(
        self, 
        poll_interval: float = 1.0, 
        min_profit: float = 0.001,
        near_arbitrage_threshold: float = 0.998  # Уведомлять если сумма < 99.5%
    ):
        self.monitor = HybridMonitor()
        self.monitor._poll_interval = poll_interval
        self.notifier: TelegramNotifier = None
        self.min_profit = min_profit  # Минимальная прибыль (0.1%)
        self.near_threshold = near_arbitrage_threshold  # Порог для "почти арбитража"
        self.running = False
        
        # Кэш уведомлений (чтобы не спамить)
        self._notified: Set[str] = set()
        self._last_prices: Dict[str, float] = {}  # Для отслеживания изменений
        self._scan_count = 0
        
    async def start(self):
        """Запуск бота"""
        logger.info("🚀 Starting Real-time Arbitrage Bot...")
        
        # Инициализация
        await self.monitor.connect()
        
        self.notifier = TelegramNotifier()
        await self.notifier.connect()
        
        self.running = True
        
        # Уведомление о запуске
        await self.notifier.send_message(
            "🚀 <b>Arbitrage Bot запущен!</b>\n\n"
            "📊 Рынков: 18,000\n"
            "⏱ Скан: 20-25 сек\n"
            f"💰 Порог: {self.near_threshold*100:.1f}%\n"
            f"📈 Мин. прибыль: {self.min_profit*100:.1f}%"
        )
        
        # Регистрируем callback
        self.monitor.on_arbitrage(self._on_arbitrage)
        
        logger.info("Bot started!")
        
        # Запускаем мониторинг с порогом near_threshold
        await self.monitor.monitor_loop(threshold=self.near_threshold)
    
    async def stop(self):
        """Остановка бота"""
        logger.info("Stopping bot...")
        self.running = False
        
        await self.monitor.close()
        
        if self.notifier:
            await self.notifier.send_message("🛑 <b>Arbitrage Bot остановлен</b>")
            await self.notifier.close()
    
    async def _on_arbitrage(self, opp: Dict):
        """Callback при обнаружении арбитража"""
        update: MarketUpdate = opp["update"]
        profit = update.arbitrage_profit
        profit_pct = profit * 100
        
        # Фильтр по минимальной прибыли
        if profit < self.min_profit:
            return
        
        market_id = update.market_id
        
        # Проверяем, изменилась ли цена значительно
        prev_total = self._last_prices.get(market_id, 1.0)
        curr_total = update.total_price
        
        # Обновляем кэш цен
        self._last_prices[market_id] = curr_total
        
        # Уведомляем только если:
        # 1. Это новая возможность
        # 2. Или прибыль увеличилась значительно
        notification_key = f"{market_id}_{int(profit_pct)}"  # Группируем по % прибыли
        
        if notification_key in self._notified:
            return
        
        # Логируем
        logger.info(f"💰 ARBITRAGE: {update.question[:40]}... | Profit: {profit_pct:.2f}%")
        
        # Формируем сообщение
        prices_str = ""
        if update.outcomes and update.prices:
            for outcome, price in zip(update.outcomes, update.prices):
                prices_str += f"\n   💵 {outcome}: ${price:.4f} ({price*100:.1f}%)"
        
        # Уровень возможности
        if profit_pct > 5:
            level = "🔥🔥🔥 ОТЛИЧНЫЙ АРБИТРАЖ!"
        elif profit_pct > 2:
            level = "🔥🔥 ХОРОШИЙ АРБИТРАЖ!"
        elif profit_pct > 1:
            level = "🔥 АРБИТРАЖ!"
        else:
            level = "💡 Арбитраж"
        
        slug = opp.get("slug", "")
        url = f"https://polymarket.com/event/{slug}" if slug else ""
        volume = opp.get("volume", 0)
        liquidity = opp.get("liquidity", 0)
        
        message = f"""
⚡ <b>{level}</b>

<b>❓ {update.question}</b>

<b>📊 Цены:</b>{prices_str}

<b>💵 Сумма:</b> ${update.total_price:.4f} ({update.total_price*100:.2f}%)
<b>✅ Выигрыш:</b> $1.00
<b>📈 ПРИБЫЛЬ: ${profit:.4f} ({profit_pct:.2f}%)</b>

<b>📊 Объем:</b> ${volume:,.0f}
<b>💧 Ликвидность:</b> ${liquidity:,.0f}

<b>🔗</b> {url}

<i>⚡ Купи ОБА исхода СЕЙЧАС!</i>
"""
        
        # Отправляем
        success = await self.notifier.send_message(message.strip())
        
        if success:
            self._notified.add(notification_key)
            
            # Очищаем старые уведомления (держим только последние 500)
            if len(self._notified) > 500:
                self._notified = set(list(self._notified)[-250:])


async def run_realtime_bot(
    min_profit: float = 0.001,
    near_threshold: float = 0.999  # Ищем если сумма < 99.9%
):
    """Запуск real-time бота"""
    bot = RealtimeArbitrageBot(
        min_profit=min_profit,
        near_arbitrage_threshold=near_threshold
    )
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)
    finally:
        await bot.stop()


def main():
    """Точка входа"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Real-time Arbitrage Bot")
    parser.add_argument("--threshold", "-t", type=float, default=0.999,
                        help="Порог суммы цен (default: 0.999 = 99.9%%)")
    parser.add_argument("--min-profit", "-p", type=float, default=0.001,
                        help="Мин. прибыль (default: 0.001 = 0.1%%)")
    
    args = parser.parse_args()
    
    print(f"""
    ╔═══════════════════════════════════════════════════════════╗
    ║       ⚡ POLYMARKET ARBITRAGE BOT ⚡                      ║
    ╠═══════════════════════════════════════════════════════════╣
    ║  📊 Рынков: ~18,000                                       ║
    ║  ⏱ Полный скан: ~20-25 сек                               ║
    ║  🔄 Rate limit: 10 req/sec (safe)                        ║
    ║  💰 Порог: {args.threshold*100:.1f}%                                         ║
    ║  📈 Мин. прибыль: {args.min_profit*100:.1f}%                                  ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    try:
        asyncio.run(run_realtime_bot(
            min_profit=args.min_profit,
            near_threshold=args.threshold
        ))
    except KeyboardInterrupt:
        print("\nБот остановлен")


if __name__ == "__main__":
    main()
