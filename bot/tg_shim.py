"""Telegram-compatible adapter for the project's aiogram-like handlers."""
import asyncio
import html as _html
import os
import re
from types import SimpleNamespace
from aiogram import Bot as AiogramBot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    Message as TgMessage, CallbackQuery as TgCallbackQuery,
    ReplyKeyboardMarkup as TgReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove as TgReplyKeyboardRemove,
    InlineKeyboardMarkup as TgInlineKeyboardMarkup,
    InlineKeyboardButton as TgInlineKeyboardButton,
    FSInputFile as TgFSInputFile,
)

_TAG_RE = re.compile(r'<[^>]+>')


def _md_to_html(text: str) -> str:
    """Telegram 'Markdown' (legacy) не понимает **жирный** и падает на спецсимволах.
    Конвертируем в HTML: **x**/*x* -> <b>, __x__ -> <i>, `x` -> <code>."""
    text = _html.escape(text or '', quote=False)
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.S)
    text = re.sub(r'(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<i>\1</i>', text, flags=re.S)
    return text


def _prepare(text, parse_mode):
    """-> (text, parse_mode) для Telegram."""
    text = '' if text is None else str(text)
    if parse_mode and parse_mode.lower() == 'markdown':
        return _md_to_html(text), 'HTML'
    if parse_mode and parse_mode.lower() == 'html':
        return text, 'HTML'
    return text, None


def _plain(text: str) -> str:
    return _html.unescape(_TAG_RE.sub('', text))


async def _safe_send(func, text, parse_mode, **kw):
    """Отправка с запасным вариантом: если разметка сломана — шлём простым текстом."""
    text, pm = _prepare(text, parse_mode)
    if len(text) > 4096:
        text = text[:4090] + '...'
    try:
        return await func(text=text, parse_mode=pm, **kw)
    except TelegramBadRequest as e:
        if 'not modified' in str(e).lower():
            return None
        if pm:
            print(f'[TELEGRAM] Разметка отклонена ({e}); отправляю простым текстом')
            return await func(text=_plain(text), parse_mode=None, **kw)
        raise


class FSInputFile:
    def __init__(self, path: str): self.path = path

class ReplyKeyboardRemove: pass
class InlineKeyboardButton:
    def __init__(self, text: str, callback_data: str | None = None):
        self.text, self.callback_data = text, callback_data
class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard=None): self.inline_keyboard = inline_keyboard or []
class ReplyKeyboardMarkup:
    def __init__(self, keyboard): self.keyboard = keyboard
class ReplyKeyboardBuilder:
    def __init__(self): self._current=[]; self._rows=[]
    def button(self, text: str): self._current.append(text)
    def adjust(self, *sizes: int):
        i=0
        for size in sizes:
            if self._current[i:i+size]: self._rows.append(self._current[i:i+size])
            i += size
        if i < len(self._current): self._rows.append(self._current[i:])
    def as_markup(self, resize_keyboard=True):
        if self._current and not self._rows: self._rows=[self._current]
        return ReplyKeyboardMarkup(self._rows)

def _markup(markup):
    if markup is None: return None
    if isinstance(markup, ReplyKeyboardRemove): return TgReplyKeyboardRemove()
    if isinstance(markup, InlineKeyboardMarkup):
        return TgInlineKeyboardMarkup(inline_keyboard=[[TgInlineKeyboardButton(text=b.text, callback_data=(b.callback_data or 'noop')[:64]) for b in row] for row in markup.inline_keyboard])
    if isinstance(markup, ReplyKeyboardMarkup):
        return TgReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=str(x)) for x in row] for row in markup.keyboard], resize_keyboard=True)
    return markup

class Message:
    def __init__(self, raw, bot):
        self._raw, self._bot = raw, bot
        self.text = getattr(raw, 'text', None) or ''
        self.chat = SimpleNamespace(id=raw.chat.id)
        self.from_user = getattr(raw, 'from_user', SimpleNamespace(id=0))
        self.message_id = raw.message_id
        self.peer_id = raw.chat.id
        self.conversation_message_id = raw.message_id
    async def answer(self, text, reply_markup=None, parse_mode=None, **kwargs):
        return await self._bot.send_message(self.chat.id, text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs)
    async def edit_text(self, text, parse_mode=None, reply_markup=None, **kwargs):
        return await _safe_send(self._bot._tg.edit_message_text, text, parse_mode, chat_id=self.chat.id, message_id=self.message_id, reply_markup=_markup(reply_markup), **kwargs)
    async def answer_document(self, file, caption=None):
        path = file.path if hasattr(file, 'path') else str(file)
        return await self._bot._tg.send_document(self.chat.id, TgFSInputFile(path), caption=caption)
    async def delete(self): return await self._bot._tg.delete_message(self.chat.id, self.message_id)

class CallbackQuery:
    def __init__(self, raw, bot):
        self._raw, self._bot = raw, bot
        self.data = raw.data or ''
        self.message = Message(raw.message, bot)
    async def answer(self, text='', show_alert=False):
        return await self._raw.answer(text=text or None, show_alert=show_alert)

