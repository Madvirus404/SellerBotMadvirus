import asyncio
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.media_group import MediaGroupBuilder

# 1. Токен получаем из переменной окружения
TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬТЕ_ВАШ_ТОКЕН_ЗДЕСЬ")

# 2. ID или Username вашего канала (для проверки подписки)
CHANNEL_ID = "-1001832911088"  # Замените на ваш канал

# 3. ID или Username чата/группы, привязанной к каналу (для проверки мута)
CHAT_ID = "-1001560743965"  # Замените на ID или username чата

dp = Dispatcher()

# Словарь для сборки альбомов
album_data = {}

# Словарь для хранения времени последней публикации пользователя: {user_id: timestamp}
user_cooldowns = {}

# Время ограничения в секундах (1 час = 3600 секунд)
COOLDOWN_TIME = 3600


def is_night_time(message_date: datetime) -> bool:
    """
    Проверяет, является ли время сообщения ночным (с 23:00 до 09:00 по московскому времени),
    опираясь на точное время из Telegram.
    """
    moscow_tz = ZoneInfo("Europe/Moscow")
    message_date_msk = message_date.astimezone(moscow_tz)
    current_hour = message_date_msk.hour

    if current_hour >= 23 or current_hour < 9:
        return True
    return False


async def check_user_restrictions(user_id: int, bot: Bot) -> tuple[bool, str]:
    """
    Проверяет:
    1. Подписан ли пользователь на канал.
    2. Есть ли у него мут (ограничения) в чате.
    """
    try:
        # 1. Проверяем подписку на канал
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        active_statuses = [
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER
        ]
        if member.status not in active_statuses:
            return False, "not_subscribed"

        # 2. Проверяем мут / ограничения в чате
        chat_member = await bot.get_chat_member(chat_id=CHAT_ID, user_id=user_id)
        if chat_member.status == ChatMemberStatus.RESTRICTED:
            if not getattr(chat_member, 'can_send_messages', True):
                return False, "muted"

        return True, "ОК"
    except Exception:
        return True, "ОК"


def validate_ad_text(text: str) -> tuple[bool, str]:
    """
    Проверяет текст объявления:
    1. Наличие запрещенных товаров.
    2. Наличие ссылок.
    3. Наличие ненормативной лексики.
    4. Длину сообщения (более 1 слова).
    5. Указание цены.
    """
    if not text:
        return False, "❌ В объявлении отсутствует текст! Добавьте описание товара и цену."

    text_lower = text.lower()

    # 1. Проверка на запрещенные товары
    illegal_keywords = [
        'оружие', 'пистолет', 'автомат', 'нож', 'патрон', 'травмат', 'пневмат',
        'наркотик', 'закладк', 'меф', 'гашиш', 'кокаин', 'героин', 'соли', 'шишки',
        'паспорт', 'права', 'диплом', 'аттестат', 'документ', 'удостоверен', 'виза',
        'контрафакт', 'реплика', 'дубликат', 'копия бренда', 'фальшив'
    ]
    for word in illegal_keywords:
        if word in text_lower:
            return False, (
                "❌ Публикация отклонена!\n"
                "Запрещено размещать объявления о продаже оружия, наркотиков, "
                "контрафакта, поддельных документов и других товаров, запрещенных законом."
            )

    # 2. Проверка на ссылки
    link_pattern = r'(https?://|www\.|\b[\w-]+\.(ru|com|net|org|su|io|me|info|biz)\b|t\.me/)'
    if re.search(link_pattern, text_lower):
        return False, "❌ В объявлении обнаружены ссылки! Публикация ссылок и сторонних каналов запрещена."

    # 3. Фильтр мата
    bad_words = ['хуй', 'пизд', 'еб', 'бля', 'сук', 'мудак', 'гондон', 'шлюх', 'трах']
    for bad_word in bad_words:
        if bad_word in text_lower:
            return False, "❌ Текст содержит запрещенные слова (мат)! Пожалуйста, отредактируйте объявление."

    # 4. Проверка длины текста
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
    """Проверяет, прошел ли час с последней публикации пользователя."""
    current_time = time.time()
    if user_id in user_cooldowns:
        elapsed_time = current_time - user_cooldowns[user_id]
        if elapsed_time < COOLDOWN_TIME:
            remaining_seconds = int(COOLDOWN_TIME - elapsed_time)
            remaining_minutes = max(1, remaining_seconds // 60)
            return False, remaining_minutes
    return True, 0


async def format_caption_with_contacts(original_text: str, user, bot: Bot) -> str:
    """Формирует итоговый текст объявления с контактами автора и ссылкой на бота."""
    user_name = user.full_name
    username = f"@{user.username}" if user.username else "нет username (пишите в ЛС)"

    bot_info = await bot.get_me()
    bot_username = bot_info.username

    footer = (
        f"\n\n------------------------\n"
        f"Продавец: {user_name} ({username})\n"
        f"Разместить объявление: @{bot_username}"
    )
    
    return f"{original_text}{footer}"


async def get_channel_invite_link(bot: Bot) -> str:
    """Получает ссылку на канал для кнопки подписки."""
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        if chat.invite_link:
            return chat.invite_link
        if chat.username:
            return f"https://t.me/{chat.username}"
    except Exception:
        pass
    return "https://t.me/"


@dp.message(CommandStart(), F.chat.type == "private")
async def command_start_handler(message: Message) -> None:
    await message.answer(
        "👋 Привет! Я бот для публикации объявлений.\n\n"
        "⛔ Правила размещения объявлений:\n"
        "Нельзя размещать объявления о продаже оружия, наркотиков, контрафакта, "
        "поддельных документов и других товаров, запрещенных законом.\n\n"
        "📌 Требования и ограничения:\n"
        "1. Обязательно прикрепите хотя бы одно фото.\n"
        "2. Напишите описание (больше одного слова, без мата и ссылок).\n"
        "3. Обязательно укажите цену (например: 1000 руб, 100$).\n"
        "4. ⏱ Публикация доступна не чаще чем 1 раз в час.\n"
        "5. 🌙 **Ночной режим:** публикация запрещена с 23:00 до 09:00 по МСК.\n"
        "6. 📢 Для публикации нужно быть подписанным на канал и не иметь мута в чате."
    )


# --- 1. Отклоняем сообщения БЕЗ фото (только в ЛС) ---
@dp.message(F.chat.type == "private", F.text & ~F.photo)
async def reject_text_only(message: Message):
    await message.answer("❌ Ошибка: Объявление должно содержать хотя бы одно фото!")


# --- 2. Обработка одиночных фото (только в ЛС) ---
@dp.message(F.chat.type == "private", F.photo & ~F.media_group_id)
async def handle_single_photo(message: Message, bot: Bot):
    user_id = message.from_user.id

    if is_night_time(message.date):
        await message.answer("🌙 **Ночной режим:** публикация объявлений временно закрыта.\nВы можете отправить объявление с 09:00 до 23:00 по московскому времени.")
        return

    is_ok, err_type = await check_user_restrictions(user_id, bot)
    if not is_ok:
        if err_type == "not_subscribed":
            channel_link = await get_channel_invite_link(bot)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_link)]
            ])
            await message.answer(
                "❌ **Вы не подписаны на наш канал!**\n\n"
                "Подпишитесь на канал, а затем отправьте объявление повторно.",
                reply_markup=keyboard
            )
        elif err_type == "muted":
            await message.answer("❌ **Вам ограничен доступ (мут)** в привязанном чате. Вы не можете публиковать объявления.")
        return

    can_post, minutes_left = check_cooldown(user_id)
    if not can_post:
        await message.answer(
            f"⏳ Вы слишком часто публикуете объявления!\n"
            f"Следующее объявление можно будет отправить через {minutes_left} мин."
        )
        return

    caption = message.caption or ""
    
    is_valid, error_message = validate_ad_text(caption)
    if not is_valid:
        await message.answer(error_message)
        return

    try:
        final_caption = await format_caption_with_contacts(caption, message.from_user, bot)
        photo_id = message.photo[-1].file_id
        
        await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=photo_id,
            caption=final_caption
        )
        
        user_cooldowns[user_id] = time.time()
        await message.answer("✅ Ваше объявление успешно опубликовано в канале!")
    except Exception as e:
        await message.answer(f"❌ Ошибка публикации: {e}")


