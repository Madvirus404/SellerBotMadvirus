import asyncio
import re
import time
from aiogram import Bot, Dispatcher, html, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.utils.media_group import MediaGroupBuilder

import os

# Получаем токен из системных переменных окружения
TOKEN = os.getenv("BOT_TOKEN")

# 2. ID или Username вашего канала
CHANNEL_ID = "-1003546501494"  # Замените на ваш канал

dp = Dispatcher()

# Словарь для сборки альбомов
album_data = {}

# Словарь для хранения времени последней публикации пользователя: {user_id: timestamp}
user_cooldowns = {}

# Время ограничения в секундах (1 час = 3600 секунд)
COOLDOWN_TIME = 3600


def validate_ad_text(text: str) -> tuple[bool, str]:
    """
    Проверяет текст объявления:
    1. Наличие запрещенных товаров (оружие, наркотики, контрафакт, документы и т.д.).
    2. Наличие ссылок.
    3. Наличие ненормативной лексики.
    4. Длину сообщения (более 1 слова).
    5. Указание цены.
    """
    if not text:
        return False, "❌ В объявлении отсутствует текст! Добавьте описание товара и цену."

    text_lower = text.lower()

    # 1. Проверка на запрещенные товары и услуги
    illegal_keywords = [
        'оружие', 'пистолет', 'автомат', 'нож', 'патрон', 'травмат', 'пневмат',
        'наркотик', 'закладк', 'меф', 'гашиш', 'кокаин', 'героин', 'соли', 'шишки',
        'паспорт', 'права', 'диплом', 'аттестат', 'документ', 'удостоверен', 'виза',
        'контрафакт', 'реплика', 'дубликат', 'копия бренда', 'фальшив'
    ]
    for word in illegal_keywords:
        if word in text_lower:
            return False, (
                "❌ **Публикация отклонена!**\n"
                "Запрещено размещать объявления о продаже оружия, наркотиков, "
                "контрафакта, поддельных документов и других товаров, запрещенных законом."
            )

    # 2. Проверка на ссылки (ищет http, https, www, t.me и популярные домены)
    link_pattern = r'(https?://|www\.|\b[\w-]+\.(ru|com|net|org|su|io|me|info|biz)\b|t\.me/)'
    if re.search(link_pattern, text_lower):
        return False, "❌ В объявлении обнаружены ссылки! Публикация ссылок и сторонних каналов запрещена."

    # 3. Базовый фильтр мата
    bad_words = ['хуй', 'пизд', 'еб', 'бля', 'сук', 'мудак', 'гондон', 'шлюх', 'трах']
    for bad_word in bad_words:
        if bad_word in text_lower:
            return False, "❌ Текст содержит запрещенные слова (мат)! Пожалуйста, отредактируйте объявление."

    # 4. Проверка длины текста (более одного слова)
    words = text.strip().split()
    if len(words) <= 1:
        return False, "❌ Описание слишком короткое! Текст должен содержать больше одного слова."

    # 5. Проверка цены
    price_pattern = r'(\d+[\d\s.,]*\s*(руб|р|rub|usd|\$|€|eur|usdt|коп)|(цена|стоимость)\s*[:\-]?\s*\d+)'
    if not re.search(price_pattern, text_lower):
        return False, (
            "❌ В объявлении не найдена цена!\n"
            "Укажите цену (например: '1000 руб', '500$', 'Цена: 1500' или '5000 р')."
        )

    return True, "ОК"


def check_cooldown(user_id: int) -> tuple[bool, int]:
    """
    Проверяет, прошел ли час с последней публикации пользователя.
    """
    current_time = time.time()
    if user_id in user_cooldowns:
        elapsed_time = current_time - user_cooldowns[user_id]
        if elapsed_time < COOLDOWN_TIME:
            remaining_seconds = int(COOLDOWN_TIME - elapsed_time)
            remaining_minutes = max(1, remaining_seconds // 60)
            return False, remaining_minutes
    return True, 0


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    await message.answer(
        "👋 **Привет! Я бот для публикации объявлений.**\n\n"
        "⛔ **Правила размещения объявлений:**\n"
        "Нельзя размещать объявления о продаже оружия, наркотиков, контрафакта, "
        "поддельных документов и других товаров, запрещенных законом.\n\n"
        "📌 **Требования к сообщению:**\n"
        "1. Обязательно прикрепите **хотя бы одно фото**.\n"
        "2. Напишите **описание** (больше одного слова, без мата и ссылок).\n"
        "3. Обязательно укажите **цену** (например: *1000 руб*, *100$*).\n"
        "4. ⏱ Публикация доступна **не чаще чем 1 раз в час**."
    )


# --- 1. Отклоняем сообщения БЕЗ фото ---
@dp.message(F.text & ~F.photo)
async def reject_text_only(message: Message):
    await message.answer("❌ **Ошибка:** Объявление должно содержать хотя бы одно фото!")


# --- 2. Обработка одиночных фото ---
@dp.message(F.photo & ~F.media_group_id)
async def handle_single_photo(message: Message, bot: Bot):
    user_id = message.from_user.id

    # Проверяем таймер (лимит 1 раз в час)
    can_post, minutes_left = check_cooldown(user_id)
    if not can_post:
        await message.answer(
            f"⏳ Вы слишком часто публикуете объявления!\n"
            f"Следующее объявление можно будет отправить через **{minutes_left} мин.**"
        )
        return

    caption = message.caption or ""
    
    # Проверяем текст (запрещенка, мат, ссылки, длина, цена)
    is_valid, error_message = validate_ad_text(caption)
    if not is_valid:
        await message.answer(error_message)
        return

    try:
        await bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )
        user_cooldowns[user_id] = time.time()
        await message.answer("✅ Ваше объявление успешно опубликовано в канале!")
    except Exception as e:
        await message.answer(f"❌ Ошибка публикации: {e}")


# --- 3. Обработка альбомов (нескольких фото) ---
@dp.message(F.media_group_id)
async def handle_media_group(message: Message, bot: Bot):
    group_id = message.media_group_id

    if group_id not in album_data:
        album_data[group_id] = []
        asyncio.create_task(send_album_later(group_id, message.chat.id, message.from_user.id, bot))

    album_data[group_id].append(message)


async def send_album_later(group_id: str, chat_id: int, user_id: int, bot: Bot):
    await asyncio.sleep(1.2)  # Ждем сбора всех картинок

    messages = album_data.pop(group_id, None)
    if not messages:
        return

    # Проверяем таймер для альбома
    can_post, minutes_left = check_cooldown(user_id)
    if not can_post:
        await bot.send_message(
            chat_id,
            f"⏳ Вы слишком часто публикуете объявления!\n"
            f"Следующее объявление можно будет отправить через **{minutes_left} мин.**"
        )
        return

    caption_text = None
    for msg in messages:
        if msg.caption:
            caption_text = msg.caption
            break  # Берём первую найденную подпись

    # Проверяем текст альбома
    is_valid, error_message = validate_ad_text(caption_text)
    if not is_valid:
        await bot.send_message(chat_id, error_message)
        return

    # Собираем альбом
    media_builder = MediaGroupBuilder()
    for msg in messages:
        photo = msg.photo[-1].file_id
        media_builder.add_photo(media=photo)

    media_builder.caption = caption_text

    try:
        await bot.send_media_group(chat_id=CHANNEL_ID, media=media_builder.build())
        user_cooldowns[user_id] = time.time()
        await bot.send_message(chat_id, "✅ Ваше объявление с альбомом успешно опубликовано!")
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Ошибка при публикации альбома: {e}")


async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен!")