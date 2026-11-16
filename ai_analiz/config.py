"""
Configuration for Solana Scalp Bot
"""
from decimal import Decimal
from pathlib import Path

# ============================================================================
# PATHS
# ============================================================================
BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "bot_state.json"
LOG_FILE = BASE_DIR / "scalp_bot.log"
DEAD_TOKENS_FILE = BASE_DIR / "dead_tokens.json"

# ============================================================================
# SOLANA & JUPITER
# ============================================================================
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
import os
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_API = f"https://api.helius.xyz/v0"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# Jupiter API endpoints (используем публичный endpoint который работает)
JUPITER_QUOTE_API = "https://public.jupiterapi.com/quote"

# ============================================================================
# BLACKLIST - токены которые НЕ покупаем
# ============================================================================
BLACKLIST_TOKENS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "USDH1SM1ojwWUga67PGrgFWUHibbjqMvuMaDkRJTgkX",   # USDH
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj",  # stSOL
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",   # mSOL
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # wETH
    "So11111111111111111111111111111111111111112",    # wSOL
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",   # JUP
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
}

# ============================================================================
# WATCHED WALLETS
# ============================================================================
WATCH_WALLETS = [
    "DeQ8dPv6ReZNQ45NfiWwS5CchWpB2BVq1QMyNV8L2uSW",
    "FhVo3mqL8PW5pH5U2CN4XE33DokiyZnUwuGpH2hmHLuM",
    "GrLZsWsSCzmZKXpqhQgWez6RpJc9Fkm8BGA4xp85Hown",
    "4x5Zv8NhYzA4NQ8sjECDDg7YKRdVPiCvXDcS3b4ZT8Bo",
    "4aAPWBtMXxYabGFotoVa7CEwxmu23XUSv7eWwYSppjAy",
    "HLnpSz9h2S4hiLQ43rnSD9XkcUThA7B8hQMKmDaiTLcC",
    "9p6rKauM2rmf7M8QZdvASBNL5ojYAELSyWpZXwEpnHd1",
]

# ============================================================================
# TRADING PARAMETERS
# ============================================================================
START_BALANCE_USD = Decimal("100")
POSITION_SIZE_USD = Decimal("1.00")   # $1.00 на сделку (меньше = больше шансов найти маршрут)
PROFIT_TARGET = Decimal("2.0")        # 200%
STOP_LOSS = Decimal("-0.50")           # -50%
MAX_OPEN_POSITIONS = 20
MIN_BALANCE_USD = Decimal("10")
MIN_SWAP_USD_VALUE = Decimal("0.10")   # Минимум $0.10
POSITION_MAX_AGE_HOURS = 1
FORCE_SELL_AFTER_SECONDS = 45  # Принудительная продажа через 300 секунсдд

# ============================================================================
# BOT SETTINGS
# ============================================================================
HEARTBEAT_INTERVAL = 60      # Telegram heartbeat каждые N секунд
SAVE_STATE_INTERVAL = 10     # Сохранять состояние каждые N итераций
SCAN_INTERVAL = 2            # секунд между сканированиями
TX_LIMIT_PER_WALLET = 10     # количество транзакций для проверки
BUY_DELAY_SECONDS = 5  # задержка перед покупкой (дать время на добавление ликвидности)
