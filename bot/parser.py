# Парсинг данных с UNU

import re
import time
import random
from datetime import datetime, timedelta
from collections import defaultdict

import requests
from bs4 import BeautifulSoup

from .config import HEADERS, USER_URL, DASHBOARD_URL
from .database import db, write_log


def parse_duration(text):
    """Превращает '10m', '1h', '2d' в секунды"""
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    try:
        if text.isdigit():
            return int(text) * 60
        val = int(text[:-1])
        unit = text[-1].lower()
        return val * units.get(unit, 0)
    except Exception:
        return None


def _num(text, cast=float, default=0):
    """Достаёт число из строки; '—', пусто и мусор -> default (у новых аккаунтов на UNU стоит прочерк)."""
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(text).replace("\xa0", " ").replace(" ", ""))
    if not m:
        return default
    try:
        return cast(float(m.group(0).replace(",", ".")))
    except Exception:
        return default


def get_all_data():
    try:
        if random.random() < 0.2:
            fake_urls = ["https://unu.im/tasks/search", "https://unu.im/notifications", "https://unu.im/"]
            requests.get(random.choice(fake_urls), headers=HEADERS, timeout=5)
        res_pub = requests.get(USER_URL, headers=HEADERS, timeout=10)
        if "login" in res_pub.url:
            return "AUTH_ERROR"
        soup_pub = BeautifulSoup(res_pub.text, "html.parser")
        table = soup_pub.find("table", class_="page-table")
        if table is None:
            write_log("Ошибка: Таблица 'page-table' не найдена. Возможно, куки сдохли.")
            return "AUTH_ERROR"
        rows = {
            row.find_all("td")[0].text.strip(): row.find_all("td")[1].text.strip()
            for row in table.find_all("tr")
            if len(row.find_all("td")) > 1
        }
        res_dash = requests.get(DASHBOARD_URL, headers=HEADERS, timeout=10)
        soup_dash = BeautifulSoup(res_dash.text, "html.parser")
        balance_block = soup_dash.find("div", class_="block-balance")
        if balance_block is None:
            write_log("Ошибка: Блок баланса не найден.")
            return "AUTH_ERROR"
        balance_span = balance_block.find("span")
        if balance_span:
            balance = _num(balance_span.text, float, 0.0)
        else:
            balance = 0.0
        data = {
            "comp": _num(rows.get("Выполнено заданий:", 0), int, 0),
            "work": _num(rows.get("Заданий в работе:", 0), int, 0),
            "rate": _num(rows.get("Успешно выполняет:", "0"), float, 0.0),
            "balance": balance,
        }
        db["history"].append((time.time(), data["comp"], data["work"], data["rate"], data["balance"]))
        return data
    except Exception as e:
        write_log(f"Ошибка парсинга: {e}")
        print(f"Ошибка сбора: {e}")
        return None


def parse_unu_time(text):
    """Извлекает время из строк типа '22 часа 11 мин'"""
    days = re.search(r"(\d+)\s*д", text)
    hours = re.search(r"(\d+)\s*ч", text)
    minutes = re.search(r"(\d+)\s*м", text)
    return timedelta(
        days=int(days.group(1)) if days else 0,
        hours=int(hours.group(1)) if hours else 0,
        minutes=int(minutes.group(1)) if minutes else 0,
    )


def get_raw_verification_tasks():
    """Собирает сырые данные о заданиях на проверке"""
    all_tasks = []
    ajax_url = "https://unu.im/include/ajax_tasks_works.php"
    current_showed_ids = ""
    start_flag = 1
    local_headers = HEADERS.copy()
    local_headers.update({"Referer": "https://unu.im/tasks/works/check", "X-Requested-With": "XMLHttpRequest"})
    now = datetime.now()
    try:
        while True:
            params = {"showed_ids": current_showed_ids, "start": start_flag, "type": "check"}
            response = requests.get(ajax_url, headers=local_headers, params=params, timeout=15)
            if response.status_code != 200:
                break
            data = response.json()
            html_content = data.get("html", "")
            if not html_content.strip():
                break
            soup = BeautifulSoup(html_content, "html.parser")
            rows = soup.find_all("div", class_="job-table__row")
            if not rows:
                break
            for row in rows:
                cells = row.find_all("div", class_="job-table__cell")
                if len(cells) < 3:
                    continue
                sum_text = cells[2].get_text(strip=True).replace("руб", "").replace(" ", "").replace(",", ".")
                price = float(sum_text) if sum_text else 0.0
                status_text = cells[1].get_text(strip=True).lower()
                time_diff = parse_unu_time(status_text)
                pay_time = now + time_diff
                clean_time = status_text.replace("на проверке", "").replace("осталось", "").strip()
                all_tasks.append(
                    {"price": price, "pay_time": pay_time, "time_label": clean_time if clean_time else "неизвестно"}
                )
            current_showed_ids = data.get("showed_ids", "")
            start_flag = 0
            if str(data.get("no_more")) == "1":
                break
            time.sleep(0.2)
        all_tasks.sort(key=lambda x: x["pay_time"])
        return all_tasks
    except Exception as e:
        print(f"Ошибка парсинга проверки: {e}")
        return []
