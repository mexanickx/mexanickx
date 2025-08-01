import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import asyncio
import os
import time
from os import environ

# Настройки
API_TOKEN = environ.get('8348898919:AAHsfBrt5QGS5_qoX8_5QLLOfSYcLh6aYAU')
if not API_TOKEN:
    raise ValueError("Не установлен TELEGRAM_BOT_TOKEN в переменных окружения")

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# Инициализация
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()


# База данных
class Database:

    def __init__(self):
        self.user_channels = {}  # {user_id: {channel_id: channel_name}}
        self.scheduled_mailings = []  # Все активные рассылки
        self.current_state = {}  # Текущее состояние пользователей


db = Database()


# Клавиатуры
def get_main_kb():
    buttons = [[KeyboardButton(text="➕ Добавить канал")],
               [KeyboardButton(text="📋 Мои каналы")],
               [KeyboardButton(text="🚀 Создать рассылку")],
               [KeyboardButton(text="❌ Удалить канал")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]],
                               resize_keyboard=True)


def get_confirm_kb():
    buttons = [[KeyboardButton(text="✅ Подтвердить")],
               [KeyboardButton(text="Отмена")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_channels_kb(user_id, prefix="select"):
    buttons = []
    if user_id in db.user_channels:
        for channel_id, channel_name in db.user_channels[user_id].items():
            buttons.append([
                InlineKeyboardButton(text=channel_name
                                     or f"Канал {channel_id}",
                                     callback_data=f"{prefix}_{channel_id}")
            ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для управления рассылками в Telegram каналах.\n"
        "Выберите действие из меню ниже:",
        reply_markup=get_main_kb())


@dp.message(F.text == "➕ Добавить канал")
async def add_channel(message: types.Message):
    user_id = message.from_user.id
    if user_id not in db.user_channels:
        db.user_channels[user_id] = {}

    db.current_state[user_id] = {"action": "awaiting_channel"}
    await message.answer(
        "📤 Перешлите любое сообщение из канала, который хотите добавить:",
        reply_markup=get_cancel_kb())


@dp.message(F.forward_from_chat)
async def handle_channel(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state.get("action") == "awaiting_channel":
        channel = message.forward_from_chat

        # Проверяем, что бот является администратором канала
        try:
            chat_member = await bot.get_chat_member(channel.id, bot.id)
            if chat_member.status not in ['administrator', 'creator']:
                await message.answer(
                    "❌ Я не являюсь администратором этого канала. "
                    "Добавьте меня в администраторы и попробуйте снова.",
                    reply_markup=get_main_kb())
                return
        except Exception as e:
            logger.error(f"Ошибка проверки администратора: {e}")
            await message.answer(
                "❌ Не удалось проверить права доступа. Убедитесь, что я добавлен в канал как администратор.",
                reply_markup=get_main_kb())
            return

        db.user_channels[user_id][channel.id] = channel.title
        await message.answer(f"✅ Канал {channel.title} успешно добавлен!",
                             reply_markup=get_main_kb())
        db.current_state.pop(user_id, None)


@dp.message(F.text == "📋 Мои каналы")
async def list_channels(message: types.Message):
    user_id = message.from_user.id
    if user_id not in db.user_channels or not db.user_channels[user_id]:
        await message.answer("У вас пока нет добавленных каналов.")
        return

    channels_list = "\n".join(
        f"{i+1}. {name}" if name else f"{i+1}. Канал (ID: {id})"
        for i, (id, name) in enumerate(db.user_channels[user_id].items()))

    await message.answer(f"📋 Ваши каналы:\n{channels_list}",
                         reply_markup=get_main_kb())


@dp.message(F.text == "🚀 Создать рассылку")
async def create_mailing(message: types.Message):
    user_id = message.from_user.id
    if user_id not in db.user_channels or not db.user_channels[user_id]:
        await message.answer(
            "У вас нет добавленных каналов. Сначала добавьте канал.",
            reply_markup=get_main_kb())
        return

    await message.answer("Выберите канал для рассылки:",
                         reply_markup=get_channels_kb(user_id))


@dp.callback_query(F.data.startswith("select_"))
async def select_channel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    channel_id = int(callback.data.split("_")[1])

    if user_id not in db.user_channels or channel_id not in db.user_channels[
            user_id]:
        await callback.answer("Этот канал не найден в вашем списке",
                              show_alert=True)
        return

    db.current_state[user_id] = {
        "action": "creating_mailing",
        "channel_id": channel_id,
        "step": "awaiting_time"
    }

    await callback.message.answer(
        f"Выбран канал: {db.user_channels[user_id][channel_id]}\n"
        "⏰ Введите время рассылки в формате ЧЧ:ММ (например, 14:30):",
        reply_markup=get_cancel_kb())
    await callback.answer()


@dp.message(lambda m: db.current_state.get(m.from_user.id, {}).get("action") ==
            "creating_mailing")
async def process_mailing(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state.get("step") == "awaiting_time":
        try:
            datetime.strptime(message.text, "%H:%M")
            user_state["time"] = message.text
            user_state["step"] = "awaiting_text"
            await message.answer("✍️ Введите текст рассылки:",
                                 reply_markup=get_cancel_kb())
        except ValueError:
            await message.answer(
                "❌ Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ (например, 14:30):",
                reply_markup=get_cancel_kb())

    elif user_state.get("step") == "awaiting_text":
        if not message.text.strip():
            await message.answer(
                "Текст рассылки не может быть пустым. Пожалуйста, введите текст:",
                reply_markup=get_cancel_kb())
            return

        user_state["text"] = message.text.strip()
        user_state["step"] = "awaiting_photo"
        await message.answer("🖼️ Отправьте изображение для рассылки:",
                             reply_markup=get_cancel_kb())

    elif user_state.get("step") == "awaiting_photo":
        if not message.photo:
            await message.answer("Пожалуйста, отправьте изображение.",
                                 reply_markup=get_cancel_kb())
            return

        photo = message.photo[-1]
        file_id = photo.file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path

        if not os.path.exists("media"):
            os.makedirs("media")

        local_path = f"media/{user_id}_{file_id}.jpg"
        await bot.download_file(file_path, local_path)
        user_state["photo_path"] = local_path

        await confirm_mailing(message)


async def confirm_mailing(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state.get("action") != "creating_mailing":
        return

    channel_id = user_state.get("channel_id")
    time_str = user_state.get("time")
    text = user_state.get("text")
    photo_path = user_state.get("photo_path")

    if None in [channel_id, time_str, text, photo_path]:
        await message.answer(
            "❌ Ошибка: недостаточно данных для создания рассылки",
            reply_markup=get_main_kb())
        db.current_state.pop(user_id, None)
        return

    # Сохраняем данные для подтверждения
    db.current_state[user_id] = {
        "action": "confirming_mailing",
        "mailing_data": {
            "channel_id": channel_id,
            "time": time_str,
            "text": text,
            "photo_path": photo_path
        }
    }

    channel_name = db.user_channels[user_id][channel_id]
    confirm_text = (f"📋 Подтвердите рассылку для канала {channel_name}:\n\n"
                    f"⏰ Время: {time_str}\n"
                    f"📝 Текст: {text}\n\n"
                    "Нажмите «✅ Подтвердить» для создания рассылки")

    with open(photo_path, 'rb') as photo_file:
        await message.answer_photo(photo=BufferedInputFile(
            photo_file.read(), filename="preview.jpg"),
                                   caption=confirm_text,
                                   reply_markup=get_confirm_kb())


@dp.message(F.text == "✅ Подтвердить")
async def finalize_mailing(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state.get("action") != "confirming_mailing":
        return

    mailing_data = user_state.get("mailing_data", {})
    channel_id = mailing_data.get("channel_id")
    time_str = mailing_data.get("time")
    text = mailing_data.get("text")
    photo_path = mailing_data.get("photo_path")

    if None in [channel_id, time_str, text, photo_path]:
        await message.answer(
            "❌ Ошибка: недостаточно данных для создания рассылки",
            reply_markup=get_main_kb())
        db.current_state.pop(user_id, None)
        return

    try:
        hour, minute = map(int, time_str.split(":"))
        channel_name = db.user_channels[user_id][channel_id]

        # Создаем уникальный ID для задания
        job_id = f"mailing_{user_id}_{channel_id}_{int(time.time())}"

        # Добавляем задание в планировщик
        scheduler.add_job(send_mailing,
                          'cron',
                          hour=hour,
                          minute=minute,
                          args=[channel_id, text, photo_path],
                          id=job_id)

        # Сохраняем информацию о рассылке
        db.scheduled_mailings.append({
            "user_id": user_id,
            "channel_id": channel_id,
            "time": time_str,
            "text": text,
            "photo_path": photo_path,
            "job_id": job_id
        })

        await message.answer(
            f"✅ Рассылка для канала {channel_name} успешно создана!\n"
            f"⏰ Время отправки: {hour:02d}:{minute:02d} (ежедневно)",
            reply_markup=get_main_kb())

    except Exception as e:
        logger.error(f"Ошибка создания рассылки: {e}")
        await message.answer(
            f"❌ Произошла ошибка при создании рассылки: {str(e)}",
            reply_markup=get_main_kb())
    finally:
        # Очищаем состояние
        db.current_state.pop(user_id, None)


async def send_mailing(channel_id: int, text: str, photo_path: str):
    try:
        # Отправляем изображение
        with open(photo_path, 'rb') as photo_file:
            await bot.send_photo(
                chat_id=channel_id,
                photo=BufferedInputFile(photo_file.read(),
                                        filename="mailing.jpg"))

        # Добавляем задержку 3 секунды
        await asyncio.sleep(3)

        # Отправляем текст отдельным сообщением
        await bot.send_message(chat_id=channel_id, text=text)
    except Exception as e:
        logger.error(f"Ошибка отправки в канал {channel_id}: {e}")


@dp.message(F.text == "❌ Удалить канал")
async def delete_channel_start(message: types.Message):
    user_id = message.from_user.id
    if user_id not in db.user_channels or not db.user_channels[user_id]:
        await message.answer("У вас нет добавленных каналов для удаления.")
        return

    await message.answer("Выберите канал для удаления:",
                         reply_markup=get_channels_kb(user_id, "delete"))


@dp.callback_query(F.data.startswith("delete_"))
async def delete_channel_confirm(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    channel_id = int(callback.data.split("_")[1])

    if user_id not in db.user_channels or channel_id not in db.user_channels[
            user_id]:
        await callback.answer("Этот канал не найден в вашем списке",
                              show_alert=True)
        return

    channel_name = db.user_channels[user_id][channel_id]
    db.current_state[user_id] = {
        "action": "deleting_channel",
        "channel_id": channel_id
    }

    await callback.message.answer(
        f"Вы уверены, что хотите удалить канал {channel_name}?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="✅ Да, удалить")],
                      [KeyboardButton(text="❌ Нет, отмена")]],
            resize_keyboard=True))
    await callback.answer()


@dp.message(F.text == "✅ Да, удалить")
async def delete_channel_final(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state.get("action") != "deleting_channel":
        return

    channel_id = user_state.get("channel_id")
    if (user_id in db.user_channels
            and channel_id in db.user_channels[user_id]):

        channel_name = db.user_channels[user_id].pop(channel_id)
        await message.answer(f"✅ Канал {channel_name} успешно удален!",
                             reply_markup=get_main_kb())

    db.current_state.pop(user_id, None)


@dp.message(F.text == "❌ Нет, отмена")
async def cancel_channel_deletion(message: types.Message):
    user_id = message.from_user.id
    db.current_state.pop(user_id, None)
    await message.answer("Удаление канала отменено.",
                         reply_markup=get_main_kb())


@dp.message(F.text == "Отмена")
async def cancel_action(message: types.Message):
    user_id = message.from_user.id
    user_state = db.current_state.get(user_id, {})

    if user_state:
        # Удаляем загруженные фото
        if "photo_path" in user_state:
            try:
                os.remove(user_state["photo_path"])
            except:
                pass
        elif ("mailing_data" in user_state
              and "photo_path" in user_state["mailing_data"]):
            try:
                os.remove(user_state["mailing_data"]["photo_path"])
            except:
                pass

    db.current_state.pop(user_id, None)
    await message.answer("Действие отменено.", reply_markup=get_main_kb())


# Запуск бота
async def main():
    if not os.path.exists("media"):
        os.makedirs("media")

    if not scheduler.running:
        scheduler.start()
        logger.info("Планировщик рассылок запущен")

    logger.info("Бот запущен и готов к работе")
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
