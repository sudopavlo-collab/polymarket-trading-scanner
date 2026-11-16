"""
Solana Scalp Bot v2.1 - DUAL STRATEGY
Две параллельные стратегии для сравнения эффективности
"""
import asyncio
import aiohttp
import logging
import sys
import signal
import os
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv
from telegram import Bot

# Импорты наших модулей
from config import *
from solana_monitor import SolanaMonitor
from jupiter_api import parse_route_amounts, get_sol_price_usd
from raydium_api import quote_with_fallback

# ============================================================================
# SETUP
# ============================================================================
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Logging
logging.basicConfig(
    level=logging.DEBUG,  # DEBUG для детального логирования
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler("dual_strategy.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# GLOBAL STATE - ДВЕ СТРАТЕГИИ
# ============================================================================
# Стратегия A: БЕЗ повторных покупок (консервативная)
balance_a = START_BALANCE_USD
open_positions_a = {}
stats_a = {
    "name": "A: No Rebuy",
    "total_buys": 0,
    "total_sells": 0,
    "total_profit": Decimal("0"),
    "total_loss": Decimal("0"),
}

# Стратегия B: С повторными покупками (агрессивная)
balance_b = START_BALANCE_USD
open_positions_b = {}
processed_tokens_b = set()  # Для отслеживания повторных покупок
stats_b = {
    "name": "B: Rebuy",
    "total_buys": 0,
    "total_sells": 0,
    "total_profit": Decimal("0"),
    "total_loss": Decimal("0"),
}

# Общие данные
dead_tokens = set()
start_time = datetime.now()
shutdown_requested = False


# ============================================================================
# TELEGRAM
# ============================================================================
async def send_telegram(text: str):
    """Отправить сообщение в Telegram"""
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Telegram error: {e}")


async def send_startup():
    """Стартовое сообщение"""
    msg = (
        "🚀 <b>DUAL STRATEGY BOT STARTED</b>\n\n"
        f"💰 Balance (each): <b>${START_BALANCE_USD:.2f}</b>\n"
        f"👀 Watching: <b>{len(WATCH_WALLETS)}</b> wallets\n"
        f"💵 Position size: <b>${POSITION_SIZE_USD}</b>\n"
        f"🎯 Target: <b>+{PROFIT_TARGET*100:.0f}%</b> | Stop: <b>{STOP_LOSS*100:.0f}%</b>\n\n"
        f"📊 <b>Strategy A:</b> No rebuy (conservative)\n"
        f"📊 <b>Strategy B:</b> Rebuy allowed (aggressive)"
    )
    await send_telegram(msg)


async def send_comparison(iteration: int):
    """Сравнительная статистика"""
    runtime = (datetime.now() - start_time).total_seconds() / 3600
    
    net_pnl_a = stats_a["total_profit"] - stats_a["total_loss"]
    net_pnl_b = stats_b["total_profit"] - stats_b["total_loss"]
    roi_a = (net_pnl_a / START_BALANCE_USD * 100) if START_BALANCE_USD > 0 else 0
    roi_b = (net_pnl_b / START_BALANCE_USD * 100) if START_BALANCE_USD > 0 else 0
    
    winner = "🏆 A" if net_pnl_a > net_pnl_b else "🏆 B" if net_pnl_b > net_pnl_a else "🤝 TIE"
    
    msg = (
        f"📊 <b>COMPARISON #{iteration}</b>\n\n"
        f"<b>Strategy A (No Rebuy):</b>\n"
        f"💰 Balance: ${balance_a:.2f}\n"
        f"📈 P&L: ${net_pnl_a:+.2f} ({roi_a:+.1f}%)\n"
        f"📊 Positions: {len(open_positions_a)}\n"
        f"🛒 {stats_a['total_buys']}B / {stats_a['total_sells']}S\n\n"
        f"<b>Strategy B (Rebuy):</b>\n"
        f"💰 Balance: ${balance_b:.2f}\n"
        f"📈 P&L: ${net_pnl_b:+.2f} ({roi_b:+.1f}%)\n"
        f"📊 Positions: {len(open_positions_b)}\n"
        f"🛒 {stats_b['total_buys']}B / {stats_b['total_sells']}S\n\n"
        f"{winner} | ⏱ {runtime:.1f}h"
    )
    await send_telegram(msg)


# ============================================================================
# SIGNAL HANDLER
# ============================================================================
def signal_handler(signum, frame):
    global shutdown_requested
    logger.info("🛑 Shutdown signal received")
    shutdown_requested = True


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ============================================================================
# TRADING LOGIC
# ============================================================================
def is_token_allowed(mint: str) -> bool:
    """Проверка разрешён ли токен"""
    return mint not in BLACKLIST_TOKENS and mint not in dead_tokens


def get_position_age_hours(pos: dict) -> float:
    """Возраст позиции в часах"""
    try:
        timestamp = datetime.fromisoformat(pos["timestamp"])
        return (datetime.now() - timestamp).total_seconds() / 3600
    except Exception:
        return 0


async def try_buy_token_strategy(mint: str, wallet: str, sol_price_usd: Decimal, 
                                  strategy: str, balance: Decimal, positions: dict, 
                                  stats: dict, processed_tokens: set = None):
    """
    Попытка купить токен для конкретной стратегии
    
    strategy: 'A' или 'B'
    processed_tokens: только для стратегии B
    """
    # Проверки
    if mint in positions:
        return False, balance
    
    if not is_token_allowed(mint):
        return False, balance
    
    if len(positions) >= MAX_OPEN_POSITIONS:
        return False, balance
    
    if balance < MIN_BALANCE_USD:
        return False, balance
    
    # Для стратегии B: проверяем processed_tokens для повторных покупок
    # Для стратегии A: используем общий monitor.processed_tokens (нет повторных покупок)
    
    # Получаем котировку
    sol_for_position = POSITION_SIZE_USD / sol_price_usd
    amount_atomic = int(sol_for_position * Decimal(1_000_000_000))
    
    logger.debug(f"[{strategy}] Trying to get quote for {mint[:8]}... (${POSITION_SIZE_USD})")
    route = quote_with_fallback(SOL_MINT, mint, amount_atomic)
    if not route:
        logger.warning(f"[{strategy}] ❌ No route for {mint[:8]}... - adding to dead_tokens")
        dead_tokens.add(mint)
        return False, balance
    
    # Парсим
    parsed = parse_route_amounts(route, in_decimals=9, out_decimals=9)
    out_amount_tokens = parsed["out_amount"]
    in_amount_sol = parsed["in_amount"]
    entry_usd = in_amount_sol * sol_price_usd
    
    if entry_usd < MIN_SWAP_USD_VALUE or balance < entry_usd:
        return False, balance
    
    # Открываем позицию
    positions[mint] = {
        "entry_usd": entry_usd,
        "amount_tokens": out_amount_tokens,
        "decimals": parsed["out_decimals"],
        "timestamp": datetime.now().isoformat(),
        "wallet": wallet,
    }
    balance -= entry_usd
    stats["total_buys"] += 1
    
    # Для стратегии B: добавляем в processed_tokens
    if processed_tokens is not None:
        processed_tokens.add(mint)
    
    logger.info(f"🟢 [{strategy}] BUY {mint[:8]}... | ${entry_usd:.4f}")
    
    return True, balance


async def check_positions_strategy(sol_price_usd: Decimal, strategy: str, 
                                    balance: Decimal, positions: dict, 
                                    stats: dict, processed_tokens: set = None):
    """Проверить позиции для конкретной стратегии"""
    
    for mint in list(positions.keys()):
        try:
            pos = positions[mint]
            
            # Получаем котировку на продажу
            amount_atomic = int(pos["amount_tokens"] * Decimal(10 ** pos["decimals"]))
            route = quote_with_fallback(mint, SOL_MINT, amount_atomic)
            
            if not route:
                # Нет ликвидности - списываем как убыток
                loss = pos["entry_usd"]
                stats["total_loss"] += loss
                stats["total_sells"] += 1
                
                logger.warning(f"💀 [{strategy}] NO LIQUIDITY {mint[:8]}... | -${loss:.4f}")
                
                del positions[mint]
                dead_tokens.add(mint)
                continue
            
            # Парсим
            parsed = parse_route_amounts(route, in_decimals=pos["decimals"], out_decimals=9)
            out_sol = parsed["out_amount"]
            cur_usd = out_sol * sol_price_usd
            pnl = (cur_usd - pos["entry_usd"]) / pos["entry_usd"]
            
            # Проверка profit target
            if pnl >= PROFIT_TARGET:
                balance += cur_usd
                profit = cur_usd - pos["entry_usd"]
                stats["total_profit"] += profit
                stats["total_sells"] += 1
                
                logger.info(f"🟢 [{strategy}] SELL {mint[:8]}... | +{pnl*100:.1f}% | +${profit:.4f}")
                
                del positions[mint]
                
                # Для стратегии B: удаляем из processed_tokens чтобы разрешить повторную покупку
                if processed_tokens is not None:
                    processed_tokens.discard(mint)
            
            # Проверка stop loss
            elif pnl <= STOP_LOSS:
                balance += cur_usd
                loss = pos["entry_usd"] - cur_usd
                stats["total_loss"] += loss
                stats["total_sells"] += 1
                
                logger.info(f"🔴 [{strategy}] STOP LOSS {mint[:8]}... | {pnl*100:.1f}% | -${loss:.4f}")
                
                del positions[mint]
                
                # Для стратегии B: НЕ удаляем из processed_tokens (не покупаем убыточные повторно)
            
            # Проверка возраста
            else:
                age_hours = get_position_age_hours(pos)
                if age_hours >= POSITION_MAX_AGE_HOURS:
                    balance += cur_usd
                    pnl_usd = cur_usd - pos["entry_usd"]
                    if pnl_usd > 0:
                        stats["total_profit"] += pnl_usd
                    else:
                        stats["total_loss"] += abs(pnl_usd)
                    stats["total_sells"] += 1
                    
                    logger.info(f"⏰ [{strategy}] AUTO-CLOSE {mint[:8]}... | {age_hours:.1f}h | {pnl*100:+.1f}%")
                    
                    del positions[mint]
        
        except Exception as e:
            logger.error(f"[{strategy}] Error checking position {mint[:8]}: {e}")
    
    return balance


# ============================================================================
# MAIN LOOP
# ============================================================================
async def monitor_wallets():
    """Главный цикл мониторинга"""
    global balance_a, balance_b, shutdown_requested
    
    async with aiohttp.ClientSession() as session:
        monitor = SolanaMonitor(session)
        
        await send_startup()
        logger.info("🚀 Dual Strategy Bot started")
        logger.info(f"👀 Watching {len(WATCH_WALLETS)} wallets")
        
        iteration = 0
        last_comparison = 0
        
        while not shutdown_requested:
            try:
                iteration += 1
                
                # Получаем цену SOL
                sol_price_usd = get_sol_price_usd()
                if not sol_price_usd:
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue
                
                # Сканируем новые токены
                new_tokens = await monitor.scan_all_wallets(limit=TX_LIMIT_PER_WALLET)
                
                # Обрабатываем новые токены для обеих стратегий
                for wallet, mint in new_tokens:  # ← ИСПРАВЛЕНО: правильный порядок!
                    # Стратегия A: НЕТ повторных покупок
                    # new_tokens уже отфильтрованы monitor.processed_tokens, поэтому просто покупаем
                    bought_a, balance_a = await try_buy_token_strategy(
                        mint, wallet, sol_price_usd, 'A', 
                        balance_a, open_positions_a, stats_a
                    )
                    
                    # Стратегия B: ЕСТЬ повторные покупки (использует processed_tokens_b)
                    if mint not in processed_tokens_b:
                        bought_b, balance_b = await try_buy_token_strategy(
                            mint, wallet, sol_price_usd, 'B',
                            balance_b, open_positions_b, stats_b, processed_tokens_b
                        )
                
                # Проверяем открытые позиции обеих стратегий
                balance_a = await check_positions_strategy(
                    sol_price_usd, 'A', balance_a, open_positions_a, stats_a
                )
                balance_b = await check_positions_strategy(
                    sol_price_usd, 'B', balance_b, open_positions_b, stats_b, processed_tokens_b
                )
                
                # Сравнительная статистика каждые 60 итераций
                if iteration - last_comparison >= 60:
                    await send_comparison(iteration)
                    last_comparison = iteration
                    
                    # Логируем в консоль
                    net_a = stats_a["total_profit"] - stats_a["total_loss"]
                    net_b = stats_b["total_profit"] - stats_b["total_loss"]
                    logger.info(f"📊 Iter #{iteration} | A: ${balance_a:.2f} ({net_a:+.2f}) | B: ${balance_b:.2f} ({net_b:+.2f})")
                
                await asyncio.sleep(SCAN_INTERVAL)
            
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(SCAN_INTERVAL)
        
        # Финальная статистика
        await send_final_stats()


async def send_final_stats():
    """Финальная статистика при остановке"""
    runtime = (datetime.now() - start_time).total_seconds() / 3600
    
    net_pnl_a = stats_a["total_profit"] - stats_a["total_loss"]
    net_pnl_b = stats_b["total_profit"] - stats_b["total_loss"]
    roi_a = (net_pnl_a / START_BALANCE_USD * 100) if START_BALANCE_USD > 0 else 0
    roi_b = (net_pnl_b / START_BALANCE_USD * 100) if START_BALANCE_USD > 0 else 0
    
    winner = "🏆 Strategy A" if net_pnl_a > net_pnl_b else "🏆 Strategy B" if net_pnl_b > net_pnl_a else "🤝 TIE"
    
    msg = (
        "🛑 <b>BOT STOPPED - FINAL RESULTS</b>\n\n"
        f"<b>Strategy A (No Rebuy):</b>\n"
        f"💰 Final: ${balance_a:.2f}\n"
        f"📈 P&L: ${net_pnl_a:+.2f} ({roi_a:+.1f}%)\n"
        f"📊 Open: {len(open_positions_a)}\n"
        f"🛒 {stats_a['total_buys']}B / {stats_a['total_sells']}S\n"
        f"💚 Profit: ${stats_a['total_profit']:.2f}\n"
        f"💔 Loss: ${stats_a['total_loss']:.2f}\n\n"
        f"<b>Strategy B (Rebuy):</b>\n"
        f"💰 Final: ${balance_b:.2f}\n"
        f"📈 P&L: ${net_pnl_b:+.2f} ({roi_b:+.1f}%)\n"
        f"📊 Open: {len(open_positions_b)}\n"
        f"🛒 {stats_b['total_buys']}B / {stats_b['total_sells']}S\n"
        f"💚 Profit: ${stats_b['total_profit']:.2f}\n"
        f"💔 Loss: ${stats_b['total_loss']:.2f}\n\n"
        f"<b>{winner}</b>\n"
        f"⏱ Runtime: {runtime:.1f}h"
    )
    await send_telegram(msg)
    logger.info("✓ Bot shutdown complete")


# ============================================================================
# ENTRY POINT
# ============================================================================
if __name__ == "__main__":
    logger.info("="*60)
    logger.info("  SOLANA DUAL STRATEGY BOT - Paper Trading")
    logger.info("="*60)
    
    try:
        asyncio.run(monitor_wallets())
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
