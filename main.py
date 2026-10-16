import asyncio
import requests
import time
import json
import os
import re
import random
from datetime import datetime, timedelta
from itertools import groupby
from collections import defaultdict

from bs4 import BeautifulSoup
from bot.tg_shim import Bot, Dispatcher, types, F, Command, ReplyKeyboardBuilder, InlineKeyboardMarkup, InlineKeyboardButton
from bot.config import LOG_FILE, DB_FILE, BOT_TOKEN, TELEGRAM_CHAT_ID, USER_URL, DASHBOARD_URL, HEADERS, CHECK_INTERVAL, DAY_SEED_PROFIT, DAY_SEED_TASKS
from bot.webserver import start_web_server, keep_alive_task
from bot.database import db, load_db, save_db, create_backup, get_now, write_log
from bot.parser import get_all_data, get_raw_verification_tasks, parse_duration, parse_unu_time
from bot.utils import get_rank_info, get_rank, split_message_text, build_verification_text, get_verif_keyboard, get_progress_bar, calculate_eta, get_time_bar, calculate_today_totals
from bot.keyboards import main_menu, calculation_menu, withdrawal_options_kb, sleep_manage_menu, settings_menu
from bot.monitoring import monitoring_task, payout_monitor_task, midnight_check, _log_monitoring_state
from bot.handlers import router as bot_handlers_router

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(bot_handlers_router)


@dp.message(F.text.in_(["📊 Средние показатели", "📌 Быстрый срез"]))
async def show_averages(message: types.Message):
    pts = db["history"]
    if len(pts) < 10:
        await message.answer("📉 Мало данных для статистики. Поработай еще немного!")
        return

    total_profit = 0
    start_time = pts[0][0]
    end_time = pts[-1][0]
    duration_days = (end_time - start_time) / 86400

    for i in range(len(pts)-1):
        diff = pts[i+1][4] - pts[i][4]
        if 0 < diff < 150: total_profit += diff

    avg_daily = total_profit / max(duration_days, 1)
    avg_hourly = avg_daily / 24
    
    # Считаем "активное" время (когда были изменения баланса)
    active_hours = len(set([datetime.fromtimestamp(p[0]).hour for p in pts]))

    text = (
        f"🧮 **Средняя статистика за все время:**\n\n"
        f"💰 В день: `{round(avg_daily, 2)}` ₽\n"
        f"🕒 В час (24/7): `{round(avg_hourly, 2)}` ₽\n"
        f"⏱ В минуту: `{round(avg_hourly/60, 4)}` ₽\n"
        f"📈 Эффективность: `{round(total_profit / len(pts), 2)}` ₽ за одну проверку бота\n\n"
        f"📅 Дней в системе: `{round(duration_days, 1)}`"
    )
    await message.answer(text, reply_markup=calculation_menu(), parse_mode="Markdown")

