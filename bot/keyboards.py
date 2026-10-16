# Клавиатуры меню (Telegram)

from .database import db
from .tg_shim import ReplyKeyboardBuilder


def main_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🧭 Дашборд")
    kb.button(text="🧠 Инструменты")
    kb.button(text="⏱ Фокус-таймер" if not db.get("timer_end") else "⏳ Статус таймера")
    kb.button(text="🌙 Режим сна")
    kb.button(text="🧾 Выплаты в проверке")
    kb.button(text="📉 Быстрый анализ")
    kb.button(text="🗓 Журнал по дням")
    kb.button(text="🚀 Моя цель")
    kb.button(text="🧬 Путь за все время")
    kb.button(text="🗂 Архив отчетов")
    kb.button(text="🧰 Логи и сервис")
    kb.button(text="🎛 Параметры")
    kb.button(text="🪪 О системе")
    kb.button(text="⏸ Стоп" if db.get("is_running") else "▶️ Старт")
    kb.adjust(2, 2, 2, 2, 2, 2, 1, 1)
    return kb.as_markup()


def calculation_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📌 Быстрый срез")
    kb.button(text="🧠 Глубокий разбор")
    kb.button(text="💳 Калькулятор выплат")
    kb.button(text="🔮 Прогноз на дату")
    kb.button(text="🎯 План до цели")
    kb.button(text="🛟 Контроль просадок")
    kb.button(text="⬅️ Назад")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def withdrawal_options_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🏦 На карту (чистыми)")
    kb.button(text="🧾 Сумма с сайта")
    kb.button(text="↩️ К инструментам")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


def sleep_manage_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="⏰ Сдвинуть время")
    kb.button(text="☀️ Выключить сон")
    kb.button(text="⬅️ Назад")
    kb.adjust(2, 1)
    return kb.as_markup()


def settings_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📐 Пороги сигналов")
    kb.button(text="🔔 Сценарий выплат")
    kb.button(text="🛰 Частота сканирования")
    kb.button(text="🧩 Модули и алерты")
    kb.button(text="🪪 О системе")
    kb.button(text="⬅️ Назад")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def settings_limits_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="⚙️ Настроить Выполнено")
    kb.button(text="⚙️ Настроить В работе")
    kb.button(text="⚙️ Настроить Баланс")
    kb.button(text="⬅️ В настройки")
    kb.adjust(1)
    return kb.as_markup()


def settings_payout_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🔔 Переключить выплаты")
    kb.button(text="⏳ Интервалы выплат")
    kb.button(text="⬅️ В настройки")
    kb.adjust(1)
    return kb.as_markup()


def settings_toggles_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📢 Сводки по метрикам")
    kb.button(text="💰 Крупные оплаты 30+ ₽")
    kb.button(text="😴 Анти-простой 1 час")
    kb.button(text="🎖 Новый ранг")
    kb.button(text="🎯 Достижение цели")
    kb.button(text="⬅️ В настройки")
    kb.adjust(2, 2, 1)
    return kb.as_markup()
