import asyncio
import os
import json
import logging
import signal
import sys
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Set
from pathlib import Path

import aiohttp
import socket
import requests
from dotenv import load_dotenv
from telegram import Bot

# ============================================================================
# CONFIGURATION
# ============================================================================

# Load secrets
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Paths
BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "bot_state.json"
LOG_FILE = BASE_DIR / "scalp_bot.log"
DEAD_TOKENS_FILE = BASE_DIR / "dead_tokens.json"

# Solana RPC (public)
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# Mints
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# ============================================================================
# BLACKLIST - токены которые НЕ покупаем
# ============================================================================
BLACKLIST_TOKENS = {
    # Stablecoins
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "USDH1SM1ojwWUga67PGrgFWUHibbjqMvuMaDkRJTgkX",   # USDH
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj",  # stSOL
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",   # mSOL
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # wETH
    "So11111111111111111111111111111111111111112",    # wSOL (native)
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK (too liquid)
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",   # JUP
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
}

# Wallets to watch
WATCH_WALLETS = [
    "DeQ8dPv6ReZNQ45NfiWwS5CchWpB2BVq1QMyNV8L2uSW",
    "FhVo3mqL8PW5pH5U2CN4XE33DokiyZnUwuGpH2hmHLuM",
    "GrLZsWsSCzmZKXpqhQgWez6RpJc9Fkm8BGA4xp85Hown",
    "4x5Zv8NhYzA4NQ8sjECDDg7YKRdVPiCvXDcS3b4ZT8Bo",
]

# ============================================================================
# TRADING PARAMETERS
# ============================================================================
START_BALANCE_USD = Decimal("100")
POSITION_SIZE_USD = Decimal("1")      # $1 per trade
PROFIT_TARGET = Decimal("0.25")       # 25% profit target
STOP_LOSS = Decimal("-0.50")          # -50% stop loss
MAX_OPEN_POSITIONS = 20               # Maximum concurrent positions
MIN_BALANCE_USD = Decimal("10")       # Stop trading if balance drops below
HEARTBEAT_INTERVAL = 60               # Telegram heartbeat every N iterations
SAVE_STATE_INTERVAL = 10              # Save state every N iterations
POSITION_MAX_AGE_HOURS = 24           # Auto-close positions older than N hours
MIN_SWAP_USD_VALUE = Decimal("0.10")  # Minimum swap value to consider (filter dust)

# ============================================================================
# LOGGING SETUP
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
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
open_positions: Dict[str, Dict] = {}  # mint -> {entry_usd, amount_tokens, decimals, timestamp}
seen_tokens: Dict[str, List[str]] = {w: [] for w in WATCH_WALLETS}
seen_signatures: Dict[str, Set[str]] = {w: set() for w in WATCH_WALLETS}  # Track processed tx signatures
dead_tokens: Set[str] = set()  # Tokens without liquidity
all_seen_mints: Set[str] = set()  # All mints we've ever seen (to avoid re-buying)
stats = {
    "total_buys": 0,
    "total_sells": 0,
    "total_profit": Decimal("0"),
    "total_loss": Decimal("0"),
    "api_errors": 0,
    "start_time": datetime.now().isoformat(),
}

aiohttp_timeout = aiohttp.ClientTimeout(total=10)
shutdown_requested = False

# ============================================================================
# STATE MANAGEMENT
# ============================================================================
def save_state():
    """Save bot state to JSON file."""
    global balance_usd, open_positions, seen_tokens, dead_tokens, stats
    
    state = {
        "balance_usd": str(balance_usd),
        "open_positions": {
            mint: {
                "entry_usd": str(pos["entry_usd"]),
                "amount_tokens": str(pos["amount_tokens"]),
                "decimals": pos["decimals"],
                "timestamp": pos.get("timestamp", datetime.now().isoformat()),
            }
            for mint, pos in open_positions.items()
        },
        "seen_tokens": seen_tokens,
        "stats": {
            "total_buys": stats["total_buys"],
            "total_sells": stats["total_sells"],
            "total_profit": str(stats["total_profit"]),
            "total_loss": str(stats["total_loss"]),
            "api_errors": stats["api_errors"],
            "start_time": stats["start_time"],
        },
        "last_save": datetime.now().isoformat(),
    }
    
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        logger.debug(f"State saved: balance={balance_usd:.2f}, positions={len(open_positions)}")
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


