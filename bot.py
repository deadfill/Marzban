import logging
import logging.handlers
import random
import os
import asyncio
import traceback
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from marzban import MarzbanAPI, UserCreate, ProxySettings, UserModify
from datetime import datetime, timedelta, timezone
import aiomysql
from yookassa import Configuration, Payment
from typing import Optional, List, Tuple, Dict, Any
from dotenv import load_dotenv
import json
from pydantic import BaseModel
import string
import socket
from urllib.parse import urlparse
import ipaddress
import httpx



# === Настройка логирования ===
def setup_logging():
    """Настройка логирования."""
    # Ограничиваем лишние сообщения от других библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    # Настройка корневого логгера
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    return logging.getLogger(__name__)

logger = setup_logging()


# === Конфигурация ===
load_dotenv()
API_TOKEN = os.getenv('BOT_TOKEN')
MARZBAN_URL = os.getenv('MARZBAN_URL')  # Используем только значение из переменной окружения
MARZBAN_USERNAME = os.getenv('MARZBAN_USERNAME')
MARZBAN_PASSWORD = os.getenv('MARZBAN_PASSWORD')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
WEBHOOK_PATH = os.getenv('WEBHOOK_PATH', '/webhook')
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEBAPP_HOST = os.getenv('WEBAPP_HOST', '0.0.0.0')
WEBAPP_PORT = int(os.getenv('WEBAPP_PORT', 88))
WEBHOOK_SSL_CERT = os.getenv('WEBHOOK_SSL_CERT')
WEBHOOK_SSL_PRIV = os.getenv('WEBHOOK_SSL_PRIV')

Configuration.account_id = os.getenv('YOOKASSA_ACCOUNT_ID')
Configuration.secret_key = os.getenv('YOOKASSA_SECRET_KEY')

# Проверка и вывод используемых URL для отладки
logger.info(f"Используемый MARZBAN_URL: {MARZBAN_URL}")

# Проверка на окончание URL слешем
if MARZBAN_URL and not MARZBAN_URL.endswith('/'):
    MARZBAN_URL = f"{MARZBAN_URL}/"
    logger.info(f"MARZBAN_URL скорректирован: {MARZBAN_URL}")

# Проверка обязательных переменных окружения
if not WEBHOOK_HOST:
    logger.error("Переменная окружения WEBHOOK_HOST не установлена")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Проверка обязательных переменных окружения
if not MARZBAN_URL:
    logger.error("Переменная окружения MARZBAN_URL не установлена")
if not MARZBAN_USERNAME:
    logger.error("Переменная окружения MARZBAN_USERNAME не установлена")
if not MARZBAN_PASSWORD:
    logger.error("Переменная окружения MARZBAN_PASSWORD не установлена")

# Глобальный пул соединений
db_pool = None

# Глобальный кэш токена
_token_cache = {
    "token": None,
    "expires_at": 0
}


# === Константы ===
DEFAULT_TEST_PERIOD = 7  # 7 дней тестового доступа


# === Утилитные функции ===
def generate_random_string(length=8):
    """Генерирует случайную строку указанной длины."""
    characters = string.ascii_lowercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))


async def init_db_pool():
    """Инициализирует пул соединений с базой данных."""
    global db_pool
    try:
        logger.info("Начало инициализации пула соединений с БД")
        
        # Получаем параметры подключения из переменных окружения
        db_host = os.getenv('DB_HOST', 'localhost')
        db_port = int(os.getenv('DB_PORT', '3306'))
        db_user = os.getenv('DB_USER', 'root')
        db_password = os.getenv('DB_PASSWORD', '')
        db_name = os.getenv('DB_NAME', 'marzban')
        
        logger.info(f"Параметры подключения: хост={db_host}, порт={db_port}, пользователь={db_user}, БД={db_name}")
        
        # Создаем пул соединений
        db_pool = await aiomysql.create_pool(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            db=db_name,
            autocommit=False
        )
        
        logger.info("Пул соединений с БД успешно инициализирован")
        
        # Проверяем соединение
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                result = await cur.fetchone()
                if result and result[0] == 1:
                    logger.info("Соединение с БД успешно установлено и проверено")
                else:
                    logger.error("Не удалось проверить соединение с БД")
    except Exception as e:
        logger.error(f"Ошибка при инициализации пула соединений с БД: {str(e)}")
        logger.error(traceback.format_exc())
        db_pool = None


async def close_db_pool():
    """Закрывает пул соединений с базой данных."""
    global db_pool
    if db_pool:
        db_pool.close()
        await db_pool.wait_closed()
        logger.info("Пул соединений закрыт")


async def initialize_db():
    """Создаёт необходимые таблицы в базе данных."""
    try:
        logger.info("Начало инициализации базы данных")
        
        if not db_pool:
            logger.error("Не удалось инициализировать базу данных: пул соединений не инициализирован")
            return
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Проверяем версию MySQL
                await cur.execute("SELECT VERSION()")
                version = await cur.fetchone()
                logger.info(f"Версия MySQL: {version[0]}")
                
                # Проверяем существование таблицы telegram_users
                logger.info("Проверяем существование таблицы telegram_users")
                await cur.execute("""
                    SELECT COUNT(*)
                    FROM information_schema.tables 
                    WHERE table_schema = DATABASE()
                    AND table_name = 'telegram_users'
                """)
                exists = (await cur.fetchone())[0]
                
                logger.info(f"Проверка существования таблицы telegram_users: {exists > 0}")
                
                if not exists:
                    logger.info("Создание таблицы telegram_users...")
                    try:
                        await cur.execute('''
                            CREATE TABLE telegram_users (
                                user_id BIGINT PRIMARY KEY,
                                test_period TINYINT DEFAULT 1,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            )
                        ''')
                        await conn.commit()
                        logger.info("Таблица telegram_users успешно создана")
                        
                        # Тестовая запись для проверки
                        test_id = 9999999999  # Очень большой ID, который вряд ли будет использоваться
                        logger.info(f"Создаем тестовую запись с ID {test_id}")
                        await cur.execute(
                            "INSERT INTO telegram_users (user_id, test_period, created_at) VALUES (%s, 1, CURRENT_TIMESTAMP)",
                            (test_id,)
                        )
                        await conn.commit()
                        
                        # Проверяем, что запись создана
                        await cur.execute("SELECT COUNT(*) FROM telegram_users WHERE user_id = %s", (test_id,))
                        test_exists = (await cur.fetchone())[0] > 0
                        logger.info(f"Тестовая запись создана: {test_exists}")
                        
                        # Удаляем тестовую запись
                        await cur.execute("DELETE FROM telegram_users WHERE user_id = %s", (test_id,))
                        await conn.commit()
                        logger.info("Тестовая запись удалена")
                    except Exception as create_error:
                        logger.error(f"Ошибка при создании таблицы telegram_users: {create_error}")
                        logger.error(traceback.format_exc())
                else:
                    logger.info("Таблица telegram_users уже существует")
                    
                    # Проверяем структуру таблицы
                    logger.info("Проверяем структуру таблицы telegram_users")
                    await cur.execute("DESCRIBE telegram_users")
                    columns = await cur.fetchall()
                    for column in columns:
                        logger.info(f"Колонка: {column}")
 
                # Проверяем существование таблицы payments
                logger.info("Проверяем существование таблицы payments")
                await cur.execute("""
                    SELECT COUNT(*)
                    FROM information_schema.tables 
                    WHERE table_schema = DATABASE()
                    AND table_name = 'payments'
                """)
                payments_exists = (await cur.fetchone())[0]
                
                logger.info(f"Проверка существования таблицы payments: {payments_exists > 0}")
                
                if not payments_exists:
                    logger.info("Создание таблицы payments...")
                    try:
                        await cur.execute('''
                            CREATE TABLE payments (
                                payment_id VARCHAR(255) PRIMARY KEY,
                                user_id BIGINT,
                                amount DECIMAL(10,2),
                                income_amount DECIMAL(10,2),
                                status VARCHAR(50),
                                description TEXT,
                                payment_method VARCHAR(50),
                                payment_method_details TEXT,
                                created_at TIMESTAMP,
                                captured_at TIMESTAMP,
                                metadata TEXT,
                                FOREIGN KEY (user_id) REFERENCES telegram_users(user_id)
                            )
                        ''')
                        await conn.commit()
                        logger.info("Таблица payments успешно создана")
                    except Exception as create_error:
                        logger.error(f"Ошибка при создании таблицы payments: {create_error}")
                        logger.error(traceback.format_exc())
                else:
                    logger.info("Таблица payments уже существует")
                
                # Проверяем существование таблицы message_tasks
                logger.info("Проверяем существование таблицы message_tasks")
                await cur.execute("""
                    SELECT COUNT(*)
                    FROM information_schema.tables 
                    WHERE table_schema = DATABASE()
                    AND table_name = 'message_tasks'
                """)
                message_tasks_exists = (await cur.fetchone())[0]
                
                logger.info(f"Проверка существования таблицы message_tasks: {message_tasks_exists > 0}")
                
                if not message_tasks_exists:
                    logger.info("Создание таблицы message_tasks...")
                    try:
                        await cur.execute('''
                            CREATE TABLE message_tasks (
                                id INT AUTO_INCREMENT PRIMARY KEY,
                                task_type VARCHAR(50) NOT NULL,
                                cron_expression VARCHAR(100) NOT NULL,
                                message_text TEXT NOT NULL,
                                is_active BOOLEAN DEFAULT TRUE,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                last_run TIMESTAMP NULL,
                                next_run TIMESTAMP NULL
                            )
                        ''')
                        await conn.commit()
                        logger.info("Таблица message_tasks успешно создана")
                    except Exception as create_error:
                        logger.error(f"Ошибка при создании таблицы message_tasks: {create_error}")
                        logger.error(traceback.format_exc())
                else:
                    logger.info("Таблица message_tasks уже существует")
                
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {str(e)}")
        logger.error(traceback.format_exc())


