import asyncio
import logging
import re
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import MessageEntityType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from beanie import Document, init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
import os

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MONGODB_URL = os.getenv("MONGODB_URL")
DB_NAME = "group_guardian"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# FIXED: Use DefaultBotProperties
default_props = DefaultBotProperties(parse_mode="HTML")
bot = Bot(token=BOT_TOKEN, default=default_props)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ================ BEANIE MODELS ================
class RecurringMessage(BaseModel):
    type: str  # text, photo, video
    text: str
    file_id: str | None = None
    buttons: list[dict] = []
    interval: int  # seconds
    last_sent: float = 0

class GroupConfig(Document):
    chat_id: str
    recurring_data: list[RecurringMessage] = []
    anti_link: bool = True
    anti_mention: bool = True
    banned_words: list[str] = []

    class Settings:
        name = "groups"

# ================ STATES ================
class PanelStates(StatesGroup):
    waiting_banned_word = State()

class RecurringStates(StatesGroup):
    waiting_content = State()
    waiting_interval = State()
    waiting_buttons = State()

# ================ PANEL ================
def get_main_panel():
    kb = [
        [InlineKeyboardButton("Recurring Ads", callback_data="recurring")],
        [InlineKeyboardButton("Anti-Link", callback_data="toggle_link"),
         InlineKeyboardButton("Anti-Mention", callback_data="toggle_mention")],
        [InlineKeyboardButton("Banned Words", callback_data="banned_words")],
        [InlineKeyboardButton("Back", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_recurring_panel():
    kb = [
        [InlineKeyboardButton("Add New", callback_data="add_recurring")],
        [InlineKeyboardButton("Stop All", callback_data="stop_all_recurring")],
        [InlineKeyboardButton("Back", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_banned_words_panel():
    kb = [
        [InlineKeyboardButton("Add Word", callback_data="add_banned")],
        [InlineKeyboardButton("Clear All", callback_data="clear_banned")],
        [InlineKeyboardButton("Back", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ================ HELPERS ================
async def is_admin(chat_id: int, user_id: int):
    if user_id == OWNER_ID:
        return True
    try:
        admins = await bot.get_chat_administrators(chat_id)
        return any(a.user.id == user_id for a in admins)
    except:
        return False

def has_link_or_mention(message: types.Message):
    entities = message.entities or [] + (message.caption_entities or [])
    for e in entities:
        if e.type in [
            MessageEntityType.URL, MessageEntityType.TEXT_LINK,
            MessageEntityType.MENTION, MessageEntityType.TEXT_MENTION
        ]:
            return True
    text = message.text or message.caption or ""
    return bool(re.search(r'http[s]?://|www\.|t\.me|@', text))

def contains_banned_word(text: str, banned: list):
    if not text or not banned:
        return False
    text = text.lower()
    return any(word.lower() in text for word in banned)

# ================ RECURRING LOOP ================
async def send_recurring(chat_id: int):
    group = await GroupConfig.find_one(GroupConfig.chat_id == str(chat_id))
    if not group or not group.recurring_data:
        return

    now = datetime.now().timestamp()
    updated = False

    for item in group.recurring_data:
        if now - item.last_sent < item.interval:
            continue

        builder = InlineKeyboardBuilder()
        for b in item.buttons:
            builder.row(InlineKeyboardButton(text=b["text"], url=b["url"]))

        try:
            if item.type == "photo":
                await bot.send_photo(chat_id, item.file_id, caption=item.text, reply_markup=builder.as_markup())
            elif item.type == "video":
                await bot.send_video(chat_id, item.file_id, caption=item.text, reply_markup=builder.as_markup())
            else:
                await bot.send_message(
                    chat_id, item.text, reply_markup=builder.as_markup(),
                    disable_web_page_preview=True  # This is now allowed in send_message
                )
            item.last_sent = now
            updated = True
        except Exception as e:
            log.error(f"Send error: {e}")

    if updated:
        await group.save()

async def recurring_loop():
    while True:
        await asyncio.sleep(60)
        async for group in GroupConfig.find({"recurring_data.0": {"$exists": True}}):
            asyncio.create_task(send_recurring(int(group.chat_id)))

# ================ MESSAGE HANDLER ================
@dp.message()
async def handle_message(message: types.Message):
    if message.chat.type not in ["supergroup", "group"]:
        return

    chat_id = str(message.chat.id)
    group = await GroupConfig.find_one(GroupConfig.chat_id == chat_id)

    if not group:
        group = GroupConfig(chat_id=chat_id)
        await group.insert()

    if message.from_user and await is_admin(message.chat.id, message.from_user.id):
        return

    if (group.anti_link or group.anti_mention) and has_link_or_mention(message):
        try:
            await message.delete()
        except:
            pass

    text = message.text or message.caption or ""
    if group.banned_words and contains_banned_word(text, group.banned_words):
        try:
            await message.delete()
        except:
            pass

# ================ PANEL ================
@dp.message(Command("panel"))
async def panel_cmd(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return await message.reply("Only owner.")
    await message.reply("Group Guardian Panel", reply_markup=get_main_panel())

@dp.callback_query(lambda c: c.data == "back_main")
async def back_main(callback: CallbackQuery):
    await callback.message.edit_text("Group Guardian Panel", reply_markup=get_main_panel())

# --- RECURRING ---
@dp.callback_query(lambda c: c.data == "recurring")
async def panel_recurring(callback: CallbackQuery):
    group = await GroupConfig.find_one(GroupConfig.chat_id == str(callback.message.chat.id))
    count = len(group.recurring_data) if group else 0
    await callback.message.edit_text(
        f"<b>Recurring Ads</b>\nActive: {count}",
        reply_markup=get_recurring_panel()
    )

@dp.callback_query(lambda c: c.data == "add_recurring")
async def add_recurring_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RecurringStates.waiting_content)
    await callback.message.edit_text("Send message (text/photo/video):")

@dp.message(RecurringStates.waiting_content)
async def get_content(message: types.Message, state: FSMContext):
    data = {"text": message.caption or message.text or "", "type": "text", "file_id": None}
    if message.photo:
        data.update({"type": "photo", "file_id": message.photo[-1].file_id})
    elif message.video:
        data.update({"type": "video", "file_id": message.video.file_id})
    await state.update_data(content=data)
    await message.reply("Interval in minutes:")
    await state.set_state(RecurringStates.waiting_interval)

@dp.message(RecurringStates.waiting_interval)
async def get_interval(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) < 1:
        return await message.reply("Number > 0")
    await state.update_data(interval=int(message.text) * 60)
    await message.reply("Add buttons? (Yes/No)")
    await state.set_state(RecurringStates.waiting_buttons)

@dp.message(RecurringStates.waiting_buttons)
async def get_buttons(message: types.Message, state: FSMContext):
    data = await state.get_data()
    buttons = []
    if message.text.strip().lower() != "no":
        for line in message.text.strip().split("\n"):
            if "|" in line:
                t, u = line.split("|", 1)
                buttons.append({"text": t.strip(), "url": u.strip()})
    content = data["content"]
    content.update({
        "buttons": buttons,
        "interval": data["interval"],
        "last_sent": 0
    })
    msg = RecurringMessage(**content)
    group = await GroupConfig.find_one(GroupConfig.chat_id == str(message.chat.id))
    if not group:
        group = GroupConfig(chat_id=str(message.chat.id))
    group.recurring_data.append(msg)
    await group.save()
    await message.reply("Recurring saved!", reply_markup=get_main_panel())
    await state.clear()

@dp.callback_query(lambda c: c.data == "stop_all_recurring")
async def stop_all(callback: CallbackQuery):
    group = await GroupConfig.find_one(GroupConfig.chat_id == str(callback.message.chat.id))
    if group:
        group.recurring_data = []
        await group.save()
    await callback.message.edit_text("All stopped.", reply_markup=get_main_panel())

# --- TOGGLES ---
@dp.callback_query(lambda c: c.data in ["toggle_link", "toggle_mention"])
async def toggle_feature(callback: CallbackQuery):
    field = "anti_link" if callback.data == "toggle_link" else "anti_mention"
    group = await GroupConfig.find_one(GroupConfig.chat_id == str(callback.message.chat.id))
    if not group:
        group = GroupConfig(chat_id=str(callback.message.chat.id))
    setattr(group, field, not getattr(group, field))
    await group.save()
    await callback.answer(f"{field.replace('_', ' ').title()}: {'ON' if getattr(group, field) else 'OFF'}")

# --- BANNED WORDS ---
@dp.callback_query(lambda c: c.data == "banned_words")
async def banned_menu(callback: CallbackQuery):
    await callback.message.edit_text("Banned Words", reply_markup=get_banned_words_panel())

@dp.callback_query(lambda c: c.data == "add_banned")
async def add_banned_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PanelStates.waiting_banned_word)
    await callback.message.edit_text("Send word:")

@dp.message(PanelStates.waiting_banned_word)
async def save_banned(message: types.Message, state: FSMContext):
    word = message.text.strip()
    group = await GroupConfig.find_one(GroupConfig.chat_id == str(message.chat.id))
    if not group:
        group = GroupConfig(chat_id=str(message.chat.id))
    if word not in group.banned_words:
        group.banned_words.append(word)
        await group.save()
    await message.reply(f"Banned: `{word}`", reply_markup=get_main_panel())
    await state.clear()

@dp.callback_query(lambda c: c.data == "clear_banned")
async def clear_banned(callback: CallbackQuery):
    group = await GroupConfig.find_one(GroupConfig.chat_id == str(callback.message.chat.id))
    if group:
        group.banned_words = []
        await group.save()
    await callback.message.edit_text("Cleared.", reply_markup=get_main_panel())

# ================ STARTUP ================
async def on_startup():
    client = AsyncIOMotorClient(MONGODB_URL)
    await init_beanie(database=client[DB_NAME], document_models=[GroupConfig])
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_my_commands([types.BotCommand(command="panel", description="Open panel")])

async def main():
    dp.startup.register(on_startup)
    asyncio.create_task(recurring_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