def load_state():
    """Load bot state from JSON file."""
    global balance_usd, open_positions, seen_tokens, dead_tokens, stats
    
    if not STATE_FILE.exists():
        logger.info("No state file found, starting fresh")
        return False
    
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
        
        balance_usd = Decimal(state.get("balance_usd", str(START_BALANCE_USD)))
        
        open_positions = {
            mint: {
                "entry_usd": Decimal(pos["entry_usd"]),
                "amount_tokens": Decimal(pos["amount_tokens"]),
                "decimals": pos["decimals"],
                "timestamp": pos.get("timestamp", datetime.now().isoformat()),
            }
            for mint, pos in state.get("open_positions", {}).items()
        }
        
        seen_tokens.update(state.get("seen_tokens", {}))
        
        saved_stats = state.get("stats", {})
        stats["total_buys"] = saved_stats.get("total_buys", 0)
        stats["total_sells"] = saved_stats.get("total_sells", 0)
        stats["total_profit"] = Decimal(saved_stats.get("total_profit", "0"))
        stats["total_loss"] = Decimal(saved_stats.get("total_loss", "0"))
        stats["api_errors"] = saved_stats.get("api_errors", 0)
        
        logger.info(f"✓ State loaded: balance={balance_usd:.2f}, positions={len(open_positions)}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to load state: {e}")
        return False


def load_dead_tokens():
    """Load dead tokens list."""
    global dead_tokens
    
    if not DEAD_TOKENS_FILE.exists():
        return
    
    try:
        with open(DEAD_TOKENS_FILE, 'r') as f:
            dead_tokens = set(json.load(f))
        logger.info(f"✓ Loaded {len(dead_tokens)} dead tokens")
    except Exception as e:
        logger.error(f"Failed to load dead tokens: {e}")


def save_dead_tokens():
    """Save dead tokens list."""
    try:
        with open(DEAD_TOKENS_FILE, 'w') as f:
            json.dump(list(dead_tokens), f)
    except Exception as e:
        logger.error(f"Failed to save dead tokens: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    logger.info("🛑 Shutdown signal received, saving state...")
    shutdown_requested = True
    save_state()
    save_dead_tokens()
    logger.info("✓ State saved, exiting...")
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ============================================================================
# TELEGRAM NOTIFICATIONS
# ============================================================================
async def send_telegram(text: str):
    """Send message to Telegram."""
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode='HTML')
    except Exception as exc:
        logger.error(f"Telegram error: {exc}")