# --- 3. Обработка альбомов (только в ЛС) ---
@dp.message(F.chat.type == "private", F.media_group_id)
async def handle_media_group(message: Message, bot: Bot):
    group_id = message.media_group_id

    if group_id not in album_data:
        album_data[group_id] = []
        asyncio.create_task(send_album_later(group_id, message.chat.id, message.from_user, message.date, bot))

    album_data[group_id].append(message)


async def send_album_later(group_id: str, chat_id: int, user, message_date: datetime, bot: Bot):
    await asyncio.sleep(1.2)

    messages = album_data.pop(group_id, None)
    if not messages:
        return

    user_id = user.id

    if is_night_time(message_date):
        await bot.send_message(
            chat_id,
            "🌙 **Ночной режим:** публикация объявлений временно закрыта.\nВы можете отправить объявление с 09:00 до 23:00 по московскому времени."
        )
        return

    is_ok, err_type = await check_user_restrictions(user_id, bot)
    if not is_ok:
        if err_type == "not_subscribed":
            channel_link = await get_channel_invite_link(bot)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_link)]
            ])
            await bot.send_message(
                chat_id,
                "❌ **Вы не подписаны на наш канал!**\n\n"
                "Подпишитесь на канал, а затем отправьте объявление повторно.",
                reply_markup=keyboard
            )
        elif err_type == "muted":
            await bot.send_message(chat_id, "❌ **Вам ограничен доступ (мут)** в привязанном чате. Вы не можете публиковать объявления.")
        return

    can_post, minutes_left = check_cooldown(user_id)
    if not can_post:
        await bot.send_message(
            chat_id,
            f"⏳ Вы слишком часто публикуете объявления!\n"
            f"Следующее объявление можно будет отправить через {minutes_left} мин."
        )
        return

    caption_text = None
    for msg in messages:
        if msg.caption:
            caption_text = msg.caption
            break

    is_valid, error_message = validate_ad_text(caption_text)
    if not is_valid:
        await bot.send_message(chat_id, error_message)
        return

    final_caption = await format_caption_with_contacts(caption_text, user, bot)

    media_builder = MediaGroupBuilder()
    for msg in messages:
        photo = msg.photo[-1].file_id
        media_builder.add_photo(media=photo)

    media_builder.caption = final_caption

    try:
        await bot.send_media_group(chat_id=CHANNEL_ID, media=media_builder.build())
        user_cooldowns[user_id] = time.time()
        await bot.send_message(chat_id, "✅ Ваше объявление с альбомом успешно опубликовано!")
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Ошибка при публикации альбома: {e}")


async def main() -> None:
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен!")