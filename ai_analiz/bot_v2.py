"""
Solana Scalp Bot v2.0 - Paper Trading
Отслеживает транзакции кошельков и копирует сделки
"""
import asyncio
import aiohttp
import socket
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
from solana_monitor import SolanaMonitor  # Обычный RPC (Helius rate limit слишком строгий)
from jupiter_api import parse_route_amounts, get_sol_price_usd, check_can_sell
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
    level=logging.DEBUG,  # Включаем DEBUG для детального логирования
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# GLOBAL STATE
# ============================================================================
balance_usd = START_BALANCE_USD
open_positions = {}  # mint -> {entry_usd, amount_tokens, decimals, timestamp, wallet}
dead_tokens = set()
stats = {
    "total_buys": 0,
    "total_sells": 0,
    "total_profit": Decimal("0"),
    "total_loss": Decimal("0"),
    "start_time": datetime.now().isoformat(),
}
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
        "🚀 <b>SCALP BOT V2 STARTED</b>\n\n"
        f"💰 Balance: <b>${balance_usd:.2f}</b>\n"
        f"👀 Watching: <b>{len(WATCH_WALLETS)}</b> wallets\n"
        f"💵 Position size: <b>${POSITION_SIZE_USD}</b>\n"
        f"🎯 Target: <b>+{PROFIT_TARGET*100:.0f}%</b> | Stop: <b>{STOP_LOSS*100:.0f}%</b>\n"
        f"📊 Max positions: <b>{MAX_OPEN_POSITIONS}</b>"
    )
    await send_telegram(msg)


async def send_heartbeat(sol_price: Decimal, iteration: int):
    """Heartbeat сообщение"""
    runtime = datetime.now() - datetime.fromisoformat(stats["start_time"])
    hours = runtime.total_seconds() / 3600
    net_pnl = stats["total_profit"] - stats["total_loss"]
    
    msg = (
        f"💓 <b>HEARTBEAT</b> (#{iteration})\n\n"
        f"💰 Balance: <b>${balance_usd:.2f}</b>\n"
        f"📊 Positions: <b>{len(open_positions)}</b>\n"
        f"💵 SOL: <b>${sol_price:.2f}</b>\n"
        f"🛒 Buys: {stats['total_buys']} | Sells: {stats['total_sells']}\n"
        f"📈 Net P&L: <b>${net_pnl:.2f}</b>\n"
        f"⏱ Runtime: {hours:.1f}h"
    )
    await send_telegram(msg)


# ============================================================================
# SIGNAL HANDLER
# ============================================================================
def signal_handler(signum, frame):
    """Graceful shutdown"""
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
    if mint in BLACKLIST_TOKENS:
        return False
    if mint in dead_tokens:
        return False
    return True


def get_position_age_hours(pos: dict) -> float:
    """Возраст позиции в часах"""
    try:
        timestamp = datetime.fromisoformat(pos["timestamp"])
        age = datetime.now() - timestamp
        return age.total_seconds() / 3600
    except Exception:
        return 0


