#!/usr/bin/env python3
"""
Polymarket Event Analyzer - Точка входа
Запуск: python run_analyzer.py
"""
import sys
import os

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from polymarket_analyzer.bot import main

if __name__ == "__main__":
    main()
