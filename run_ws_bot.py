#!/usr/bin/env python3
"""
WebSocket Arbitrage Bot - Мгновенный мониторинг
Запуск: python run_ws_bot.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from polymarket_analyzer.ws_bot import main

if __name__ == "__main__":
    main()
