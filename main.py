from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

@dp.message(Command("panel"))
async def panel(message: types.Message):
    if message.chat.type != "private":
        return await message.reply("Only in DM!")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("TEST BUTTON", callback_data="test")]
    ])
    await message.reply("PANEL WORKS!", reply_markup=kb)

@dp.callback_query(lambda c: c.data == "test")
async def test_cb(callback: types.CallbackQuery):
    await callback.answer("IT WORKS!")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

