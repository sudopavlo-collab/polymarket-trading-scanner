#!/usr/bin/env python3
"""
Closing Events Bot - Ловим вершки перед закрытием!
Запуск: python run_closing_bot.py

Примеры:
  python run_closing_bot.py                           # Дефолт: 5 мин, 95%
  python run_closing_bot.py -m 10 -p 0.90             # 10 мин, 90%
  python run_closing_bot.py -m 2 -p 0.98 -i 10        # 2 мин, 98%, проверка каждые 10 сек
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from polymarket_analyzer.closing_bot import main

if __name__ == "__main__":
    main()