async def get_access_token():
    """Получает токен доступа к Marzban API."""
    global _token_cache
    
    # Проверяем кэшированный токен
    current_time = datetime.now().timestamp()
    if _token_cache["token"] and _token_cache["expires_at"] > current_time:
        logger.info("Используем кэшированный токен")
        return _token_cache["token"]
    
    try:
        # Проверяем параметры
        if not MARZBAN_URL or not MARZBAN_USERNAME or not MARZBAN_PASSWORD:
            logger.error("Отсутствуют необходимые параметры для получения токена")
            logger.error(f"MARZBAN_URL: {MARZBAN_URL}")
            logger.error(f"MARZBAN_USERNAME: {MARZBAN_USERNAME}")
            logger.error(f"MARZBAN_PASSWORD: {'*' * len(MARZBAN_PASSWORD) if MARZBAN_PASSWORD else 'не установлен'}")
            return None
        
        # Создаем экземпляр API
        logger.info(f"Попытка подключения к Marzban API: {MARZBAN_URL}")
        api = MarzbanAPI(base_url=MARZBAN_URL)
        
        # Конфигурация SSL и таймаутов для httpx клиента
        api.client.verify = False  # Отключаем проверку SSL сертификата для внутренней коммуникации
        api.client.timeout = httpx.Timeout(30.0, connect=10.0)  # Увеличиваем таймауты
        
        # Проверка сетевого соединения
        try:
            # Разбираем URL для получения хоста
            parsed_url = urlparse(MARZBAN_URL)
            hostname = parsed_url.netloc.split(':')[0]
            logger.info(f"Проверка разрешения имени хоста: {hostname}")
            
            ip = socket.gethostbyname(hostname)
            logger.info(f"Успешно получен IP для {hostname}: {ip}")
        except Exception as dns_error:
            logger.error(f"Ошибка разрешения имени {hostname}: {dns_error}")
        
        # Получаем токен
        logger.info(f"Выполняем запрос на получение токена с учетными данными: {MARZBAN_USERNAME}")
        try:
            token_response = await api.get_token(
                username=MARZBAN_USERNAME,
                password=MARZBAN_PASSWORD
            )
            logger.info("Токен успешно получен")
            
            if token_response and hasattr(token_response, 'access_token'):
                # Сохраняем токен в кэш на 1 час
                _token_cache["token"] = token_response.access_token
                _token_cache["expires_at"] = current_time + 3600
                return token_response.access_token
            else:
                logger.error(f"Получен неверный ответ от API при запросе токена: {token_response}")
                return None
        except httpx.ConnectTimeout as ct_error:
            logger.error(f"Таймаут подключения к {MARZBAN_URL}: {ct_error}")
            return None
        except httpx.ConnectError as ce_error:
            logger.error(f"Ошибка подключения к {MARZBAN_URL}: {ce_error}")
            return None
        except Exception as token_error:
            logger.error(f"Ошибка при получении токена: {token_error}")
            logger.error(traceback.format_exc())
            return None
    except Exception as e:
        logger.error(f"Общая ошибка получения токена: {e}")
        logger.error(traceback.format_exc())
        return None


# === Утилитные функции ===
async def check_user_exists(user_id):
    """Проверяет, существует ли пользователь в базе данных."""
    try:
        # Проверяем подключение к БД
        if not db_pool:
            logger.error("Не удалось проверить пользователя: пул соединений с базой данных не инициализирован")
            return False
            
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                query = "SELECT COUNT(*) FROM telegram_users WHERE user_id = %s"
                await cur.execute(query, (user_id,))
                result = await cur.fetchone()
                exists = result[0] > 0
                logger.info(f"Проверка пользователя {user_id}: {'существует' if exists else 'не существует'}")
                return exists
    except Exception as e:
        logger.error(f"Ошибка при проверке существования пользователя {user_id}: {str(e)}")
        logger.error(traceback.format_exc())
        # Если возникла ошибка при проверке, считаем, что пользователь не существует
        return False


async def register_user(user_id):
    """Регистрирует нового пользователя в базе данных."""
    try:
        # Проверяем подключение к БД
        if not db_pool:
            logger.error("Не удалось зарегистрировать пользователя: пул соединений с базой данных не инициализирован")
            return False
            
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Проверяем, существует ли пользователь
                check_query = "SELECT COUNT(*) FROM telegram_users WHERE user_id = %s"
                await cur.execute(check_query, (user_id,))
                result = await cur.fetchone()
                exists = result[0] > 0
                
                if not exists:
                    # Если пользователя нет, добавляем его
                    insert_query = "INSERT INTO telegram_users (user_id, test_period, created_at) VALUES (%s, 1, CURRENT_TIMESTAMP)"
                    await cur.execute(insert_query, (user_id,))
                    await conn.commit()
                    logger.info(f"Пользователь {user_id} зарегистрирован в базе данных")
                    return True
                else:
                    logger.info(f"Пользователь {user_id} уже существует в базе данных")
                    return True
                    
    except Exception as e:
        logger.error(f"Ошибка при регистрации пользователя {user_id}: {str(e)}")
        logger.error(traceback.format_exc())
        return False


async def get_user_devices(user_id: int) -> List[Tuple[str, str, int]]:
    """Получает список устройств пользователя и оставшиеся дни.
    
    Возвращает список кортежей (username, link, days_left)
    """
    token = await get_access_token()
    if not token:
        logger.error("Не удалось получить токен для get_user_devices")
        return []
        
    try:
        api = MarzbanAPI(base_url=MARZBAN_URL)
        
        # Получаем все устройства из Marzban API
        all_users = await api.get_users(token)
        # Фильтруем только устройства этого пользователя
        user_devices = [user for user in all_users.users if f"_{user_id}" in user.username and user.status == "active"]
        
        if not user_devices:
            return []
        
        result = []
        
        for device in user_devices:
            # Получаем детали устройства
            user_details = await api.get_user(device.username, token)
            if user_details and user_details.links:
                # Вычисляем срок истечения
                if device.expire:
                    days_left = (datetime.fromtimestamp(device.expire) - datetime.now()).days
                    
                    # Возвращаем только те ключи, у которых не истек срок
                    if days_left >= 0:
                        result.append((device.username, user_details.links[0], days_left))
        
        return result
    
    except Exception as e:
        logger.error(f"Ошибка при получении устройств пользователя {user_id}: {e}")
        logger.error(traceback.format_exc())
        return []


