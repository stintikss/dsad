# UNU Monitor — Telegram-бот на Render (Web Service)

## 0. Безопасность — сделайте первым делом
Токен бота и cookie UNU уже были отправлены в чат/на скриншоты, считайте их скомпрометированными.
1. **Токен:** Telegram → @BotFather → `/revoke` → выберите бота → скопируйте НОВЫЙ токен.
2. **Cookie UNU:** выйдите из аккаунта на unu.im и войдите заново, затем возьмите свежие cookie (см. п. 4).
3. Репозиторий на GitHub делайте **Private**. Токены и cookie в файлы не вставляйте — только в Render → Environment.

## 1. Что лежит в архиве
```
main.py                  запуск бота
requirements.txt         зависимости
render.yaml              (необязательно) blueprint Render
.env.example             шаблон переменных (только образец)
.gitignore
bot/
  config.py  database.py  parser.py  monitoring.py  utils.py
  keyboards.py  about_texts.py  tg_shim.py  webserver.py
  handlers/  __init__.py  about.py  settings.py
```

## 2. Загрузка на GitHub (через браузер)
1. github.com → **New repository** → имя `unu-monitor`, **Private** → Create.
2. Распакуйте ZIP. Зайдите **внутрь** папки `unubot_telegram_ready`.
3. В репозитории: **Add file → Upload files**. Перетащите **содержимое** папки: `main.py`, `requirements.txt`, `render.yaml`, `.env.example`, `.gitignore` и папку `bot` целиком (папки `__pycache__` не нужны).
4. **Commit changes**.
5. Проверьте: `main.py` и `requirements.txt` лежат в **корне** репозитория, а не в подпапке.
   (Файл `.gitignore` начинается с точки и может быть скрыт в Finder — Cmd+Shift+. покажет скрытые файлы. Без него тоже работает.)

## 3. Создание Web Service на Render
1. render.com → **New + → Web Service** → подключите GitHub → выберите `unu-monitor`.
2. Настройки:

| Поле | Значение |
|---|---|
| Language | Python 3 |
| Branch | main |
| Region | любой (Frankfurt ближе) |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python main.py` |
| Instance Type | Free |

3. В блоке **Environment Variables** добавьте (KEY слева, VALUE справа):

| KEY | VALUE |
|---|---|
| `BOT_TOKEN` | новый токен от BotFather |
| `UNU_USER_ID` | `4436590` |
| `UNU_COOKIE` | строка cookie (п. 4) |
| `TELEGRAM_CHAT_ID` | `0` сейчас, потом ваш chat ID (п. 5) |
| `TZ` | `Europe/Moscow` (или ваш пояс, напр. `Europe/Berlin`) |
| `PYTHON_VERSION` | `3.11.9` |

4. **Deploy Web Service**. Через 2–4 минуты в **Logs** должно быть:
   `[WEB] HTTP-сервер слушает порт ...` и `[TELEGRAM] Бот @имя подключён. Long polling запущен.` → статус **Live**.

## 4. Как собрать UNU_COOKIE
Одна строка, без переносов, формат:
```
id=4436590; hash=ВАШ_HASH; PHPSESSID=ВАШ_PHPSESSID; turbohash=ВАШ_TURBOHASH
```
Берите значения из браузера (DevTools → Application → Cookies → unu.im): `id`, `hash`, `PHPSESSID`, `turbohash`.
Остальные (`_ym_*`, `cid`, `mdd`, `name`) не нужны. Домены, даты и флаги в строку не пишем.

## 5. Первый запуск
1. Откройте бота в Telegram, отправьте `/start`. Бот ответит «🚀 Бот запущен!» и покажет **ваш chat ID**.
2. Скопируйте это число в Render → Environment → `TELEGRAM_CHAT_ID` → Save (сервис перезапустится).
   Зачем: бот приватный и отвечает только владельцу, а на Free-тарифе файл БД стирается при перезапуске — с заданным `TELEGRAM_CHAT_ID` мониторинг стартует сам, без нового `/start`.
3. Проверьте `https://ИМЯ-СЕРВИСА.onrender.com/health` — должно быть `{"status":"ok",...}`.

## 6. Чтобы Free-сервис не засыпал
Free Web Service засыпает через 15 минут без входящих запросов. В коде есть самопинг каждые 10 минут, но надёжнее добавить внешний:
1. uptimerobot.com → Add New Monitor → HTTP(s).
2. URL: `https://ИМЯ-СЕРВИСА.onrender.com/health`, интервал 5 минут.

## 7. Что исправлено в коде
- Добавлен HTTP-сервер (`/health`) — без него Web Service не стартует.
- `**жирный**` и спецсимволы ломали Telegram Markdown → теперь конвертируется в HTML, при ошибке отправляется простым текстом.
- Запросы к UNU вынесены в потоки: бот больше не «замирает» во время загрузки сайта.
- Обработка апдейтов не блокирует приём новых сообщений; ошибки обработчиков логируются.
- Бот приватный: чужие пользователи получают «Это приватный бот».
- `/start`, «💰 Баланс» и др. больше не падают при ошибке авторизации UNU (раньше в БД записывалась строка `AUTH_ERROR`).
- После перезапуска стартовые значения подтягиваются с UNU — не будет ложного «+весь баланс».
- Часовой пояс задаётся `TZ` (Render по умолчанию в UTC).
- Убраны ненужные зависимости (fastapi, uvicorn, pywebpush, cryptography) и следы VK.
- Жёстко прошитая «база дня» (+30 ₽ / 29 задач) теперь 0; при желании задайте `DAY_SEED_PROFIT`, `DAY_SEED_TASKS`.
- Webhook на localhost по умолчанию отключён.

## 8. Ограничения Free-тарифа
- Файлы стираются при каждом перезапуске/деплое: история, цели и настройки из `db_save.json` сбросятся. Для хранения нужен платный Disk/Postgres.
- Free-сервис имеет ограничение часов в месяц; для 24/7 может понадобиться платный тариф.
- При деплое два экземпляра работают секунду-другую одновременно — в логах возможна ошибка Conflict, она пройдёт сама.

## 9. Если что-то не работает
| Симптом | Что делать |
|---|---|
| `Не задан BOT_TOKEN` | Добавьте `BOT_TOKEN` в Environment |
| `Unauthorized` / 401 | Токен неверный или отозван — вставьте новый |
| Бот молчит | Смотрите Logs; проверьте, что Start Command = `python main.py` |
| «UNU не принял cookie» / «Куки слетели» | Обновите `UNU_COOKIE` (п. 4), сохраните, дождитесь рестарта, `/start` |
| `Port scan timeout` / `No open ports` | Это Web Service: проверьте, что деплоится именно эта версия кода |
| `Conflict: terminated by other getUpdates` | Запущен второй экземпляр (старый Background Worker, локальный запуск). Остановите лишние |
| Время неверное | Поменяйте `TZ` |
| `ModuleNotFoundError` | Build Command должен быть `pip install -r requirements.txt`, файл — в корне репо |

Если раньше создавали Background Worker с этим ботом — удалите его, иначе будет Conflict.
