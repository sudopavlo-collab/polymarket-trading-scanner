"""
Telegram Notifier
Отправка уведомлений о событиях Polymarket в Telegram
"""
import logging
import asyncio
from typing import List, Optional
from datetime import datetime, timezone

import aiohttp

from .config import TELEGRAM_CONFIG, TelegramConfig
from .polymarket_client import Market, Event

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Отправка уведомлений в Telegram"""
    
    def __init__(self, config: TelegramConfig = TELEGRAM_CONFIG):
        self.config = config
        self.base_url = f"https://api.telegram.org/bot{config.bot_token}"
        self.session: Optional[aiohttp.ClientSession] = None
        self._sent_notifications: set = set()  # Для избежания дублей
    
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
    
    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_preview: bool = True
    ) -> bool:
        """Отправка сообщения в канал"""
        if self.session is None:
            await self.connect()
        
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.config.channel_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview
        }
        
        try:
            async with self.session.post(url, json=payload) as response:
                if response.status == 200:
                    logger.info("Message sent successfully")
                    return True
                else:
                    error = await response.text()
                    logger.error(f"Failed to send message: {response.status} - {error}")
                    return False
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False
    
    def _format_price(self, price: float) -> str:
        """Форматирование цены"""
        if price < 0.01:
            return f"${price:.4f}"
        elif price < 0.1:
            return f"${price:.3f}"
        else:
            return f"${price:.2f}"
    
    def _format_time_until_end(self, hours: Optional[float]) -> str:
        """Форматирование времени до окончания"""
        if hours is None:
            return "N/A"
        
        if hours < 0:
            return "⏰ Завершено"
        elif hours < 1:
            minutes = int(hours * 60)
            return f"🔥 {minutes} мин"
        elif hours < 24:
            return f"⚡ {hours:.1f} ч"
        elif hours < 168:  # 7 дней
            days = hours / 24
            return f"📅 {days:.1f} дн"
        else:
            days = hours / 24
            return f"📆 {days:.0f} дн"
    
    def _format_volume(self, volume: float) -> str:
        """Форматирование объема"""
        if volume >= 1_000_000:
            return f"${volume/1_000_000:.2f}M"
        elif volume >= 1_000:
            return f"${volume/1_000:.1f}K"
        else:
            return f"${volume:.0f}"
    
    def format_market_alert(self, market: Market) -> str:
        """Форматирование алерта для рынка"""
        # Формируем цены исходов
        prices_str = ""
        if market.outcomes and market.outcome_prices:
            for outcome, price in zip(market.outcomes, market.outcome_prices):
                emoji = "🟢" if price < 0.1 else "🟡" if price < 0.3 else "🔵"
                prices_str += f"\n   {emoji} {outcome}: {self._format_price(price)} ({price*100:.1f}%)"
        
        time_str = self._format_time_until_end(market.time_until_end)
        volume_str = self._format_volume(market.volume)
        
        # Определяем срочность
        urgency = ""
        if market.time_until_end and market.time_until_end < 24:
            urgency = "🚨 СРОЧНО! "
        elif market.time_until_end and market.time_until_end < 72:
            urgency = "⚠️ "
        
        url = f"https://polymarket.com/event/{market.slug}" if market.slug else ""
        
        message = f"""
{urgency}<b>📊 Событие с низкой ценой</b>

<b>❓ {market.question}</b>

<b>💰 Цены:</b>{prices_str}

<b>⏱ До окончания:</b> {time_str}
<b>📈 Объем:</b> {volume_str}
<b>💧 Ликвидность:</b> {self._format_volume(market.liquidity)}

<b>🔗 Ссылка:</b> {url}
"""
        return message.strip()
    
    def format_arbitrage_alert(self, market: Market) -> str:
        """Форматирование алерта для арбитражной возможности"""
        # Формируем цены исходов
        prices_str = ""
        total_cost = 0.0
        if market.outcomes and market.outcome_prices:
            for outcome, price in zip(market.outcomes, market.outcome_prices):
                prices_str += f"\n   💵 {outcome}: {self._format_price(price)} ({price*100:.1f}%)"
                total_cost += price
        
        profit = market.arbitrage_profit
        profit_pct = profit * 100
        
        time_str = self._format_time_until_end(market.time_until_end)
        volume_str = self._format_volume(market.volume)
        
        # Определяем уровень возможности
        if profit_pct > 5:
            level = "🔥🔥🔥 ОТЛИЧНАЯ ВОЗМОЖНОСТЬ!"
        elif profit_pct > 2:
            level = "🔥🔥 ХОРОШАЯ ВОЗМОЖНОСТЬ!"
        elif profit_pct > 1:
            level = "🔥 ВОЗМОЖНОСТЬ"
        else:
            level = "💡 Арбитраж"
        
        url = f"https://polymarket.com/event/{market.slug}" if market.slug else ""
        
        message = f"""
💰 <b>{level}</b>

<b>❓ {market.question}</b>

<b>📊 Цены исходов:</b>{prices_str}

<b>💵 Общая стоимость:</b> {self._format_price(total_cost)} ({total_cost*100:.2f}%)
<b>✅ Гарантированный выигрыш:</b> $1.00
<b>📈 ПРИБЫЛЬ:</b> <b>{self._format_price(profit)} ({profit_pct:.2f}%)</b>

<b>⏱ До окончания:</b> {time_str}
<b>📊 Объем:</b> {volume_str}
<b>💧 Ликвидность:</b> {self._format_volume(market.liquidity)}

<b>🔗</b> {url}

