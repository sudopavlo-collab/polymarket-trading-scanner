"""
CLI интерфейс для Polymarket Analyzer
Позволяет запускать различные команды
"""
import argparse
import asyncio
import sys
import logging

from .config import ANALYZER_CONFIG
from .polymarket_client import PolymarketClient
from .telegram_notifier import TelegramNotifier
from .analyzer import EventAnalyzer
from .bot import run_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def cmd_test_telegram():
    """Тест отправки в Telegram"""
    print("🧪 Тестирование Telegram...")
    
    async with TelegramNotifier() as notifier:
        success = await notifier.send_message(
            "🧪 <b>Тестовое сообщение</b>\n\n"
            "✅ Polymarket Analyzer настроен правильно!\n"
            "Бот готов к работе."
        )
        
        if success:
            print("✅ Сообщение успешно отправлено!")
        else:
            print("❌ Ошибка отправки сообщения")
            sys.exit(1)


async def cmd_test_api():
    """Тест подключения к Polymarket API"""
    print("🧪 Тестирование Polymarket API...")
    
    async with PolymarketClient() as client:
        markets = await client.get_markets(limit=5)
        
        if markets:
            print(f"✅ API работает! Получено {len(markets)} рынков:")
            for m in markets:
                print(f"  • {m.question[:60]}...")
                print(f"    Цены: {m.outcome_prices}")
        else:
            print("❌ Не удалось получить данные с API")
            sys.exit(1)


async def cmd_analyze():
    """Запуск одиночного анализа"""
    print("📊 Запуск анализа...")
    
    async with PolymarketClient() as client:
        analyzer = EventAnalyzer(client, ANALYZER_CONFIG)
        result = await analyzer.fetch_and_analyze()
        
        print(f"\n{'='*60}")
        print(f"📈 РЕЗУЛЬТАТЫ АНАЛИЗА")
        print(f"{'='*60}")
        print(f"Всего рынков: {result.total_markets}")
        print(f"Отфильтровано: {result.filtered_markets}")
        print(f"С суммой цен <$1: {len(result.low_price_markets)}")
        print(f"Заканчиваются скоро: {len(result.ending_soon_markets)}")
        print(f"Срочные (<24ч): {len(result.urgent_markets)}")
        print(f"🔥 АРБИТРАЖ: {len(result.arbitrage_markets)}")
        
        if result.arbitrage_markets:
            print(f"\n{'='*60}")
            print(f"💰 АРБИТРАЖНЫЕ ВОЗМОЖНОСТИ (сумма цен < 1.0)")
            print(f"{'='*60}")
            for i, m in enumerate(result.arbitrage_markets[:15], 1):
                hours = m.time_until_end or 0
                profit = m.arbitrage_profit * 100
                total = m.total_price
                print(f"\n  {i}. {m.question[:55]}...")
                print(f"     Сумма цен: ${total:.4f} ({total*100:.2f}%)")
                print(f"     📈 ПРИБЫЛЬ: {profit:.2f}%")
                print(f"     ⏱ Осталось: {hours:.1f}ч | Объем: ${m.volume:,.0f}")
                if m.outcomes and m.outcome_prices:
                    for outcome, price in zip(m.outcomes, m.outcome_prices):
                        print(f"        {outcome}: ${price:.4f}")
        else:
            print(f"\n❌ Арбитражных возможностей не найдено")
        
        if result.urgent_markets:
            print(f"\n🚨 СРОЧНЫЕ СОБЫТИЯ (<24 часа):")
            for i, m in enumerate(result.urgent_markets[:5], 1):
                hours = m.time_until_end or 0
                print(f"  {i}. {m.question[:50]}...")
                print(f"     Сумма: ${m.total_price:.4f} | Осталось: {hours:.1f}ч")


async def cmd_send_summary():
    """Отправка сводки в Telegram"""
    print("📤 Отправка сводки...")
    
    async with PolymarketClient() as client:
        async with TelegramNotifier() as notifier:
            analyzer = EventAnalyzer(client, ANALYZER_CONFIG)
            result = await analyzer.fetch_and_analyze()
            
            success = await notifier.notify_summary(result.low_price_markets[:20])
            
            if success:
                print("✅ Сводка отправлена!")
            else:
                print("❌ Ошибка отправки сводки")


async def cmd_list_low_price(threshold: float = 0.1):
    """Список событий с очень низкой ценой"""
    print(f"🔍 Поиск событий с ценой < ${threshold}...")
    
    async with PolymarketClient() as client:
        analyzer = EventAnalyzer(client, ANALYZER_CONFIG)
        result = await analyzer.fetch_and_analyze()
        
        low_price = [m for m in result.low_price_markets if m.min_price < threshold]
        
        print(f"\n📋 Найдено {len(low_price)} событий с ценой < ${threshold}:\n")
        
        for i, m in enumerate(low_price, 1):
            hours = m.time_until_end
            time_str = f"{hours:.1f}ч" if hours else "N/A"
            
            print(f"{i}. {m.question}")
            print(f"   💰 Мин. цена: ${m.min_price:.4f} ({m.min_price*100:.2f}%)")
            print(f"   ⏱ До окончания: {time_str}")
            print(f"   📈 Объем: ${m.volume:,.0f}")
            if m.slug:
                print(f"   🔗 https://polymarket.com/event/{m.slug}")
            print()


def main():
    """Главная функция CLI"""
    parser = argparse.ArgumentParser(
        description="Polymarket Event Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  python -m polymarket_analyzer.cli run          # Запустить бота
  python -m polymarket_analyzer.cli analyze      # Одиночный анализ
  python -m polymarket_analyzer.cli test-tg      # Тест Telegram
  python -m polymarket_analyzer.cli test-api     # Тест API
  python -m polymarket_analyzer.cli summary      # Отправить сводку
  python -m polymarket_analyzer.cli low --threshold 0.05  # Найти события <5%
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Команда")
    
    # Команда run
    subparsers.add_parser("run", help="Запустить бота в режиме мониторинга")
    
    # Команда analyze
    subparsers.add_parser("analyze", help="Выполнить одиночный анализ")
    
    # Команда test-tg
    subparsers.add_parser("test-tg", help="Тест отправки в Telegram")
    
    # Команда test-api
    subparsers.add_parser("test-api", help="Тест подключения к API")
    
    # Команда summary
    subparsers.add_parser("summary", help="Отправить сводку в Telegram")
    
    # Команда low
    low_parser = subparsers.add_parser("low", help="Показать события с низкой ценой")
    low_parser.add_argument(
        "--threshold", "-t",
        type=float,
        default=0.1,
        help="Порог цены (по умолчанию 0.1 = 10%%)"
    )
    
    args = parser.parse_args()
    
    if args.command == "run":
        asyncio.run(run_bot())
    elif args.command == "analyze":
        asyncio.run(cmd_analyze())
    elif args.command == "test-tg":
        asyncio.run(cmd_test_telegram())
    elif args.command == "test-api":
        asyncio.run(cmd_test_api())
    elif args.command == "summary":
        asyncio.run(cmd_send_summary())
    elif args.command == "low":
        asyncio.run(cmd_list_low_price(args.threshold))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
