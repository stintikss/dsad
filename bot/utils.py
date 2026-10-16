# Утилиты: ранги, форматирование, прогресс-бары

import re
import time
from datetime import datetime
from itertools import groupby
from collections import defaultdict

from .tg_shim import InlineKeyboardMarkup, InlineKeyboardButton

from .database import db
import os
import json
import requests
import asyncio
from .config import WEBHOOK_URL


def get_rank(xp):
    ranks = [
        (0, "Новичок 🌱"),
        (1000, "Подмастерье 🔨"),
        (5000, "Фармер 🚜"),
        (15000, "Магнат 💎"),
        (50000, "Акула UNU 🦈"),
        (100000, "Легенда 👑"),
    ]
    current_rank = ranks[0][1]
    next_rank_xp = ranks[1][0]
    for x, r in ranks:
        if xp >= x:
            current_rank = r
        else:
            next_rank_xp = x
            break
    return current_rank, next_rank_xp


def get_rank_info(total_earned):
    ranks = [
        (0, "Новичок 🌱"),
        (100, "Стажёр 📋"),
        (300, "Трудяга 🛠"),
        (700, "Опытный 🎖"),
        (1500, "Мастер 🏆"),
        (3000, "Специалист 💎"),
        (7000, "Фармер-Про 🚜"),
        (15000, "Бизнесмен 💼"),
        (30000, "Магнат 💰"),
        (70000, "Акула UNU 🦈"),
        (150000, "Олигарх 🏰"),
        (500000, "Легенда 👑"),
    ]
    current_r = ranks[0]
    next_r = ranks[1]
    for i, r in enumerate(ranks):
        if total_earned >= r[0]:
            current_r = r
            next_r = ranks[i + 1] if i + 1 < len(ranks) else r
        else:
            break
    if next_r[0] != current_r[0]:
        progress = int(((total_earned - current_r[0]) / (next_r[0] - current_r[0])) * 100)
    else:
        progress = 100
    return current_r[1], next_r[0], next_r[1], progress


def split_message_text(text, limit=4000):
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return parts


def build_verification_text(tasks, detailed=False):
    if not tasks:
        return "✅ На проверке сейчас ничего нет."
    total_sum = sum(t["price"] for t in tasks)
    days_grouped = defaultdict(list)
    for t in tasks:
        date_str = t["pay_time"].strftime("%Y-%m-%d")
        days_grouped[date_str].append(t)
    now_date = datetime.now().date()
    weekdays = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    report = [
        "📊 <b>СВОДКА ПО ПРОВЕРКАМ</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📦 Всего заданий: <b>{len(tasks)}</b> шт.",
        f"💰 Ожидаемая прибыль: <b>{round(total_sum, 2)} ₽</b>",
        "━━━━━━━━━━━━━━━━━━━━\n",
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
        day_sum = sum(t["price"] for t in day_tasks)
        report.append(f"{day_title} — {len(day_tasks)} зад. | <b>{round(day_sum, 2)} ₽</b>")
        if detailed:
            report.append("┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈")
            for label, group in groupby(day_tasks, key=lambda x: x["time_label"]):
                sub_tasks = list(group)
                g_count = len(sub_tasks)
                g_sum = sum(t["price"] for t in sub_tasks)
                g_time = sub_tasks[0]["pay_time"]
                time_left = g_time - datetime.now()
                hours, rem = divmod(time_left.seconds, 3600)
                minutes, seconds = divmod(rem, 60)
                time_parts = []
                if time_left.days > 0:
                    time_parts.append(f"{time_left.days}д")
                if hours > 0:
                    time_parts.append(f"{hours}ч")
                if minutes > 0:
                    time_parts.append(f"{minutes}м")
                time_left_str = " ".join(time_parts) if time_parts else f"{seconds}с"
                category_match = re.search(r"(\d+)\s*(минут|час|дн)", label)
                category = f"({category_match.group(0)})" if category_match else ""
                report.append(
                    f"▫️ {g_time.strftime('%H:%M')} ⏳ {time_left_str} "
                    f"• <b>{g_count} шт.</b> {category} • {round(g_sum, 2)} ₽"
                )
            report.append("")
    if not detailed:
        report.append("\n<i>Нажмите кнопку ниже, чтобы увидеть точное время каждой выплаты.</i>")
    return "\n".join(report)


def get_verif_keyboard(detailed=False):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 Показать детали" if not detailed else "📉 Скрыть детали",
                    callback_data="verif_toggle",
                )
            ],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="verif_refresh")],
        ]
    )
    return kb


def calculate_eta(remaining_money, history):
    if not history or len(history) < 10:
        return "Нужно больше данных"
    now = time.time()
    recent_pts = [p for p in history if p[0] > (now - 21600)]
    if len(recent_pts) < 2:
        return "Темп не определен"
    total_recent_profit = 0
    for i in range(len(recent_pts) - 1):
        diff = recent_pts[i + 1][4] - recent_pts[i][4]
        if 0 < diff < 150:
            total_recent_profit += diff
    time_span_mins = (recent_pts[-1][0] - recent_pts[0][0]) / 60
    if time_span_mins <= 0 or total_recent_profit <= 0:
        return "Жду новых оплат..."
    profit_per_min = total_recent_profit / time_span_mins
    mins_needed = remaining_money / profit_per_min
    if mins_needed > 1440:
        return f"~{round(mins_needed / 1440, 1)} дн."
    if mins_needed > 60:
        return f"~{round(mins_needed / 60, 1)} ч."
    return f"~{int(mins_needed)} мин."


