"""
WebSocket Arbitrage Bot
Real-time мониторинг через WebSocket - мгновенные уведомления!
"""
import asyncio
import logging
import sys
from typing import Set, Dict

from .config import LOG_LEVEL, LOG_FORMAT
from .telegram_notifier import TelegramNotifier
from .ws_monitor import WebSocketMonitor, MarketInfo

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ws_arbitrage_bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


class WebSocketArbitrageBot:
    """WebSocket бот для мгновенного арбитража"""
    
    def __init__(self, min_profit: float = 0.001):
        self.monitor = WebSocketMonitor()
        self.notifier: TelegramNotifier = None
        self.min_profit = min_profit
        self.running = False
        
        # Кэш уведомлений
        self._notified: Set[str] = set()
    
    async def start(self):
        """Запуск бота"""
        logger.info("🚀 Starting WebSocket Arbitrage Bot...")
        
        # Инициализация
        await self.monitor.connect()
        
        self.notifier = TelegramNotifier()
        await self.notifier.connect()
        
        self.running = True
        
        # Уведомление о запуске
        await self.notifier.send_message(
            "🚀 <b>WebSocket Arbitrage Bot</b>\n\n"
            "⚡ Real-time мониторинг\n"
            "📊 Подписка на ВСЕ рынки\n"
            f"📈 Мин. прибыль: {self.min_profit*100:.1f}%\n\n"
            "Загружаю рынки..."
        )
        
        # Регистрируем callback
        self.monitor.on_arbitrage(self._on_arbitrage)
        
        logger.info("Bot initialized, starting WebSocket monitor...")
        
        # Запускаем мониторинг
        await self.monitor.run()
    
    async def stop(self):
        """Остановка бота"""
        logger.info("Stopping bot...")
        self.running = False
        
        await self.monitor.close()
        
        if self.notifier:
            await self.notifier.send_message("🛑 <b>WebSocket Bot остановлен</b>")
            await self.notifier.close()
    
    async def _on_arbitrage(self, market: MarketInfo):
        """Callback при обнаружении арбитража"""
        profit = market.arbitrage_profit
        profit_pct = profit * 100
        
        # Фильтр по минимальной прибыли
        if profit < self.min_profit:
            return
        
        # Проверяем дубликаты
        notification_key = f"{market.condition_id}_{int(profit_pct)}"
        if notification_key in self._notified:
            return
        
        # Логируем
        logger.info(f"💰 ARBITRAGE: {market.question[:40]}... | Profit: {profit_pct:.2f}%")
        
        # Формируем сообщение
        prices_str = ""
        if market.outcomes:
            for i, outcome in enumerate(market.outcomes):
                if i < len(market.best_asks):
                    price = market.best_asks[i]
                elif i < len(market.prices):
                    price = market.prices[i]
                else:
                    price = 0.5
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
        
        url = f"https://polymarket.com/event/{market.slug}" if market.slug else ""
        
        message = f"""
⚡ <b>{level}</b>

<b>{market.question}</b>

<b>📊 Цены (ask):</b>{prices_str}

<b>💵 Сумма:</b> ${market.total_price:.4f} ({market.total_price*100:.2f}%)
<b>📈 ПРИБЫЛЬ: ${profit:.4f} ({profit_pct:.2f}%)</b>

<b>📊 Объем:</b> ${market.volume:,.0f}
<b>💧 Ликвидность:</b> ${market.liquidity:,.0f}

<b>🔗</b> {url}

<i>⚡ МГНОВЕННОЕ уведомление через WebSocket!</i>
"""
        
        # Отправляем
        success = await self.notifier.send_message(message.strip())
        
        if success:
            self._notified.add(notification_key)
            
            # Очищаем старые
            if len(self._notified) > 500:
                self._notified = set(list(self._notified)[-250:])


async def run_ws_bot(min_profit: float = 0.001):
    """Запуск WebSocket бота"""
    bot = WebSocketArbitrageBot(min_profit=min_profit)
    
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
    
    parser = argparse.ArgumentParser(description="WebSocket Arbitrage Bot")
    parser.add_argument("--min-profit", "-p", type=float, default=0.001,
                        help="Мин. прибыль (default: 0.001 = 0.1%%)")
    
    args = parser.parse_args()
    
    print(f"""
    ╔═══════════════════════════════════════════════════════════╗
    ║       ⚡ WEBSOCKET ARBITRAGE BOT ⚡                       ║
    ╠═══════════════════════════════════════════════════════════╣
    ║  📡 Real-time WebSocket подписка                         ║
    ║  📊 Все ~35,000 токенов                                  ║
    ║  ⚡ Мгновенные уведомления                               ║
    ║  📈 Мин. прибыль: {args.min_profit*100:.1f}%                                  ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    try:
        asyncio.run(run_ws_bot(min_profit=args.min_profit))
    except KeyboardInterrupt:
        print("\nБот остановлен")


if __name__ == "__main__":
    main()
