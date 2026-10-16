# Настройки Telegram-бота и подключения к UNU
import os
import time
from dotenv import load_dotenv
load_dotenv()

# Часовой пояс (Render работает в UTC). Меняется переменной TZ, например Europe/Moscow.
os.environ.setdefault('TZ', 'Europe/Moscow')
if hasattr(time, 'tzset'):
    time.tzset()

BOT_VERSION = '2.2-TG'
BOT_NAME = 'UNU Monitor Telegram'
LOG_FILE = os.environ.get('LOG_FILE', 'bot_log.txt')
DB_FILE = os.environ.get('DB_FILE', 'db_save.json')

# Сохранены старые имена как алиасы, чтобы остальные модули проекта не ломались.
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
TELEGRAM_CHAT_ID = int(os.environ.get('TELEGRAM_CHAT_ID', '0') or '0')

# Дополнительные пользователи бота: числовые chat ID через запятую (например 111,222)
ALLOWED_CHAT_IDS = {int(x) for x in os.environ.get('ALLOWED_CHAT_IDS', '').replace(' ', '').split(',') if x.lstrip('-').isdigit()}

UNU_USER_ID = os.environ.get('UNU_USER_ID', '4436590').strip()
USER_URL = f'https://unu.im/users/{UNU_USER_ID}'
DASHBOARD_URL = 'https://unu.im/dashboard'
UNU_COOKIE = os.environ.get('UNU_COOKIE', '').strip()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
}
if UNU_COOKIE:
    HEADERS['Cookie'] = UNU_COOKIE
CHECK_INTERVAL = 60
WEBHOOK_URL = os.environ.get('UNUBOT_WEBHOOK_URL', '')  # необязательно

# Стартовая «база» дня (раньше было жёстко +30 ₽ и 29 задач). По умолчанию 0.
DAY_SEED_PROFIT = float(os.environ.get('DAY_SEED_PROFIT', '0') or 0)
DAY_SEED_TASKS = int(os.environ.get('DAY_SEED_TASKS', '0') or 0)
DEFAULT_SETTINGS = {
    'monitoring_interval_min': 60, 'monitoring_interval_max': 95,
    'payout_intervals': [60, 30, 10, 5, 1], 'payout_enabled': True,
    'notify_updates': True, 'notify_big_payout': True, 'notify_idle_hour': True,
    'notify_rank': True, 'notify_goal': True,
}
DEFAULT_DB = {
    'last': {'comp': 0, 'work': 0, 'rate': 0.0, 'balance': 0.0},
    'notified': {'comp': 0, 'work': 0, 'balance': 0.0},
    'limits': {'comp': 2, 'work': 2, 'bal': 10.0}, 'history': [],
    'temp_target': None, 'is_running': False, 'paused_until': None,
    'pre_sleep_data': None, 'timer_end': None, 'input_mode': None, 'goal': 0,
    'analysis_archive': [], 'goal_daily': 0, 'day_start_bal': 0, 'day_start_comp': 0,
    'day_seed_date': None, 'day_seed_profit': 0.0, 'day_seed_tasks': 0, 'xp': 0,
    'settings': dict(DEFAULT_SETTINGS), 'payout_settings': {'enabled': True, 'intervals': [60, 30, 10, 5, 1]},
}