@dp.message(F.text.in_(["🔮 Прогноз дохода", "🔮 Прогноз на дату"]))
async def forecast_start(message: types.Message):
    db["input_mode"] = "forecast_date"
    await message.answer(
        "🔮 **Машина предсказаний**\n\n"
        "Введите дату, до которой рассчитать доход в формате `ДД.ММ` (например, `01.07`):\n"
        "Бот рассчитает прибыль, исходя из твоего среднего темпа за всё время.",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(lambda msg: db.get("input_mode") == "forecast_date")
async def handle_forecast(message: types.Message):
    db["input_mode"] = None
    try:
        text = message.text.strip()
        now = datetime.now()
        
        # Парсинг даты (поддержка ДД.ММ и ДД.ММ.ГГГГ)
        parts = text.split('.')
        if len(parts) == 2:
            day, month = map(int, parts)
            target_date = datetime(now.year, month, day)
            if target_date < now:
                target_date = target_date.replace(year=now.year + 1)
        elif len(parts) == 3:
            day, month, year = map(int, parts)
            # Если год указан двумя цифрами (напр. 26)
            if year < 100: year += 2000 
            target_date = datetime(year, month, day)
        else:
            raise ValueError

        days_diff = (target_date - now).days
        if days_diff <= 0:
            await message.answer("❌ Дата должна быть в будущем!", reply_markup=main_menu())
            return

        # Расчет статистики
        pts = db["history"]
        if len(pts) < 10:
            await message.answer("⚠️ Недостаточно данных в истории для прогноза.")
            return

        total_profit = 0
        duration_days = (pts[-1][0] - pts[0][0]) / 86400
        for i in range(len(pts)-1):
            diff = pts[i+1][4] - pts[i][4]
            if 0 < diff < 150: total_profit += diff
        
        avg_daily = total_profit / max(duration_days, 1)
        
        # Результаты
        predicted_profit = avg_daily * days_diff
        final_balance = db["last"]["balance"] + predicted_profit
        
        res = (
            f"🔮 **Прогноз до {target_date.strftime('%d.%m.%Y')}**\n"
            f"📅 Дней ожидания: `{days_diff}`\n"
            f"━━━━━━━━━━━━━━\n"
            f"💰 Ожидаемая прибыль: `+{round(predicted_profit, 2)}` ₽\n"
            f"🏦 Баланс к этой дате: `{round(final_balance, 2)}` ₽\n"
            f"━━━━━━━━━━━━━━\n"
            f"📈 Твой темп: `{round(avg_daily, 2)}` ₽/день"
        )
        await message.answer(res, reply_markup=calculation_menu(), parse_mode="Markdown")
        
    except Exception:
        await message.answer("❌ Ошибка формата! Используй:\n`ДД.ММ` или `ДД.ММ.ГГГГ`", reply_markup=main_menu())

@dp.message(F.text.in_(["🎯 Декомпозиция цели", "🎯 План до цели"]))
async def goal_decomposition(message: types.Message):
    if db["goal"] <= 0:
        await message.answer("🎯 Сначала установи цель в главном меню!")
        return

    pts = db["history"]
    total_earned = 0
    total_tasks = 0
    
    # Считаем среднюю цену одного задания
    for i in range(len(pts)-1):
        d_bal = pts[i+1][4] - pts[i][4]
        d_task = pts[i+1][1] - pts[i][1]
        if 0 < d_bal < 150:
            total_earned += d_bal
            if d_task > 0: total_tasks += d_task

    avg_task_price = total_earned / total_tasks if total_tasks > 0 else 1.5 # дефолт 1.5р
    remaining = db["goal"] - db["last"]["balance"]
    
    if remaining <= 0:
        await message.answer("✅ Цель уже достигнута!")
        return

    tasks_needed = int(remaining / avg_task_price)
    
    text = (
        f"🎯 **Как достичь цели {db['goal']} ₽?**\n\n"
        f"💵 Нужно еще заработать: `{round(remaining, 2)}` ₽\n"
        f"💎 Твой средний чек: `{round(avg_task_price, 2)}` ₽/зад.\n\n"
        f"🛠 Тебе осталось выполнить примерно:\n"
        f"└ 🔥 **{tasks_needed}** заданий\n\n"
        f"_*Расчет основан на твоей истории выполнений._"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text.in_(["📉 Анализ просадок", "🛟 Контроль просадок"]))
async def analyze_slumps(message: types.Message):
    pts = db["history"]
    if len(pts) < 20:
        await message.answer("⚠️ Мало данных для поиска пауз в доходе.")
        return

    slumps = []
    current_slump_start = None
    
    # Ищем отрезки длиннее 2 часов без изменения баланса
    for i in range(len(pts)-1):
        p1, p2 = pts[i], pts[i+1]
        time_diff = (p2[0] - p1[0]) / 3600  # в часах
        bal_diff = p2[4] - p1[4]

        if bal_diff <= 0:
            if current_slump_start is None:
                current_slump_start = p1[0]
        else:
            if current_slump_start:
                duration = (p1[0] - current_slump_start) / 3600
                if duration >= 2: # Фиксируем паузы более 2 часов
                    slumps.append(duration)
                current_slump_start = None

    if not slumps:
        await message.answer("🚀 Просадок не обнаружено! Ты работаешь очень стабильно.")
        return

    avg_slump = sum(slumps) / len(slumps)
    max_slump = max(slumps)

    text = (
        f"📉 **Анализ простоев (AFK):**\n\n"
        f"⏳ Средняя пауза в доходе: `{round(avg_slump, 1)}` ч.\n"
        f"🚫 Самый долгий застой: `{round(max_slump, 1)}` ч.\n"
        f"📊 Всего найдено пауз: `{len(slumps)}` шт.\n\n"
        f"💡 *Совет: Если пауза затянулась, попробуй сменить категорию задач или зайти позже.*"
    )
    await message.answer(text, parse_mode="Markdown")
@dp.message(F.text.in_(["🧮 Расчеты", "🧠 Инструменты"]))
async def show_calc_menu(message: types.Message):
    await message.answer("Выберите тип анализа:", reply_markup=calculation_menu())

@dp.message(Command("week_stats"))
async def week_stats(message: types.Message):
    # Группируем прибыль по дням недели (0=Пн, 6=Вс)
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    profit_map = {i: 0 for i in range(7)}
    
    for i in range(len(db["history"])-1):
        p1, p2 = db["history"][i], db["history"][i+1]
        diff = p2[4] - p1[4]
        if 0 < diff < 150:
            day_idx = datetime.fromtimestamp(p2[0]).weekday()
            profit_map[day_idx] += diff

    text = "📅 **Эффективность по дням:**\n\n"
    max_val = max(profit_map.values()) if any(profit_map.values()) else 1
    
    for i in range(7):
        bar_len = int((profit_map[i] / max_val) * 8) # Мини-график
        bar = "🟩" * bar_len + "⬜" * (8 - bar_len)
        text += f"{weekdays[i]}: {bar} `{round(profit_map[i], 1)}` ₽\n"
    
    await message.answer(text, reply_markup=main_menu(), parse_mode="Markdown")

def get_rank_info(total_earned):
    ranks = [
        (0, "Новичок 🌱"), (100, "Стажёр 📋"), (300, "Трудяга 🛠"),
        (700, "Опытный 🎖"), (1500, "Мастер 🏆"), (3000, "Специалист 💎"),
        (7000, "Фармер-Про 🚜"), (15000, "Бизнесмен 💼"), (30000, "Магнат 💰"),
        (70000, "Акула UNU 🦈"), (150000, "Олигарх 🏰"), (500000, "Легенда 👑")
    ]
    
    current_r = ranks[0]
    next_r = ranks[1]
    
    for i, r in enumerate(ranks):
        if total_earned >= r[0]:
            current_r = r
            next_r = ranks[i+1] if i+1 < len(ranks) else r
        else:
            break
            
    # Считаем прогресс в процентах до след. ранга
    if next_r[0] != current_r[0]:
        progress = int(((total_earned - current_r[0]) / (next_r[0] - current_r[0])) * 100)
    else:
        progress = 100
        
    return current_r[1], next_r[0], next_r[1], progress

@dp.message(Command("insight"))
async def daily_insight(message: types.Message):
    pts = db["history"]
    if len(pts) < 50:
        await message.answer("🔮 Мне нужно больше данных для предсказания...")
        return
        
    # Считаем средний профит за последние 3 дня
    now_ts = time.time()
    three_days_pts = [p for p in pts if p[0] > (now_ts - 259200)]
    
    total_p = 0
    for i in range(len(three_days_pts)-1):
        d = three_days_pts[i+1][4] - three_days_pts[i][4]
        if 0 < d < 150: total_p += d
            
    avg_daily = round(total_p / 3, 2)
    tomorrow_max = round(avg_daily * 1.2, 2) # Оптимистичный прогноз (+20%)
    
    await message.answer(
        f"🔮 **Денежный прогноз на завтра:**\n\n"
        f"Опираясь на твой темп, завтра ты сможешь сделать около `{avg_daily}` ₽.\n"
        f"Но если выложишься на полную, я вижу цифру `{tomorrow_max}` ₽! 🚀\n\n"
        f"Удачное время для работы: `10:00 - 14:00` (по твоей истории).", 
        reply_markup=main_menu()
    )

@dp.message(F.text.in_(["💸 Калькулятор вывода", "💳 Калькулятор выплат"]))
async def withdrawal_info(message: types.Message):
    balance = db["last"]["balance"]
    
    text = (
        f"💸 **ИНФОРМАЦИЯ О ВЫВОДЕ СРЕДСТВ**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Твой текущий баланс: `{balance}` ₽\n\n"
        f"⚠️ **Условия системы:**\n"
        f"├ Стандартная комиссия: `8%` + `60 ₽` (фикс)\n"
        f"└ Минимальный порог обычно: `500-1000 ₽`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Куда ты планируешь выводить средства?"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=withdrawal_options_kb())

@dp.message(F.text.in_(["💳 Хочу на карту", "🏦 На карту (чистыми)"]))
async def start_calc_to_card(message: types.Message):
    db["input_mode"] = "calc_target_on_card"
    await message.answer("💰 **Сколько ты хочешь получить на карту чистоганом?**\nВведите сумму (например, 500):")

@dp.message(F.text.in_(["📤 Вывожу сумму с сайта", "🧾 Сумма с сайта"]))
async def start_calc_from_balance(message: types.Message):
    db["input_mode"] = "calc_from_site_balance"
    await message.answer("💳 **Какую сумму ты ставишь на вывод с сайта?**\nВведите сумму (например, 1000):")

@dp.message(lambda msg: db.get("input_mode") in ["calc_target_on_card", "calc_from_site_balance"])
async def process_withdrawal_calculation(message: types.Message):
    mode = db["input_mode"]
    db["input_mode"] = None # Сбрасываем режим
    
    try:
        amount = float(message.text.replace(',', '.'))
        fee_pct = 0.08  # 8%
        fee_fix = 60    # 60 руб
        
        if mode == "calc_target_on_card":
            # Формула: (Нужная сумма / 0.92) + 60
            # Мы сначала компенсируем процент, потом добавляем фикс
            to_withdraw = (amount / (1 - fee_pct)) + fee_fix
            
            text = (
                f"🎯 **ЦЕЛЬ: ПОЛУЧИТЬ {amount} ₽ НА КАРТУ**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Тебе нужно поставить на вывод: `{round(to_withdraw, 2)}` ₽\n\n"
                f"📊 **Детализация:**\n"
                f"├ Комиссия 8%: `{round(to_withdraw * fee_pct, 2)}` ₽\n"
                f"├ Фикс. сбор: `{fee_fix}` ₽\n"
                f"└ Итого комиссия: `{round(to_withdraw - amount, 2)}` ₽\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💳 На карту придет ровно: `{amount}` ₽"
            )

        elif mode == "calc_from_site_balance":
            # Формула: (Сумма - 60) * 0.92
            if amount <= fee_fix:
                await message.answer("❌ Сумма вывода должна быть больше 60 ₽!")
                return
                
            on_card = (amount - fee_fix) * (1 - fee_pct)
            total_fee = amount - on_card
            
            text = (
                f"📤 **ВЫВОД {amount} ₽ С САЙТА**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 На карту поступит: **{round(on_card, 2)}** ₽\n\n"
                f"📉 **Потери на комиссиях:**\n"
                f"├ Процент (8%): `{round((amount-fee_fix) * fee_pct, 2)}` ₽\n"
                f"├ Фикс. сбор: `{fee_fix}` ₽\n"
                f"└ Всего удержано: `{round(total_fee, 2)}` ₽\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ *Проверь лимиты своей карты перед выводом!*"
            )

        await message.answer(text, parse_mode="Markdown", reply_markup=withdrawal_options_kb())

    except ValueError:
        await message.answer("❌ Пожалуйста, введи только число (например: 550.50)", reply_markup=withdrawal_options_kb())

# Кнопка возврата в подменю расчетов
@dp.message(F.text.in_(["⬅️ Назад в расчеты", "↩️ К инструментам"]))
async def back_to_calc(message: types.Message):
    await message.answer("Возвращаемся к расчетам...", reply_markup=calculation_menu())
    
@dp.message(Command("top"))
async def show_top_hours(message: types.Message):
    if len(db["history"]) < 10:
        await message.answer("Мало данных для анализа продуктивности.")
        return

    hourly_profit = {} # {час: профит}
    for i in range(len(db["history"]) - 1):
        p1, p2 = db["history"][i], db["history"][i+1]
        diff = p2[4] - p1[4]
        if 0 < diff < 150:
            hour = datetime.fromtimestamp(p2[0]).hour
            hourly_profit[hour] = hourly_profit.get(hour, 0) + diff

    if not hourly_profit:
        await message.answer("Зафиксированных приходов пока нет.")
        return

    # Сортируем по прибыли
    sorted_hours = sorted(hourly_profit.items(), key=lambda x: x[1], reverse=True)
    
    text = "🔥 **Твои самые продуктивные часы:**\n"
    for hour, profit in sorted_hours[:5]:
        text += f"▫️ {hour:02d}:00 — {hour:02d}:59  =>  `+{round(profit, 2)}` ₽\n"
    
    await message.answer(text, reply_markup=main_menu(), parse_mode="Markdown")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    db["peer_id"] = message.chat.id
    db["chat_id"] = message.chat.id
    save_db()
    print(f"[START] chat_id={message.chat.id} — впишите это число в TELEGRAM_CHAT_ID на Render")
    data = await asyncio.to_thread(get_all_data)
    if data == "AUTH_ERROR":
        await message.answer(
            "⚠️ UNU не принял cookie (сессия устарела или UNU_COOKIE пустой).\n"
            "Обновите UNU_COOKIE в Render → Environment и нажмите /start снова.\n"
            f"Ваш chat ID: {message.chat.id}"
        )
        return
    if not isinstance(data, dict):
        await message.answer("⚠️ Не удалось получить данные с UNU (сайт недоступен). Попробуйте /start через минуту.")
        return
    db["last"] = data
    save_db()
    await message.answer(
        f"🚀 Бот запущен!\nМониторинг активен (интервал ~1.5 мин).\nВаш chat ID: {message.chat.id}",
        reply_markup=main_menu(),
    )
    if not db["is_running"]:
        asyncio.create_task(monitoring_task(bot, message.chat.id))
        asyncio.create_task(payout_monitor_task(bot, message.chat.id))
        asyncio.create_task(midnight_check(bot, message.chat.id))

@dp.message(F.text == "🧹 Очистить логи")
async def clear_logs(message: types.Message):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%d.%m %H:%M:%S')}] Логи очищены пользователем\n")
    await message.answer("✅ Файл логов полностью очищен.")

@dp.message(F.text == "📥 Скачать лог")
async def send_log_file(message: types.Message):
    if os.path.exists(LOG_FILE):
        file = types.FSInputFile(LOG_FILE)
        await message.answer_document(file, caption="📂 Полная история событий")
    else:
        await message.answer("Файл логов еще не создан.")

@dp.message(F.text.in_(["📊 Анализ", "📉 Быстрый анализ"]))
async def ask_period(message: types.Message):
    write_log(f"Нажата кнопка: Анализ")
    db["input_mode"] = "analyze_period"
    await message.answer(
        "📊 **За какой период сделать расчет?**\n\n"
        "Пиши число и букву:\n"
        "• `30m` — 30 минут\n"
        "• `2h` — 2 часа\n"
        "• `1d` — 1 день\n"
        "• `1w` — 1 неделя",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(F.text.in_(["📜 Архив анализов", "🗂 Архив отчетов"]))
async def show_archive(message: types.Message):
    if not db.get("analysis_archive"):
        await message.answer("Архив пока пуст.")
        return
    history_text = "\n".join(db["analysis_archive"][-10:])
    await message.answer(f"Последние расчеты:\n\n`{history_text}`", parse_mode="Markdown")

@dp.message(Command("set_goal"))
async def set_goal_start(message: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="💰 Цель: Баланс")
    kb.button(text="📈 Цель: За день")
    kb.button(text="⬅️ Назад")
    kb.adjust(2, 1)
    
    await message.answer(
        "🎯 **Какую цель поставим?**\n\n"
        "1. **Баланс** — общая сумма на счету (напр. 1100 ₽).\n"
        "2. **За день** — сколько хочешь заработать сегодня сверху (напр. 600 ₽).",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )

def calculate_eta(remaining_money, history):
    """Считает, через сколько времени будет достигнута цель"""
    if not history or len(history) < 10:
        return "Нужно больше данных"
    
    # Берем данные за последние 6 часов для оценки текущего темпа
    now = time.time()
    recent_pts = [p for p in history if p[0] > (now - 21600)]
    
    if len(recent_pts) < 2:
        return "Темп не определен"
        
    total_recent_profit = 0
    for i in range(len(recent_pts)-1):
        diff = recent_pts[i+1][4] - recent_pts[i][4]
        if 0 < diff < 150: # игнорируем пополнения и выводы
            total_recent_profit += diff
            
    # Считаем профит в минуту
    time_span_mins = (recent_pts[-1][0] - recent_pts[0][0]) / 60
    if time_span_mins <= 0 or total_recent_profit <= 0:
        return "Жду новых оплат..."
        
    profit_per_min = total_recent_profit / time_span_mins
    
    # Сколько минут нужно для закрытия остатка
    mins_needed = remaining_money / profit_per_min
    
    if mins_needed > 1440: # больше суток
        return f"~{round(mins_needed / 1440, 1)} дн."
    elif mins_needed > 60:
        return f"~{round(mins_needed / 60, 1)} ч."
    else:
        return f"~{int(mins_needed)} мин."

def parse_unu_time(text):
    """Извлекает время из строк типа '22 часа 11 мин'"""
    days = re.search(r'(\d+)\s*д', text)
    hours = re.search(r'(\d+)\s*ч', text)
    minutes = re.search(r'(\d+)\s*м', text)
    
    return timedelta(
        days=int(days.group(1)) if days else 0,
        hours=int(hours.group(1)) if hours else 0,
        minutes=int(minutes.group(1)) if minutes else 0
    )

def get_raw_verification_tasks():
    """Собирает сырые данные о заданиях на проверке"""
    all_tasks = []
    ajax_url = "https://unu.im/include/ajax_tasks_works.php"
    current_showed_ids = ""
    start_flag = 1
    
    local_headers = HEADERS.copy()
    local_headers.update({'Referer': "https://unu.im/tasks/works/check", 'X-Requested-With': 'XMLHttpRequest'})

    now = datetime.now()

    try:
        while True:
            params = {"showed_ids": current_showed_ids, "start": start_flag, "type": "check"}
            response = requests.get(ajax_url, headers=local_headers, params=params, timeout=15)
            if response.status_code != 200: break
            
            data = response.json()
            html_content = data.get('html', '')
            if not html_content.strip(): break

            soup = BeautifulSoup(html_content, 'html.parser')
            rows = soup.find_all('div', class_='job-table__row')
            if not rows: break

            for row in rows:
                cells = row.find_all('div', class_='job-table__cell')
                if len(cells) < 3: continue
                
                sum_text = cells[2].get_text(strip=True).replace('руб', '').replace(' ', '').replace(',', '.')
                price = float(sum_text) if sum_text else 0.0
                
                status_text = cells[1].get_text(strip=True).lower()
                time_diff = parse_unu_time(status_text)
                pay_time = now + time_diff
                
                clean_time = status_text.replace('на проверке', '').replace('осталось', '').strip()
                
                all_tasks.append({
                    'price': price,
                    'pay_time': pay_time,
                    'time_label': clean_time if clean_time else "неизвестно"
                })

            current_showed_ids = data.get('showed_ids', '')
            start_flag = 0 
            if str(data.get('no_more')) == "1": break
            time.sleep(0.2)

        all_tasks.sort(key=lambda x: x['pay_time'])
        return all_tasks

    except Exception as e:
        print(f"Ошибка парсинга проверки: {e}")
        return []

def build_verification_text(tasks, detailed=False):
    """Формирует красивый текст (сводку или детальный отчет)"""
    if not tasks:
        return "✅ На проверке сейчас ничего нет."

    total_sum = sum(t['price'] for t in tasks)
    
    # Группируем по календарным дням
    days_grouped = defaultdict(list)
    for t in tasks:
        date_str = t['pay_time'].strftime("%Y-%m-%d")
        days_grouped[date_str].append(t)

    now_date = datetime.now().date()
    weekdays = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    report = [
        f"📊 <b>СВОДКА ПО ПРОВЕРКАМ</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📦 Всего заданий: <b>{len(tasks)}</b> шт.",
        f"💰 Ожидаемая прибыль: <b>{round(total_sum, 2)} ₽</b>",
        f"━━━━━━━━━━━━━━━━━━━━\n"
    ]

    for d_str, day_tasks in days_grouped.items():
        t_date = datetime.strptime(d_str, "%Y-%m-%d").date()
        diff_days = (t_date - now_date).days

        if diff_days == 0:
            day_title = "🟩 <b>СЕГОДНЯ</b>"
        elif diff_days == 1:
            day_title = "🟨 <b>ЗАВТРА</b>"
        else:
            day_title = f"⬜ <b>{t_date.strftime('%d.%m')} ({weekdays[t_date.weekday()]})</b>"

        day_sum = sum(t['price'] for t in day_tasks)
        report.append(f"{day_title} — {len(day_tasks)} зад. | <b>{round(day_sum, 2)} ₽</b>")
        
        if detailed:
            # Детальная разбивка внутри дня
            report.append(f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈")
            for label, group in groupby(day_tasks, key=lambda x: x['time_label']):
                sub_tasks = list(group)
                g_count = len(sub_tasks)
                g_sum = sum(t['price'] for t in sub_tasks)
                g_time = sub_tasks[0]['pay_time']
                
                # Форматируем оставшееся время
                time_left = g_time - datetime.now()
                hours, rem = divmod(time_left.seconds, 3600)
                minutes, seconds = divmod(rem, 60)
                time_parts = []
                if time_left.days > 0: time_parts.append(f"{time_left.days}д")
                if hours > 0: time_parts.append(f"{hours}ч")
                if minutes > 0: time_parts.append(f"{minutes}м")
                time_left_str = " ".join(time_parts) if time_parts else f"{seconds}с"

                # Извлекаем лимит
                category_match = re.search(r'(\d+)\s*(минут|час|дн)', label)
                category = f"({category_match.group(0)})" if category_match else ""

                report.append(
                    f"▫️ {g_time.strftime('%H:%M')} ⏳ {time_left_str} "
                    f"• <b>{g_count} шт.</b> {category} • {round(g_sum, 2)} ₽"
                )
            report.append("") # Пустая строка после каждого дня

    if not detailed:
        report.append(f"\n<i>Нажмите кнопку ниже, чтобы увидеть точное время каждой выплаты.</i>")

    return "\n".join(report)

def get_verif_keyboard(detailed=False):
    """Клавиатура для управления отчетом"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📄 Показать детали" if not detailed else "📉 Скрыть детали", 
                callback_data="verif_toggle"
            )
        ],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="verif_refresh")]
    ])
    return kb

@dp.message(F.text.in_(["🔍 На проверке", "🧾 Выплаты в проверке"]))
async def check_verification(message: types.Message):
    wait_msg = await message.answer("⏳ Считаю все задания на проверке (это может занять время)...")
    
    tasks = await asyncio.to_thread(get_raw_verification_tasks)
    
    # Сохраняем таски во временную память, чтобы не парсить заново при нажатии кнопок
    db["temp_verif_tasks"] = tasks
    db["verif_detailed"] = False
    
    text = build_verification_text(tasks, detailed=False)
    
    if not tasks:
        await wait_msg.edit_text(text)
        return

    await wait_msg.edit_text(text, parse_mode="HTML", reply_markup=get_verif_keyboard(detailed=False))

@dp.callback_query(F.data == "verif_toggle")
async def toggle_verif_details(call: types.CallbackQuery):
    tasks = db.get("temp_verif_tasks")
    if not tasks:
        await call.answer("Данные устарели, нажмите 'Обновить'", show_alert=True)
        return

    is_currently_detailed = db.get("verif_detailed", False)
    new_detailed_state = not is_currently_detailed
    db["verif_detailed"] = new_detailed_state

    text = build_verification_text(tasks, detailed=new_detailed_state)
    
    try:
        # Если текст слишком длинный, Телеграм не даст отправить его с инлайн-клавиатурой в 1 сообщении
        if len(text) > 4000:
            chunks = split_message_text(text)
            await call.message.edit_text(chunks[0], parse_mode="HTML")
            for i in range(1, len(chunks)):
                # Кнопки цепляем только на последнее сообщение
                markup = get_verif_keyboard(detailed=new_detailed_state) if i == len(chunks)-1 else None
                await call.message.answer(chunks[i], parse_mode="HTML", reply_markup=markup)
        else:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_verif_keyboard(detailed=new_detailed_state))
    except Exception as e:
        await call.answer("Нет изменений.", show_alert=False)
        
    await call.answer()

@dp.callback_query(F.data == "verif_refresh")
async def refresh_verif(call: types.CallbackQuery):
    await call.answer("Обновляю данные с сайта...")
    
    # Заново парсим
    tasks = await asyncio.to_thread(get_raw_verification_tasks)
    db["temp_verif_tasks"] = tasks
    
    is_detailed = db.get("verif_detailed", False)
    
    text = build_verification_text(tasks, detailed=is_detailed)
    
    try:
        if len(text) <= 4000:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=get_verif_keyboard(detailed=is_detailed))
            await call.answer("Данные успешно обновлены!", show_alert=False)
        else:
            # Для длинных текстов переотправляем
            await call.message.delete()
            chunks = split_message_text(text)
            for i in range(len(chunks)):
                markup = get_verif_keyboard(detailed=is_detailed) if i == len(chunks)-1 else None
                await call.message.answer(chunks[i], parse_mode="HTML", reply_markup=markup)
    except:
        await call.answer("Данные актуальны, ничего не изменилось.", show_alert=False)

def get_verification_stats():
    """Собирает данные через AJAX и формирует подробный отчет"""
    all_tasks = []
    ajax_url = "https://unu.im/include/ajax_tasks_works.php"
    current_showed_ids = ""
    start_flag = 1
    
    local_headers = HEADERS.copy()
    local_headers.update({'Referer': "https://unu.im/tasks/works/check", 'X-Requested-With': 'XMLHttpRequest'})

    now = datetime.now()
    midnight = now.replace(hour=23, minute=59, second=59, microsecond=0)

    try:
        while True:
            params = {"showed_ids": current_showed_ids, "start": start_flag, "type": "check"}
            response = requests.get(ajax_url, headers=local_headers, params=params, timeout=15)
            if response.status_code != 200: break
            
            data = response.json()
            html_content = data.get('html', '')
            if not html_content.strip(): break

            soup = BeautifulSoup(html_content, 'html.parser')
            rows = soup.find_all('div', class_='job-table__row')
            if not rows: break

            for row in rows:
                cells = row.find_all('div', class_='job-table__cell')
                if len(cells) < 3: continue
                
                sum_text = cells[2].get_text(strip=True).replace('руб', '').replace(' ', '').replace(',', '.')
                price = float(sum_text) if sum_text else 0.0
                
                status_text = cells[1].get_text(strip=True).lower()
                time_diff = parse_unu_time(status_text)
                pay_time = now + time_diff
                
                clean_time = status_text.replace('на проверке', '').replace('осталось', '').strip()
                
                all_tasks.append({
                    'price': price,
                    'pay_time': pay_time,
                    'time_label': clean_time if clean_time else "неизвестно"
                })

            current_showed_ids = data.get('showed_ids', '')
            start_flag = 0 
            if str(data.get('no_more')) == "1": break
            time.sleep(0.2)

        if not all_tasks:
            return None, 0

        # Сортировка и генерация текста
        all_tasks.sort(key=lambda x: x['pay_time'])
        
        report = [
            f"📥 <b>Задания на проверке:</b>",
            f"🔹 Количество: <code>{len(all_tasks)}</code> шт.",
            f"💰 Общая сумма: <code>{round(sum(t['price'] for t in all_tasks), 2)}</code> ₽",
            f"<i>Показаны данные со всех страниц</i>\n"
        ]

        today_count, today_sum = 0, 0.0
        tomorrow_count, tomorrow_sum = 0, 0.0
        separator_added = False

        for label, group in groupby(all_tasks, key=lambda x: x['time_label']):
            tasks = list(group)
            g_count = len(tasks)
            g_sum = sum(t['price'] for t in tasks)
            g_time = tasks[0]['pay_time']
            
            # 1. Рассчитываем точный обратный отсчет (Осталось времени)
            time_left = g_time - datetime.now()
            
            # Форматируем красиво: "2д 5ч 11м" или "15м 30с"
            days = time_left.days
            hours, rem = divmod(time_left.seconds, 3600)
            minutes, seconds = divmod(rem, 60)
            
            time_parts = []
            if days > 0: time_parts.append(f"{days}д")
            if hours > 0: time_parts.append(f"{hours}ч")
            if minutes > 0: time_parts.append(f"{minutes}м")
            if days == 0 and hours == 0: # Показываем секунды, если осталось меньше часа
                time_parts.append(f"{seconds}с")
            
            time_left_str = " ".join(time_parts) if time_parts else "0с"

            # 2. Определяем категорию (то, что было в скобках "по ...")
            # Извлекаем из сырого текста первую цифру времени (лимит на проверку)
            category_match = re.search(r'(\d+)\s*(минут|час|дн)', label)
            category = f"по {category_match.group(0)}" if category_match else ""

            # 3. Проверка дня
            if g_time > midnight and not separator_added:
                report.append(f"------------------")
                report.append(f"<b>🏁 КОНЕЦ СЕГОДНЯ: {today_count} зад. | {round(today_sum, 2)} ₽</b>")
                report.append(f"------------------")
                separator_added = True

            # 4. ФОРМИРУЕМ КРАСИВУЮ СТРОКУ
            # Формат: ⏳ 4м 20с - 2 зад. (по 4 минуты) • Сегодня в 14:24 • 1.60 ₽
            day_label = "Сегодня" if g_time <= midnight else "Завтра"
            
            # Если это уже после завтрашнего дня, пишем дату
            if g_time > (midnight + timedelta(days=1)):
                day_label = g_time.strftime("%d.%m")

            line = (
                f"⏳ {time_left_str} - <b>{g_count} зад.</b> ({category}) "
                f"• {day_label} в {g_time.strftime('%H:%M')} "
                f"• <b>{round(g_sum, 2)} ₽</b>"
            )
            report.append(line)

            if g_time <= midnight:
                today_count += g_count; today_sum += g_sum
            else:
                tomorrow_count += g_count; tomorrow_sum += g_sum

        if separator_added:
            report.append(f"\n<b>СЛЕД. ДЕНЬ: ЗАДАНИЙ {tomorrow_count} | ПРИБЫЛЬ {round(tomorrow_sum, 2)} ₽</b>")
        else:
            report.append(f"------------------\n<b>ИТОГО СЕГОДНЯ: {today_count} шт. | {round(today_sum, 2)} ₽</b>")

        return "\n".join(report), len(all_tasks)

    except Exception as e:
        return f"❌ Ошибка: {e}", 0
        

# Обработчик выбора типа цели
@dp.message(F.text.in_(["💰 Цель: Баланс", "📈 Цель: За день"]))
async def set_goal_step2(message: types.Message):
    if "Баланс" in message.text:
        db["input_mode"] = "set_goal_bal"
        msg = "Введите сумму итогового баланса (напр. 1100):"
    else:
        db["input_mode"] = "set_goal_daily"
        msg = "Сколько планируете заработать за сегодня (напр. 600):"
    
    await message.answer(msg, reply_markup=types.ReplyKeyboardRemove())


@dp.message(F.text.in_(["📊 Глубокая аналитика", "🧠 Глубокий разбор"]))
async def deep_analytics(message: types.Message):
    pts = db["history"]
    if len(pts) < 10:
        await message.answer("⚠️ Недостаточно данных для глубокого анализа. Продолжай работать!")
        return

    total_earned = 0
    total_tasks = 0
    start_ts = pts[0][0]
    end_ts = pts[-1][0]
    
    # Считаем чистый профит и задачи
    for i in range(len(pts)-1):
        d_bal = pts[i+1][4] - pts[i][4]
        d_task = pts[i+1][1] - pts[i][1]
        if 0 < d_bal < 200: # Фильтр аномалий
            total_earned += d_bal
            if d_task > 0: total_tasks += d_task

    duration_days = max((end_ts - start_ts) / 86400, 0.1)
    avg_per_day = total_earned / duration_days
    avg_per_hour = avg_per_day / 24
    avg_task_price = total_earned / total_tasks if total_tasks > 0 else 0

    text = (
        f"📊 **ПОЛНЫЙ ФИНАНСОВЫЙ ОТЧЕТ**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 **Твоя продуктивность:**\n"
        f"├ Средний доход в день: `{round(avg_per_day, 2)}` ₽\n"
        f"├ Доход в час (24/7): `{round(avg_per_hour, 2)}` ₽\n"
        f"└ Стоимость 1 задания: `{round(avg_task_price, 2)}` ₽\n\n"
        f"⚙️ **Общие показатели:**\n"
        f"├ Всего заработано: `{round(total_earned, 2)}` ₽\n"
        f"├ Выполнено заданий: `{total_tasks}` шт.\n"
        f"└ Дней в системе: `{round(duration_days, 1)}` дн.\n\n"
        f"🚀 **Твой потенциал:**\n"
        f"Если работать по 8 часов активно, твой доход составит около `{round(avg_per_hour * 3 * 8, 2)}` ₽ в день (с учетом активного выполнения)."
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text.in_(["💸 Калькулятор вывода", "💳 Калькулятор выплат"]))
async def withdrawal_calc(message: types.Message):
    balance = db["last"]["balance"]
    # Комиссия биржи UNU обычно около 9% на карты/кошельки
    fee = 0.09 
    net_money = balance * (1 - fee)
    fee_amount = balance * fee

    text = (
        f"💸 **РАСЧЕТ ВЫВОДА СРЕДСТВ**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Текущий баланс: `{balance}` ₽\n"
        f"📉 Комиссия системы (9%): `-{round(fee_amount, 2)}` ₽\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 **Чистыми на руки: `{round(net_money, 2)}` ₽**\n\n"
        f"💡 *Для вывода ровно 500 ₽, твой баланс должен быть ~550 ₽.*"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(lambda msg: db.get("input_mode") == "forecast_date")
async def handle_forecast(message: types.Message):
    db["input_mode"] = None
    try:
        raw = message.text.strip()
        now = datetime.now()
        parts = raw.split('.')
        
        if len(parts) == 2: # ДД.ММ
            day, month = map(int, parts)
            target = datetime(now.year, month, day)
            if target < now: target = target.replace(year=now.year + 1)
        elif len(parts) == 3: # ДД.ММ.ГГГГ
            day, month, year = map(int, parts)
            if year < 100: year += 2000
            target = datetime(year, month, day)
        else: raise ValueError

        days_diff = (target - now).days
        if days_diff <= 0:
            await message.answer("❌ Эта дата уже в прошлом!")
            return

        # Берем среднее из истории
        pts = db["history"]
        total = 0
        for i in range(len(pts)-1):
            diff = pts[i+1][4] - pts[i][4]
            if 0 < diff < 150: total += diff
        
        daily = total / max((pts[-1][0] - pts[0][0]) / 86400, 1)
        forecast_profit = daily * days_diff
        total_bal = db["last"]["balance"] + forecast_profit

        text = (
            f"🔮 **ПРОГНОЗ ДО {target.strftime('%d.%m.%Y')}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ Временной горизонт: `{days_diff}` дн.\n"
            f"📈 Средний темп: `{round(daily, 2)}` ₽/сутки\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Ожидаемая прибыль: `+{round(forecast_profit, 2)}` ₽\n"
            f"🏦 Итоговый баланс: `{round(total_bal, 2)}` ₽\n\n"
            f"🎯 *До цели ({db['goal']} ₽) останется: "
            f"{max(0, round(db['goal'] - total_bal, 2))} ₽*"
        )
        await message.answer(text, parse_mode="Markdown", reply_markup=calculation_menu())
    except:
        await message.answer("❌ Неверный формат! Используй: `01.05.2025` или `01.05`")

@dp.message(F.text == "💰 Баланс")
async def show_balance(message: types.Message):
    write_log(f"Нажата кнопка: Баланс")
    data = await asyncio.to_thread(get_all_data)
    if isinstance(data, dict):
        db["last"]["balance"] = data["balance"]
        await message.answer(f"💰 Ваш текущий баланс: **{data['balance']} руб.**", parse_mode="Markdown")
    elif data == "AUTH_ERROR":
        await message.answer("❌ Ошибка авторизации UNU. Обновите UNU_COOKIE в Render → Environment.")
    else:
        await message.answer("⚠️ Не удалось получить баланс, попробуйте позже.")

@dp.message(F.text == "📊 Отчет 24ч")
async def daily_report(message: types.Message):
    write_log(f"Нажата кнопка: Отчет 24ч")
    now = time.time()
    day_ago = now - 86400
    past_data = [h for h in db["history"] if h[0] > day_ago]
    
    if len(past_data) < 2:
        await message.answer("Недостаточно данных для отчета.")
        return
    
    first = past_data[0]
    last = past_data[-1]
    
    profit = round(last[4] - first[4], 2)
    done = last[1] - first[1]
    
    await message.answer(
        f"📅 **Итоги за 24 часа:**\n\n"
        f"💰 Заработано: `{profit}` ₽\n"
        f"✅ Выполнено: `{done}` заданий\n"
        f"📈 Начальный баланс: `{first[4]}` ₽",
        parse_mode="Markdown"
    )

@dp.message(F.text.in_(["📈 Статус", "🧭 Дашборд"]))
async def show_status(message: types.Message):
    d = await asyncio.to_thread(get_all_data)
    
    if d == "AUTH_ERROR":
        await message.answer("❌ **Ошибка авторизации!**\nОбновите UNU_COOKIE в Render → Environment.")
        return

    if not d or not isinstance(d, dict):
        await message.answer("⚠️ Не удалось получить данные. Сайт UNU временно недоступен.")
        return

    # Считаем общий заработок для ранга
    pts = db["history"]
    total_earned = 0
    if len(pts) >= 2:
        for i in range(len(pts)-1):
            diff = pts[i+1][4] - pts[i][4]
            if 0 < diff < 150:
                total_earned += diff
    
    rank, next_xp, next_name, perc = get_rank_info(total_earned)
    
    # --- ВИЗУАЛИЗАЦИЯ ПРОГРЕСС-БАРА ---
    bar_size = 12
    filled = int(bar_size * perc / 100)
    # Используем разные символы для красоты
    xp_bar = "🟦" * filled + "⬜" * (bar_size - filled)

    # Формируем красивый текст
    text = (
        f"📋 **ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ**\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎖 **Ранг:** `{rank}`\n"
        f"✨ **Опыт:** `{round(total_earned, 1)}` / `{next_xp}` XP\n"
        f"└ {xp_bar} {perc}%\n"
        f"🎯 **До `{next_name}`:** `{round(next_xp - total_earned, 1)}` XP\n"
        f"━━━━━━━━━━━━━━\n"
        f"📊 **Показатели:**\n"
        f"✅ Выполнено: `{d['comp']}`\n"
        f"🛠 В работе: `{d['work']}`\n"
        f"💰 Баланс: `{d['balance']} ₽`\n"
        f"━━━━━━━━━━━━━━\n"
        f"⌚ _Обновлено в {get_now()}_"
    )
    
    # Добавим одну Inline-кнопку для быстрого обновления
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_status")]
    ])

    await message.answer(text, reply_markup=main_menu(), parse_mode="Markdown")
@dp.message(F.text == "⬅️ Назад")
async def back_to_main(message: types.Message):
    write_log(f"Нажата кнопка: Назад")
    await message.answer("Главное меню", reply_markup=main_menu())

@dp.message(F.text.in_(["⏲ Таймер", "⏱ Фокус-таймер"]))
async def set_timer_start(message: types.Message):
    write_log(f"Нажата кнопка: Таймер")
    db["input_mode"] = "timer"
    await message.answer("На сколько минут запустить таймер? (введи число)", reply_markup=types.ReplyKeyboardRemove())

@dp.message(F.text.in_(["⏳ Чек таймера", "⏳ Статус таймера"]))
async def check_timer(message: types.Message):
    write_log(f"Нажата кнопка: Чек таймера")
    remains = db["timer_end"] - datetime.now()
    if remains.total_seconds() > 0:
        await message.answer(f"⏳ Осталось: {str(remains).split('.')[0]}", reply_markup=main_menu())
    else:
        db["timer_end"] = None
        await message.answer("Таймер истек!", reply_markup=main_menu())

@dp.message(F.text.in_(["🎯 Цель", "🚀 Моя цель"]))
@dp.message(Command("goal"))
async def check_goal_now(message: types.Message):
    if db.get("goal", 0) <= 0:
        kb = ReplyKeyboardBuilder()
        kb.button(text="➕ Поставить цель")
        kb.button(text="⬅️ Назад")
        await message.answer("🎯 Цель еще не установлена. Начнем марафон?", reply_markup=kb.as_markup(resize_keyboard=True))
        return

    current_bal = db["last"]["balance"]
    
    # Считаем темп для прогноза времени
    now_ts = time.time()
    pts = [p for p in db["history"] if p[0] > (now_ts - 10800)] # за последние 3 часа
    profit_3h = 0
    if len(pts) >= 2:
        for i in range(len(pts)-1):
            diff = pts[i+1][4] - pts[i][4]
            if 0 < diff < 150: profit_3h += diff
    profit_per_hour = round(profit_3h / 3, 2)
    
    text = f"📊 **Статус выполнения цели**\n"
    text += get_progress_bar(current_bal, db["goal"], profit_per_hour)
    
    # КНОПКИ УПРАВЛЕНИЯ
    kb = ReplyKeyboardBuilder()
    if current_bal >= db["goal"]:
        kb.button(text="🔥 Поставить новую цель")
    else:
        kb.button(text="✏️ Изменить цель")
        kb.button(text="❌ Удалить цель")
    
    kb.button(text="⬅️ Назад")
    kb.adjust(2, 1)
    
    await message.answer(text, reply_markup=kb.as_markup(resize_keyboard=True), parse_mode="Markdown")

# Кнопка "Удалить цель"
@dp.message(F.text == "❌ Удалить цель")
async def delete_goal(message: types.Message):
    db["goal"] = 0
    db["goal_daily"] = 0
    save_db()
    await message.answer("🗑 Цель удалена.", reply_markup=main_menu())

# Кнопки "Изменить", "Новая" или "Поставить"
@dp.message(F.text.in_(["✏️ Изменить цель", "🔥 Поставить новую цель", "➕ Поставить цель"]))
async def change_goal_btn(message: types.Message):
    # Просто вызываем уже готовую логику установки цели
    await set_goal_start(message)

def get_time_bar():
    now = datetime.now()
    # Сколько минут прошло с начала дня
    passed_mins = now.hour * 60 + now.minute
    total_mins = 24 * 60
    
    percent = int((passed_mins / total_mins) * 100)
    width = 10
    filled = int(width * percent / 100)
    bar = "🟦" * filled + "⬜" * (width - filled)
    
    hours_left = 23 - now.hour
    mins_left = 59 - now.minute
    
    return f"⏳ До конца дня: `[{bar}]` {percent}%\n🕒 Осталось: `{hours_left}ч {mins_left}м`"


@dp.message(F.text.in_(["📜 Логи", "🧰 Логи и сервис"]))
async def show_bot_logs(message: types.Message):
    write_log(f"Нажата кнопка: Логи")
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        last_logs = "".join(lines[-15:])
        await message.answer(f"Последние события:\n\n`{last_logs}`", parse_mode="Markdown")
    else:
        await message.answer("Файл логов пуст.")

@dp.message(F.text.in_(["⏸ Стоп", "▶️ Старт"]))
async def toggle_monitoring(message: types.Message):
    if message.text == "⏸ Стоп":
        db["is_running"] = False
        write_log("Мониторинг остановлен пользователем")
        await message.answer("🛑 Мониторинг остановлен.", reply_markup=main_menu())
    else:
        if not db["is_running"]:
            asyncio.create_task(monitoring_task(bot, message.chat.id))
            write_log("Мониторинг запущен пользователем")
            await message.answer("🚀 Мониторинг возобновлен!", reply_markup=main_menu())
        else:
            await message.answer("⚡ Мониторинг уже работает.", reply_markup=main_menu())
    save_db()

@dp.message(F.text.in_(["🌍 Lifetime", "🧬 Путь за все время"]))
async def lifetime(message: types.Message):
    pts = db["history"]
    if not pts: return
    
    total_earned = 0
    total_withdrawn = 0
    for i in range(len(pts)-1):
        d = pts[i+1][4] - pts[i][4]
        if d <= -150: total_withdrawn += abs(d)
        elif d > 0: total_earned += d

    total_tasks = pts[-1][1] - pts[0][1]
    avg_price = round(total_earned / total_tasks, 2) if total_tasks > 0 else 0
    
    await message.answer(
        f"🌍 **За всё время работы:**\n"
        f"💰 Заработано: `{round(total_earned, 2)}` ₽\n"
        f"💸 Выведено: `{round(total_withdrawn, 2)}` ₽\n"
        f"✅ Выполнено: `{total_tasks}` задач\n"
        f"💎 Средний чек: `{avg_price}` ₽/зад."
    )

@dp.message(F.text.in_(["📅 По дням", "🗓 Журнал по дням"]))
async def daily_journal(message: types.Message):
    if not db.get("history"):
        await message.answer("История пуста.")
        return

    days_data = {}
    for entry in db["history"]:
        date_key = datetime.fromtimestamp(entry[0]).strftime("%Y-%m-%d")
        if date_key not in days_data:
            days_data[date_key] = []
        days_data[date_key].append(entry)

    today_key = datetime.now().strftime("%Y-%m-%d")
    if db.get("day_seed_date") != today_key:
        db["day_seed_date"] = today_key
        db["day_seed_profit"] = DAY_SEED_PROFIT
        db["day_seed_tasks"] = DAY_SEED_TASKS
        save_db()

    all_days = set(days_data.keys())
    all_days.add(today_key)
    sorted_keys = sorted(all_days, key=lambda x: datetime.strptime(x, "%Y-%m-%d"))

    report_header = f"📅 **Полные итоги за все время ({len(sorted_keys)} дн.):**\n\n"
    lines = []
    for date_key in sorted_keys:
        pts = days_data.get(date_key, [])

        day_profit = 0.0
        for i in range(len(pts) - 1):
            diff = float(pts[i + 1][4]) - float(pts[i][4])
            if 0 < diff < 150:
                day_profit += diff

        day_tasks = max(0, int(pts[-1][1]) - int(pts[0][1])) if len(pts) >= 2 else 0

        if date_key == today_key:
            today_profit, today_tasks = calculate_today_totals(
                current_bal=float(db["last"]["balance"]),
                current_comp=int(db["last"]["comp"]),
            )
            day_profit = float(today_profit)
            day_tasks = int(today_tasks)

        date_label = datetime.strptime(date_key, "%Y-%m-%d").strftime("%d.%m")
        lines.append(f"▫️ *{date_label}*: `+{round(day_profit, 1)}`₽ | `{int(day_tasks)}` шт.")

    # Логика разделения длинного сообщения
    full_report = report_header + "\n".join(lines)
    
    if len(full_report) <= 4000:
        await message.answer(full_report, parse_mode="Markdown")
    else:
        # Разбиваем на части по 4000 символов
        chunk_size = 4000
        for i in range(0, len(full_report), chunk_size):
            chunk = full_report[i:i + chunk_size]
            await message.answer(chunk, parse_mode="Markdown")

@dp.message(Command("goal"))
async def check_goal_now(message: types.Message):
    if db["goal"] <= 0:
        await message.answer("🎯 Цель не установлена. Используй `/set_goal 500`", reply_markup=main_menu())
        return

    # Берем текущий баланс из последней проверки
    current_bal = db["last"]["balance"]
    
    # Используем твою функцию прогресс-бара
    # (profit_per_hour можно передать 0, если не нужен расчет времени в этой команде)
    text = f"📊 **Текущий прогресс:**\n"
    text += get_progress_bar(current_bal, db["goal"], 0)
    
    await message.answer(text, reply_markup=main_menu(), parse_mode="Markdown")

@dp.message(F.text.in_(["🕒 Сон", "🌙 Режим сна"]))
async def handle_sleep_button(message: types.Message):
    # Если сон УЖЕ включен
    if db.get("paused_until") and datetime.now() < db["paused_until"]:
        until_str = db["paused_until"].strftime("%H:%M (%d.%m)")
        
        # Считаем сколько осталось
        diff = db["paused_until"] - datetime.now()
        hours, remainder = divmod(int(diff.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        
        text = (
            f"💤 **Режим сна активен**\n"
            f"⏰ Пробуждение в: `{until_str}`\n"
            f"⏳ Осталось: `{hours}ч {minutes}м`\n\n"
            f"📊 **Зафиксированные данные:**\n"
            f"💰 Баланс: {db['pre_sleep_data']['balance']} ₽\n"
            f"✅ Выполнено: {db['pre_sleep_data']['comp']}"
        )
        await message.answer(text, reply_markup=sleep_manage_menu(), parse_mode="Markdown")
    else:
        # Если сон НЕ включен — запускаем обычный процесс установки
        db["input_mode"] = "sleep"
        await message.answer("До скольки отключить мониторинг? (Формат ЧЧ:ММ, например 08:00)", 
                             reply_markup=types.ReplyKeyboardRemove())

@dp.message(F.text.in_(["🌅 Проснуться сейчас", "☀️ Выключить сон"]))
async def cancel_sleep(message: types.Message):
    db["paused_until"] = None
    db["pre_sleep_data"] = None
    save_db()
    await message.answer("☀️ Режим сна отменен! Мониторинг возобновлен.", reply_markup=main_menu())

@dp.message(F.text.in_(["⏰ Изменить время", "⏰ Сдвинуть время"]))
async def change_sleep_time(message: types.Message):
    db["input_mode"] = "sleep"
    await message.answer("Введите новое время пробуждения (ЧЧ:ММ):", 
                         reply_markup=types.ReplyKeyboardRemove())

# --- УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ВВОДА ---
@dp.message(lambda msg: db["input_mode"] is not None)
async def handle_inputs(message: types.Message):
    mode = db["input_mode"]
    db["input_mode"] = None 
    
    # 1. Логика ТАЙМЕРА
    if mode == "timer":
        if message.text.isdigit():
            mins = int(message.text)
            db["timer_end"] = datetime.now() + timedelta(minutes=mins)
            await message.answer(f"✅ Таймер на {mins} мин запущен!", reply_markup=main_menu())
            
            async def wait_timer(m, chat_id):
                await asyncio.sleep(m * 60)
                if db["timer_end"]: 
                    await bot.send_message(chat_id, f"🔔 **ВРЕМЯ ВЫШЛО!** ({m} мин)")
                    db["timer_end"] = None
            
            asyncio.create_task(wait_timer(mins, message.chat.id))
        else:
            await message.answer("❌ Введи число (минуты).", reply_markup=main_menu())

    elif mode == "set_goal_bal":
        if message.text.isdigit():
            db["goal"] = int(message.text)
            db["goal_daily"] = 0 # Сбрасываем дневную, если ставим общую
            save_db()
            await message.answer(f"🎯 Цель по балансу `{db['goal']} ₽` установлена!", reply_markup=main_menu())
        else:
            await message.answer("❌ Введите число.", reply_markup=main_menu())

    elif mode == "set_goal_daily":
        if message.text.isdigit():
            val = int(message.text)
            db["goal_daily"] = val
            
            # --- ИСПРАВЛЕНИЕ ЛОГИКИ ---
            # Вместо прибавления к текущему балансу, 
            # мы прибавляем цель к балансу, который был в начале дня.
            # Если данных о начале дня нет, используем текущий.
            
            start_bal = db.get("day_start_bal", 0)
            if start_bal == 0:
                # Если бот только запустился и не знает баланс на 00:00
                db["goal"] = db["last"]["balance"] + val
            else:
                db["goal"] = start_bal + val
            # ---------------------------

            save_db()
            await message.answer(
                f"🎯 Цель: Заработать `{val} ₽` за сегодня (всего).\n"
                f"Нужно достичь баланса: `{db['goal']} ₽`", 
                reply_markup=main_menu()
            )

    # 2. Логика СНА
    elif mode == "sleep":
        try:
            time_str = message.text.strip()
            t = datetime.strptime(time_str, "%H:%M").time()
            now = datetime.now()
            target = datetime.combine(now.date(), t)
            
            if target <= now:
                target += timedelta(days=1)
            
            current_stats = await asyncio.to_thread(get_all_data)
            if current_stats == "AUTH_ERROR" or current_stats is None:
                await message.answer("⚠️ Ошибка связи с сайтом. Сон отменен.", reply_markup=main_menu())
                return

            db["pre_sleep_data"] = current_stats
            db["paused_until"] = target
            
            text = (f"💤 **Мониторинг уходит в сон**\n"
                    f"⏰ Пробуждение: `{target.strftime('%d.%m %H:%M')}`\n\n"
                    f"📊 **Статистика зафиксирована:**\n"
                    f"💰 Баланс: {current_stats['balance']} ₽\n"
                    f"✅ Выполнено: {current_stats['comp']}\n"
                    f"🛠 В работе: {current_stats['work']}")
            
            await message.answer(text, reply_markup=main_menu(), parse_mode="Markdown")
        except:
            await message.answer("❌ Ошибка формата! Пиши ЧЧ:ММ (напр. 08:00)", reply_markup=main_menu())

    # 3. Логика АНАЛИЗА ПЕРИОДА
    elif mode == "analyze_period":
        seconds = parse_duration(message.text)
        if not seconds or not db["history"]:
            await message.answer("❌ Нет данных.", reply_markup=main_menu())
            return

        now_ts = time.time()
        # Данные текущего периода
        curr_pts = [p for p in db["history"] if p[0] > (now_ts - seconds)]
        # Данные предыдущего такого же периода (для сравнения)
        prev_pts = [p for p in db["history"] if (now_ts - 2*seconds) < p[0] <= (now_ts - seconds)]

        if len(curr_pts) < 2:
            await message.answer("⚠️ Мало данных.", reply_markup=main_menu())
            return

        def calc_stats(pts):
            profit = 0
            tasks = pts[-1][1] - pts[0][1] if pts else 0
            for i in range(len(pts)-1):
                diff = pts[i+1][4] - pts[i][4]
                if 0 < diff < 150: profit += diff
            return round(profit, 2), tasks

        curr_profit, curr_tasks = calc_stats(curr_pts)
        
        # Сравнение
        compare_text = ""
        if len(prev_pts) >= 2:
            prev_profit, _ = calc_stats(prev_pts)
            if prev_profit > 0:
                diff_perc = ((curr_profit - prev_profit) / prev_profit) * 100
                icon = "📈" if diff_perc > 0 else "📉"
                compare_text = f"{icon} `{diff_perc:+.1f}%` к прошлому периоду\n"

        # Вычисляем темп в час для прогресс-бар

        hours = seconds / 3600
        profit_per_hour = round(curr_profit / hours, 2) if hours > 0 else 0
        
        # Передаем ТРИ аргумента, как требует функция
        goal_text = get_progress_bar(db["last"]["balance"], db["goal"], profit_per_hour)

        res_text = (
            f"🚀 **Аналитика за {message.text}**\n"
            f"━━━━━━━━━━━━━━\n"
            f"💰 Прибыль: `+{curr_profit}` ₽\n"
            f"{compare_text}"
            f"✅ Выполнено: `{curr_tasks}` шт.\n"
            f"━━━━━━━━━━━━━━\n"
            f"⚡ Темп: `{round(curr_profit / (seconds/3600), 2)}` ₽/час"
            f"{goal_text}"
        )
        await message.answer(res_text, reply_markup=main_menu(), parse_mode="Markdown")

# --- ЗАПУСК ---
async def main():
    # 1) HTTP-порт для Render Web Service поднимаем первым делом
    await start_web_server()
    asyncio.create_task(keep_alive_task())
    load_db()
    db["is_running"] = False  # как в back04: сбрасываем флаг при старте процесса
    # База для сегодняшнего отсчёта: +30 ₽ и 29 задач, дальше значения растут сверху.
    today_key = datetime.now().strftime("%Y-%m-%d")
    if db.get("day_seed_date") != today_key:
        db["day_seed_date"] = today_key
        db["day_seed_profit"] = DAY_SEED_PROFIT
        db["day_seed_tasks"] = DAY_SEED_TASKS
    if not db.get("day_start_bal"):
        db["day_start_bal"] = float(db.get("last", {}).get("balance", 0))
    if not db.get("day_start_comp"):
        db["day_start_comp"] = int(db.get("last", {}).get("comp", 0))
    save_db()
    print("[STARTUP] БД загружена. Проверка настроек мониторинга:")
    _log_monitoring_state()
    default_peer = TELEGRAM_CHAT_ID or db.get("peer_id")
    if default_peer:
        db["peer_id"] = db["chat_id"] = default_peer
        # после перезапуска БД может быть пустой: берём стартовые значения с UNU,
        # иначе первое уведомление покажет весь баланс как "прирост"
        init = await asyncio.to_thread(get_all_data)
        if isinstance(init, dict):
            db["last"] = init
            db["notified"] = {k: init[k] for k in ("comp", "work", "balance")}
            save_db()
        asyncio.create_task(midnight_check(bot, default_peer))
        asyncio.create_task(monitoring_task(bot, default_peer))
        asyncio.create_task(payout_monitor_task(bot, default_peer))
    else:
        print("[STARTUP] TELEGRAM_CHAT_ID не задан — мониторинг запустится после команды /start в Telegram.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот выключен.")