# === Создание пользователя ===
async def create_vless_user(user_id: int) -> Optional[str]:
    """Создает нового пользователя VLESS с тестовым периодом."""
    try:
        # Получаем токен
        token = await get_access_token()
        if not token:
            logger.error("Не удалось получить токен для create_vless_user")
            return None

        # Генерируем уникальное имя пользователя
        marzban_username = f"{generate_random_string()}_{user_id}"
        
        # Устанавливаем время истечения на DEFAULT_TEST_PERIOD дней вперед
        expire_timestamp = int((datetime.now(timezone.utc) + timedelta(days=DEFAULT_TEST_PERIOD)).timestamp())
        expire_date = datetime.fromtimestamp(expire_timestamp, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"Создание ключа: {marzban_username}, срок: {expire_date}")

        # Создаем объект пользователя с настройками
        new_user = UserCreate(
            username=marzban_username,
            proxies={"vless": ProxySettings(flow="xtls-rprx-vision")},
            inbounds={'vless': ['VLESS TCP REALITY']},
            expire=expire_timestamp
        )

        # Инициализируем API и создаем пользователя
        api = MarzbanAPI(base_url=MARZBAN_URL)
        await api.add_user(new_user, token)
        
        # Получаем информацию о созданном пользователе
        user_info = await api.get_user(marzban_username, token)
        if not user_info:
            logger.error(f"Не удалось получить информацию о пользователе {marzban_username}")
            return None
            
        if not user_info.links:
            logger.error(f"У пользователя {marzban_username} нет ссылок")
            return None
            
        logger.info(f"Пользователь {marzban_username} создан")
        return user_info.links[0]
    except Exception as e:
        logger.error(f"Ошибка создания пользователя: {e}")
        logger.error(traceback.format_exc())
        return None


# === Клавиатуры ===
def get_main_menu_keyboard():
    """Клавиатура главного меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Купить VPN | Продлить", callback_data="buy_vpn")],
        [InlineKeyboardButton(text="🔑 Мои активные ключи", callback_data="my_keys")],
        [InlineKeyboardButton(text="🔌 Как подключиться", callback_data="help")],
        [InlineKeyboardButton(text="👥 Партнерская программа", callback_data="affiliate")]
    ])

def get_back_to_menu_keyboard():
    """Клавиатура с кнопкой возврата в главное меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
    ])

def get_tariff_keyboard(username: str = None):
    """Клавиатура с тарифами для нового ключа или продления."""
    prefix = "pay_" + username + "_" if username else "new_key_"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 150₽ - 30 дней", callback_data=f"{prefix}30_150")],
        [InlineKeyboardButton(text="💳 400₽ - 90 дней", callback_data=f"{prefix}90_400")],
        [InlineKeyboardButton(text="💳 750₽ - 180 дней", callback_data=f"{prefix}180_750")],
        [InlineKeyboardButton(text="💳 1400₽ - 365 дней", callback_data=f"{prefix}365_1400")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
    ])

def get_platform_keyboard():
    """Клавиатура выбора платформы."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Android", callback_data="instruction_android")],
        [InlineKeyboardButton(text="🍎 iOS", callback_data="instruction_ios")],
        [InlineKeyboardButton(text="💻 Windows", callback_data="instruction_windows")],
        [InlineKeyboardButton(text="🖥 macOS", callback_data="instruction_macos")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
    ])

def get_buy_vpn_keyboard(devices):
    """Клавиатура меню покупки/продления VPN."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Создать новый ключ", callback_data="create_new_key")],
        *[[InlineKeyboardButton(
            text=f"🔑 {username.split('_')[0]} ({days} дн.)", 
            callback_data=f"pay_key_{username}")]
          for username, _, days in devices],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
    ])

def get_my_keys_keyboard(devices):
    """Клавиатура активных ключей."""
    return InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(
            text=f"🔑 {username.split('_')[0]} ({days} дн.)", 
            callback_data=f"device_info_{username}")]
          for username, _, days in devices],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
    ])


# === Обработчики команд и callback-запросов ===
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(SCRIPT_DIR, "media")

# Убедимся, что папка media существует
if not os.path.exists(MEDIA_DIR):
    os.makedirs(MEDIA_DIR)

@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    """Обработчик команды /start."""
    user_id = message.from_user.id
    is_new_user = False
    
    logger.info(f"Команда /start от пользователя {user_id} ({message.from_user.username or 'без имени'})")
    
    # Регистрируем пользователя в базе данных
    try:
        # Проверяем, существует ли пользователь
        user_exists = await check_user_exists(user_id)
        
        # Если пользователя нет, регистрируем его
        if not user_exists:
            registration_success = await register_user(user_id)
            if registration_success:
                is_new_user = True
            else:
                logger.error(f"Не удалось зарегистрировать пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка при регистрации пользователя {user_id}: {e}")
        logger.error(traceback.format_exc())
    
    # Отправляем приветственное сообщение
    try:
        photo = FSInputFile(os.path.join(MEDIA_DIR, "logo.jpg"))
        await message.answer_photo(
            photo=photo,
            caption="🌐 *XGuard VPN*\n\n"
                    "✅ Максимальная скорость\n"
                    "✅ Работает везде и всегда\n"
                    "✅ Доступен iPhone, Android, Windows, TV\n"
                    "✅ Лучшие технологии шифрования\n\n"
                    "Выберите действие:",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке приветственного сообщения пользователю {user_id}: {e}")
        logger.error(traceback.format_exc())
    
    # Если пользователь новый, создаем ключ и отправляем специальное сообщение
    if is_new_user:
        await asyncio.sleep(1)  # Небольшая задержка для последовательности сообщений
        try:
            # Проверяем, имеет ли пользователь право на тестовый период
            async with db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT test_period FROM telegram_users WHERE user_id = %s", (user_id,))
                    test_period_result = await cur.fetchone()
                    
                    if test_period_result and test_period_result[0] == 1:
                        vpn_link = await activate_test_period(user_id)
                        
                        if vpn_link:
                            # Создаем клавиатуру с кнопками для перехода к инструкциям
                            instructions_kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="📱 Android", callback_data="instruction_android"),
                                 InlineKeyboardButton(text="🍎 iOS", callback_data="instruction_ios")],
                                [InlineKeyboardButton(text="💻 Windows", callback_data="instruction_windows"),
                                 InlineKeyboardButton(text="🖥 macOS", callback_data="instruction_macos")],
                                [InlineKeyboardButton(text="📋 Мои ключи", callback_data="my_keys")],
                                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
                            ])
                            
                            # Отправляем специальное сообщение о тестовом доступе
                            await message.answer(
                                "🎁 <b>ПОЗДРАВЛЯЕМ!</b> 🎁\n\n"
                                "✅ Вам предоставлен <b>БЕСПЛАТНЫЙ тестовый доступ на 7 дней</b> к нашему VPN!\n\n"
                                "🔑 <b>Ваша VPN-ссылка для подключения:</b>\n"
                                f"<code>{vpn_link}</code>\n\n"
                                "📲 <b>Как подключиться?</b>\n"
                                "1. Установите приложение для вашей платформы\n"
                                "2. Скопируйте вашу VPN-ссылку\n"
                                "3. Вставьте её в приложение\n\n"
                                "👇 <b>Выберите ваше устройство для подробной инструкции:</b>",
                                reply_markup=instructions_kb,
                                parse_mode="HTML"
                            )
                            
                            # Дополнительное пояснение по использованию
                            await asyncio.sleep(1)
                            await message.answer(
                                "💡 <b>Совет:</b> Используйте раздел «Мои ключи» для управления вашим VPN-доступом.\n\n"
                                "⏱ Не забудьте, что ваш тестовый доступ действует <b>7 дней</b>.\n"
                                "После этого вы можете продлить его в разделе «Купить VPN».",
                                parse_mode="HTML"
                            )
                        else:
                            logger.error(f"Не удалось создать VPN-доступ для пользователя {user_id}")
                            await message.answer(
                                "❌ <b>К сожалению, не удалось создать VPN-доступ.</b>\n\n"
                                "Пожалуйста, попробуйте позже или обратитесь в поддержку.",
                                parse_mode="HTML"
                            )
                    else:
                        logger.info(f"Пользователь {user_id} уже использовал тестовый период")
        except Exception as e:
            logger.error(f"Ошибка при создании ключа для пользователя {user_id}: {e}")
            logger.error(traceback.format_exc())


@dp.callback_query(lambda c: c.data == "menu_main")
async def handle_menu_main(callback: types.CallbackQuery):
    """Обработчик возврата в главное меню."""
    photo = FSInputFile(os.path.join(MEDIA_DIR, "logo.jpg"))
    await bot.send_photo(
        chat_id=callback.from_user.id,
        photo=photo,
        caption="🌐 <b>SAFENET VPN</b>\n\n"
                "✅ Максимальная скорость\n"
                "✅ Работает везде и всегда\n"
                "✅ Доступен iPhone, Android, Windows, TV\n"
                "✅ Лучшие технологии шифрования\n\n"
                "Выберите действие:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "my_keys")
async def handle_my_keys(callback: types.CallbackQuery):
    """Обработчик отображения активных ключей."""
    user_id = callback.from_user.id
    devices = await get_user_devices(user_id)
    
    if not devices:
        await bot.send_message(
            callback.from_user.id,
            "⚠️ У вас нет активных ключей!\n\nСоздайте новый ключ в разделе 'Купить VPN'.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Купить VPN", callback_data="buy_vpn")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
            ])
        )
    else:
        message = "🔐 <b>Ваши ключи VPN:</b>\n\n"
        for username, _, days in devices:
            username_display = username.split('_')[0]
            message += f"🔑 <b>{username_display}</b> ({days} дн.)\n"
            
        await bot.send_message(
            callback.from_user.id,
            message,
            reply_markup=get_my_keys_keyboard(devices),
            parse_mode="HTML"
        )
        
    await callback.answer()


