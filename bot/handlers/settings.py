# Расширенные настройки

from ..tg_shim import Router, F, Message, ReplyKeyboardBuilder

from ..database import db, save_db, write_log
from ..keyboards import (
    main_menu,
    settings_menu,
    settings_limits_menu,
    settings_payout_menu,
    settings_toggles_menu,
)

router = Router(name="settings")

# --- Главное меню настроек ---
@router.message(F.text.in_(["⚙️ Настройки", "🎛 Параметры"]))
async def show_settings(message: Message):
    write_log("Нажата кнопка: Настройки")
    s = db.get("settings") or {}
    lim = db.get("limits") or {}
    payout = db.get("payout_settings") or {}
    text = (
        "⚙️ <b>Центр настроек</b>\n\n"
        "<b>Текущие параметры:</b>\n"
        f"• 📋 Пороги: Выполнено ±{lim.get('comp', 2)}, В работе ±{lim.get('work', 2)}, Баланс ±{lim.get('bal', 10)} ₽\n"
        f"• 🔔 Скоро выплата: {'✅ Включено' if payout.get('enabled', True) else '❌ Выключено'}\n"
        f"• ⏱ Мониторинг: {s.get('monitoring_interval_min', 60)}-{s.get('monitoring_interval_max', 95)} сек\n\n"
        "Выберите раздел ниже:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=settings_menu())


@router.message(F.text == "⬅️ В настройки")
async def back_to_settings(message: Message):
    await show_settings(message)


# --- Пороги уведомлений ---
@router.message(F.text.in_(["📋 Пороги уведомлений", "📐 Пороги сигналов"]))
async def show_limits_menu(message: Message):
    await message.answer(
        "📋 <b>Пороги уведомлений</b>\n\nВыберите, какой параметр хотите изменить.",
        parse_mode="HTML",
        reply_markup=settings_limits_menu(),
    )


@router.message(lambda msg: msg.text and "Настроить" in msg.text)
async def set_limit_step1(message: Message):
    txt = message.text
    if "Выполнено" in txt:
        db["temp_target"] = "comp"
    elif "работе" in txt:
        db["temp_target"] = "work"
    elif "Баланс" in txt:
        db["temp_target"] = "bal"
    await message.answer(
        f"Введите число (порог) для «{txt.split('Настроить', maxsplit=1)[-1].strip()}»:",
        reply_markup=message.reply_markup,
    )


# --- Скоро выплата ---
@router.message(F.text.in_(["🔔 Уведомления «Скоро выплата»", "🔔 Сценарий выплат"]))
async def show_payout_settings(message: Message):
    await message.answer(
        "🔔 <b>Скоро выплата</b>\n\nУправление напоминаниями о предстоящих выплатах.",
        parse_mode="HTML",
        reply_markup=settings_payout_menu(),
    )


@router.message(F.text.in_(["🔔 Вкл/Выкл СКОРО ВЫПЛАТА", "🔔 Переключить выплаты"]))
async def toggle_payout_notif(message: Message):
    if "payout_settings" not in db:
        db["payout_settings"] = {"enabled": True, "intervals": [60, 30, 10, 5, 1]}
    db["payout_settings"]["enabled"] = not db["payout_settings"]["enabled"]
    save_db()
    state = "включены ✅" if db["payout_settings"]["enabled"] else "выключены ❌"
    await message.answer(f"Уведомления «Скоро выплата» теперь {state}.", reply_markup=settings_payout_menu())


@router.message(F.text.in_(["⏳ Интервалы выплат (мин)", "⏳ Интервалы выплат"]))
async def set_payout_intervals(message: Message):
    db["input_mode"] = "payout_intervals"
    current = ", ".join(map(str, db.get("payout_settings", {}).get("intervals", [60, 30, 10, 5, 1])))
    await message.answer(
        f"Текущие интервалы (минуты до выплаты): <code>{current}</code>\n\n"
        "Введите новые через запятую (например: <code>60, 30, 15, 5, 1</code>):",
        parse_mode="HTML",
    )


# --- Интервал мониторинга ---
@router.message(F.text.in_(["⏱ Интервал мониторинга", "🛰 Частота сканирования"]))
async def show_monitoring_interval(message: Message):
    db["input_mode"] = "monitoring_interval"
    save_db()  # сохраняем, чтобы при следующем сообщении обработчик видел режим
    s = db.get("settings") or {}
    lo = s.get("monitoring_interval_min", 60)
    hi = s.get("monitoring_interval_max", 95)
    await message.answer(
        f"Сейчас мониторинг опрашивает сайт каждые <b>{lo}–{hi}</b> сек (случайное значение в этом диапазоне).\n\n"
        "Введите новый интервал в формате: <code>мин макс</code> (например: <code>45 120</code> или <code>10 15</code> сек):",
        parse_mode="HTML",
    )


# --- Вкл/выкл функций ---
@router.message(F.text.in_(["🔧 Вкл/выкл функций", "🧩 Модули и алерты"]))
async def show_toggles(message: Message):
    s = db.get("settings") or {}
    lines = [
        f"📢 Сводки (выполнено/в работе/баланс): {'✅' if s.get('notify_updates', True) else '❌'}",
        f"💰 Крупные оплаты (30+ ₽): {'✅' if s.get('notify_big_payout', True) else '❌'}",
        f"😴 Напоминание «тишина 1 час»: {'✅' if s.get('notify_idle_hour', True) else '❌'}",
        f"🎖 Уведомление о новом ранге: {'✅' if s.get('notify_rank', True) else '❌'}",
        f"🎯 Уведомление о достижении цели: {'✅' if s.get('notify_goal', True) else '❌'}",
    ]
    await message.answer(
        "🔧 <b>Вкл/выкл функций</b>\n\n" + "\n".join(lines) + "\n\nНажмите кнопку для переключения.",
        parse_mode="HTML",
        reply_markup=settings_toggles_menu(),
    )


@router.message(F.text.in_(["📢 Сводки (выполнено/в работе/баланс)", "📢 Сводки по метрикам"]))
async def toggle_notify_updates(message: Message):
    if "settings" not in db:
        db["settings"] = {}
    db["settings"]["notify_updates"] = not db["settings"].get("notify_updates", True)
    save_db()
    state = "включены ✅" if db["settings"]["notify_updates"] else "выключены ❌"
    await message.answer(f"Сводки «Обновление» теперь {state}.", reply_markup=settings_toggles_menu())


@router.message(F.text.in_(["💰 Крупные оплаты (30+ ₽)", "💰 Крупные оплаты 30+ ₽"]))
async def toggle_notify_big_payout(message: Message):
    if "settings" not in db:
        db["settings"] = {}
    db["settings"]["notify_big_payout"] = not db["settings"].get("notify_big_payout", True)
    save_db()
    state = "включены ✅" if db["settings"]["notify_big_payout"] else "выключены ❌"
    await message.answer(f"Уведомления о крупных оплатах теперь {state}.", reply_markup=settings_toggles_menu())


@router.message(F.text.in_(["😴 Напоминание «тишина 1 час»", "😴 Анти-простой 1 час"]))
async def toggle_notify_idle(message: Message):
    if "settings" not in db:
        db["settings"] = {}
    db["settings"]["notify_idle_hour"] = not db["settings"].get("notify_idle_hour", True)
    save_db()
    state = "включено ✅" if db["settings"]["notify_idle_hour"] else "выключено ❌"
    await message.answer(f"Напоминание «тишина 1 час» теперь {state}.", reply_markup=settings_toggles_menu())


@router.message(F.text.in_(["🎖 Уведомление о новом ранге", "🎖 Новый ранг"]))
async def toggle_notify_rank(message: Message):
    if "settings" not in db:
        db["settings"] = {}
    db["settings"]["notify_rank"] = not db["settings"].get("notify_rank", True)
    save_db()
    state = "включено ✅" if db["settings"]["notify_rank"] else "выключено ❌"
    await message.answer(f"Уведомление о новом ранге теперь {state}.", reply_markup=settings_toggles_menu())


@router.message(F.text.in_(["🎯 Уведомление о достижении цели", "🎯 Достижение цели"]))
async def toggle_notify_goal(message: Message):
    if "settings" not in db:
        db["settings"] = {}
    db["settings"]["notify_goal"] = not db["settings"].get("notify_goal", True)
    save_db()
    state = "включено ✅" if db["settings"]["notify_goal"] else "выключено ❌"
    await message.answer(f"Уведомление о достижении цели теперь {state}.", reply_markup=settings_toggles_menu())


# --- Ввод порога (Выполнено / В работе / Баланс) ---
def _is_limit_input(msg):
    if db.get("temp_target") not in ("comp", "work", "bal") or not msg.text:
        return False
    t = msg.text.replace(",", ".").strip()
    if "." in t:
        parts = t.split(".")
        return len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit()
    return t.isdigit()


@router.message(lambda msg: _is_limit_input(msg))
async def set_limit_step2(message: Message):
    target_key = db["temp_target"]
    val = float(message.text.replace(",", ".")) if "." in message.text or "," in message.text else int(message.text)
    db["limits"][target_key] = val
    db["temp_target"] = None
    save_db()
    await message.answer("✅ Порог сохранён.", reply_markup=main_menu())


# --- Ввод интервалов выплат ---
@router.message(lambda msg: db.get("input_mode") == "payout_intervals" and msg.text)
async def handle_payout_intervals(message: Message):
    db["input_mode"] = None
    try:
        parts = [p.strip() for p in message.text.split(",") if p.strip()]
        intervals = [int(p) for p in parts if p.isdigit()]
        if not intervals:
            raise ValueError("Нужны числа")
        if "payout_settings" not in db:
            db["payout_settings"] = {"enabled": True, "intervals": [60, 30, 10, 5, 1]}
        db["payout_settings"]["intervals"] = sorted(set(intervals), reverse=True)
        save_db()
        await message.answer(
            f"✅ Интервалы сохранены: {db['payout_settings']['intervals']} мин.",
            reply_markup=settings_menu(),
        )
    except Exception:
        await message.answer("❌ Введите числа через запятую (например: 60, 30, 15, 5, 1).", reply_markup=settings_menu())


# --- Ввод интервала мониторинга ---
@router.message(lambda msg: db.get("input_mode") == "monitoring_interval" and msg.text and len(msg.text.strip().split()) >= 2)
async def handle_monitoring_interval(message: Message):
    parts = message.text.strip().split()
    try:
        a, b = parts[0], parts[1]
        lo, hi = int(a), int(b)
        if lo < 10 or hi > 300:
            await message.answer("❌ Числа должны быть от 10 до 300 сек.", reply_markup=settings_menu())
            db["input_mode"] = None
            save_db()
            return
        if lo >= hi:
            lo, hi = hi, lo
        if "settings" not in db or not isinstance(db.get("settings"), dict):
            db["settings"] = {}
        db["settings"]["monitoring_interval_min"] = lo
        db["settings"]["monitoring_interval_max"] = hi
        db["input_mode"] = None
        save_db()
        print(f"[SETTINGS] Интервал мониторинга сохранён в БД: min={lo}, max={hi} сек.")
        await message.answer(
            f"✅ Интервал мониторинга сохранён: <b>{lo}–{hi}</b> сек. (применится со следующего цикла).",
            parse_mode="HTML",
            reply_markup=settings_menu(),
        )
    except ValueError:
        await message.answer("❌ Введите два целых числа через пробел (например: 45 120 или 10 15).", reply_markup=settings_menu())
        db["input_mode"] = None
        save_db()
    except Exception as e:
        db["input_mode"] = None
        save_db()
        write_log(f"Ошибка ввод интервала мониторинга: {e}")
        await message.answer("❌ Не удалось сохранить. Попробуйте ещё раз: два числа через пробел (например: 10 15).", reply_markup=settings_menu())