def calculate_today_totals(current_bal=None, current_comp=None):
    """Считает прибыль/задачи за текущий день (плюсовые изменения + хвост + seed)."""
    now = datetime.now()
    today_key = now.strftime("%Y-%m-%d")
    if current_bal is None:
        current_bal = float(db.get("last", {}).get("balance", 0.0))
    if current_comp is None:
        current_comp = int(db.get("last", {}).get("comp", 0))

    today_pts = [
        p for p in db.get("history", [])
        if datetime.fromtimestamp(p[0]).strftime("%Y-%m-%d") == today_key
    ]

    earned_today = 0.0
    tasks_today = 0

    for i in range(len(today_pts) - 1):
        diff = float(today_pts[i + 1][4]) - float(today_pts[i][4])
        if 0 < diff < 150:
            earned_today += diff

    if len(today_pts) >= 2:
        tasks_today = max(0, int(today_pts[-1][1]) - int(today_pts[0][1]))

    if today_pts:
        tail_profit = float(current_bal) - float(today_pts[-1][4])
        if 0 < tail_profit < 150:
            earned_today += tail_profit
        tail_tasks = int(current_comp) - int(today_pts[-1][1])
        if tail_tasks > 0:
            tasks_today += tail_tasks
    else:
        day_start_bal = float(db.get("day_start_bal", current_bal))
        day_start_comp = int(db.get("day_start_comp", current_comp))
        tail_profit = float(current_bal) - day_start_bal
        if tail_profit > 0:
            earned_today += tail_profit
        tail_tasks = int(current_comp) - day_start_comp
        if tail_tasks > 0:
            tasks_today += tail_tasks

    if db.get("day_seed_date") == today_key:
        earned_today += float(db.get("day_seed_profit", 0.0))
        tasks_today += int(db.get("day_seed_tasks", 0))

    return round(earned_today, 2), int(tasks_today)


def get_progress_bar(current_bal, goal, profit_per_hour):
    if not goal:
        return "\n🎯 Цель не установлена."
    now = datetime.now()
    earned_today, _ = calculate_today_totals(current_bal=current_bal, current_comp=db.get("last", {}).get("comp", 0))
    goal_daily = db.get("goal_daily", 0)
    roi = (earned_today / goal_daily * 100) if goal_daily > 0 else 0
    hours_left = (23 - now.hour) + (59 - now.minute) / 60
    forecast_end_day = current_bal + (profit_per_hour * hours_left)
    percent_total = min(int((current_bal / goal) * 100), 100)
    bar_total = "█" * int(10 * percent_total / 100) + "░" * (10 - int(10 * percent_total / 100))
    if goal_daily > 0:
        percent_goal = min(int((earned_today / goal_daily) * 100), 100)
        bar_goal = "🔵" * int(10 * percent_goal / 100) + "⚪" * (10 - int(10 * percent_goal / 100))
    else:
        percent_goal = 0
        bar_goal = "⚪" * 10
    remains = max(round(goal - current_bal, 2), 0)
    remains_daily = 0
    if goal_daily > 0:
        remains_daily = max(round(goal_daily - earned_today, 2), 0)
    remains_to_earn = remains_daily if goal_daily > 0 else remains
    eta_text = calculate_eta(remains_to_earn, db["history"])
    res = f"\n📊 **АНАЛИЗ ВЫПОЛНЕНИЯ:**\n"
    res += f"🎯 План: `{goal_daily} ₽` | **ROI: {round(roi, 1)}%**\n"
    res += f"📈 За сегодня: `+{round(earned_today, 2)}` ₽\n"
    res += f"🏆 **Прогресс дня:** `[{bar_goal}]` {percent_goal}%\n"
    res += f"💰 **До цели:** `[{bar_total}]` {percent_total}%\n"
    res += f"🏁 Целевая сумма: `{goal} ₽` (Нужно по балансу: `{round(remains, 2)}` ₽)\n"
    res += f"🔮 Прогноз к 00:00: `{round(forecast_end_day, 2)}` ₽\n"
    res += f"\n{get_time_bar()}\n"
    res += "━━━━━━━━━━━━━━\n"
    if remains_to_earn > 0:
        res += f"➕ Нужно заработать плюсом: `+{round(remains_to_earn, 2)}` ₽\n"
        res += f"💼 До общей цели по балансу: `+{round(remains, 2)}` ₽\n"
        if goal_daily > 0:
            res += f"📌 До плана дня: `+{round(remains_daily, 2)}` ₽\n"
        res += f"⏳ Оценка по темпу: `{eta_text}`"
    else:
        res += "✅ **Цель выполнена! Красава!** 🔥"
    if profit_per_hour > 0:
        hours_until_midnight = 24 - now.hour
        predicted_bonus = round(profit_per_hour * hours_until_midnight, 2)
        res += f"\n\n🔮 **Прогноз:**\nЕсли не сбавишь темп, до конца дня прилетит еще `+{predicted_bonus}` ₽"
    return res


def get_time_bar():
    now = datetime.now()
    passed_mins = now.hour * 60 + now.minute
    total_mins = 24 * 60
    percent = int((passed_mins / total_mins) * 100)
    width = 10
    filled = int(width * percent / 100)
    bar = "🟦" * filled + "⬜" * (width - filled)
    hours_left = 23 - now.hour
    mins_left = 59 - now.minute
    return f"⏳ До конца дня: `[{bar}]` {percent}%\n🕒 Осталось: `{hours_left}ч {mins_left}м`"


async def send_webhook_notification(title: str, body: str, data: dict = None):
    """Отправляет уведомление на внешний webhook (web backend). Не мешает основному потоку — выполняется в thread."""
    if not WEBHOOK_URL:
        return False
    payload = {"title": title, "body": body, "data": data or {}}

    def _post():
        try:
            r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
            return r.status_code
        except Exception:
            return None

    status = await asyncio.to_thread(_post)
    return status