@dp.callback_query(lambda c: c.data == "buy_vpn")
async def handle_buy_vpn(callback: types.CallbackQuery):
    """Обработчик кнопки покупки/продления VPN."""
    user_id = callback.from_user.id
    devices = await get_user_devices(user_id)
    
    await bot.send_message(
        callback.from_user.id,
        "💳 <b>Покупка VPN</b>\n\n"
        "1️⃣ Выбери необходимый тариф ниже 👇\n"
        "2️⃣ Внеси платеж\n"
        "3️⃣ И получи ключ с простой инструкцией\n"
        "😉\n\n"
        "👍 Пользователи говорят, что готовы платить\n"
        "за эту скорость и удобство даже больше\n"
        "✅ Проверь, насколько понравится тебе",
        reply_markup=get_buy_vpn_keyboard(devices),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "create_new_key")
async def handle_create_new_key(callback: types.CallbackQuery):
    """Обработчик создания нового ключа."""
    keyboard = get_tariff_keyboard()
    
    message = "💰 <b>Выберите тарифный план</b>\n\n"
    message += "✅ Безлимитный трафик\n"
    message += "✅ Максимальная скорость\n"
    message += "✅ Доступ ко всем серверам\n"
    message += "✅ Поддержка 24/7"
    
    await bot.send_message(
        callback.from_user.id,
        message,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("new_key_"))
async def handle_new_key_payment(callback: types.CallbackQuery):
    """Обработчик оплаты нового ключа."""
    parts = callback.data.split("_")  # new_key_30_150
    days = parts[2]
    amount = parts[3]
    
    payment = Payment.create({
        "amount": {"value": amount, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": "https://your-site.com/success"},
        "capture": True,
        "description": f"Создание нового ключа на {days} дней",
        "metadata": {
            "user_id": callback.from_user.id, 
            "action": "new_key", 
            "days": days, 
            "amount": amount
        }
    })
    
    await bot.send_message(
        callback.from_user.id,
        f"📅 Период: <b>{days} дней</b>\n"
        f"💰 Сумма: <b>{amount} RUB</b>\n\n"
        f"Для оплаты перейдите по ссылке:\n {payment.confirmation.confirmation_url}",
        reply_markup=get_back_to_menu_keyboard(),
        parse_mode="HTML"  # HTML для корректного форматирования текста
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("pay_key_"))
async def handle_pay_key(callback: types.CallbackQuery):
    """Обработчик выбора ключа для оплаты."""
    username = callback.data.split("pay_key_")[1]
    keyboard = get_tariff_keyboard(username)
    
    await bot.send_message(
        callback.from_user.id,
        "💰 Выберите тарифный план\n\n"
        "✅ Безлимитный трафик\n"
        "✅ Максимальная скорость\n"
        "✅ Доступ ко всем серверам\n"
        "✅ Поддержка 24/7",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("pay_"))
async def handle_payment(callback: types.CallbackQuery):
    """Обработчик создания платежа."""
    parts = callback.data.split("_")  # pay_username_30_150
    username = "_".join(parts[1:-2])  # Собираем username обратно
    days = parts[-2]
    amount = parts[-1]
    
    payment = Payment.create({
        "amount": {"value": amount, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": "https://your-site.com/success"},
        "capture": True,
        "description": f"Продление ключа {username.split('_')[0]} на {days} дней",
        "metadata": {
            "user_id": callback.from_user.id, 
            "username": username, 
            "days": days, 
            "amount": amount,
            "action": "extend_key"  # Добавляем тип действия
        }
    })
    
    await bot.send_message(
        callback.from_user.id,
        f"🔑 Ключ: <b>{username.split('_')[0]}</b>\n"
        f"📅 Период: <b>{days} дней</b>\n"
        f"💰 Сумма: <b>{amount} RUB</b>\n\n"
        f"Для оплаты перейдите по ссылке:\n {payment.confirmation.confirmation_url}",
        reply_markup=get_back_to_menu_keyboard(),
        parse_mode="HTML"  # HTML для корректного форматирования текста
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "help")
async def handle_help(callback: types.CallbackQuery):
    """Обработчик кнопки помощи."""
    message = "<b>👨‍💻 Служба поддержки</b>\n\n"
    message += "По всем вопросам обращайтесь:\n"
    message += "👨‍💻 Администратор: @safenet_admin\n"
    message += "⚡️ Время работы: 24/7\n"
    message += "📝 Среднее время ответа: 5-10 минут"
    
    await bot.send_message(
        callback.from_user.id,
        message,
        reply_markup=get_back_to_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "affiliate")
async def handle_affiliate(callback: types.CallbackQuery):
    """Обработчик партнерской программы."""
    await bot.send_message(
        callback.from_user.id,
        "👥 <b>Партнерская программа</b>\n\n"
        "💰 Зарабатывайте с нами:\n"
        "• 20% с каждой оплаты реферала\n"
        "• Моментальные выплаты\n"
        "• Без ограничений по рефералам\n\n"
        "🔗 Ваша реферальная ссылка:\n"
        f"<code>https://t.me/safenet_bot?start={callback.from_user.id}</code>",
        reply_markup=get_back_to_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("device_info_"))
async def handle_device_info(callback: types.CallbackQuery):
    """Обработчик отображения информации о ключе."""
    username = callback.data.split("device_info_")[1]
    user_id = callback.from_user.id
    
    token = await get_access_token()
    if not token:
        await bot.send_message(
            user_id,
            "❌ Ошибка\nНе удалось получить информацию о ключе.\nПопробуйте позже.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode="HTML"
        )
        return await callback.answer()

    try:
        api = MarzbanAPI(base_url=MARZBAN_URL)
        user_info = await api.get_user(username, token)
        
        if not user_info or not user_info.links:
            await bot.send_message(
                user_id,
                "❌ Ключ не найден или был удален.",
                reply_markup=get_back_to_menu_keyboard(),
                parse_mode="HTML"
            )
            return await callback.answer()
            
        vless_link = user_info.links[0]
        
        # Вычисляем срок действия
        days_left = 0
        created_at = "Неизвестно"
        expire_date = "Неизвестно"
        
        if user_info.expire:
            expire_timestamp = user_info.expire
            expire_dt = datetime.fromtimestamp(expire_timestamp)
            days_left = (expire_dt - datetime.now()).days
            expire_date = expire_dt.strftime('%d.%m.%Y')
        
        # Пытаемся получить дату создания из Marzban API, если возможно
        if hasattr(user_info, 'created_at'):
            created_at_value = user_info.created_at
            try:
                # Если дата - строка в формате ISO
                if isinstance(created_at_value, str):
                    # Простой парсер ISO 8601 дат без использования dateutil
                    try:
                        # Для формата "2023-03-07T17:52:36.617Z"
                        if 'T' in created_at_value:
                            date_part = created_at_value.split('T')[0]
                            year, month, day = map(int, date_part.split('-'))
                            created_at = f"{day:02d}.{month:02d}.{year}"
                        # Для формата "2023-03-07 17:52:36"
                        elif ' ' in created_at_value:
                            date_part = created_at_value.split(' ')[0]
                            year, month, day = map(int, date_part.split('-'))
                            created_at = f"{day:02d}.{month:02d}.{year}"
                        else:
                            created_at = created_at_value
                    except Exception:
                        created_at = created_at_value
                # Если дата - timestamp (число)
                elif isinstance(created_at_value, (int, float)):
                    created_at = datetime.fromtimestamp(created_at_value).strftime('%d.%m.%Y')
            except Exception as date_error:
                logger.error(f"Ошибка при обработке даты created_at: {date_error}")
                created_at = "Неизвестно"
        
        # Формируем сообщение с информацией о ключе
        username_display = username.split('_')[0]
        
        message = f"🔑 <b>Информация о ключе</b>\n\n"
        message += f"ID: <b>{username_display}</b>\n"
        message += f"Создан: <b>{created_at}</b>\n"
        message += f"Истекает: <b>{expire_date}</b>\n"
        message += f"Осталось дней: <b>{days_left}</b>\n"
        
        message += f"\n<b>Ссылка для подключения:</b>\n<code>{vless_link}</code>\n\n"
        message += "Выберите вашу платформу для инструкции по подключению:"
        
        await bot.send_message(
            user_id,
            message,
            reply_markup=get_platform_keyboard(),
            parse_mode="HTML"
        )
            
    except Exception as e:
        logger.error(f"Ошибка при отображении информации о ключе: {e}")
        logger.error(traceback.format_exc())
        await bot.send_message(
            user_id,
            "❌ Произошла ошибка при получении информации о ключе.\nПожалуйста, попробуйте позже.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode="HTML"
        )
    
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("instruction_android"))
async def handle_instruction_android(callback: types.CallbackQuery):
    """Обработчик инструкции для Android."""
    # Создаем клавиатуру с кнопкой скачивания
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 Скачать V2rayNG", url="https://play.google.com/store/apps/details?id=com.v2ray.ang")]
    ])
    
    await bot.send_message(
        callback.from_user.id,
        "📱 <b>Инструкция для Android</b>\n\n"
        "1. Установите приложение V2rayNG\n"
        "2. Нажмите + в правом верхнем углу\n"
        "3. Выберите 'Импорт из буфера обмена'\n"
        "4. Вставьте скопированную конфигурацию\n"
        "5. Нажмите на значок V для подключения",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("instruction_ios"))