class _Filter:
    def __init__(self, fn): self.fn=fn
    def check(self, obj): return self.fn(obj)
class _TextFactory:
    def in_(self, values): return _Filter(lambda m: m.text in values)
    def __eq__(self, other): return _Filter(lambda m: m.text == other)
class _DataFactory:
    def __eq__(self, other): return _Filter(lambda c: c.data == other)
class F:
    text=_TextFactory(); data=_DataFactory()
class Command:
    def __init__(self, *commands): self.commands={x.lower().lstrip('/') for x in commands}
    def check(self, message):
        text=(message.text or '').strip().lower()
        return any(text == c or text == '/'+c or text.startswith('/'+c+' ') for c in self.commands)
class _types:
    Message=Message; CallbackQuery=CallbackQuery; FSInputFile=FSInputFile
    ReplyKeyboardRemove=ReplyKeyboardRemove; InlineKeyboardMarkup=InlineKeyboardMarkup
    InlineKeyboardButton=InlineKeyboardButton
    ReplyKeyboardMarkup=ReplyKeyboardMarkup
types=_types()

class Bot:
    def __init__(self, token=None):
        token = token or os.getenv('BOT_TOKEN', '')
        if not token: raise RuntimeError('Не задан BOT_TOKEN в Environment Render.')
        self._tg=AiogramBot(token=token)
    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kwargs):
        return await _safe_send(self._tg.send_message, text, parse_mode, chat_id=chat_id, reply_markup=_markup(reply_markup), **kwargs)

class Router:
    def __init__(self, name=''): self.name=name; self._message_handlers=[]; self._callback_handlers=[]
    def message(self, *filters):
        def deco(fn): self._message_handlers.append((filters,fn)); return fn
        return deco
    def callback_query(self, flt):
        def deco(fn): self._callback_handlers.append((flt,fn)); return fn
        return deco
    def include_router(self, router):
        self._message_handlers.extend(router._message_handlers); self._callback_handlers.extend(router._callback_handlers)
class Dispatcher:
    def __init__(self): self._message_handlers=[]; self._callback_handlers=[]
    def message(self,*filters):
        def deco(fn): self._message_handlers.append((filters,fn)); return fn
        return deco
    def callback_query(self,flt):
        def deco(fn): self._callback_handlers.append((flt,fn)); return fn
        return deco
    def include_router(self,router):
        self._message_handlers.extend(router._message_handlers); self._callback_handlers.extend(router._callback_handlers)
    @staticmethod
    def _match(filters,obj):
        for f in filters:
            if hasattr(f,'check') and not f.check(obj): return False
            if callable(f) and not hasattr(f,'check') and not f(obj): return False
        return True
    @staticmethod
    def _owner_ids():
        from .config import TELEGRAM_CHAT_ID, ALLOWED_CHAT_IDS
        from .database import db
        ids = set(ALLOWED_CHAT_IDS)
        if TELEGRAM_CHAT_ID:
            ids.add(int(TELEGRAM_CHAT_ID))
        elif db.get('peer_id'):
            ids.add(int(db['peer_id']))
        return ids

    def _allowed(self, chat_id, text=''):
        """Доступ только владельцу. Пока владелец не задан — пускаем только /start."""
        owners = self._owner_ids()
        if owners:
            return chat_id in owners
        return (text or '').strip().lower().startswith('/start')

    async def _handle(self, update, bot):
        try:
            if update.message:
                msg = Message(update.message, bot)
                if not self._allowed(msg.chat.id, msg.text):
                    await msg.answer(f'⛔ Это приватный бот. Ваш chat ID: {msg.chat.id}\nПередайте его владельцу, чтобы получить доступ.')
                    return
                for filters, handler in self._message_handlers:
                    if self._match(filters, msg):
                        await handler(msg); break
            elif update.callback_query:
                call = CallbackQuery(update.callback_query, bot)
                if not self._allowed(call.message.chat.id):
                    await call.answer('Нет доступа', show_alert=True)
                    return
                for flt, handler in self._callback_handlers:
                    if self._match((flt,), call):
                        await handler(call); break
        except Exception as e:
            import traceback
            print(f'[TELEGRAM] Ошибка обработчика: {e}\n{traceback.format_exc()}')

    async def start_polling(self, bot):
        offset = None
        tasks = set()
        me = await bot._tg.get_me()
        print(f'[TELEGRAM] Бот @{me.username} подключён. Long polling запущен. Напишите боту /start.')
        await bot._tg.delete_webhook(drop_pending_updates=False)
        while True:
            try:
                updates = await bot._tg.get_updates(offset=offset, timeout=30, allowed_updates=['message', 'callback_query'], request_timeout=45)
                for update in updates:
                    offset = update.update_id + 1
                    t = asyncio.create_task(self._handle(update, bot))  # не блокируем приём апдейтов
                    tasks.add(t); t.add_done_callback(tasks.discard)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f'[TELEGRAM] Ошибка polling: {e}')
                await asyncio.sleep(5)