async def try_buy_token(mint: str, wallet: str, sol_price_usd: Decimal):
    """Попытаться купить токен"""
    global balance_usd
    
    # Проверки
    if mint in open_positions:
        return False
    
    if not is_token_allowed(mint):
        logger.info(f"⛔ Blacklisted: {mint[:8]}...")
        return False
    
    if len(open_positions) >= MAX_OPEN_POSITIONS:
        return False
    
    if balance_usd < MIN_BALANCE_USD:
        return False
    
    logger.info(f"🔍 New token detected: {mint} (${POSITION_SIZE_USD})")
    logger.info(f"   Jupiter: https://jup.ag/swap/SOL-{mint}")
    logger.info(f"   Birdeye: https://birdeye.so/token/{mint}?chain=solana")
    
    # НОВАЯ ЛОГИКА: Покупаем ТОЛЬКО если есть реальная котировка
    logger.info(f"⚡ INSTANT BUY - Trying to get quote...")
    
    sol_for_position = POSITION_SIZE_USD / sol_price_usd
    amount_atomic = int(sol_for_position * Decimal(1_000_000_000))
    
    # Пробуем получить реальную котировку
    route = quote_with_fallback(SOL_MINT, mint, amount_atomic)
    
    if not route:
        # Нет маршрута - ПРОПУСКАЕМ токен
        logger.warning(f"⚠️ No route found - SKIPPING token {mint[:8]}... (no liquidity yet)")
        dead_tokens.add(mint)  # Добавляем в blacklist чтобы не пробовать снова
        return False
    
    # Есть маршрут - используем реальные данные
    parsed = parse_route_amounts(route, in_decimals=9, out_decimals=9)
    out_amount_tokens = parsed["out_amount"]
    in_amount_sol = parsed["in_amount"]
    entry_usd = in_amount_sol * sol_price_usd
    decimals = parsed["out_decimals"]
    logger.info(f"✅ Got real quote: {out_amount_tokens:,.0f} tokens for ${entry_usd:.2f}")
    
    if balance_usd < entry_usd:
        logger.warning(f"⛔ Insufficient balance: ${balance_usd:.2f} < ${entry_usd:.2f}")
        return False
    
    # Проверка ликвидности на продажу (ОТКЛЮЧЕНА - проверим при продаже)
    # logger.info(f"🔍 Checking sell liquidity for {mint[:8]}...")
    # can_sell = check_can_sell(mint, out_amount_tokens, parsed["out_decimals"])
    # if not can_sell:
    #     logger.warning(f"⛔ No sell route for {mint[:8]}...")
    #     dead_tokens.add(mint)
    #     return False
    
    # Открываем позицию
    open_positions[mint] = {
        "entry_usd": entry_usd,
        "amount_tokens": out_amount_tokens,
        "decimals": decimals,
        "timestamp": datetime.now().isoformat(),
        "wallet": wallet,
    }
    balance_usd -= entry_usd
    stats["total_buys"] += 1
    
    # Уведомление
    price_per_token = entry_usd / out_amount_tokens if out_amount_tokens > 0 else Decimal("0")
    jup_link = f"https://jup.ag/swap/SOL-{mint}"
    
    msg = (
        f"⚡ <b>INSTANT BUY</b>\n\n"
        f"💱 <b>{sol_for_position:.6f} SOL → {out_amount_tokens:,.0f} tokens</b>\n"
        f"💵 Cost: <b>${entry_usd:.2f}</b>\n"
        f"📊 Price: ${price_per_token:.10f}/token\n\n"
        f"👛 From: <code>{wallet[:8]}...</code>\n"
        f"🪙 Token: <code>{mint}</code>\n\n"
        f"🔗 <a href='{jup_link}'>Jupiter</a>\n"
        f"💰 Balance: <b>${balance_usd:.2f}</b>"
    )
    await send_telegram(msg)
    logger.info(f"⚡ INSTANT BUY {mint[:8]}... | ${entry_usd:.2f}")
    
    return True