<i>Купи ОБА исхода и получи гарантированную прибыль!</i>
"""
        return message.strip()
    
    def format_event_alert(self, event: Event) -> str:
        """Форматирование алерта для события"""
        markets_info = ""
        for i, market in enumerate(event.markets[:5], 1):  # Максимум 5 рынков
            min_price = market.min_price
            emoji = "🟢" if min_price < 0.1 else "🟡" if min_price < 0.3 else "🔵"
            time_str = self._format_time_until_end(market.time_until_end)
            markets_info += f"\n{i}. {emoji} <b>{market.question[:80]}</b>"
            markets_info += f"\n   Min: {self._format_price(min_price)} | {time_str}"
        
        if len(event.markets) > 5:
            markets_info += f"\n... и еще {len(event.markets) - 5} рынков"
        
        url = f"https://polymarket.com/event/{event.slug}" if event.slug else ""
        
        message = f"""
🎯 <b>СОБЫТИЕ: {event.title}</b>

<b>📊 Рынки ({len(event.markets)}):</b>
{markets_info}

<b>🔗 Ссылка:</b> {url}
"""
        return message.strip()
    
    def format_summary_alert(
        self,
        markets: List[Market],
        title: str = "📋 Сводка событий с низкой ценой"
    ) -> str:
        """Форматирование сводного алерта"""
        if not markets:
            return f"{title}\n\n✅ Нет событий, соответствующих критериям"
        
        # Сортируем по времени до окончания
        sorted_markets = sorted(
            markets,
            key=lambda m: m.time_until_end if m.time_until_end else float('inf')
        )
        
        lines = [f"<b>{title}</b>\n"]
        lines.append(f"📊 Найдено: {len(markets)} событий\n")
        
        # Группируем по срочности
        urgent = [m for m in sorted_markets if m.time_until_end and m.time_until_end < 24]
        soon = [m for m in sorted_markets if m.time_until_end and 24 <= m.time_until_end < 72]
        later = [m for m in sorted_markets if m.time_until_end and m.time_until_end >= 72]
        no_date = [m for m in sorted_markets if m.time_until_end is None]
        
        if urgent:
            lines.append(f"\n🚨 <b>СРОЧНЫЕ (&lt;24ч):</b> {len(urgent)}")
            for m in urgent[:5]:
                lines.append(f"• {m.question[:60]}... - {self._format_price(m.min_price)}")
        
        if soon:
            lines.append(f"\n⚠️ <b>Скоро (24-72ч):</b> {len(soon)}")
            for m in soon[:5]:
                lines.append(f"• {m.question[:60]}... - {self._format_price(m.min_price)}")
        
        if later:
            lines.append(f"\n📅 <b>Позже (&gt;72ч):</b> {len(later)}")
            for m in later[:3]:
                lines.append(f"• {m.question[:60]}... - {self._format_price(m.min_price)}")
        
        lines.append(f"\n\n⏰ Обновлено: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        
        return "\n".join(lines)
    
    async def notify_market(self, market: Market) -> bool:
        """Отправка уведомления о рынке"""
        # Проверка на дубликаты
        notification_key = f"market_{market.id}"
        if notification_key in self._sent_notifications:
            logger.debug(f"Skipping duplicate notification for market {market.id}")
            return False
        
        message = self.format_market_alert(market)
        success = await self.send_message(message)
        
        if success:
            self._sent_notifications.add(notification_key)
        
        return success
    
    async def notify_arbitrage(self, market: Market) -> bool:
        """Отправка уведомления об арбитражной возможности"""
        notification_key = f"arb_{market.id}"
        if notification_key in self._sent_notifications:
            logger.debug(f"Skipping duplicate arbitrage notification for market {market.id}")
            return False
        
        message = self.format_arbitrage_alert(market)
        success = await self.send_message(message)
        
        if success:
            self._sent_notifications.add(notification_key)
        
        return success
    
    async def notify_event(self, event: Event) -> bool:
        """Отправка уведомления о событии"""
        notification_key = f"event_{event.id}"
        if notification_key in self._sent_notifications:
            return False
        
        message = self.format_event_alert(event)
        success = await self.send_message(message)
        
        if success:
            self._sent_notifications.add(notification_key)
        
        return success
    
    async def notify_summary(self, markets: List[Market]) -> bool:
        """Отправка сводного уведомления"""
        message = self.format_summary_alert(markets)
        return await self.send_message(message)
    
    async def notify_startup(self) -> bool:
        """Уведомление о запуске бота"""
        message = """
🤖 <b>Polymarket Analyzer запущен!</b>

✅ Бот начал мониторинг событий
📊 Критерии: цена &lt; $1.00
⏱ Интервал проверки: 60 сек

Буду отправлять уведомления о:
• Событиях с низкой ценой
• Заканчивающихся событиях
• Периодические сводки
"""
        return await self.send_message(message.strip())
    
    async def notify_error(self, error: str) -> bool:
        """Уведомление об ошибке"""
        message = f"""
⚠️ <b>Ошибка Polymarket Analyzer</b>

{error}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
        return await self.send_message(message.strip())
    
    def clear_sent_cache(self):
        """Очистка кэша отправленных уведомлений"""
        self._sent_notifications.clear()
        logger.info("Notification cache cleared")


async def test_notifier():
    """Тестирование уведомлений"""
    async with TelegramNotifier() as notifier:
        # Тестовое сообщение
        success = await notifier.send_message(
            "🧪 <b>Тестовое сообщение</b>\n\nPolymarket Analyzer работает!"
        )
        print(f"Test message sent: {success}")


if __name__ == "__main__":
    asyncio.run(test_notifier())