async def handle_instruction_ios(callback: types.CallbackQuery):
    """Обработчик инструкции для iOS."""
    # Создаем клавиатуру с кнопкой скачивания
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 Скачать Streisand", url="https://apps.apple.com/us/app/streisand/id6450534064")]
    ])
    
    await bot.send_message(
        callback.from_user.id,
        "🍎 <b>Инструкция для iOS</b>\n\n"
        "1. Установите приложение Streisand\n"
        "2. Нажмите + в правом верхнем углу\n"
        "3. Выберите 'Импорт из буфера обмена'\n"
        "4. Вставьте скопированную конфигурацию\n"
        "5. Нажмите 'Подключить'",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("instruction_windows"))
async def handle_instruction_windows(callback: types.CallbackQuery):
    """Обработчик инструкции для Windows."""
    # Создаем клавиатуру с кнопкой скачивания
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 Скачать Hiddify", url="https://github.com/hiddify/hiddify-app/releases/latest/download/Hiddify-Windows-Setup-x64.Msix")]
    ])
    
    await bot.send_message(
        callback.from_user.id,
        "💻 <b>Инструкция для Windows</b>\n\n"
        "1. Скачайте и установите приложение Hiddify\n"
        "2. Нажмите правой кнопкой на значок в трее\n"
        "4. Выберите 'Импорт из буфера обмена' (Import from Clipboard)\n"
        "5. Вставьте скопированную конфигурацию\n"
        "6. Нажмите на значок соединения для подключения",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("instruction_macos"))
async def handle_instruction_macos(callback: types.CallbackQuery):
    """Обработчик инструкции для macOS."""
    # Создаем клавиатуру с кнопкой скачивания
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 Скачать Hiddify", url="https://apps.apple.com/ru/app/hiddify-proxy-vpn/id6596777532")]
    ])
    
    await bot.send_message(
        callback.from_user.id,
        "🖥 <b>Инструкция для macOS</b>\n\n"
        "1. Скачайте и установите приложение Hiddify\n"
        "2. Откройте приложение и нажмите на его значок в строке меню\n"
        "3. В Hiddify: нажмите «+» → «Импорт из буфера обмена»\n"
        "4. Вставьте скопированную конфигурацию\n"
        "5. Выберите созданный профиль и нажмите 'Подключить'",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


# === API эндпоинты для внешнего использования ===
# API Secret Token для авторизации запросов
API_SECRET_TOKEN = os.getenv('API_SECRET_TOKEN', 'your-secret-token-change-this')

# Список допустимых IP-адресов ЮKassa
ALLOWED_IP_RANGES = [
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11/32",
    "77.75.156.35/32",
    "77.75.154.128/25",
    "2a02:5180::/32",
    "185.17.131.255",
    "54.86.50.139"
]

# Функция для проверки IP-адреса
def is_ip_allowed(client_ip):
    """Проверяет, относится ли IP-адрес к списку разрешенных IP-адресов ЮKassa."""
    for ip_range in ALLOWED_IP_RANGES:
        network = ipaddress.ip_network(ip_range)
        if ipaddress.ip_address(client_ip) in network:
            return True
    return False

# Функция для преобразования ISO 8601 даты в формат MySQL datetime
def convert_iso_to_mysql_datetime(iso_date_str: str) -> str:
    """Преобразует ISO 8601 дату в формат MySQL datetime."""
    if not iso_date_str:
        return None
    try:
        # Ручной парсинг ISO 8601 даты
        if 'T' in iso_date_str:
            # Формат типа "2023-03-07T17:52:36.617Z"
            date_part, time_part = iso_date_str.split('T')
            # Убираем миллисекунды и часовой пояс
            if '.' in time_part:
                time_part = time_part.split('.')[0]
            elif '+' in time_part:
                time_part = time_part.split('+')[0]
            elif 'Z' in time_part:
                time_part = time_part.replace('Z', '')
            
            return f"{date_part} {time_part}"
        elif ' ' in iso_date_str:
            # Формат уже в виде "2023-03-07 17:52:36"
            return iso_date_str
        else:
            # Неизвестный формат, возвращаем текущую дату
            return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(f"Ошибка преобразования даты '{iso_date_str}': {e}")
        # В случае ошибки возвращаем текущее время
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# Функция для проверки JWT токена Marzban
async def verify_marzban_token(token: str) -> bool:
    """Проверяет валидность JWT токена Marzban"""
    try:
        # Создаем экземпляр API Marzban
        api = MarzbanAPI(base_url=MARZBAN_URL)
        # Проверяем токен, пытаясь получить текущего пользователя
        current_admin = await api.get_current_admin(token)
        # Если успешно получили информацию, токен действителен
        return current_admin is not None
    except Exception as e:
        logger.error(f"Ошибка при проверке токена Marzban: {e}")
        return False

# Обработчик для перенаправления на страницу сообщений с проверкой авторизации
async def handle_messages_page(request: web.Request):
    """Обработчик страницы сообщений с проверкой авторизации Marzban."""
    try:
        # Проверка авторизации через токен Marzban
        marzban_token = request.cookies.get('token')
        if not marzban_token or not await verify_marzban_token(marzban_token):
            # Перенаправляем на страницу входа Marzban
            logger.info("Попытка доступа к странице сообщений без авторизации")
            return web.HTTPFound(f"{MARZBAN_URL}dashboard/#/login")
        
        # Пользователь авторизован, перенаправляем на страницу сообщений
        return web.HTTPFound(f"{MARZBAN_URL}dashboard/#/messages")
    except Exception as e:
        logger.error(f"Ошибка в обработчике страницы сообщений: {e}")
        logger.error(traceback.format_exc())
        return web.Response(text="Ошибка сервера", status=500)

# Обработчик для отправки сообщений через API
async def handle_api_send_message(request: web.Request):
    """Обработчик API для отправки сообщений всем пользователям бота."""
    try:
        # Добавляем подробное логирование запроса для отладки
        logger.info(f"Получен запрос на отправку сообщения: {request.method} {request.url}")
        logger.info(f"Заголовки запроса: {request.headers}")
        
        # Проверка авторизации через токен API
        api_auth_header = request.headers.get('Authorization', '')
        if api_auth_header.startswith('Bearer ') and api_auth_header.replace('Bearer ', '') == API_SECRET_TOKEN:
            # Авторизация через API токен успешна
            pass
        else:
            # Проверка авторизации через токен Marzban
            marzban_token = request.cookies.get('token')
            if not marzban_token or not await verify_marzban_token(marzban_token):
                return web.json_response({"success": False, "error": "Unauthorized"}, status=401)
        
        # Продолжение обработки запроса
        # Обработка multipart/form-data или application/json
        image_data = None
        if request.content_type.startswith('multipart/form-data'):
            data = await request.post()
            text = data.get('text', '')
            all_users = data.get('all_users', 'true').lower() == 'true'
            user_ids = data.get('user_ids', '[]')
            try:
                user_ids = json.loads(user_ids) if user_ids else []
            except:
                user_ids = []
                
            # Обработка изображения
            image = data.get('image')
            has_image = image is not None
            
            if has_image:
                # Сохраняем изображение во временный файл
                temp_dir = os.path.join(os.getcwd(), "temp")
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)
                    
                temp_image_path = os.path.join(temp_dir, f"temp_image_{int(datetime.now().timestamp())}.jpg")
                with open(temp_image_path, "wb") as f:
                    f.write(image.file.read())
                    
                # Перезагружаем файл
                image_data = temp_image_path
        else:
            data = await request.json()
            text = data.get('text', '')
            all_users = data.get('all_users', True)
            user_ids = data.get('user_ids', [])
            has_image = False
            
        if not text:
            return web.json_response({"success": False, "error": "Text is required"}, status=400)
        
        # Получаем список пользователей
        if all_users:
            async with db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT user_id FROM telegram_users")
                    telegram_ids = [row[0] for row in await cur.fetchall()]
        else:
            telegram_ids = user_ids
            
        if not telegram_ids:
            return web.json_response({"success": True, "message": "No users to send to"})
                
        # Отправляем сообщения
        success_count = 0
        failed_count = 0
        
        for user_id in telegram_ids:
            try:
                if has_image and image_data:
                    # Создаем InputFile из пути к файлу
                    input_file = FSInputFile(image_data)
                    # Отправляем фото с текстом
                    await bot.send_photo(
                        chat_id=user_id, 
                        photo=input_file,
                        caption=text,
                        parse_mode="HTML"
                    )
                else:
                    # Отправляем только текст
                    await bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode="HTML"
                    )
                success_count += 1
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
                failed_count += 1
        
        # Удаляем временный файл, если он был создан
        if has_image and image_data and os.path.exists(image_data):
            os.remove(image_data)
                
        return web.json_response({
            "success": True,
            "message": f"Sent to {success_count}, failed {failed_count}"
        })
                
    except Exception as e:
        logger.error(f"Ошибка в API отправки сообщений: {e}")
        logger.error(traceback.format_exc())
        # Удаляем временный файл в случае ошибки
        if 'image_data' in locals() and image_data and os.path.exists(image_data):
            try:
                os.remove(image_data)
            except:
                pass
        return web.json_response({"success": False, "error": str(e)}, status=500)


