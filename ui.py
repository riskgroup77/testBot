"""Umumiy tugmalar va klaviaturalar."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

BTN_TEST = "📝 Test yechish"
BTN_VIDEO = "🎬 Video analizlarni tomosha qilish"
BTN_ADMIN = "⚙️ Admin panel"
MENU_TEXTS = {BTN_TEST, BTN_VIDEO, BTN_ADMIN}


def main_menu(admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_TEST)], [KeyboardButton(text=BTN_VIDEO)]]
    if admin:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True,
                               input_field_placeholder="Xizmatni tanlang 👇")


def ikb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows if row
    ])
