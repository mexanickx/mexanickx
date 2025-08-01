import logging
import os
import asyncio
import threading
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InputFile, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настройки
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# Инициализация логгера
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# База данных
class Database:
    def __init__(self):
        self.user_channels = {}
        self.scheduled_mailings = []
        self.current_state = {}

db = Database()

# Клавиатуры
def get_main_kb():
    buttons = [
        [KeyboardButton(text="➕ Добавить канал")],
        [KeyboardButton(text="📋 Мои каналы")],
        [KeyboardButton(text="🚀 Создать рассылку")],
        [KeyboardButton(text="❌ Удалить канал")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена")]], 
        resize_keyboard=True
    )

def get_confirm_kb():
    buttons = [
        [KeyboardButton(text="✅ Подтвердить")],
        [KeyboardButton(text="Отмена")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_channels_kb(user_id, prefix="select"):
    buttons = []
    if user_id in db.user_channels:
        for channel_id, channel_name in db.user_channels[user_id].items():
            buttons.append([
                InlineKeyboardButton(
                    text=channel_name or f"Канал {channel_id}",
                    callback_data=f"{prefix}_{channel_id}"
                )
            ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Фиктивный веб-сервер для Render
async def health_check(request):
    return web.Response(text="Bot is running")

def run_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    web.run_app(app, port=int(os.getenv("PORT", 8080)))

# Обработчики команд
@dp.message_handler(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для управления рассылками в Telegram каналах.\n"
        "Выберите действие из меню ниже:",
        reply_markup=get_main_kb()
    )

@dp.message_handler(lambda message: message.text == "➕ Добавить канал")
async def add_channel(message: types.Message):
    user_id = message.from_user.id
    if user_id not in db.user_channels:
        db.user_channels[user_id] = {}

    db.current_state[user_id] = {"action": "awaiting_channel"}
    await message.answer(
        "📤 Перешлите любое сообщение из канала, который хотите добавить:",
        reply_markup=get_cancel_kb()
    )

@dp.message_handler(content_types=types.ContentType.ANY, is_forwarded=True)
async def handle_channel(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state.get("action") == "awaiting_channel":
        channel = message.forward_from_chat
        if not channel:
            await message.answer("❌ Не удалось получить информацию о канале")
            return

        try:
            chat_member = await bot.get_chat_member(channel.id, bot.id)
            if chat_member.status not in ['administrator', 'creator']:
                await message.answer(
                    "❌ Я не являюсь администратором этого канала.",
                    reply_markup=get_main_kb()
                )
                return
        except Exception as e:
            logger.error(f"Ошибка проверки администратора: {e}")
            await message.answer(
                "❌ Не удалось проверить права доступа.",
                reply_markup=get_main_kb()
            )
            return

        db.user_channels[user_id][channel.id] = channel.title
        await message.answer(
            f"✅ Канал {channel.title} успешно добавлен!",
            reply_markup=get_main_kb()
        )
        db.current_state.pop(user_id, None)

@dp.message_handler(lambda message: message.text == "📋 Мои каналы")
async def list_channels(message: types.Message):
    user_id = message.from_user.id
    if user_id not in db.user_channels or not db.user_channels[user_id]:
        await message.answer("У вас пока нет добавленных каналов.")
        return

    channels_list = "\n".join(
        f"{i+1}. {name}" if name else f"{i+1}. Канал (ID: {id})"
        for i, (id, name) in enumerate(db.user_channels[user_id].items())
    )
    
    await message.answer(
        f"📋 Ваши каналы:\n{channels_list}",
        reply_markup=get_main_kb()
    )

@dp.message_handler(lambda message: message.text == "🚀 Создать рассылку")
async def create_mailing(message: types.Message):
    user_id = message.from_user.id
    if user_id not in db.user_channels or not db.user_channels[user_id]:
        await message.answer(
            "У вас нет добавленных каналов. Сначала добавьте канал.",
            reply_markup=get_main_kb()
        )
        return

    await message.answer(
        "Выберите канал для рассылки:",
        reply_markup=get_channels_kb(user_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("select_"))
async def select_channel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    channel_id = int(callback.data.split("_")[1])

    if user_id not in db.user_channels or channel_id not in db.user_channels[user_id]:
        await callback.answer("Этот канал не найден в вашем списке", show_alert=True)
        return

    db.current_state[user_id] = {
        "action": "creating_mailing",
        "channel_id": channel_id,
        "step": "awaiting_time"
    }

    await callback.message.answer(
        f"Выбран канал: {db.user_channels[user_id][channel_id]}\n"
        "⏰ Введите время рассылки в формате ЧЧ:ММ (например, 14:30):",
        reply_markup=get_cancel_kb()
    )
    await callback.answer()

@dp.message_handler(lambda message: db.current_state.get(message.from_user.id, {}).get("action") == "creating_mailing")
async def process_mailing(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state.get("step") == "awaiting_time":
        try:
            datetime.strptime(message.text, "%H:%M")
            user_state["time"] = message.text
            user_state["step"] = "awaiting_text"
            await message.answer(
                "✍️ Введите текст рассылки:",
                reply_markup=get_cancel_kb()
            )
        except ValueError:
            await message.answer(
                "❌ Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ (например, 14:30):",
                reply_markup=get_cancel_kb()
            )

    elif user_state.get("step") == "awaiting_text":
        if not message.text.strip():
            await message.answer(
                "Текст рассылки не может быть пустым. Пожалуйста, введите текст:",
                reply_markup=get_cancel_kb()
            )
            return

        user_state["text"] = message.text.strip()
        user_state["step"] = "awaiting_media"
        await message.answer(
            "🖼️ Отправьте изображение для рассылки (или нажмите 'пропустить' для текстовой рассылки):",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="пропустить")]],
                resize_keyboard=True
            )
        )

    elif user_state.get("step") == "awaiting_media":
        if message.text and message.text.lower() == "пропустить":
            user_state["media_path"] = None
            await confirm_mailing(message)
        elif message.photo:
            # Удаляем предыдущее изображение, если оно было
            if "media_path" in user_state and user_state["media_path"]:
                try:
                    os.remove(user_state["media_path"])
                except:
                    pass
            
            photo = message.photo[-1]
            file_id = photo.file_id
            file = await bot.get_file(file_id)
            file_path = file.file_path

            if not os.path.exists("media"):
                os.makedirs("media")

            local_path = f"media/{user_id}_{file_id}.jpg"
            await bot.download_file(file_path, local_path)
            user_state["media_path"] = local_path
            await confirm_mailing(message)
        else:
            await message.answer(
                "Пожалуйста, отправьте изображение или нажмите 'пропустить'.",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="пропустить")]],
                    resize_keyboard=True
                )
            )

    elif user_state.get("step") == "awaiting_media":
        if message.text and message.text.lower() == "пропустить":
            user_state["media_path"] = None
            await confirm_mailing(message)
        elif message.photo:
            photo = message.photo[-1]
            file_id = photo.file_id
            file = await bot.get_file(file_id)
            file_path = file.file_path

            if not os.path.exists("media"):
                os.makedirs("media")

            local_path = f"media/{user_id}_{file_id}.jpg"
            await bot.download_file(file_path, local_path)
            user_state["media_path"] = local_path
            await confirm_mailing(message)
        else:
            await message.answer(
                "Пожалуйста, отправьте изображение или нажмите 'пропустить'.",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="пропустить")]],
                    resize_keyboard=True
                )
            )

