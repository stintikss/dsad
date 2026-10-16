# О боте и техническая информация

from ..tg_shim import Router, F, Message, InlineKeyboardMarkup, InlineKeyboardButton

from ..database import db
from ..keyboards import main_menu
from ..about_texts import ABOUT_FULL, TECH_INFO

router = Router(name="about")


@router.message(F.text.in_(["ℹ️ О боте", "🪪 О системе"]))
async def cmd_about(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Техническая информация", callback_data="tech_info")]
        ]
    )
    await message.answer(ABOUT_FULL, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "tech_info")
async def callback_tech_info(callback):
    await callback.message.answer(TECH_INFO, parse_mode="HTML")
    await callback.answer()
