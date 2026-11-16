#!/usr/bin/env python3
"""
Real-time Arbitrage Bot - Быстрый мониторинг
Запуск: python run_realtime.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from polymarket_analyzer.realtime_bot import main

if __name__ == "__main__":
    main()