async def confirm_mailing(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state.get("action") != "creating_mailing":
        return

    channel_id = user_state.get("channel_id")
    time_str = user_state.get("time")
    text = user_state.get("text")
    media_path = user_state.get("media_path")

    if None in [channel_id, time_str, text]:
        await message.answer(
            "❌ Ошибка: недостаточно данных для создания рассылки",
            reply_markup=get_main_kb()
        )
        db.current_state.pop(user_id, None)
        return

    db.current_state[user_id] = {
        "action": "confirming_mailing",
        "mailing_data": {
            "channel_id": channel_id,
            "time": time_str,
            "text": text,
            "media_path": media_path
        }
    }

    channel_name = db.user_channels[user_id][channel_id]
    confirm_text = (
        f"📋 Подтвердите рассылку для канала {channel_name}:\n\n"
        f"⏰ Время: {time_str}\n"
        f"📝 Текст: {text}\n\n"
        "Нажмите «✅ Подтвердить» для создания рассылки"
    )

    if media_path:
        await message.answer_photo(
            photo=InputFile(media_path),
            caption=confirm_text,
            reply_markup=get_confirm_kb()
        )
    else:
        await message.answer(
            confirm_text,
            reply_markup=get_confirm_kb()
        )

@dp.message_handler(lambda message: message.text == "✅ Подтвердить")
async def finalize_mailing(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state.get("action") != "confirming_mailing":
        return

    mailing_data = user_state.get("mailing_data", {})
    channel_id = mailing_data.get("channel_id")
    time_str = mailing_data.get("time")
    text = mailing_data.get("text")
    media_path = mailing_data.get("media_path")

    if None in [channel_id, time_str, text]:
        await message.answer(
            "❌ Ошибка: недостаточно данных для создания рассылки",
            reply_markup=get_main_kb()
        )
        db.current_state.pop(user_id, None)
        return

    try:
        hour, minute = map(int, time_str.split(":"))
        channel_name = db.user_channels[user_id][channel_id]

        job_id = f"mailing_{user_id}_{channel_id}_{int(time.time())}"

        scheduler.add_job(
            send_mailing,
            'cron',
            hour=hour,
            minute=minute,
            args=[channel_id, text, media_path],
            id=job_id
        )

        db.scheduled_mailings.append({
            "user_id": user_id,
            "channel_id": channel_id,
            "time": time_str,
            "text": text,
            "media_path": media_path,
            "job_id": job_id
        })

        await message.answer(
            f"✅ Рассылка для канала {channel_name} успешно создана!\n"
            f"⏰ Время отправки: {hour:02d}:{minute:02d} (ежедневно)",
            reply_markup=get_main_kb()
        )

    except Exception as e:
        logger.error(f"Ошибка создания рассылки: {e}")
        await message.answer(
            f"❌ Произошла ошибка при создании рассылки: {str(e)}",
            reply_markup=get_main_kb()
        )
    finally:
        db.current_state.pop(user_id, None)

async def send_mailing(channel_id: int, text: str, media_path: str):
    try:
        if media_path:
            await bot.send_photo(
                chat_id=channel_id,
                photo=InputFile(media_path),
                caption=text
            )
        else:
            await bot.send_message(
                chat_id=channel_id,
                text=text
            )
    except Exception as e:
        logger.error(f"Ошибка отправки в канал {channel_id}: {e}")

@dp.message_handler(lambda message: message.text == "❌ Удалить канал")
async def delete_channel_start(message: types.Message):
    user_id = message.from_user.id
    if user_id not in db.user_channels or not db.user_channels[user_id]:
        await message.answer("У вас нет добавленных каналов для удаления.")
        return

    await message.answer(
        "Выберите канал для удаления:",
        reply_markup=get_channels_kb(user_id, "delete")
    )

@dp.callback_query_handler(lambda c: c.data.startswith("delete_"))
async def delete_channel_confirm(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    channel_id = int(callback.data.split("_")[1])

    if user_id not in db.user_channels or channel_id not in db.user_channels[user_id]:
        await callback.answer("Этот канал не найден в вашем списке", show_alert=True)
        return

    channel_name = db.user_channels[user_id][channel_id]
    db.current_state[user_id] = {
        "action": "deleting_channel",
        "channel_id": channel_id
    }

    await callback.message.answer(
        f"Вы уверены, что хотите удалить канал {channel_name}?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Да, удалить")],
                [KeyboardButton(text="❌ Нет, отмена")]
            ],
            resize_keyboard=True
        )
    )
    await callback.answer()

