# Фоновый мониторинг и уведомления

import asyncio
import time
import random
from datetime import datetime, timedelta
from collections import defaultdict

from .tg_shim import Bot, InlineKeyboardMarkup, InlineKeyboardButton

from .database import db, save_db, get_now, write_log
from .parser import get_all_data, get_raw_verification_tasks
from .utils import get_rank_info, send_webhook_notification

sent_notifications = set()


def _get_monitoring_interval():
    s = db.get("settings") or {}
    lo = int(s.get("monitoring_interval_min", 60))
    hi = int(s.get("monitoring_interval_max", 95))
    lo = max(10, min(lo, 300))
    hi = max(10, min(hi, 300))
    if lo > hi:
        lo, hi = hi, lo
    sec = random.randint(lo, hi)
    print(f"[MONITOR] Интервал из настроек: min={s.get('monitoring_interval_min', 60)}, max={s.get('monitoring_interval_max', 95)} -> после ограничений {lo}-{hi} сек, выбран {sec} сек")
    return sec


def _feature_enabled(key):
    s = db.get("settings") or {}
    return s.get(key, True)


def _log_monitoring_state():
    """Печать текущих настроек мониторинга при запуске."""
    s = db.get("settings") or {}
    lim = db.get("limits") or {}
    print("[MONITOR] ========== НАСТРОЙКИ МОНИТОРИНГА ==========")
    print(f"[MONITOR] Интервал: {s.get('monitoring_interval_min', 60)}–{s.get('monitoring_interval_max', 95)} сек")
    print(f"[MONITOR] Сводки (notify_updates): {s.get('notify_updates', True)}")
    print(f"[MONITOR] Пороги: comp±{lim.get('comp', 2)}, work±{lim.get('work', 2)}, bal±{lim.get('bal', 10)}")
    print(f"[MONITOR] last: {db.get('last', {})}")
    print(f"[MONITOR] notified: {db.get('notified', {})}")
    print("[MONITOR] ===========================================")


