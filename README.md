# 🎯 Polymarket Event Analyzer

Бот для мониторинга событий Polymarket с низкой ценой и уведомлениями в Telegram.

## 📋 Возможности

- ✅ Мониторинг всех активных рынков Polymarket
- ✅ Фильтрация событий с ценой < $1.00
- ✅ Отслеживание заканчивающихся событий
- ✅ Уведомления в Telegram о:
  - Срочных событиях (< 24 часа)
  - Событиях с очень низкой ценой (< 5%)
  - Лучших возможностях
- ✅ Периодические сводки (каждые 6 часов)
- ✅ CLI интерфейс для ручного анализа

## 🚀 Быстрый старт

### 1. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 2. Тестирование

```bash
# Тест Telegram
python -m polymarket_analyzer.cli test-tg

# Тест API
python -m polymarket_analyzer.cli test-api

# Одиночный анализ
python -m polymarket_analyzer.cli analyze
```

### 3. Запуск бота

```bash
python run_analyzer.py
```

или

```bash
python -m polymarket_analyzer.cli run
```

## 📖 CLI Команды

| Команда | Описание |
|---------|----------|
| `run` | Запустить бота в режиме мониторинга |
| `analyze` | Выполнить одиночный анализ |
| `test-tg` | Тест отправки в Telegram |
| `test-api` | Тест подключения к API |
| `summary` | Отправить сводку в Telegram |
| `low --threshold 0.05` | Показать события с ценой < 5% |

## ⚙️ Конфигурация

Настройки находятся в `polymarket_analyzer/config.py`:

```python
ANALYZER_CONFIG = AnalyzerConfig(
    price_threshold=1.0,   # Порог цены для уведомлений
    check_interval=60,     # Интервал проверки (секунды)
    min_volume=0.0,        # Минимальный объем
    days_until_end=7       # Дней до окончания для мониторинга
)
```

## 📊 Структура проекта

```
polymarket_analyzer/
├── __init__.py          # Инициализация пакета
├── config.py            # Конфигурация и API ключи
├── polymarket_client.py # Клиент Polymarket API
├── telegram_notifier.py # Отправка уведомлений в Telegram
├── analyzer.py          # Анализатор событий
├── bot.py               # Главный модуль бота
└── cli.py               # CLI интерфейс
```

## 🔔 Формат уведомлений

### Алерт о событии:
```
🚨 СРОЧНО! 📊 Событие с низкой ценой

❓ Will Bitcoin reach $100k by end of 2024?

💰 Цены:
   🟢 Yes: $0.0234 (2.3%)
   🔵 No: $0.9766 (97.7%)

⏱ До окончания: 🔥 45 мин
📈 Объем: $1.2M
💧 Ликвидность: $50.5K

🔗 Ссылка: https://polymarket.com/event/...
```

### Сводка:
```
📋 Сводка событий с низкой ценой

📊 Найдено: 45 событий

🚨 СРОЧНЫЕ (<24ч): 5
• Event 1... - $0.02
• Event 2... - $0.05

⚠️ Скоро (24-72ч): 12
• Event 3... - $0.08

📅 Позже (>72ч): 28
• Event 4... - $0.15

⏰ Обновлено: 2024-01-15 14:30 UTC
```

## ⚠️ Важно

- Бот использует публичный API Polymarket
- API ключи нужны для аутентифицированных запросов
- Не храните API ключи в публичных репозиториях
- Для продакшена используйте переменные окружения

## 📝 Логирование

Логи сохраняются в файл `polymarket_analyzer.log` и выводятся в консоль.

## 🛠 Разработка

```bash
# Запуск тестов
pytest

# Запуск с отладкой
LOG_LEVEL=DEBUG python run_analyzer.py
```
