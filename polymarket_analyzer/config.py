"""
Конфигурация Polymarket Analyzer
"""
import os
from dataclasses import dataclass
from typing import Optional

@dataclass
class PolymarketCredentials:
    api_key: str
    secret: str
    passphrase: str

@dataclass
class TelegramConfig:
    bot_token: str
    channel_id: str

@dataclass
class AnalyzerConfig:
    # Порог цены для уведомлений (события с ценой < threshold)
    price_threshold: float = 1.0
    # Интервал проверки в секундах
    check_interval: int = 60
    # Минимальный объем для фильтрации
    min_volume: float = 0.0
    # Количество дней до окончания события для мониторинга
    days_until_end: int = 7

# API Credentials - Primary (set via environment)
PRIMARY_CREDENTIALS = PolymarketCredentials(
    api_key=os.getenv("POLY_API_KEY", ""),
    secret=os.getenv("POLY_API_SECRET", ""),
    passphrase=os.getenv("POLY_API_PASSPHRASE", ""),
)

# API Credentials - Secondary (backup)
SECONDARY_CREDENTIALS = PolymarketCredentials(
    api_key=os.getenv("POLY_API_KEY_2", ""),
    secret=os.getenv("POLY_API_SECRET_2", ""),
    passphrase=os.getenv("POLY_API_PASSPHRASE_2", ""),
)

# Telegram Configuration
TELEGRAM_CONFIG = TelegramConfig(
    bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    channel_id=os.getenv("TELEGRAM_CHANNEL_ID", ""),
)

# Analyzer Settings
ANALYZER_CONFIG = AnalyzerConfig(
    price_threshold=1.0,  # Цена < 1.0 (т.е. < $1 или < 100%)
    check_interval=60,     # Проверка каждые 60 секунд
    min_volume=0.0,        # Минимальный объем
    days_until_end=365     # Мониторим все события (до 1 года)
)

# Polymarket API URLs
POLYMARKET_API_BASE = "https://clob.polymarket.com"
POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