async def monitoring_task(bot: Bot, chat_id: int):
    if db.get("is_running"):
        print(f"[{get_now()}] [MONITOR] Уже запущен в другом процессе — повторный запуск пропущен. Запустите только один экземпляр бота (один терминал с python main.py).")
        return
    db["is_running"] = True
    print(f"[{get_now()}] [MONITOR] Фоновый мониторинг запущен, chat_id={chat_id}")
    _log_monitoring_state()
    db["last_activity"] = time.time()

    while True:
        current_chat_id = db.get("chat_id", chat_id)
        if db.get("paused_until"):
            if datetime.now() < db["paused_until"]:
                await asyncio.sleep(30)
                continue
            pre_wake_pause = random.randint(60, 300)
            print(f"[{get_now()}] Время сна вышло. Жду {pre_wake_pause} сек...")
            await asyncio.sleep(pre_wake_pause)
            new_data = await asyncio.to_thread(get_all_data)
            if new_data and new_data != "AUTH_ERROR":
                old = db.get("pre_sleep_data", db["last"])
                diff_bal = round(new_data["balance"] - old["balance"], 2)
                msg_text = f"☀️ **Бот проснулся!**\n💰 Изменения: {diff_bal:+g} ₽"
                await bot.send_message(current_chat_id, msg_text)
                try:
                    await send_webhook_notification("Бот проснулся", f"Изменения: {diff_bal:+g} ₽")
                except Exception:
                    pass
                db["paused_until"] = None
                db["last"].update(new_data)
                save_db()

        interval = _get_monitoring_interval()
        print(f"[{get_now()}] [MONITOR] Спим {interval} сек до следующего опроса...")
        await asyncio.sleep(interval)

        if db.get("paused_until"):
            continue

        print(f"[{get_now()}] [MONITOR] Опрос сайта...")
        new = await asyncio.to_thread(get_all_data)
        if new == "AUTH_ERROR":
            print(f"[{get_now()}] [MONITOR] AUTH_ERROR — куки слетели")
            await bot.send_message(current_chat_id, "⚠️ Куки слетели!")
            try:
                await send_webhook_notification("Ошибка авторизации", "Куки слетели на UNU — обнови сессию")
            except Exception:
                pass
            db["is_running"] = False
            break
        if not new or not isinstance(new, dict):
            print(f"[{get_now()}] [MONITOR] Нет данных или не dict: {type(new).__name__}")
            continue

        old = db["last"]
        lim = db["limits"]
        lim_comp = int(lim.get("comp", 2))
        lim_work = int(lim.get("work", 2))
        lim_bal = float(lim.get("bal", 10))
        if "notified" not in db:
            db["notified"] = dict(old)
            print(f"[{get_now()}] [MONITOR] Инициализирован notified из last")

        d_comp = int(new["comp"]) - int(old.get("comp", 0))
        d_work = int(new["work"]) - int(old.get("work", 0))
        d_bal = round(float(new["balance"]) - float(old.get("balance", 0)), 2)
        now_time = get_now()
        print(f"[{get_now()}] [MONITOR] Данные: old comp={old.get('comp')} work={old.get('work')} bal={old.get('balance')} -> new comp={new['comp']} work={new['work']} bal={new['balance']} | delta comp={d_comp} work={d_work} bal={d_bal}")

        if d_bal > 0 or d_work > 0:
            db["last_activity"] = time.time()

        if _feature_enabled("notify_idle_hour"):
            idle_time = time.time() - db.get("last_activity", time.time())
            if 3600 <= idle_time < 3700:
                text = "😴 **Тишина уже целый час...**\nПора взять задание в работу!"
                await bot.send_message(current_chat_id, text)
                try:
                    await send_webhook_notification("Тишина 1 час", "Пора взять задание в работу")
                except Exception:
                    pass
                db["last_activity"] = time.time() - 3701

        if d_bal > 0 and _feature_enabled("notify_rank"):
            pts = db["history"]
            total_earned = 0
            for i in range(len(pts) - 1):
                diff = pts[i + 1][4] - pts[i][4]
                if 0 < diff < 150:
                    total_earned += diff
            old_rank, _, _, _ = get_rank_info(total_earned - d_bal)
            new_rank, next_xp, next_name, _ = get_rank_info(total_earned)
            if old_rank != new_rank:
                text = f"🎊 **НОВЫЙ РАНГ: {new_rank}** 🎊\nДо {next_name} осталось {next_xp - total_earned} XP!"
                await bot.send_message(current_chat_id, text, parse_mode="Markdown")
                try:
                    await send_webhook_notification("Новый ранг", f"{new_rank} — до {next_name}: {next_xp - total_earned} XP")
                except Exception:
                    pass

        if _feature_enabled("notify_goal") and db.get("goal", 0) > 0 and new["balance"] >= db["goal"]:
            total_daily = round(new["balance"] - db.get("day_start_bal", new["balance"]), 2)
            text = f"🎉 **ЦЕЛЬ ВЫПОЛНЕНА!**\n💰 За сегодня: +{total_daily} ₽\nТы машина! 🔥"
            await bot.send_message(current_chat_id, text, parse_mode="Markdown")
            try:
                await send_webhook_notification("Цель выполнена", f"За сегодня: +{total_daily} ₽")
            except Exception:
                pass
            db["goal"] = 0
            db["goal_daily"] = 0
            save_db()

        if _feature_enabled("notify_big_payout") and d_bal >= 3:
            try:
                if d_bal >= 30:
                    msg = f"🤑 **ЖИРНЫЙ ЧЕК!**\n💰 +{d_bal} ₽! 🔥"
                elif d_bal >= 10:
                    msg = f"✅ Отличная оплата: +{d_bal} ₽"
                else:
                    msg = f"Копеечка в копилку: +{d_bal} ₽ 🪙"
                await bot.send_message(current_chat_id, msg, parse_mode="Markdown")
                try:
                    await send_webhook_notification("Оплата", msg)
                except Exception:
                    pass
            except Exception:
                pass

        notify_updates_on = _feature_enabled("notify_updates")
        print(f"[{get_now()}] [MONITOR] notify_updates={notify_updates_on}, пороги: comp>={lim_comp}, work>={lim_work}, bal>={lim_bal}")

        if not notify_updates_on:
            print(f"[{get_now()}] [MONITOR] Сводки выключены — обновляю last и пропускаю уведомления")
            if d_bal != 0 or d_comp != 0 or d_work != 0:
                db["last"].update(new)
                save_db()
            continue

        # Логика как в back04/back09_01: считаем изменения сразу от last
        temp_msgs = []
        if abs(d_comp) >= lim_comp and d_comp != 0:
            temp_msgs.append((f"Выполнено: {old['comp']} → {new['comp']} ({d_comp:+d})", "✅"))
        if abs(d_work) >= lim_work and d_work != 0:
            temp_msgs.append((f"В работе: {old['work']} → {new['work']} ({d_work:+d})", "🛠"))
        if abs(d_bal) >= lim_bal and d_bal != 0:
            temp_msgs.append((f"Баланс: {old['balance']} → {new['balance']} ({d_bal:+g} ₽)", "💰"))

        if temp_msgs:
            all_icons = "".join([m[1] for m in temp_msgs])
            final_lines = [f"{i+1}:{len(temp_msgs)}{all_icons} {m[1]}{m[0]}" for i, m in enumerate(temp_msgs)]
            print(f"[{get_now()}] [MONITOR] ОТПРАВКА УВЕДОМЛЕНИЯ «Обновление» в chat_id={current_chat_id}: {final_lines}")
            try:
                text = f"🔔 **Обновление**\n" + "\n".join(final_lines) + f"\n⌚ {now_time}"
                await bot.send_message(current_chat_id, text, parse_mode="Markdown")
                await send_webhook_notification("Обновление данных", '\n'.join(final_lines))
            except Exception:
                pass
        else:
            print(f"[{get_now()}] [MONITOR] Нет строк для сводки — уведомление не отправлено")

        if d_bal != 0 or d_comp != 0 or d_work != 0:
            db["last"].update(new)
            save_db()
            print(f"[{get_now()}] [MONITOR] last обновлён и сохранён в БД")