@dp.message_handler(lambda message: message.text == "✅ Да, удалить")
async def delete_channel_final(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state.get("action") != "deleting_channel":
        return

    channel_id = user_state.get("channel_id")
    if user_id in db.user_channels and channel_id in db.user_channels[user_id]:
        channel_name = db.user_channels[user_id].pop(channel_id)
        await message.answer(
            f"✅ Канал {channel_name} успешно удален!",
            reply_markup=get_main_kb()
        )

    db.current_state.pop(user_id, None)

@dp.message_handler(lambda message: message.text == "❌ Нет, отмена")
async def cancel_channel_deletion(message: types.Message):
    user_id = message.from_user.id
    db.current_state.pop(user_id, None)
    await message.answer(
        "Удаление канала отменено.",
        reply_markup=get_main_kb()
    )

@dp.message_handler(lambda message: message.text == "Отмена")
async def cancel_action(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state:
        if "media_path" in user_state and user_state["media_path"]:
            try:
                os.remove(user_state["media_path"])
            except:
                pass
        elif "mailing_data" in user_state and "media_path" in user_state["mailing_data"]:
            try:
                os.remove(user_state["mailing_data"]["media_path"])
            except:
                pass

    db.current_state.pop(user_id, None)
    await message.answer(
        "Действие отменено.",
        reply_markup=get_main_kb()
    )

async def on_startup(_):
    if not os.path.exists("media"):
        os.makedirs("media")

    if not scheduler.running:
        scheduler.start()
        logger.info("Планировщик рассылок запущен")

    logger.info("Бот запущен и готов к работе")

if __name__ == '__main__':
    # Запуск фиктивного веб-сервера для Render
    threading.Thread(target=run_web_server, daemon=True).start()
    
    # Запуск бота
    from aiogram import executor
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)