# Добавляем маршруты для API сообщений
def setup_api_routes(app):
    """Настраивает маршруты API для веб-сервера."""
    # Страница сообщений Marzban
    app.router.add_get('/dashboard/messages', handle_messages_page)
    
    # API для отправки сообщений
    app.router.add_post('/api/messages/send', handle_api_send_message)
    
    # API для совместимости со старыми клиентами
    app.router.add_post('/api/send_message', handle_api_send_message)
    
    # API для обработки задач с сообщениями (массовых рассылок)
    app.router.add_post('/api/messages/tasks', handle_api_messages_tasks)
    
    # API для прямой отправки сообщений через API
    app.router.add_post('/api/messages/direct', handle_api_messages_send)
    
    # Webhook для YooKassa
    app.router.add_post('/api/webhook/yookassa', handle_yookassa_webhook)
    
    # Проверяем и создаем директорию static, если она не существует
    static_dir = os.path.join(SCRIPT_DIR, "static")
    if not os.path.exists(static_dir):
        logger.info(f"Директория {static_dir} не существует, создаем...")
        os.makedirs(static_dir)
        logger.info(f"Директория {static_dir} успешно создана")
    
    # Настраиваем статические файлы
    app.router.add_static('/static/', path=static_dir, name="static")


# === Запуск бота ===
async def on_startup(bot: Bot):
    """Обработчик события запуска бота."""
    logger.info("Запуск бота...")
    
    # Выводим значения важных переменных окружения
    logger.info(f"MARZBAN_URL: {MARZBAN_URL}")
    logger.info(f"MARZBAN_USERNAME: {MARZBAN_USERNAME}")
    logger.info(f"DEFAULT_TEST_PERIOD: {DEFAULT_TEST_PERIOD}")
    logger.info(f"MEDIA_DIR: {MEDIA_DIR if 'MEDIA_DIR' in globals() else 'не определено'}")
    logger.info(f"WEBHOOK_URL: {WEBHOOK_URL}")
    
    # Устанавливаем вебхук
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        logger.info(f"Устанавливаем вебхук на {WEBHOOK_URL}")
        
        # Настраиваем SSL, если сертификат указан
        if WEBHOOK_SSL_CERT:
            logger.info("Используем SSL сертификат для вебхука")
            # Создаем InputFile из пути к файлу
            cert = FSInputFile(WEBHOOK_SSL_CERT)
            await bot.set_webhook(
                url=WEBHOOK_URL, 
                certificate=cert,
                drop_pending_updates=True
            )
        else:
            logger.info("Используем вебхук без SSL сертификата")
            await bot.set_webhook(
                url=WEBHOOK_URL,
                drop_pending_updates=True
            )
    
    # Инициализируем пул соединений с базой данных
    try:
        logger.info("Инициализация пула соединений с базой данных...")
        await init_db_pool()
        
        if not db_pool:
            logger.error("Не удалось инициализировать пул соединений с базой данных. Бот не может продолжить работу.")
            return
        
        logger.info("Инициализация базы данных...")
        await initialize_db()
        logger.info("База данных успешно инициализирована.")
    except Exception as db_error:
        logger.error(f"Критическая ошибка при инициализации базы данных: {db_error}")
        logger.error(traceback.format_exc())


async def on_shutdown(bot: Bot):
    """Обработчик события остановки бота."""
    logger.info("Остановка бота...")
    
    # Отключаем вебхук
    await bot.delete_webhook()
    
    # Закрываем соединение с базой данных
    logger.info("Закрытие соединения с базой данных...")
    await close_db_pool()
    logger.info("Соединение с базой данных закрыто")


async def main():
    """Главная функция бота."""
    # Устанавливаем обработчики событий
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Создаем приложение aiohttp
    app = web.Application()

    # Настраиваем вебхук
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    
    # Настраиваем запуск веб-сервера
    setup_application(app, dp, bot=bot)
    
    # Настраиваем API маршруты
    setup_api_routes(app)
    
    # Запускаем веб-сервер
    ssl_context = None
    if WEBHOOK_SSL_CERT and WEBHOOK_SSL_PRIV:
        logger.info("Запускаем веб-сервер с SSL")
        import ssl
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(WEBHOOK_SSL_CERT, WEBHOOK_SSL_PRIV)
    
    logger.info(f"Запускаем веб-сервер на {WEBAPP_HOST}:{WEBAPP_PORT}")
    
    try:
        # Запускаем веб-сервер
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(
            runner, 
            host=WEBAPP_HOST, 
            port=WEBAPP_PORT, 
            ssl_context=ssl_context
        )
        for resource in app.router.resources():
            logger.info(f"Зарегистрирован маршрут: {resource}")
        await site.start()
        
        # Держим приложение запущенным
        logger.info(f"Бот запущен на {WEBHOOK_URL}")
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        logger.error(traceback.format_exc())
    finally:
        if 'runner' in locals():
            await runner.cleanup()



async def activate_test_period(user_id: int) -> Optional[str]:
    """Создает VPN ключ на 7 дней для нового пользователя и обновляет флаг test_period."""
    try:
        # Используем локальную функцию create_marzban_user
        
        # Создаем ключ на DEFAULT_TEST_PERIOD дней
        result = await create_marzban_user(user_id, DEFAULT_TEST_PERIOD)
        
        if not result["success"]:
            logger.error(f"Не удалось создать ключ для пользователя {user_id}: {result.get('error')}")
            return None
        
        # Обновляем флаг test_period
        try:
            async with db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "UPDATE telegram_users SET test_period = 0 WHERE user_id = %s",
                        (user_id,)
                    )
                    await conn.commit()
        except Exception as db_error:
            logger.error(f"Ошибка при обновлении флага test_period: {db_error}")
            logger.error(traceback.format_exc())
            # Продолжаем выполнение, даже если обновление флага не удалось
        
        logger.info(f"Создан ключ для пользователя {user_id}")
        return result["link"]
                
    except Exception as e:
        logger.error(f"Ошибка при создании ключа для пользователя {user_id}: {e}")
        logger.error(traceback.format_exc())
        return None


# Обработчики для API работы с сообщениями
async def handle_api_messages_tasks(request: web.Request):
    """Обработчик API для получения задач сообщений."""
    try:
        # Проверка авторизации через токен Marzban
        marzban_token = request.cookies.get('token')
        if not marzban_token or not await verify_marzban_token(marzban_token):
            return web.json_response({"detail": "Unauthorized"}, status=401)
        
        # Получаем задачи из базы данных
        tasks = []
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM message_tasks ORDER BY created_at DESC")
                columns = [column[0] for column in cur.description]
                tasks = [dict(zip(columns, row)) for row in await cur.fetchall()]
        
        return web.json_response(tasks)
    except Exception as e:
        logger.error(f"Ошибка при получении задач сообщений: {e}")
        logger.error(traceback.format_exc())
        return web.json_response({"detail": str(e)}, status=500)