async def payout_monitor_task(bot: Bot, admin_id: int):
    print(f"[{get_now()}] Мониторинг выплат запущен...")
    while True:
        try:
            payout_cfg = db.get("payout_settings", {"enabled": True, "intervals": [60, 30, 10, 5, 1]})
            if not payout_cfg.get("enabled", True):
                await asyncio.sleep(60)
                continue

            tasks = await asyncio.to_thread(get_raw_verification_tasks)
            if not tasks:
                await asyncio.sleep(60)
                continue

            now = datetime.now()
            grouped = defaultdict(list)
            for t in tasks:
                rounded_minute = (t["pay_time"].minute // 5) * 5
                t_key = t["pay_time"].replace(minute=rounded_minute, second=0, microsecond=0)
                grouped[t_key].append(t)

            data = await asyncio.to_thread(get_all_data)
            current_balance = float(data.get("balance", 0)) if data and data != "AUTH_ERROR" else 0.0
            thresholds = payout_cfg.get("intervals", [60, 30, 10, 5, 1])

            for pay_time, t_list in grouped.items():
                diff = pay_time - now
                minutes_left = int(diff.total_seconds() / 60)
                for limit in thresholds:
                    total_pay_sum = sum(t["price"] for t in t_list)
                    count = len(t_list)
                    notif_id = f"p_{pay_time.day}_{pay_time.hour}_{limit}_{round(total_pay_sum, 1)}"
                    if limit - 2 <= minutes_left <= limit + 2 and notif_id not in sent_notifications:
                        tasks_after = len(tasks) - count
                        balance_after = current_balance + total_pay_sum
                        end_full = pay_time.strftime("%d.%m в %H:%M")
                        min_word = "минута" if limit == 1 else "минут" if 2 <= limit <= 4 else "минут"
                        msg = (
                            f"🔔 <b>Скоро выплата по проверкам</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"⏰ <b>Закончится:</b> {end_full}\n"
                            f"⏳ <b>Осталось:</b> ~{limit} {min_word}\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"💰 <b>Сумма к поступлению:</b> <code>+{round(total_pay_sum, 2)}</code> ₽\n"
                            f"📦 <b>Заданий в этой группе:</b> {count} шт.\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📈 <b>Баланс после:</b> <code>{round(balance_after, 2)}</code> ₽\n"
                            f"📋 <b>Останется на проверке:</b> {tasks_after} шт.\n"
                        )
                        kb = InlineKeyboardMarkup(
                            inline_keyboard=[
                                [InlineKeyboardButton(text="🔎 Посмотреть детали", callback_data="verif_toggle")]
                            ]
                        )
                        await bot.send_message(admin_id, msg, parse_mode="HTML", reply_markup=kb)
                        sent_notifications.add(notif_id)

            if len(sent_notifications) > 500:
                sent_notifications.clear()
        except Exception as e:
            print(f"Ошибка в мониторинге выплат: {e}")
        await asyncio.sleep(60)


async def midnight_check(bot: Bot, chat_id: int):
    print(f"[{get_now()}] Ночная проверка запущена.")
    while True:
        now = datetime.now()
        tomorrow = now + timedelta(days=1)
        midnight = datetime.combine(tomorrow.date(), datetime.min.time())
        wait_seconds = (midnight - now).total_seconds() + 5
        await asyncio.sleep(wait_seconds)
        from .database import create_backup
        create_backup()
        if db.get("goal", 0) > 0:
            await bot.send_message(
                chat_id,
                f"🌑 **Итоги дня**\n"
                f"К сожалению, цель в `{db.get('goal_daily', 0)} ₽` не была достигнута.\n"
                f"Завтра будет новый день и новые возможности! 💪",
                parse_mode="Markdown",
            )
            db["goal"] = 0
            db["goal_daily"] = 0
        data = await asyncio.to_thread(get_all_data)
        if data and data != "AUTH_ERROR":
            db["day_start_bal"] = data["balance"]
            db["day_start_comp"] = data["comp"]
            print(f"[{get_now()}] Новый день начат. Баланс зафиксирован.")
            save_db()