async def send_startup_message():
    """Send startup notification with config."""
    msg = (
        "🚀 <b>SCALP BOT STARTED</b>\n\n"
        f"💰 Balance: <b>${balance_usd:.2f}</b> USDC\n"
        f"📊 Open positions: <b>{len(open_positions)}</b>\n"
        f"👀 Watching: <b>{len(WATCH_WALLETS)}</b> wallets\n"
        f"💵 Position size: <b>${POSITION_SIZE_USD}</b>\n"
        f"🎯 Profit target: <b>{PROFIT_TARGET*100:.0f}%</b>\n"
        f"🛑 Stop loss: <b>{STOP_LOSS*100:.0f}%</b>\n"
        f"📈 Max positions: <b>{MAX_OPEN_POSITIONS}</b>\n"
        f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await send_telegram(msg)


async def send_heartbeat(sol_price: Decimal, iteration: int):
    """Send periodic heartbeat message."""
    runtime = datetime.now() - datetime.fromisoformat(stats["start_time"])
    hours = runtime.total_seconds() / 3600
    
    net_pnl = stats["total_profit"] - stats["total_loss"]
    pnl_emoji = "📈" if net_pnl >= 0 else "📉"
    
    msg = (
        f"💓 <b>HEARTBEAT</b> (iter #{iteration})\n\n"
        f"💰 Balance: <b>${balance_usd:.2f}</b>\n"
        f"📊 Open: <b>{len(open_positions)}</b> positions\n"
        f"💵 SOL: <b>${sol_price:.2f}</b>\n"
        f"🛒 Buys: {stats['total_buys']} | Sells: {stats['total_sells']}\n"
        f"{pnl_emoji} Net P&L: <b>${net_pnl:.2f}</b>\n"
        f"⏱ Runtime: {hours:.1f}h\n"
        f"⚠️ API errors: {stats['api_errors']}"
    )
    await send_telegram(msg)


async def send_shutdown_message():
    """Send shutdown notification."""
    net_pnl = stats["total_profit"] - stats["total_loss"]
    
    msg = (
        "🛑 <b>SCALP BOT STOPPED</b>\n\n"
        f"💰 Final balance: <b>${balance_usd:.2f}</b>\n"
        f"📊 Open positions: {len(open_positions)}\n"
        f"🛒 Total buys: {stats['total_buys']}\n"
        f"💸 Total sells: {stats['total_sells']}\n"
        f"📈 Total profit: ${stats['total_profit']:.2f}\n"
        f"📉 Total loss: ${stats['total_loss']:.2f}\n"
        f"💵 Net P&L: <b>${net_pnl:.2f}</b>"
    )
    await send_telegram(msg)


# Jupiter Quote API endpoints (multiple fallbacks)
JUP_QUOTE_URLS = [
    "https://quote-api.jup.ag/v6/quote",
]

async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict, max_retries: int = 3):
    """Fetch JSON with retries, sending standard headers required by the Jupiter API."""
    default_headers = {
        "Accept": "application/json",
        "User-Agent": "scalp-bot/1.0 (+https://github.com)"
    }
    for attempt in range(max_retries):
        try:
            async with session.get(url, params=params, headers=default_headers) as resp:
                return await resp.json()
        except (aiohttp.ClientConnectorError, aiohttp.ClientError) as e:
            print(f"Network error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
            else:
                raise


def quote_sync(in_mint: str, out_mint: str, amount_atomic: int, max_retries: int = 2):
    """Get quote from Jupiter API using correct endpoints."""
    import urllib3
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    # ✅ ПРАВИЛЬНЫЕ рабочие эндпоинты (проверены в ноябре 2025)
    urls = [
        "https://lite-api.jup.ag/swap/v1/quote",  # Legacy Swap API v1 - БЕСПЛАТНЫЙ
        "https://public.jupiterapi.com/quote",     # QuickNode публичный endpoint
    ]
    
    params = {
        "inputMint": in_mint,
        "outputMint": out_mint,
        "amount": str(amount_atomic),  # Важно: строка!
        "slippageBps": "100",
    }
    
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    
    session = requests.Session()
    retry_strategy = Retry(
        total=1,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Пробуем каждый URL по очереди
    for idx, url in enumerate(urls):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=15)
            
            # Проверяем статус
            if resp.status_code != 200:
                print(f"Endpoint {idx+1} returned {resp.status_code}")
                continue
            
            data = resp.json()
            
            # Проверяем на ошибки
            if "error" in data:
                print(f"Endpoint {idx+1} error: {data.get('error')}")
                continue
            
            # Парсим разные форматы ответа
            route = None
            
            # Формат 1: Legacy API v1 - возвращает массив в data[]
            if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
                route = data["data"][0]
                print(f"✓ Got quote from endpoint {idx+1} (Legacy format)")
            
            # Формат 2: Прямой ответ без обертки
            elif "outAmount" in data and "inAmount" in data:
                route = data
                print(f"✓ Got quote from endpoint {idx+1} (Direct format)")
            
            if route:
                session.close()
                return route
                
        except requests.exceptions.RequestException as e:
            print(f"✗ Endpoint {idx+1} connection failed: {type(e).__name__}")
            continue
        except Exception as e:
            print(f"✗ Endpoint {idx+1} error: {str(e)[:60]}")
            continue
    
    session.close()
    print(f"✗ All Jupiter endpoints failed for {in_mint[:8]}...->{out_mint[:8]}...")
    return None


def parse_route_amounts(route: dict, in_decimals: int = 9, out_decimals: int = 9):
    """Универсальный парсинг amounts из route для разных версий API."""
    out_amount_raw = Decimal(route.get("outAmount", 0))
    in_amount_raw = Decimal(route.get("inAmount", 0))
    
    # Пытаемся получить decimals из ответа (Legacy API)
    if "outTokenDecimals" in route and "inTokenDecimals" in route:
        out_dec = route["outTokenDecimals"]
        in_dec = route["inTokenDecimals"]
    else:
        # Используем переданные значения (новый API)
        out_dec = out_decimals
        in_dec = in_decimals
    
    out_amount = out_amount_raw / Decimal(10 ** out_dec)
    in_amount = in_amount_raw / Decimal(10 ** in_dec)
    
    return {
        "out_amount": out_amount,
        "in_amount": in_amount,
        "out_decimals": out_dec,
        "in_decimals": in_dec,
    }


async def quote(session: aiohttp.ClientSession, in_mint: str, out_mint: str, amount_atomic: int):
    """Async wrapper for quote_sync."""
    return quote_sync(in_mint, out_mint, amount_atomic)


def check_can_sell_token(mint: str, amount_tokens: Decimal, decimals: int) -> bool:
    """Check if token can be sold back to SOL (liquidity check)."""
    try:
        amount_atomic = int(amount_tokens * Decimal(10 ** decimals))
        route = quote_sync(mint, SOL_MINT, amount_atomic)
        return route is not None
    except Exception:
        return False


def is_token_allowed(mint: str) -> bool:
    """Check if token is allowed to buy (not in blacklist)."""
    if mint in BLACKLIST_TOKENS:
        return False
    if mint in dead_tokens:
        return False
    return True


def get_position_age_hours(pos: dict) -> float:
    """Get position age in hours."""
    try:
        timestamp = datetime.fromisoformat(pos.get("timestamp", datetime.now().isoformat()))
        age = datetime.now() - timestamp
        return age.total_seconds() / 3600
    except Exception:
        return 0


async def get_sol_price_usd(session: aiohttp.ClientSession) -> Decimal:
    """Берём цену SOL в USDC по маленькой котировке через Jupiter."""
    try:
        quote_route = await quote(
            session,
            in_mint=SOL_MINT,
            out_mint=USDC_MINT,
            amount_atomic=int(Decimal("0.01") * Decimal(1_000_000_000)),  # 0.01 SOL
        )
        
        if not quote_route:
            return Decimal("0")

        # Универсальный парсинг для разных версий API
        out_amount_raw = Decimal(quote_route.get("outAmount", 0))
        in_amount_raw = Decimal(quote_route.get("inAmount", 0))
        
        # Пытаемся получить decimals из ответа
        # Legacy API v1 возвращает поля с Decimals, новые - нет
        if "outTokenDecimals" in quote_route and "inTokenDecimals" in quote_route:
            out_decimals = quote_route["outTokenDecimals"]
            in_decimals = quote_route["inTokenDecimals"]
        else:
            # Используем известные значения для SOL/USDC
            in_decimals = 9   # SOL
            out_decimals = 6  # USDC
        
        out_amount = out_amount_raw / Decimal(10 ** out_decimals)
        in_amount = in_amount_raw / Decimal(10 ** in_decimals)
        
        if in_amount == 0:
            return Decimal("0")
            
        price = out_amount / in_amount
        print(f"✓ SOL price: ${price:.2f} USDC")
        return price
        
    except Exception as e:
        print(f"✗ Error getting SOL price: {type(e).__name__}: {str(e)[:80]}")
        return Decimal("0")


async def get_token_accounts(session: aiohttp.ClientSession, wallet: str):
    """Get current token accounts (for balance checking)."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [wallet, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding": "jsonParsed"}],
    }
    async with session.post(SOLANA_RPC, json=payload) as resp:
        res = await resp.json()
        return res["result"]["value"] if "result" in res else []


async def get_recent_signatures(session: aiohttp.ClientSession, wallet: str, limit: int = 10):
    """Get recent transaction signatures for a wallet."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [wallet, {"limit": limit}],
    }
    async with session.post(SOLANA_RPC, json=payload) as resp:
        res = await resp.json()
        return res.get("result", [])