async def check_positions(sol_price_usd: Decimal):
    """Проверить открытые позиции"""
    global balance_usd
    
    for mint in list(open_positions.keys()):
        try:
            pos = open_positions[mint]
            
            # Получаем котировку на продажу (пробуем Jupiter и Raydium)
            amount_atomic = int(pos["amount_tokens"] * Decimal(10 ** pos["decimals"]))
            route = quote_with_fallback(mint, SOL_MINT, amount_atomic)
            
            if not route:
                # Нет ликвидности - списываем как убыток
                loss = pos["entry_usd"]
                stats["total_loss"] += loss
                stats["total_sells"] += 1
                
                msg = (
                    f"💀 <b>NO LIQUIDITY - DEAD TOKEN</b>\n\n"
                    f"💱 <b>{pos['amount_tokens']:,.2f} tokens</b>\n"
                    f"📉 Loss: <b>-${loss:.4f}</b>\n\n"
                    f"🪙 <code>{mint}</code>\n"
                    f"💰 Balance: <b>${balance_usd:.2f}</b>"
                )
                await send_telegram(msg)
                logger.warning(f"💀 NO LIQUIDITY {mint[:8]}... | -${loss:.4f} (dead token)")
                
                # Удаляем позицию и добавляем в dead_tokens
                del open_positions[mint]
                dead_tokens.add(mint)
                continue
            
            # Парсим
            parsed = parse_route_amounts(route, in_decimals=pos["decimals"], out_decimals=9)
            out_sol = parsed["out_amount"]
            cur_usd = out_sol * sol_price_usd
            pnl = (cur_usd - pos["entry_usd"]) / pos["entry_usd"]
            
            # Проверяем возраст позиции в секундах
            entry_time = datetime.fromisoformat(pos["timestamp"])
            age_seconds = (datetime.now() - entry_time).total_seconds()
            
            # Логируем текущее состояние позиции
            logger.debug(f"📊 {mint[:8]}... | P&L: {pnl*100:+.1f}% | ${cur_usd:.4f} | Age: {age_seconds:.0f}s")
            
            # ПРИНУДИТЕЛЬНАЯ ПРОДАЖА через 120 секунд
            if age_seconds >= FORCE_SELL_AFTER_SECONDS:
                balance_usd += cur_usd
                pnl_usd = cur_usd - pos["entry_usd"]
                
                if pnl_usd >= 0:
                    stats["total_profit"] += pnl_usd
                else:
                    stats["total_loss"] += abs(pnl_usd)
                stats["total_sells"] += 1
                
                emoji = "⏰" if pnl >= 0 else "⏰"
                msg = (
                    f"{emoji} <b>FORCE SELL (120s) {pnl*100:+.1f}%</b>\n\n"
                    f"💱 <b>{pos['amount_tokens']:,.2f} tokens → {out_sol:.6f} SOL</b>\n"
                    f"💵 Value: <b>${cur_usd:.4f}</b>\n"
                    f"{'📈 Profit' if pnl_usd >= 0 else '📉 Loss'}: <b>{'+' if pnl_usd >= 0 else ''}{pnl_usd:.4f}</b>\n\n"
                    f"🪙 <code>{mint}</code>\n"
                    f"💰 Balance: <b>${balance_usd:.2f}</b>"
                )
                await send_telegram(msg)
                logger.info(f"⏰ FORCE SELL {mint[:8]}... | {pnl*100:+.1f}% | {'+' if pnl_usd >= 0 else ''}{pnl_usd:.4f} (120s)")
                del open_positions[mint]
                continue
            
            # Проверка profit target
            if pnl >= PROFIT_TARGET:
                balance_usd += cur_usd
                profit = cur_usd - pos["entry_usd"]
                stats["total_profit"] += profit
                stats["total_sells"] += 1
                
                msg = (
                    f"🟢 <b>SELL +{pnl*100:.1f}%</b>\n\n"
                    f"💱 <b>{pos['amount_tokens']:,.2f} tokens → {out_sol:.6f} SOL</b>\n"
                    f"💵 Value: <b>${cur_usd:.4f}</b>\n"
                    f"📈 Profit: <b>+${profit:.4f}</b>\n\n"
                    f"🪙 <code>{mint}</code>\n"
                    f"💰 Balance: <b>${balance_usd:.2f}</b>"
                )
                await send_telegram(msg)
                logger.info(f"🟢 SELL {mint[:8]}... | +{pnl*100:.1f}% | +${profit:.4f}")
                del open_positions[mint]
            
            # Проверка stop loss
            elif pnl <= STOP_LOSS:
                balance_usd += cur_usd
                loss = pos["entry_usd"] - cur_usd
                stats["total_loss"] += loss
                stats["total_sells"] += 1
                
                msg = (
                    f"🔴 <b>STOP LOSS {pnl*100:.1f}%</b>\n\n"
                    f"💱 <b>{pos['amount_tokens']:,.2f} tokens → {out_sol:.6f} SOL</b>\n"
                    f"💵 Value: <b>${cur_usd:.4f}</b>\n"
                    f"📉 Loss: <b>-${loss:.4f}</b>\n\n"
                    f"🪙 <code>{mint}</code>\n"
                    f"💰 Balance: <b>${balance_usd:.2f}</b>"
                )
                await send_telegram(msg)
                logger.info(f"🔴 STOP LOSS {mint[:8]}... | {pnl*100:.1f}% | -${loss:.4f}")
                del open_positions[mint]
            
            # Проверка возраста позиции
            else:
                age_hours = get_position_age_hours(pos)
                if age_hours >= POSITION_MAX_AGE_HOURS:
                    balance_usd += cur_usd
                    pnl_usd = cur_usd - pos["entry_usd"]
                    
                    if pnl_usd >= 0:
                        stats["total_profit"] += pnl_usd
                    else:
                        stats["total_loss"] += abs(pnl_usd)
                    stats["total_sells"] += 1
                    
                    msg = (
                        f"⏰ <b>AUTO-CLOSE (Age: {age_hours:.1f}h)</b>\n\n"
                        f"💱 <b>{pos['amount_tokens']:,.2f} tokens → {out_sol:.6f} SOL</b>\n"
                        f"💵 Value: <b>${cur_usd:.4f}</b>\n"
                        f"📊 P&L: <b>{pnl*100:+.1f}%</b> (${pnl_usd:+.4f})\n\n"
                        f"🪙 <code>{mint}</code>\n"
                        f"💰 Balance: <b>${balance_usd:.2f}</b>"
                    )
                    await send_telegram(msg)
                    logger.info(f"⏰ AUTO-CLOSE {mint[:8]}... | {age_hours:.1f}h | {pnl*100:+.1f}%")
                    del open_positions[mint]
        
        except Exception as e:
            logger.error(f"Error checking position {mint[:8]}: {e}")