async def handle_api_messages_send(request: web.Request):
    """Обработчик API для отправки сообщений всем пользователям."""
    try:
        # Проверка авторизации через токен Marzban
        marzban_token = request.cookies.get('token')
        if not marzban_token or not await verify_marzban_token(marzban_token):
            return web.json_response({"detail": "Unauthorized"}, status=401)
        
        if request.content_type.startswith('multipart/form-data'):
            data = await request.post()
            message_text = data.get('message', '')
            
            # Обработка изображения
            image = data.get('image')
            has_image = image is not None
        else:
            data = await request.json()
            message_text = data.get('message', '')
            has_image = False
            image = None
        
        if not message_text:
            return web.json_response({"detail": "Message text is required"}, status=400)
        
        # Получаем список пользователей
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT user_id FROM telegram_users")
                user_ids = [row[0] for row in await cur.fetchall()]
        
        if not user_ids:
            return web.json_response({"message": "No users to send to"})
        
        # Отправляем сообщения
        success_count = 0
        failed_count = 0
        
        for user_id in user_ids:
            try:
                if has_image and image:
                    # Создаем временный файл для изображения
                    temp_dir = os.path.join(os.getcwd(), "temp")
                    if not os.path.exists(temp_dir):
                        os.makedirs(temp_dir)
                    
                    temp_image_path = os.path.join(temp_dir, f"temp_image_{int(datetime.now().timestamp())}.jpg")
                    with open(temp_image_path, "wb") as f:
                        f.write(image.file.read())
                    
                    # Отправляем фото с текстом
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=FSInputFile(temp_image_path),
                        caption=message_text,
                        parse_mode="HTML"
                    )
                    
                    # Удаляем временный файл
                    if os.path.exists(temp_image_path):
                        os.remove(temp_image_path)
                else:
                    # Отправляем только текст
                    await bot.send_message(
                        chat_id=user_id,
                        text=message_text,
                        parse_mode="HTML"
                    )
                success_count += 1
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
                failed_count += 1
        
        return web.json_response({
            "message": f"Сообщение отправлено {success_count} пользователям, не удалось отправить {failed_count}"
        })
    except Exception as e:
        logger.error(f"Ошибка в API отправки сообщений: {e}")
        logger.error(traceback.format_exc())
        return web.json_response({"detail": str(e)}, status=500)


# Функция для сохранения платежа в базу данных
async def save_payment(payment_data: dict):
    """Сохраняет информацию о платеже в базу данных."""
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                payment_obj = payment_data['object']
                payment_method = payment_obj['payment_method']
                user_id = int(payment_obj['metadata']['user_id'])
                
                # Проверяем существование пользователя в базе данных
                await cur.execute("SELECT COUNT(*) FROM telegram_users WHERE user_id = %s", (user_id,))
                user_exists = (await cur.fetchone())[0] > 0
                
                # Если пользователя нет, создаем его автоматически
                if not user_exists:
                    logger.info(f"Пользователь {user_id} не найден, создаем автоматически")
                    await cur.execute(
                        "INSERT INTO telegram_users (user_id, test_period, created_at) VALUES (%s, 1, CURRENT_TIMESTAMP)",
                        (user_id,)
                    )
                    await conn.commit()
                    logger.info(f"Пользователь {user_id} успешно создан")
                
                # Преобразуем даты
                created_at = convert_iso_to_mysql_datetime(payment_obj['created_at'])
                captured_at = convert_iso_to_mysql_datetime(payment_obj['captured_at'])
                
                await cur.execute('''
                    INSERT INTO payments (
                        payment_id, user_id, amount, income_amount, status,
                        description, payment_method, payment_method_details,
                        created_at, captured_at, metadata
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                ''', (
                    payment_obj['id'],
                    user_id,
                    float(payment_obj['amount']['value']),
                    float(payment_obj['income_amount']['value']),
                    payment_obj['status'],
                    payment_obj['description'],
                    payment_method['type'],
                    str(payment_method),
                    created_at,
                    captured_at,
                    str(payment_obj['metadata'])
                ))
                await conn.commit()
                logger.info(f"Платеж {payment_obj['id']} успешно сохранен в базе данных")
    except Exception as e:
        logger.error(f"Ошибка при сохранении платежа: {e}")
        logger.error(traceback.format_exc())

# Обработчик вебхука от YooKassa
async def handle_yookassa_webhook(request: web.Request):
    """Обработчик вебхука от YooKassa для обработки платежей."""
    try:
        # Получаем IP-адрес клиента
        client_ip = request.remote
        # Проверяем, что запрос пришел с IP-адреса ЮKassa
        if not is_ip_allowed(client_ip):
            logger.error(f"Запрос получен с недопустимого IP-адреса: {client_ip}")
            return web.json_response({"status": "error", "message": "Invalid IP address"}, status=403)
        
        # Парсим JSON из тела запроса
        data = await request.json()
        logger.info(f"Получен вебхук от YooKassa: {data}")
        
        # Проверяем тип события
        event_type = data.get('event')
        if event_type != 'payment.succeeded':
            logger.info(f"Получено событие: {event_type}. Ожидалось payment.succeeded.")
            return web.json_response({"status": "ignored"})
        
        # Сохраняем информацию о платеже
        await save_payment(data)
        
        # Получаем ID платежа и метаданные из данных
        payment_id = data['object']['id']
        metadata = data['object']['metadata']
        user_id = int(metadata.get('user_id'))
        subscription_duration = metadata.get('subscription_duration')
        amount_value = float(data['object']['amount']['value'])
        
        # Преобразуем сумму платежа в целое число (если balance INT)
        payment_amount = int(amount_value)
        
        logger.info(f"Payment ID: {payment_id}, User ID: {user_id}, Amount: {payment_amount}, Subscription Duration: {subscription_duration}")
        
        # Проверяем статус платежа через API ЮKassa
        payment = Payment.find_one(payment_id)
        if payment.status != 'succeeded':
            logger.warning(f"Статус платежа {payment_id} не является succeeded.")
            return web.json_response({"status": "ignored"})
        
        # Выполняем все операции: создание/продление, отправку уведомления
        result = await process_successful_payment(user_id, payment_amount, metadata)
        
        if result and result.get("success", False):
            logger.info(f"Платеж {payment_id} успешно обработан: {result}")
            return web.json_response({"status": "success", "result": result})
        else:
            logger.error(f"Ошибка при обработке платежа {payment_id}: {result}")
            return web.json_response({"status": "error", "message": result.get("error", "Unknown error")})
    
    except Exception as e:
        logger.error(f"Ошибка при обработке вебхука: {e}")
        logger.error(traceback.format_exc())
        return web.json_response({"status": "error", "message": "Internal Server Error"}, status=500)

