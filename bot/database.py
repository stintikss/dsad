# Хранение и загрузка/сохранение БД

import json
import os
import shutil
from datetime import datetime
from copy import deepcopy

from .config import DB_FILE, LOG_FILE, DEFAULT_DB, DEFAULT_SETTINGS

db = deepcopy(DEFAULT_DB)


def get_now():
    return datetime.now().strftime("%H:%M:%S")


def write_log(text):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%d.%m %H:%M:%S')}] {text}\n")


def save_db():
    temp_file = DB_FILE + ".tmp"
    try:
        to_save = {}
        for k, v in db.items():
            if k == "paused_until" and v is not None and hasattr(v, "isoformat"):
                to_save[k] = v.isoformat()
            elif k == "history" and isinstance(v, list):
                to_save[k] = [[float(x) if isinstance(x, (int, float)) else x for x in row] for row in v]
            else:
                to_save[k] = v
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=4)
        os.replace(temp_file, DB_FILE)
    except Exception as e:
        write_log(f"Ошибка сохранения БД: {e}")
        if os.path.exists(DB_FILE):
            try:
                shutil.copy(DB_FILE, "db_backup.json")
            except Exception:
                pass


def load_db(silent=False):
    """silent=True — не печатать в консоль (для вызова из middleware при каждом апдейте)."""
    global db
    default_keys = set(DEFAULT_DB.keys())
    if not os.path.exists(DB_FILE):
        return
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
        if not raw.strip():
            print("[БД] Файл пуст.")
            return
        loaded = json.loads(raw)
        for key in default_keys:
            if key not in loaded:
                continue
            val = loaded[key]
            if key == "paused_until" and val is not None:
                try:
                    if isinstance(val, (int, float)):
                        db[key] = datetime.fromtimestamp(val)
                    elif isinstance(val, str):
                        db[key] = datetime.fromisoformat(val.replace("Z", "+00:00"))
                    else:
                        db[key] = val
                except Exception:
                    db[key] = None
            else:
                db[key] = val
        if isinstance(db.get("history"), list):
            db["history"] = [e for e in db["history"] if isinstance(e, (list, tuple)) and len(e) >= 5]
        if not isinstance(db.get("notified"), dict):
            db["notified"] = {"comp": db["last"]["comp"], "work": db["last"]["work"], "balance": db["last"]["balance"]}
        else:
            for k in ("comp", "work", "balance"):
                if k not in db["notified"]:
                    db["notified"][k] = db["last"].get(k, 0)
        if "settings" not in db or not isinstance(db.get("settings"), dict):
            db["settings"] = dict(DEFAULT_SETTINGS)
        else:
            for k, v in DEFAULT_SETTINGS.items():
                if k not in db["settings"]:
                    db["settings"][k] = v
        if "payout_settings" not in db or not isinstance(db.get("payout_settings"), dict):
            db["payout_settings"] = {"enabled": True, "intervals": [60, 30, 10, 5, 1]}
        if "day_start_comp" not in db:
            db["day_start_comp"] = int(db.get("last", {}).get("comp", 0))
        s = db.get("settings") or {}
        if not silent:
            print(f"[{get_now()}] БД загружена. Записей в истории: {len(db['history'])} | Интервал мониторинга: {s.get('monitoring_interval_min', 60)}–{s.get('monitoring_interval_max', 95)} сек, notify_updates={s.get('notify_updates', True)}")
    except json.JSONDecodeError as e:
        print(f"БД повреждена ({e}). Пробую бэкап...")
        if os.path.exists("db_backup.json"):
            try:
                with open("db_backup.json", "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                for key in default_keys:
                    if key in loaded:
                        db[key] = loaded[key]
                if isinstance(db.get("history"), list):
                    db["history"] = [e for e in db["history"] if isinstance(e, (list, tuple)) and len(e) >= 5]
                if not isinstance(db.get("notified"), dict):
                    db["notified"] = {"comp": db["last"]["comp"], "work": db["last"]["work"], "balance": db["last"]["balance"]}
                print("[БД] Восстановлено из бэкапа.")
            except Exception:
                print("[БД] Бэкап повреждён.")
    except Exception as e:
        print(f"[БД] Ошибка загрузки: {e}")


def create_backup():
    try:
        if os.path.exists(DB_FILE) and os.path.getsize(DB_FILE) > 0:
            shutil.copy(DB_FILE, "db_backup.json")
            print(f"[{get_now()}] Резервная копия создана.")
        else:
            print("[БД] Файл пуст или отсутствует, бэкап не создан.")
    except Exception as e:
        print(f"Ошибка бэкапа: {e}")