async def get_transaction(session: aiohttp.ClientSession, signature: str):
    """Get transaction details by signature."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
    }
    async with session.post(SOLANA_RPC, json=payload) as resp:
        res = await resp.json()
        return res.get("result")


def extract_token_mints_from_tx(tx_data: dict) -> List[str]:
    """Extract token mints from a transaction (looking for swaps)."""
    mints = set()
    
    if not tx_data:
        return []
    
    try:
        # Check pre/post token balances for swapped tokens
        meta = tx_data.get("meta", {})
        
        # Post token balances show what tokens were received
        post_balances = meta.get("postTokenBalances", [])
        pre_balances = meta.get("preTokenBalances", [])
        
        # Find tokens that increased (bought)
        pre_amounts = {b.get("mint"): int(b.get("uiTokenAmount", {}).get("amount", 0)) for b in pre_balances}
        
        for balance in post_balances:
            mint = balance.get("mint")
            post_amount = int(balance.get("uiTokenAmount", {}).get("amount", 0))
            pre_amount = pre_amounts.get(mint, 0)
            
            # Token was bought if post > pre
            if post_amount > pre_amount and mint:
                mints.add(mint)
        
        # Also check inner instructions for swap programs
        inner_instructions = meta.get("innerInstructions", [])
        for inner in inner_instructions:
            for ix in inner.get("instructions", []):
                if "parsed" in ix:
                    parsed = ix["parsed"]
                    if isinstance(parsed, dict):
                        info = parsed.get("info", {})
                        if "mint" in info:
                            mints.add(info["mint"])
                            
    except Exception as e:
        logger.debug(f"Error extracting mints: {e}")
    
    return list(mints)


async def initialize_seen_tokens(session: aiohttp.ClientSession):
    """Load current tokens from wallets as 'already seen' to avoid buying old tokens."""
    global seen_tokens
    
    logger.info("📋 Initializing seen tokens from wallets...")
    total_tokens = 0
    
    for wallet in WATCH_WALLETS:
        try:
            accs = await get_token_accounts(session, wallet)
            mints = [a["account"]["data"]["parsed"]["info"]["mint"] for a in accs]
            seen_tokens[wallet] = mints
            total_tokens += len(mints)
            logger.info(f"  Wallet {wallet[:8]}...: {len(mints)} tokens")
        except Exception as e:
            logger.error(f"  Failed to load tokens for {wallet[:8]}...: {e}")
            seen_tokens[wallet] = []
    
    logger.info(f"✓ Initialized {total_tokens} existing tokens (will NOT buy these)")
    return total_tokens


async def monitor_wallets():
    """Main trading loop with full state management."""
    global balance_usd, open_positions, seen_tokens, dead_tokens, stats, shutdown_requested

    # Use ThreadedResolver to avoid local DNS issues
    resolver = aiohttp.ThreadedResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, family=socket.AF_INET)

    async with aiohttp.ClientSession(timeout=aiohttp_timeout, connector=connector) as session:
        
        # IMPORTANT: First load all existing tokens as "seen" to avoid buying old tokens!
        if not any(seen_tokens.values()):  # Only if seen_tokens is empty (fresh start)
            await initialize_seen_tokens(session)
        
        # Send startup message
        await send_startup_message()
        logger.info(f"🚀 Bot started | Balance: ${balance_usd:.2f} | Positions: {len(open_positions)}")
        logger.info(f"👀 Watching for NEW tokens only (existing tokens ignored)")

        iteration = 0
        last_sol_price = Decimal("0")

        try:
            while not shutdown_requested:
                iteration += 1

                # Get SOL price
                sol_price_usd = await get_sol_price_usd(session)
                if sol_price_usd <= 0:
                    stats["api_errors"] += 1
                    logger.warning("Cannot fetch SOL price, skipping iteration")
                    await asyncio.sleep(2)
                    continue
                
                last_sol_price = sol_price_usd

                # Check if balance is too low
                if balance_usd < MIN_BALANCE_USD:
                    logger.warning(f"⚠️ Balance ${balance_usd:.2f} below minimum ${MIN_BALANCE_USD}")
                    # Don't open new positions, but still check existing ones

                sol_for_position = POSITION_SIZE_USD / sol_price_usd

                # ============================================================
                # SCAN WALLETS FOR NEW TOKENS (via recent transactions)
                # ============================================================
                if balance_usd >= MIN_BALANCE_USD and len(open_positions) < MAX_OPEN_POSITIONS:
                    for wallet in WATCH_WALLETS:
                        try:
                            # Get recent transactions for this wallet
                            signatures = await get_recent_signatures(session, wallet, limit=5)
                            
                            new_mints = []
                            for sig_info in signatures:
                                sig = sig_info.get("signature")
                                if not sig or sig in seen_signatures.get(wallet, set()):
                                    continue
                                
                                # Mark signature as seen
                                if wallet not in seen_signatures:
                                    seen_signatures[wallet] = set()
                                seen_signatures[wallet].add(sig)
                                
                                # Get transaction details
                                tx_data = await get_transaction(session, sig)
                                if tx_data:
                                    # Extract bought tokens from this transaction
                                    tx_mints = extract_token_mints_from_tx(tx_data)
                                    for mint in tx_mints:
                                        if mint not in all_seen_mints:
                                            new_mints.append(mint)
                                            all_seen_mints.add(mint)
                                            logger.info(f"🔔 New token detected in tx: {mint[:8]}...")

                            for mint in new_mints:
                                # Skip if already in position, dead, or at max positions
                                if mint in open_positions:
                                    continue
                                if mint in dead_tokens:
                                    continue
                                if len(open_positions) >= MAX_OPEN_POSITIONS:
                                    break
                                
                                # Check blacklist
                                if not is_token_allowed(mint):
                                    logger.info(f"⛔ Skipping blacklisted token: {mint[:8]}...")
                                    continue

                                route = await quote(
                                    session,
                                    in_mint=SOL_MINT,
                                    out_mint=mint,
                                    amount_atomic=int(sol_for_position * Decimal(1_000_000_000)),
                                )
                                if not route:
                                    logger.debug(f"No route SOL->{mint[:8]}...")
                                    dead_tokens.add(mint)
                                    continue

                                # Parse route amounts
                                parsed = parse_route_amounts(route, in_decimals=9, out_decimals=9)
                                
                                out_amount_tokens = parsed["out_amount"]
                                in_amount_sol = parsed["in_amount"]
                                entry_usd = in_amount_sol * sol_price_usd
                                
                                # Check minimum swap value
                                if entry_usd < MIN_SWAP_USD_VALUE:
                                    logger.info(f"⛔ Skipping dust token (${entry_usd:.4f} < ${MIN_SWAP_USD_VALUE}): {mint[:8]}...")
                                    continue

                                if balance_usd < entry_usd:
                                    logger.warning("Not enough balance to open position")
                                    continue
                                
                                # Check if we can sell this token (liquidity check)
                                logger.info(f"🔍 Checking sell liquidity for {mint[:8]}...")
                                can_sell = check_can_sell_token(mint, out_amount_tokens, parsed["out_decimals"])
                                if not can_sell:
                                    logger.warning(f"⛔ No sell route for {mint[:8]}... - skipping")
                                    dead_tokens.add(mint)
                                    continue

                                # Open position
                                open_positions[mint] = {
                                    "entry_usd": entry_usd,
                                    "amount_tokens": out_amount_tokens,
                                    "decimals": parsed["out_decimals"],
                                    "timestamp": datetime.now().isoformat(),
                                    "entry_sol": in_amount_sol,
                                }
                                balance_usd -= entry_usd
                                stats["total_buys"] += 1

                                # Calculate price per token
                                price_per_token = entry_usd / out_amount_tokens if out_amount_tokens > 0 else Decimal("0")
                                
                                # Jupiter swap link
                                jup_link = f"https://jup.ag/swap/SOL-{mint}"
                                
                                msg = (
                                    f"🟢 <b>BUY</b>\n\n"
                                    f"💱 <b>{in_amount_sol:.6f} SOL → {out_amount_tokens:,.2f} tokens</b>\n"
                                    f"💵 Cost: <b>${entry_usd:.4f}</b> USDC\n"
                                    f"📊 Price: ${price_per_token:.10f}/token\n\n"
                                    f"👛 Wallet:\n<code>{wallet}</code>\n\n"
                                    f"🪙 Token:\n<code>{mint}</code>\n\n"
                                    f"🔗 <a href='{jup_link}'>Open on Jupiter</a>\n\n"
                                    f"💰 Balance: <b>${balance_usd:.2f}</b>"
                                )
                                await send_telegram(msg)
                                logger.info(f"🟢 BUY {mint[:8]}... | {in_amount_sol:.6f} SOL → {out_amount_tokens:,.2f} tokens | ${entry_usd:.4f}")

                        except Exception as e:
                            logger.error(f"Wallet scan error: {e}")
                            stats["api_errors"] += 1

                # ============================================================
                # CHECK EXISTING POSITIONS
                # ============================================================
                for mint in list(open_positions.keys()):
                    try:
                        pos = open_positions[mint]
                        
                        # Track failed attempts
                        if "failed_checks" not in pos:
                            pos["failed_checks"] = 0

                        amount_atomic = int(pos["amount_tokens"] * Decimal(10 ** pos["decimals"]))
                        route = await quote(
                            session,
                            in_mint=mint,
                            out_mint=SOL_MINT,
                            amount_atomic=amount_atomic,
                        )
                        
                        if not route:
                            pos["failed_checks"] += 1
                            
                            # Only mark as dead after 3 consecutive failures
                            if pos["failed_checks"] >= 3:
                                dead_tokens.add(mint)
                                loss = pos["entry_usd"]
                                stats["total_loss"] += loss
                                
                                jup_link = f"https://jup.ag/swap/{mint}-SOL"
                                msg = (
                                    f"💀 <b>NO LIQUIDITY</b>\n\n"
                                    f"🪙 Token:\n<code>{mint}</code>\n\n"
                                    f"💸 Entry: ${pos['entry_usd']:.4f}\n"
                                    f"📉 Loss: <b>-${loss:.4f}</b>\n\n"
                                    f"🔗 <a href='{jup_link}'>Check on Jupiter</a>\n\n"
                                    f"💰 Balance: <b>${balance_usd:.2f}</b>"
                                )
                                await send_telegram(msg)
                                logger.warning(f"💀 NO LIQUIDITY {mint[:8]}... | Loss: -${loss:.4f}")
                                del open_positions[mint]
                            else:
                                logger.info(f"⚠️ No route for {mint[:8]}... (attempt {pos['failed_checks']}/3)")
                            continue
                        
                        # Reset failed checks on success
                        pos["failed_checks"] = 0

                        # Parse route amounts
                        parsed = parse_route_amounts(route, in_decimals=pos["decimals"], out_decimals=9)
                        out_sol = parsed["out_amount"]
                        cur_usd = out_sol * sol_price_usd
                        pnl = (cur_usd - pos["entry_usd"]) / pos["entry_usd"]
                        
                        # Check profit target
                        if pnl >= PROFIT_TARGET:
                            balance_usd += cur_usd
                            profit = cur_usd - pos["entry_usd"]
                            stats["total_profit"] += profit
                            stats["total_sells"] += 1

                            jup_link = f"https://jup.ag/swap/{mint}-SOL"
                            msg = (
                                f"🟢 <b>SELL +{pnl*100:.1f}%</b>\n\n"
                                f"💱 <b>{pos['amount_tokens']:,.2f} tokens → {out_sol:.6f} SOL</b>\n"
                                f"💵 Value: <b>${cur_usd:.4f}</b> USDC\n"
                                f"📈 Profit: <b>+${profit:.4f}</b>\n\n"
                                f"🪙 Token:\n<code>{mint}</code>\n\n"
                                f"🔗 <a href='{jup_link}'>View on Jupiter</a>\n\n"
                                f"💰 Balance: <b>${balance_usd:.2f}</b>"
                            )
                            await send_telegram(msg)
                            logger.info(f"🟢 SELL {mint[:8]}... | +{pnl*100:.1f}% | +${profit:.4f}")
                            del open_positions[mint]
                        
                        # Check stop loss
                        elif pnl <= STOP_LOSS:
                            balance_usd += cur_usd
                            loss = pos["entry_usd"] - cur_usd
                            stats["total_loss"] += loss
                            stats["total_sells"] += 1

                            jup_link = f"https://jup.ag/swap/{mint}-SOL"
                            msg = (
                                f"🔴 <b>STOP LOSS {pnl*100:.1f}%</b>\n\n"
                                f"💱 <b>{pos['amount_tokens']:,.2f} tokens → {out_sol:.6f} SOL</b>\n"
                                f"💵 Value: <b>${cur_usd:.4f}</b> USDC\n"
                                f"📉 Loss: <b>-${loss:.4f}</b>\n\n"
                                f"🪙 Token:\n<code>{mint}</code>\n\n"
                                f"🔗 <a href='{jup_link}'>View on Jupiter</a>\n\n"
                                f"💰 Balance: <b>${balance_usd:.2f}</b>"
                            )
                            await send_telegram(msg)
                            logger.info(f"🔴 STOP LOSS {mint[:8]}... | {pnl*100:.1f}% | -${loss:.4f}")
                            del open_positions[mint]
                        
                        # Check position age - auto-close old positions
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

                                jup_link = f"https://jup.ag/swap/{mint}-SOL"
                                pnl_emoji = "📈" if pnl >= 0 else "📉"
                                msg = (
                                    f"⏰ <b>AUTO-CLOSE (Age: {age_hours:.1f}h)</b>\n\n"
                                    f"💱 <b>{pos['amount_tokens']:,.2f} tokens → {out_sol:.6f} SOL</b>\n"
                                    f"💵 Value: <b>${cur_usd:.4f}</b> USDC\n"
                                    f"{pnl_emoji} P&L: <b>{pnl*100:+.1f}%</b> (${pnl_usd:+.4f})\n\n"
                                    f"🪙 Token:\n<code>{mint}</code>\n\n"
                                    f"🔗 <a href='{jup_link}'>View on Jupiter</a>\n\n"
                                    f"💰 Balance: <b>${balance_usd:.2f}</b>"
                                )
                                await send_telegram(msg)
                                logger.info(f"⏰ AUTO-CLOSE {mint[:8]}... | Age: {age_hours:.1f}h | {pnl*100:+.1f}%")
                                del open_positions[mint]
                    
                    except Exception as e:
                        logger.error(f"Error checking position {mint[:8]}...: {e}")
                        stats["api_errors"] += 1
                        continue

                # ============================================================
                # PERIODIC TASKS
                # ============================================================
                if iteration % SAVE_STATE_INTERVAL == 0:
                    save_state()
                    save_dead_tokens()

                # Send heartbeat
                if iteration % HEARTBEAT_INTERVAL == 0:
                    await send_heartbeat(sol_price_usd, iteration)
                    logger.info(f"💓 Heartbeat #{iteration} | Balance: ${balance_usd:.2f} | Positions: {len(open_positions)}")

                await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("🛑 Bot cancelled, saving state...")
        except Exception as e:
            logger.error(f"❌ Critical error: {e}")
            await send_telegram(f"❌ <b>CRITICAL ERROR</b>\n{str(e)[:200]}")
        finally:
            # Final save
            save_state()
            save_dead_tokens()
            await send_shutdown_message()
            logger.info("✓ Bot shutdown complete")


def test_jupiter_now():
    """Быстрый тест Jupiter API прямо сейчас."""
    print("\n" + "="*50)
    print("TESTING JUPITER API ENDPOINTS")
    print("="*50 + "\n")
    
    result = quote_sync(
        SOL_MINT,
        USDC_MINT,
        10000000  # 0.01 SOL
    )
    
    if result:
        print("\n✅ SUCCESS! Jupiter API is working")
        print(f"Quote data keys: {list(result.keys())}")
        return True
    else:
        print("\n❌ FAILED! Jupiter API not accessible")
        return False


def main():
    """Main entry point with state loading."""
    print("\n" + "="*60)
    print("  SOLANA SCALP BOT v2.0")
    print("  Paper Trading Mode")
    print("="*60 + "\n")
    
    # Load previous state
    logger.info("Loading state...")
    load_state()
    load_dead_tokens()
    
    logger.info(f"Balance: ${balance_usd:.2f}")
    logger.info(f"Open positions: {len(open_positions)}")
    logger.info(f"Dead tokens: {len(dead_tokens)}")
    logger.info(f"Watching {len(WATCH_WALLETS)} wallets")
    
    # Test Jupiter API
    if not test_jupiter_now():
        logger.error("⚠️ Jupiter API test failed. Check your internet connection.")
        print("Press Ctrl+C to exit or wait 10 seconds to retry anyway...")
        import time
        time.sleep(10)
    
    # Run main loop
    try:
        asyncio.run(monitor_wallets())
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user")
    finally:
        save_state()
        save_dead_tokens()
        logger.info("✓ Final state saved")


if __name__ == "__main__":
    main()