# ============================================================================
# MAIN LOOP
# ============================================================================
async def main():
    """Главный цикл бота"""
    global shutdown_requested
    
    logger.info("="*60)
    logger.info("  SOLANA SCALP BOT V2.0 - Paper Trading")
    logger.info("="*60)
    
    # Настройка aiohttp
    resolver = aiohttp.ThreadedResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, family=socket.AF_INET)
    timeout = aiohttp.ClientTimeout(total=10)
    
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        # Инициализация монитора
        monitor = SolanaMonitor(session)
        
        # ВАЖНО: Сканируем существующие токены и добавляем в dead_tokens
        # Чтобы не покупать токены которые уже торгуются
        logger.info("📋 Scanning existing tokens to blacklist...")
        initial_scan = await monitor.scan_all_wallets(limit=20)
        for wallet, mint in initial_scan:
            dead_tokens.add(mint)
            logger.debug(f"   ⛔ Blacklisted existing token: {mint[:8]}... from {wallet[:8]}...")
        logger.info(f"✅ Blacklisted {len(initial_scan)} existing tokens")
        
        # Стартовое сообщение
        await send_startup()
        logger.info(f"🚀 Bot started | Balance: ${balance_usd:.2f}")
        logger.info(f"👀 Watching {len(WATCH_WALLETS)} wallets for NEW transactions only")
        
        iteration = 0
        last_heartbeat = 0
        
        try:
            while not shutdown_requested:
                iteration += 1
                
                # Получаем цену SOL
                sol_price_usd = get_sol_price_usd()
                if sol_price_usd <= 0:
                    logger.warning("Cannot fetch SOL price, retrying...")
                    await asyncio.sleep(2)
                    continue
                
                # Сканируем транзакции всех кошельков
                new_tokens = await monitor.scan_all_wallets(limit=TX_LIMIT_PER_WALLET)
                
                # Пытаемся купить новые токены
                for wallet, mint in new_tokens:
                    await try_buy_token(mint, wallet, sol_price_usd)
                
                # Проверяем открытые позиции
                await check_positions(sol_price_usd)
                
                # Логируем каждые 10 итераций
                if iteration % 10 == 0:
                    logger.info(f"💓 Iter #{iteration} | Positions: {len(open_positions)} | Balance: ${balance_usd:.2f}")
                
                # Heartbeat
                if iteration - last_heartbeat >= HEARTBEAT_INTERVAL:
                    await send_heartbeat(sol_price_usd, iteration)
                    last_heartbeat = iteration
                
                # Пауза
                await asyncio.sleep(SCAN_INTERVAL)
        
        except asyncio.CancelledError:
            logger.info("🛑 Bot cancelled")
        except Exception as e:
            logger.error(f"❌ Critical error: {e}")
            await send_telegram(f"❌ <b>CRITICAL ERROR</b>\n{str(e)[:200]}")
        finally:
            # Финальное сообщение
            net_pnl = stats["total_profit"] - stats["total_loss"]
            msg = (
                "🛑 <b>BOT STOPPED</b>\n\n"
                f"💰 Final balance: <b>${balance_usd:.2f}</b>\n"
                f"📊 Open positions: {len(open_positions)}\n"
                f"🛒 Buys: {stats['total_buys']} | Sells: {stats['total_sells']}\n"
                f"📈 Profit: ${stats['total_profit']:.2f}\n"
                f"📉 Loss: ${stats['total_loss']:.2f}\n"
                f"💵 Net P&L: <b>${net_pnl:.2f}</b>"
            )
            await send_telegram(msg)
            logger.info("✓ Bot shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user")