# Обработка успешного платежа
async def process_successful_payment(user_id: int, payment_amount: int, payment_data: dict):
    """Обработка успешного платежа с созданием/продлением пользователя напрямую."""
    try:
        # Определяем действие автоматически, если его нет
        action = payment_data.get("action")
        if not action and "username" in payment_data:
            action = "extend_key"
        elif not action:
            action = "new_key"
            
        days = int(payment_data.get("days", 0))
        amount = float(payment_data.get("amount", 0))
        
        logger.critical(f"[PAYMENT DEBUG] Действие: {action}, дней: {days}, сумма: {amount}")
        
        # Обработка в зависимости от действия
        if action == "new_key":
            # Создаем нового пользователя напрямую в Marzban
            result = await create_marzban_user(user_id, days)
            
            if result["success"]:
                username = result["username"]
                vless_link = result["link"]
                
                # Отправляем уведомление пользователю
                message = (
                    f"✅ <b>Ваш новый ключ создан!</b>\n\n"
                    f"🔑 Имя ключа: <code>{username.split('_')[0]}</code>\n"
                    f"📅 Срок действия: <b>{days}</b> дней\n\n"
                    f"Ваша ссылка для подключения:\n<code>{vless_link}</code>"
                )
                await bot.send_message(chat_id=user_id, text=message, parse_mode="HTML")
                
                logger.info(f"Успешно создан ключ {username} для пользователя {user_id}")
                return {"success": True, "username": username, "link": vless_link}
            else:
                error_message = result.get("error", "Неизвестная ошибка")
                # Отправляем уведомление об ошибке
                await bot.send_message(
                    chat_id=user_id, 
                    text=f"❌ Произошла ошибка при создании ключа. Пожалуйста, обратитесь в поддержку.\n\nКод ошибки: {error_message}",
                    parse_mode="HTML"
                )
                logger.error(f"Ошибка создания ключа: {error_message}")
                return {"success": False, "error": error_message}
        
        elif action == "extend_key" and "username" in payment_data:
            # Продлеваем существующий ключ напрямую в Marzban
            username = payment_data["username"]
            result = await extend_marzban_user(username, days)
            
            if result["success"]:
                new_expire_date = result["new_expire_date"]
                
                # Отправляем уведомление пользователю
                message = (
                    f"✅ <b>Ваш ключ успешно продлен!</b>\n\n"
                    f"🔑 Ключ: <code>{username.split('_')[0]}</code>\n"
                    f"📅 Добавлено дней: <b>{days}</b>\n"
                    f"📆 Действует до: <b>{new_expire_date}</b>"
                )
                await bot.send_message(chat_id=user_id, text=message, parse_mode="HTML")
                
                logger.info(f"Успешно продлен ключ {username} для пользователя {user_id}")
                return {"success": True, "username": username, "new_expire_date": new_expire_date}
            else:
                error_message = result.get("error", "Неизвестная ошибка")
                # Отправляем уведомление об ошибке
                await bot.send_message(
                    chat_id=user_id, 
                    text=f"❌ Произошла ошибка при продлении ключа. Пожалуйста, обратитесь в поддержку.\n\nКод ошибки: {error_message}",
                    parse_mode="HTML"
                )
                logger.error(f"Ошибка продления ключа: {error_message}")
                return {"success": False, "error": error_message}
        
        else:
            error_message = f"Неизвестное действие: {action}"
            await bot.send_message(
                chat_id=user_id, 
                text=f"❌ Произошла ошибка при обработке платежа. Пожалуйста, обратитесь в поддержку.\n\nКод ошибки: {error_message}",
                parse_mode="HTML"
            )
            logger.error(error_message)
            return {"success": False, "error": error_message}
        
    except Exception as e:
        logger.critical(f"[PAYMENT DEBUG] Критическая ошибка при обработке платежа: {e}")
        logger.critical(f"[PAYMENT DEBUG] Трассировка: {traceback.format_exc()}")
        
        # Отправляем уведомление пользователю в случае исключения
        try:
            await bot.send_message(
                chat_id=user_id,
                text="❌ Произошла непредвиденная ошибка при обработке платежа. Пожалуйста, обратитесь в поддержку.",
                parse_mode="HTML"
            )
        except Exception as notify_error:
            logger.critical(f"[PAYMENT DEBUG] Ошибка при отправке уведомления: {notify_error}")
        
        return {"success": False, "error": str(e)}

# Функция для создания нового пользователя в Marzban
async def create_marzban_user(user_id: int, days: int) -> dict:
    """Создает нового пользователя в Marzban.
    
    Args:
        user_id: ID пользователя Telegram
        days: Количество дней действия ключа
        
    Returns:
        dict: Словарь с результатами операции
        {
            "success": bool,
            "username": str,
            "link": str,
            "error": str  # только если success=False
        }
    """
    try:
        # Получаем токен
        token = await get_access_token()
        if not token:
            logger.error("Не удалось получить токен для создания пользователя в Marzban")
            return {"success": False, "error": "Ошибка авторизации в Marzban API"}

        # Генерируем уникальное имя пользователя
        marzban_username = f"{generate_random_string()}_{user_id}"
        
        # Устанавливаем время истечения на указанное количество дней вперед
        expire_timestamp = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
        expire_date = datetime.fromtimestamp(expire_timestamp, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"Создание ключа: {marzban_username}, срок: {expire_date}")

        # Создаем объект пользователя с настройками
        new_user = UserCreate(
            username=marzban_username,
            proxies={"vless": ProxySettings(flow="xtls-rprx-vision")},
            inbounds={'vless': ['VLESS TCP REALITY']},
            expire=expire_timestamp
        )

        # Отладочная информация
        logger.critical(f"[PAYMENT DEBUG] Попытка создания пользователя в Marzban API: {MARZBAN_URL}")
        logger.critical(f"[PAYMENT DEBUG] Имя пользователя для создания: {marzban_username}")
        
        # Проверка доступности хоста через DNS
        try:
            parsed_url = urlparse(MARZBAN_URL)
            hostname = parsed_url.netloc.split(':')[0]
            logger.critical(f"[PAYMENT DEBUG] Разбор URL {MARZBAN_URL}. Полученный хост: {hostname}")
            
            if hostname != 'localhost' and hostname != '127.0.0.1':
                try:
                    ip = socket.gethostbyname(hostname)
                    logger.critical(f"[PAYMENT DEBUG] Успешное разрешение DNS {hostname} -> {ip}")
                except Exception as dns_err:
                    logger.critical(f"[PAYMENT DEBUG] Ошибка при разрешении DNS {hostname}: {dns_err}")
        except Exception as parse_err:
            logger.critical(f"[PAYMENT DEBUG] Ошибка при разборе URL {MARZBAN_URL}: {parse_err}")

        # Инициализируем API и создаем пользователя
        api = MarzbanAPI(base_url=MARZBAN_URL)
        # Настройки для внутреннего взаимодействия в Docker
        api.client.verify = False
        api.client.timeout = httpx.Timeout(30.0, connect=10.0)
        
        try:
            await api.add_user(new_user, token)
            logger.critical(f"[PAYMENT DEBUG] Пользователь успешно создан в Marzban API")
        except Exception as e:
            logger.critical(f"[PAYMENT DEBUG] Ошибка при создании пользователя: {e}")
            logger.critical(f"[PAYMENT DEBUG] Тип исключения: {type(e).__name__}")
            return {"success": False, "error": f"Ошибка создания пользователя: {e}"}
        
        # Получаем информацию о созданном пользователе
        user_info = await api.get_user(marzban_username, token)
        if not user_info or not user_info.links:
            return {"success": False, "error": "Пользователь создан, но не удалось получить ссылку"}
            
        vless_link = user_info.links[0]
        logger.info(f"Пользователь {marzban_username} создан с ссылкой {vless_link}")
        
        return {
            "success": True,
            "username": marzban_username,
            "link": vless_link
        }
    except Exception as e:
        logger.error(f"Ошибка создания пользователя: {e}")
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}

# Функция для продления существующего пользователя
async def extend_marzban_user(username: str, days: int) -> dict:
    """Продлевает существующий ключ на указанное количество дней.
    
    Args:
        username: Имя пользователя в Marzban
        days: Количество дней для продления
        
    Returns:
        dict: Словарь с результатами операции
        {
            "success": bool,
            "username": str,
            "new_expire_date": str,
            "error": str  # только если success=False
        }
    """
    try:
        # Получаем токен
        token = await get_access_token()
        if not token:
            logger.error("Не удалось получить токен для продления пользователя")
            return {"success": False, "error": "Ошибка авторизации в Marzban API"}

        # Инициализируем API
        api = MarzbanAPI(base_url=MARZBAN_URL)
        api.client.verify = False
        api.client.timeout = httpx.Timeout(30.0, connect=10.0)
        
        # Получаем текущую информацию о пользователе
        logger.critical(f"[PAYMENT DEBUG] Получение информации о пользователе {username}")
        user_info = await api.get_user(username, token)
        if not user_info:
            return {"success": False, "error": f"Пользователь {username} не найден"}
            
        current_expire = user_info.expire
        
        # Рассчитываем новую дату истечения
        current_time = datetime.now(timezone.utc).timestamp()
        if current_expire < current_time:
            new_expire = int(current_time + timedelta(days=days).total_seconds())
            logger.info(f"Срок действия ключа {username} истек, устанавливаем новый срок от текущей даты")
        else:
            new_expire = int(current_expire + timedelta(days=days).total_seconds())
            logger.info(f"Продление ключа {username} на {days} дней от текущего срока")
        
        # Обновляем срок действия ключа
        logger.critical(f"[PAYMENT DEBUG] Продление ключа {username} до {datetime.fromtimestamp(new_expire)}")
        await api.modify_user(
            username=username,
            user=UserModify(expire=new_expire),
            token=token
        )
        
        # Форматируем дату для удобочитаемости
        new_expire_date = datetime.fromtimestamp(new_expire, tz=timezone.utc).strftime("%d.%m.%Y")
        logger.info(f"Успешно продлен ключ {username} до {new_expire_date}")
        
        return {
            "success": True,
            "username": username,
            "new_expire_date": new_expire_date
        }
    except Exception as e:
        logger.error(f"Ошибка при продлении ключа {username}: {e}")
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}

# Функция для проверки JWT токена Marzban


if __name__ == "__main__":
    asyncio.run(main())