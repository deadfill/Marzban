import logging
import logging.handlers
import random
import os
import asyncio
import traceback
from aiogram import Bot, Dispatcher
from aiogram import types
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, Message
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from datetime import datetime, timedelta, timezone
import aiomysql
from yookassa import Configuration, Payment
from typing import Optional, List, Tuple, Dict, Any
from dotenv import load_dotenv
import json
import string
import ipaddress
import httpx
from datetime import datetime, timedelta



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

# === Настройки базы данных ===
DB_HOST = os.getenv('MYSQL_HOST', 'localhost')
DB_PORT = int(os.getenv('MYSQL_PORT', '3306'))
DB_USER = os.getenv('MYSQL_USER', '')
DB_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
DB_NAME = os.getenv('MYSQL_DATABASE', '')

Configuration.account_id = os.getenv('YOOKASSA_ACCOUNT_ID')
Configuration.secret_key = os.getenv('YOOKASSA_SECRET_KEY')

# Проверка и вывод используемых URL для отладки

# Проверка на окончание URL слешем
if MARZBAN_URL and not MARZBAN_URL.endswith('/'):
    MARZBAN_URL = f"{MARZBAN_URL}/"

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
DB_POOL = None

# Глобальный кэш токена
_token_cache = {
    "token": None,
    "expires_at": 0
}


# === Константы ===
DEFAULT_TEST_PERIOD = 7  # Период тестового доступа (в днях)
REFERRAL_BONUS_DAYS = int(os.getenv('REFERRAL_BONUS_DAYS', 7))  # Бонусные дни за приглашение


# === Утилитные функции ===
def generate_random_string(length=8):
    """Генерирует случайную строку указанной длины."""
    characters = string.ascii_lowercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))


async def async_api_request(method, url, headers=None, json_data=None, form_data=None, params=None, timeout=10, verify=False):
    """Универсальная функция для отправки запросов к API.
    
    Args:
        method: HTTP метод ('get', 'post', 'put', 'delete')
        url: URL запроса
        headers: Заголовки запроса
        json_data: Данные для отправки в формате JSON
        form_data: Данные для отправки в формате form-data
        params: Параметры URL
        timeout: Таймаут запроса в секундах
        verify: Проверять ли SSL сертификат
        
    Returns:
        dict: Словарь с результатами запроса
        {
            "success": bool,
            "status_code": int,
            "data": object,  # данные ответа, если запрос успешен
            "error": str     # сообщение об ошибке, если запрос неуспешен
        }
    """
    try:
        async with httpx.AsyncClient(verify=verify) as client:
            method_func = getattr(client, method.lower())
            
            request_kwargs = {
                "headers": headers,
                "timeout": timeout
            }
            
            if json_data is not None:
                request_kwargs["json"] = json_data
                
            if form_data is not None:
                request_kwargs["data"] = form_data
                
            if params is not None:
                request_kwargs["params"] = params
                
            response = await method_func(url, **request_kwargs)
            
            if response.status_code in (200, 201, 204):
                try:
                    if response.content and response.headers.get('content-type', '').startswith('application/json'):
                        return {
                            "success": True,
                            "status_code": response.status_code,
                            "data": response.json()
                        }
                    else:
                        return {
                            "success": True,
                            "status_code": response.status_code,
                            "data": response.text
                        }
                except Exception as e:
                    logger.warning(f"Ошибка при парсинге JSON ответа: {e}")
                    return {
                        "success": True,
                        "status_code": response.status_code,
                        "data": response.text
                    }
            else:
                logger.error(f"Ошибка API: {response.status_code} {response.text}")
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "error": response.text
                }
    except Exception as e:
        logger.error(f"Ошибка при выполнении запроса {method.upper()} к {url}: {e}")
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "status_code": 0,
            "error": str(e)
        }

async def link_telegram_user_to_marzban(marzban_username, telegram_user_id, token):
    """Устанавливает связь между пользователем Marzban и Telegram.
    
    Args:
        marzban_username: Имя пользователя в Marzban
        telegram_user_id: ID пользователя в Telegram
        token: Токен доступа к API
        
    Returns:
        bool: True если связь установлена успешно, иначе False
    """
    try:
        api_base_url = f"{MARZBAN_URL}api"
        users_url = f"{api_base_url}/users/{marzban_username}/telegram_user"
        
        headers = {"Authorization": f"Bearer {token}"}
        json_data = {"telegram_user_id": telegram_user_id}
        
        result = await async_api_request(
            method="put",
            url=users_url,
            headers=headers,
            json_data=json_data
        )
        
        if result["success"]:
            return True
        else:
            logger.error(f"Не удалось установить связь с Telegram пользователем: {result['status_code']} {result['error']}")
            return False
    except Exception as e:
        logger.error(f"Ошибка при установке связи с Telegram пользователем: {e}")
        logger.error(traceback.format_exc())
        return False

async def create_marzban_user_basic(username, days, token):
    """Создает базового пользователя в Marzban.
    
    Args:
        username: Имя пользователя в Marzban
        days: Количество дней до истечения срока
        token: Токен доступа к API
        
    Returns:
        dict: Словарь с результатами создания
        {
            "success": bool,
            "username": str,
            "links": list,  # Список ссылок для подключения
            "error": str    # Сообщение об ошибке, если success=False
        }
    """
    try:
        # Устанавливаем время истечения на указанное количество дней вперед
        expire_timestamp = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
        expire_date = datetime.fromtimestamp(expire_timestamp, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        
        url = f"{MARZBAN_URL}api/user"
        headers = {"Authorization": f"Bearer {token}"}
        user_data = {
            "username": username,
            "proxies": {"vless": {"flow": "xtls-rprx-vision"}},
            "inbounds": {"vless": ["VLESS TCP REALITY"]},
            "expire": expire_timestamp
        }
        
        # Создаем пользователя
        create_result = await async_api_request("post", url, headers=headers, json_data=user_data)
        
        if not create_result["success"]:
            logger.error(f"Ошибка создания пользователя {username}: {create_result['status_code']} {create_result['error']}")
            return {"success": False, "error": f"Ошибка создания пользователя: {create_result['error']}"}
        
        # Получаем данные созданного пользователя
        user_url = f"{MARZBAN_URL}api/user/{username}"
        user_info = await async_api_request("get", user_url, headers=headers)
        
        if not user_info["success"]:
            logger.error(f"Не удалось получить информацию о созданном пользователе {username}")
            return {"success": True, "username": username, "links": [], "error": "Пользователь создан, но не удалось получить ссылки"}
        
        user_data = user_info["data"]
        
        # Проверяем наличие ссылок
        if "links" not in user_data or not user_data["links"]:
            logger.warning(f"У пользователя {username} нет ссылок")
            return {"success": True, "username": username, "links": [], "error": "Нет ссылок для пользователя"}
        
        return {"success": True, "username": username, "links": user_data["links"]}
        
    except Exception as e:
        logger.error(f"Ошибка при создании пользователя Marzban {username}: {e}")
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}


async def init_db_pool():
    """Инициализация пула соединений с базой данных."""
    global DB_POOL
    
    try:
        # Если пул уже существует, закрываем его
        if DB_POOL:
            try:
                DB_POOL.close()
                await DB_POOL.wait_closed()
                logger.info("Закрыт существующий пул соединений")
            except Exception as e:
                logger.error(f"Ошибка при закрытии существующего пула: {e}")
        
        # Создаем новый пул соединений с оптимальными настройками
        DB_POOL = await aiomysql.create_pool(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            db=DB_NAME,
            charset='utf8mb4',
            autocommit=True,
            maxsize=20,  # Максимальное количество соединений в пуле
            minsize=5,   # Минимальное количество готовых соединений
            pool_recycle=3600,  # Переиспользовать соединения каждый час
            echo=False,  # Не логировать все запросы
            connect_timeout=10  # Таймаут для подключения
        )
        
        # Проверяем соединение с базой данных
        async with DB_POOL.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                result = await cursor.fetchone()
                if result and result[0] == 1:
                    pass
                else:
                    logger.error("Не удалось проверить соединение с базой данных")
        
        return DB_POOL
    except Exception as e:
        logger.error(f"Ошибка при инициализации пула соединений: {e}")
        logger.error(traceback.format_exc())
        DB_POOL = None
        return None


async def add_referral(referrer_id: int, referred_id: int) -> bool:
    """Добавляет запись о том, что referred_id был приглашен referrer_id.
    
    Args:
        referrer_id: ID пользователя, который пригласил (реферер)
        referred_id: ID приглашенного пользователя (реферал)
        
    Returns:
        bool: True если успешно, False в случае ошибки
    """
    try:
        
        # Получаем токен доступа
        token = await get_access_token()
        if not token:
            logger.error("[REFERRAL_DEBUG] Не удалось получить токен доступа")
            return False
            
        headers = {"Authorization": f"Bearer {token}"}
        
        # Получаем реферальный код пользователя
        
        # Сначала пробуем получить пользователя через API telegram_user
        referrer_response = await async_api_request(
            "GET", 
            f"{MARZBAN_URL}api/telegram_user/{referrer_id}", 
            headers=headers
        )
        
        if not referrer_response or referrer_response.get("success") != True:
            logger.error(f"[REFERRAL_DEBUG] Ошибка при получении данных реферера: {referrer_response}")
            return False
                
        referrer_data = referrer_response.get("data", {})
        referral_code = referrer_data.get("referral_code")
        
        # Если нет реферального кода, генерируем его
        if not referral_code:
            
            gen_response = await async_api_request(
                "POST", 
                f"{MARZBAN_URL}api/referral/code/{referrer_id}", 
                headers=headers
            )
            
            if gen_response and gen_response.get("success") == True:
                referrer_data = gen_response.get("data", {})
                referral_code = referrer_data.get("referral_code")
            else:
                logger.error(f"[REFERRAL_DEBUG] Ошибка при генерации реферального кода: {gen_response}")
                return False
        
        # Применяем реферальный код
        bonus_days = REFERRAL_BONUS_DAYS
        
        apply_response = await async_api_request(
            "POST", 
            f"{MARZBAN_URL}api/referral/apply",
            headers=headers,
            json_data={
                "user_id": referred_id,
                "referrer_code": referral_code,
                "auto_bonus_days": bonus_days
            }
        )
        
        if not apply_response or apply_response.get("success") != True:
            logger.error(f"[REFERRAL_DEBUG] Ошибка при применении реферального кода: {apply_response}")
            return False
            
        
        # Отправляем сообщение реферу о том, что по его реферальной ссылке зарегистрировались и ему начислен бонус
        try:
            message = f"🎁 По вашей реферальной ссылке зарегистрировался новый пользователь!\n\n✅ Вам доступен бонус: {REFERRAL_BONUS_DAYS} дней.\n\nЧтобы использовать бонус, перейдите в раздел 'Партнерская программа' и выберите ключ для применения бонуса."
            await bot.send_message(referrer_id, message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"[REFERRAL_DEBUG] Ошибка при отправке уведомления о бонусе пользователю {referrer_id}: {e}")
            # Не возвращаем ошибку, так как бонус уже был начислен
        
        return True
    except Exception as e:
        logger.error(f"[REFERRAL_DEBUG] Ошибка при добавлении реферала: {e}")
        logger.error(traceback.format_exc())
        return False


async def apply_bonus_to_key(user_id: int, bonus_id: int, marzban_username: str) -> Dict[str, Any]:
    """Применяет бонус к указанному ключу.
    
    Args:
        user_id: ID пользователя Telegram
        bonus_id: ID бонуса
        marzban_username: Имя пользователя Marzban (ключ)
        
    Returns:
        Dict: Результат применения бонуса {'success': bool, 'days_added': int, 'new_expire_date': str}
    """
    try:
        token = await get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        # Применяем бонус через API
        response = await async_api_request(
            "PUT", 
            f"{MARZBAN_URL}api/referral/bonus/{bonus_id}/apply", 
            headers=headers,
            json_data={"marzban_username": marzban_username}
        )
        
        if not response or response.get("success") != True:
            logger.error(f"Ошибка при применении бонуса: {response}")
            return {"success": False, "error": "Не удалось применить бонус"}
            
        bonus_data = response.get("data", {})
        days_added = int(float(bonus_data.get("amount", 0)))
        
        # Получаем информацию о ключе
        response = await async_api_request(
            "GET", 
            f"{MARZBAN_URL}api/user/{marzban_username}", 
            headers=headers
        )
        
        if not response or response.get("success") != True:
            logger.error(f"Ошибка при получении информации о ключе: {response}")
            return {"success": True, "days_added": days_added, "new_expire_date": "Неизвестно"}
            
        user_data = response.get("data", {})
        expire_timestamp = user_data.get("expire")
        
        # Форматируем дату истечения
        if expire_timestamp:
            expire_date = datetime.fromtimestamp(expire_timestamp, tz=timezone.utc)
            formatted_date = expire_date.strftime("%d.%m.%Y %H:%M")
        else:
            formatted_date = "Неограничено"
            
        return {
            "success": True,
            "days_added": days_added,
            "new_expire_date": formatted_date
        }
    except Exception as e:
        logger.error(f"Ошибка при применении бонуса: {e}")
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}


async def get_active_bonuses(user_id: int) -> List[Dict[str, Any]]:
    """Получает список активных бонусов пользователя.
    
    Args:
        user_id: ID пользователя Telegram
        
    Returns:
        List[Dict]: Список активных бонусов
    """
    try:
        token = await get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        # Получаем активные бонусы пользователя
        response = await async_api_request(
            "GET", 
            f"{MARZBAN_URL}api/referral/bonuses/{user_id}?active_only=true", 
            headers=headers
        )
        
        if not response or response.get("success") != True:
            logger.error(f"Ошибка при получении бонусов пользователя: {response}")
            return []
            
        return response.get("data", [])
    except Exception as e:
        logger.error(f"Ошибка при получении активных бонусов: {e}")
        logger.error(traceback.format_exc())
        return []


async def get_referral_count(user_id: int) -> int:
    """Получает количество рефералов пользователя через API.
    
    Args:
        user_id: ID пользователя Telegram
        
    Returns:
        int: Количество рефералов
    """
    try:
        token = await get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        # Получаем структуру рефералов пользователя
        response = await async_api_request(
            "GET", 
            f"{MARZBAN_URL}api/referral/structure/{user_id}", 
            headers=headers
        )
        
        if not response or response.get("success") != True:
            logger.error(f"Ошибка при получении структуры рефералов: {response}")
            return 0
            
        referrals = response.get("data", [])
        return len(referrals)
    except Exception as e:
        logger.error(f"Ошибка при получении количества рефералов: {e}")
        logger.error(traceback.format_exc())
        return 0


async def get_referral_bonus_days(user_id: int) -> int:
    """Возвращает общее количество бонусных дней, доступных пользователю.
    
    Args:
        user_id: ID пользователя Telegram
        
    Returns:
        int: Количество бонусных дней
    """
    try:
        token = await get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        # Получаем бонусы пользователя через API
        response = await async_api_request(
            "GET", 
            f"{MARZBAN_URL}api/referral/bonuses/{user_id}?active_only=true", 
            headers=headers
        )
        
        if not response or response.get("success") != True:
            logger.error(f"[REFERRAL_DEBUG] Ошибка при получении бонусов пользователя: {response}")
            return 0
            
        # Добавляем подробный лог содержимого ответа
            
        # Подсчитываем сумму всех бонусов
        bonuses = response.get("data", [])
        days = sum(float(bonus.get("amount", 0)) for bonus in bonuses)
        
        return int(days)
    except Exception as e:
        logger.error(f"[REFERRAL_DEBUG] Ошибка при получении количества бонусных дней: {e}")
        logger.error(traceback.format_exc())
        return 0


async def close_db_pool():
    """Закрытие пула соединений с базой данных."""
    global DB_POOL
    
    if DB_POOL:
        DB_POOL.close()
        await DB_POOL.wait_closed()
        DB_POOL = None
        logger.info("Пул соединений с базой данных успешно закрыт")
    else:
        logger.info("Пул соединений с базой данных уже закрыт или не был инициализирован")


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
        
        # Формируем URL для запроса токена
        url = f"{MARZBAN_URL}api/admin/token"
        
        # Создаем данные для запроса (form-data)
        form_data = {
            "username": MARZBAN_USERNAME,
            "password": MARZBAN_PASSWORD
        }
        
        # Используем нашу универсальную функцию для запроса токена
        result = await async_api_request(
            method="post",
            url=url,
            form_data=form_data
        )
        
        if result["success"]:
            token_data = result["data"]
            if "access_token" in token_data:
                # Сохраняем токен в кэш на 1 час
                _token_cache["token"] = token_data["access_token"]
                _token_cache["expires_at"] = current_time + 3600
                logger.info("Получен новый токен доступа")
                return token_data["access_token"]
            else:
                logger.error(f"Неверный формат ответа при получении токена: {token_data}")
                return None
        else:
            logger.error(f"Не удалось получить токен: {result['status_code']} {result['error']}")
            return None
    except Exception as e:
        logger.error(f"Общая ошибка получения токена: {e}")
        logger.error(traceback.format_exc())
        return None


async def check_user_exists(user_id):
    """Проверяет, существует ли пользователь с указанным Telegram ID."""
    try:
        token = await get_access_token()
        if not token:
            logger.error("[REFERRAL_DEBUG] Не удалось получить токен доступа при проверке пользователя")
            return False
            
        headers = {"Authorization": f"Bearer {token}"}
        
        # Вместо получения списка всех пользователей с фильтрацией, запрашиваем конкретного пользователя
        api_base_url = f"{MARZBAN_URL}api"
        user_url = f"{api_base_url}/telegram_user/{user_id}"
        
        
        response = await async_api_request("GET", user_url, headers=headers)        
        # Пользователь существует, если запрос вернул успешный ответ
        if response and response.get("success") == True:
            return True
                
        return False
    except Exception as e:
        logger.error(f"[REFERRAL_DEBUG] Ошибка при проверке пользователя {user_id}: {e}")
        logger.error(traceback.format_exc())
        return False


async def register_user(user_id, username=None, first_name=None, last_name=None):
    """Регистрирует нового пользователя в базе данных."""
    try:
        token = await get_access_token()
        if not token:
            logger.error("[REFERRAL_DEBUG] Не удалось получить токен доступа при регистрации")
            return False
            
        headers = {"Authorization": f"Bearer {token}"}
        
        api_base_url = f"{MARZBAN_URL}api"
        users_url = f"{api_base_url}/telegram_user"
        
        # Формируем данные для создания пользователя
        user_data = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name
        }
        
        response = await async_api_request("POST", users_url, headers=headers, json_data=user_data)
        
        if not response or response.get("success") != True:
            logger.error(f"[REFERRAL_DEBUG] Ошибка при регистрации пользователя: {response}")
            return False
            
        return True
    except Exception as e:
        logger.error(f"[REFERRAL_DEBUG] Ошибка при регистрации пользователя {user_id}: {e}")
        logger.error(traceback.format_exc())
        return False


async def get_user_devices(user_id: int) -> List[Tuple[str, str, int]]:
    """Получает список устройств пользователя и оставшиеся дни.
    
    Возвращает список кортежей (username, link, days_left)
    """
    try:
        # Получаем токен доступа к API
        token = await get_access_token()
        if not token:
            logger.error("Не удалось получить токен для получения устройств пользователя")
            return []
        
        # Формируем URL для запроса только устройств конкретного пользователя
        api_base_url = f"{MARZBAN_URL}api"
        url = f"{api_base_url}/telegram_users_with_keys"
        
        # Выполняем запрос к API с фильтрацией по user_id
        headers = {"Authorization": f"Bearer {token}"}
        params = {"telegram_id": user_id}  # Передаем ID пользователя в параметрах запроса
        
        result = await async_api_request(
            method="get",
            url=url,
            headers=headers,
            params=params
        )
        
        if result["success"]:
            user_devices = result["data"]
        else:
            logger.error(f"Ошибка при получении устройств пользователя {user_id}: {result['status_code']} {result['error']}")
            return []
        
        # Если нет устройств, возвращаем пустой список
        if not user_devices:
            return []
        
        # Для каждого устройства запрашиваем детальную информацию параллельно
        async def get_device_details(device):
            marzban_username = device.get('marzban_username')
            if not marzban_username:
                return None
                
            # Получаем детальную информацию о каждом устройстве
            detail_url = f"{api_base_url}/user/{marzban_username}"
            detail_result = await async_api_request(
                method="get",
                url=detail_url,
                headers=headers
            )
            
            if detail_result["success"]:
                return (marzban_username, detail_result["data"])
            return None
            
        # Параллельно запрашиваем подробную информацию обо всех устройствах
        device_tasks = []
        for device in user_devices:
            marzban_username = device.get('marzban_username')
            if marzban_username:
                device_tasks.append(get_device_details(device))
                
        # Ждем завершения всех параллельных запросов
        detail_results = await asyncio.gather(*device_tasks, return_exceptions=True)
        
        result = []
        now = datetime.now().timestamp()
        
        # Обрабатываем полученные результаты
        for detail_result in detail_results:
            # Пропускаем ошибки и None результаты
            if detail_result is None or isinstance(detail_result, Exception):
                continue
                
            marzban_username, detail_data = detail_result
            
            # Получаем нужные данные из детального ответа
            links = detail_data.get('links', [])
            expire = detail_data.get('expire')
            
            # Пропускаем устройства без ссылок
            if not links:
                continue
                
            # Вычисляем срок истечения
            if expire:
                days_left = (datetime.fromtimestamp(expire) - datetime.fromtimestamp(now)).days
            else:
                # Если срок не задан, считаем что ключ бессрочный
                days_left = 999  # Условное большое значение для бессрочных ключей
            
            # Добавляем устройство в результат
            result.append((marzban_username, links[0], days_left))
        
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

        # Проверяем доступность API
        api_url = f"{MARZBAN_URL}api/system"
        headers = {"Authorization": f"Bearer {token}"}
        
        system_check = await async_api_request("get", api_url, headers=headers)
        if not system_check["success"]:
            logger.error(f"API недоступен. Код ответа: {system_check['status_code']}, ответ: {system_check['error']}")
            return None
            

        # Генерируем уникальное имя пользователя
        marzban_username = f"{generate_random_string()}_{user_id}"
        
        # Создаем пользователя через базовую функцию
        result = await create_marzban_user_basic(marzban_username, DEFAULT_TEST_PERIOD, token)
        
        if not result["success"]:
            logger.error(f"Ошибка создания пользователя: {result['error']}")
            return None
        
        # Устанавливаем связь с пользователем Telegram
        link_success = await link_telegram_user_to_marzban(marzban_username, user_id, token)
        if not link_success:
            logger.warning(f"Не удалось установить связь с Telegram ID {user_id} для пользователя {marzban_username}")
        
        # Проверяем наличие ссылок
        if not result["links"]:
            logger.error(f"У пользователя {marzban_username} нет ссылок")
            return None
            
        return result["links"][0]
            
    except Exception as e:
        logger.error(f"Общая ошибка создания пользователя: {e}")
        logger.error(traceback.format_exc())
        return None


# === Клавиатуры ===
def get_main_menu_keyboard():
    """Клавиатура главного меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 Купить VPN", callback_data="buy_new_vpn"),
            InlineKeyboardButton(text="🔄 Продлить VPN", callback_data="extend_vpn")
        ],
        [InlineKeyboardButton(text="🔑 Мои активные ключи", callback_data="my_keys")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
        [InlineKeyboardButton(text="👥 Партнерская программа", callback_data="affiliate")]
    ])

def get_back_to_menu_keyboard():
    """Возвращает клавиатуру с кнопкой возврата в главное меню."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Вернуться в меню", callback_data="menu_main")]
        ]
    )

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


def get_my_keys_keyboard(devices):
    """Клавиатура для раздела 'Мои ключи'."""
    buttons = []
    
    # Добавляем кнопки для каждого устройства
    for username, vless_link, days_left in devices:
        # Сокращенное отображение имени (без ID пользователя)
        display_name = username.split('_')[0]
        
        # Форматируем отображение для дней
        days_display = days_left
        if isinstance(days_left, int):
            days_display = f"{days_left} дн."
        elif days_left == 999:
            days_display = "∞ (бессрочно)"
        else:
            days_display = "неизвестно"
            
        buttons.append([
            InlineKeyboardButton(text=f"🔑 {display_name} ({days_display})", callback_data=f"device_info_{username}")
        ])
    
    # Добавляем кнопку создания нового ключа и возврата в меню
    buttons.append([InlineKeyboardButton(text="➕ Создать новый ключ", callback_data="buy_new_vpn")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def get_keys_selection_keyboard(devices, bonus_id: int):
    """Клавиатура для выбора ключа для начисления бонуса.
    
    Args:
        devices: Список устройств пользователя [(username, link, days_left), ...]
        bonus_id: ID ожидающего бонуса в базе данных
        
    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопками для выбора ключа
    """
    buttons = []
    
    # Добавляем кнопки для каждого устройства
    for username, _, days_left in devices:
        buttons.append([
            InlineKeyboardButton(
                text=f"🔑 {username} ({days_left} дней)", 
                callback_data=f"apply_bonus_{bonus_id}_{username}"
            )
        ])
    
    # Добавляем кнопку отмены
    buttons.append([
        InlineKeyboardButton(
            text="❌ Отмена", 
            callback_data=f"cancel_bonus_{bonus_id}"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name

    
    # Извлекаем параметры из команды /start заранее
    referrer_id = None
    message_text = message.text.strip()
    
    if message_text and len(message_text) > 6:  # Длиннее, чем просто "/start"
        try:
            # Извлекаем параметры из команды /start
            parts = message_text.split()
            
            if len(parts) > 1:
                param = parts[1]
                
                # Проверяем, это реферальная ссылка или активация бонуса
                if param.startswith("bonus_"):
                    # Бонусы через /start больше не обрабатываются
                    await message.answer(
                        "⚠️ Активация бонусов через команду /start более недоступна.\n"
                        "Пожалуйста, воспользуйтесь разделом 'Партнерская программа' в главном меню.",
                        reply_markup=get_back_to_menu_keyboard()
                    )
                    return
                elif param.isdigit():
                    # Это ID реферера
                    referrer_id = int(param)
                else:
                    # Это может быть буквенно-цифровой реферальный код
                    
                    # Ищем пользователя по реферальному коду через новую функцию
                    referrer_id = await find_user_by_referral_code(param)
        except Exception as e:
            logger.error(f"[REFERRAL_LOG] Ошибка при обработке параметров start: {e}")
            logger.error(traceback.format_exc())
    
    # Проверяем, существует ли пользователь
    user_exists = await check_user_exists(user_id)
    is_new_user = not user_exists
    
    
    try:
        # Важно: сначала регистрируем пользователя (если его нет), а затем применяем реферальный код
        if not user_exists:
            # Если пользователя нет, регистрируем его
            registration_success = await register_user(user_id, username, first_name, last_name)
            if registration_success:
                is_new_user = True
                
                # Делаем небольшую паузу, чтобы гарантировать, что пользователь зарегистрирован в базе
                await asyncio.sleep(0.5)
            else:
                logger.error(f"[REFERRAL_LOG] Не удалось зарегистрировать пользователя {user_id}")
        
        # Обработка реферального кода только если был передан referrer_id и это не сам пользователь
        if referrer_id and referrer_id != user_id:
            # Проверяем, существует ли реферер
            referrer_exists = await check_user_exists(referrer_id)
            
            if referrer_exists:
                # Добавляем реферальную запись
                referral_added = await add_referral(referrer_id, user_id)
                
                if referral_added:
                    pass
                else:
                    logger.error(f"[REFERRAL_LOG] Не удалось привязать пользователя {user_id} к рефереру {referrer_id}")
    except Exception as e:
        logger.error(f"Ошибка при регистрации пользователя {user_id}: {e}")
        logger.error(traceback.format_exc())
    
    # Отправляем приветственное сообщение
    try:
        photo = FSInputFile(os.path.join(MEDIA_DIR, "logo.jpg"))
        await message.answer_photo(
            photo=photo,
            caption="🌐 <b>XGUARD VPN</b>\n\n"
                    "✅ Максимальная скорость\n"
                    "✅ Работает везде и всегда\n"
                    "✅ Доступен iPhone, Android, Windows, TV\n"
                    "✅ Лучшие технологии шифрования\n\n"
                    "Выберите действие:",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке приветственного сообщения: {e}")
        logger.error(traceback.format_exc())
        
    # Если пользователь новый, создаем ключ и отправляем специальное сообщение
    if is_new_user:
        await asyncio.sleep(1)  # Небольшая задержка для последовательности сообщений
        try:
            # Создаем VPN ключ напрямую, без проверки test_period
            vpn_link = await create_vless_user(user_id)
            
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
                
                # Отправляем сообщение с ключом и инструкцию
                await message.answer(
                    f"🎁 <b>Поздравляем!</b>\n\n"
                    f"Вам доступен бесплатный тестовый период на <b>{DEFAULT_TEST_PERIOD} дней</b>!\n\n"
                    f"Ваш ключ: <code>{vpn_link}</code>\n\n"
                    f"Выберите вашу платформу для инструкции по подключению:",
                    reply_markup=instructions_kb,
                    parse_mode="HTML"
                )
            else:
                logger.error(f"Не удалось создать тестовый ключ для пользователя {user_id}")
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
        caption="🌐 <b>XGUARD VPN</b>\n\n"
                "✅ Максимальная скорость\n"
                "✅ Работает везде и всегда\n"
                "✅ Доступен iPhone, Android, Windows, TV\n"
                "✅ Лучшие технологии шифрования\n\n"
                "Выберите действие:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "buy_new_vpn")
async def handle_buy_new_vpn(callback: types.CallbackQuery):
    """Обработчик кнопки покупки нового VPN."""
    await bot.send_message(
        callback.from_user.id,
        "💳 <b>Покупка нового VPN ключа</b>\n\n"
        "1️⃣ Выбери необходимый тариф ниже 👇\n"
        "2️⃣ Внеси платеж\n"
        "3️⃣ И получи ключ с простой инструкцией\n"
        "😉\n\n"
        "👍 Пользователи говорят, что готовы платить\n"
        "за эту скорость и удобство даже больше\n"
        "✅ Проверь, насколько понравится тебе",
        reply_markup=get_tariff_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "extend_vpn")
async def handle_extend_vpn(callback: types.CallbackQuery):
    """Обработчик кнопки продления VPN."""
    user_id = callback.from_user.id
    
    # Отправка индикатора загрузки
    loading_message = await callback.bot.send_message(
        user_id,
        "⏳ Загружаем информацию о ваших ключах...",
        parse_mode="HTML"
    )
    
    # Запоминаем время начала операции
    start_time = datetime.now()
    
    # Получаем устройства пользователя
    devices = await get_user_devices(user_id)
    
    # Обеспечиваем минимальное время отображения индикатора загрузки (1 секунда)
    elapsed = (datetime.now() - start_time).total_seconds()
    if elapsed < 1:
        await asyncio.sleep(1 - elapsed)
    
    # Удаляем сообщение о загрузке
    await callback.bot.delete_message(chat_id=user_id, message_id=loading_message.message_id)
    
    if not devices:
        await bot.send_message(
            callback.from_user.id,
            "⚠️ У вас нет активных ключей для продления!\n\n"
            "Сначала создайте новый ключ, используя кнопку 'Купить VPN'.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
            ]),
            parse_mode="HTML"
        )
    else:
        await bot.send_message(
            callback.from_user.id,
            "🔄 <b>Продление VPN</b>\n\n"
            "Выберите ключ, который хотите продлить:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                *[[InlineKeyboardButton(
                    text=f"🔑 {username.split('_')[0]} ({days} дн.)", 
                    callback_data=f"pay_key_{username}")]
                  for username, _, days in devices],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
            ]),
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
    user_id = callback.from_user.id
    parts = callback.data.split("_")  # new_key_30_150
    days = parts[2]
    amount = parts[3]
    
    # Отправка индикатора загрузки
    loading_message = await callback.bot.send_message(
        user_id,
        "⏳ Создаем платеж...",
        parse_mode="HTML"
    )
    
    # Запоминаем время начала операции
    start_time = datetime.now()
    
    try:
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
        
        # Обеспечиваем минимальное время отображения индикатора загрузки (1 секунда)
        elapsed = (datetime.now() - start_time).total_seconds()
        if elapsed < 1:
            await asyncio.sleep(1 - elapsed)
        
        # Удаляем сообщение о загрузке
        await callback.bot.delete_message(chat_id=user_id, message_id=loading_message.message_id)
        
        await bot.send_message(
            callback.from_user.id,
            f"📅 Период: <b>{days} дней</b>\n"
            f"💰 Сумма: <b>{amount} RUB</b>\n\n"
            f"Для оплаты перейдите по ссылке:\n {payment.confirmation.confirmation_url}",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode="HTML"  # HTML для корректного форматирования текста
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {e}")
        logger.error(traceback.format_exc())
        await callback.bot.delete_message(chat_id=user_id, message_id=loading_message.message_id)
        await callback.bot.send_message(
            user_id,
            "❌ Произошла ошибка при создании платежа. Пожалуйста, попробуйте позже.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode="HTML"
        )


@dp.callback_query(lambda c: c.data.startswith("pay_key_"))
async def handle_pay_key(callback: types.CallbackQuery):
    """Обработчик выбора ключа для оплаты."""
    user_id = callback.from_user.id
    username = callback.data.split("pay_key_")[1]
    
    # Отправка индикатора загрузки
    loading_message = await callback.bot.send_message(
        user_id,
        "⏳ Загружаем тарифы для продления...",
        parse_mode="HTML"
    )
    
    # Запоминаем время начала операции
    start_time = datetime.now()
    
    try:
        # Получаем клавиатуру с тарифами
        keyboard = get_tariff_keyboard(username)
        
        # Обеспечиваем минимальное время отображения индикатора загрузки (1 секунда)
        elapsed = (datetime.now() - start_time).total_seconds()
        if elapsed < 1:
            await asyncio.sleep(1 - elapsed)
        
        # Удаляем сообщение о загрузке
        await callback.bot.delete_message(chat_id=user_id, message_id=loading_message.message_id)
        
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
    except Exception as e:
        logger.error(f"Ошибка при загрузке тарифов для продления: {e}")
        logger.error(traceback.format_exc())
        
        # Удаляем сообщение о загрузке
        await callback.bot.delete_message(chat_id=user_id, message_id=loading_message.message_id)
        
        await callback.bot.send_message(
            user_id,
            "❌ Произошла ошибка при загрузке тарифов. Пожалуйста, попробуйте позже.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode="HTML"
        )


@dp.callback_query(lambda c: c.data.startswith("pay_"))
async def handle_payment(callback: types.CallbackQuery):
    """Обработчик создания платежа."""
    user_id = callback.from_user.id
    parts = callback.data.split("_")  # pay_username_30_150
    username = "_".join(parts[1:-2])  # Собираем username обратно
    days = parts[-2]
    amount = parts[-1]
    
    # Отправка индикатора загрузки
    loading_message = await callback.bot.send_message(
        user_id,
        "⏳ Создаем платеж для продления...",
        parse_mode="HTML"
    )
    
    # Запоминаем время начала операции
    start_time = datetime.now()
    
    try:
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
        
        # Обеспечиваем минимальное время отображения индикатора загрузки (1 секунда)
        elapsed = (datetime.now() - start_time).total_seconds()
        if elapsed < 1:
            await asyncio.sleep(1 - elapsed)
        
        # Удаляем сообщение о загрузке
        await callback.bot.delete_message(chat_id=user_id, message_id=loading_message.message_id)
        
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
    except Exception as e:
        logger.error(f"Ошибка при создании платежа для продления: {e}")
        logger.error(traceback.format_exc())
        
        # Удаляем сообщение о загрузке
        await callback.bot.delete_message(chat_id=user_id, message_id=loading_message.message_id)
        
        await callback.bot.send_message(
            user_id,
            "❌ Произошла ошибка при создании платежа. Пожалуйста, попробуйте позже.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode="HTML"
        )

@dp.callback_query(lambda c: c.data == "help")
async def handle_help(callback: types.CallbackQuery):
    """Обработчик кнопки помощи."""
    message = "<b>❓ Центр помощи и поддержки</b>\n\n"
    message += "Выберите интересующий вас раздел:\n\n"
    message += "• У нас высокоскоростные серверы в разных странах\n"
    message += "• Техническая поддержка 24/7\n"
    message += "• Стабильное соединение и высокий уровень безопасности\n"
    message += "• Простая настройка на всех устройствах"
    
    help_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Правила/Оферта", callback_data="help_rules")],
        [InlineKeyboardButton(text="⚠️ Не работает VPN", callback_data="help_vpn_issue")],
        [InlineKeyboardButton(text="📞 Связаться с нами", callback_data="help_contact")],
        [InlineKeyboardButton(text="🔌 Как подключиться", callback_data="help_connection")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
    ])
    
    await bot.send_message(
        callback.from_user.id,
        message,
        reply_markup=help_keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "help_rules")
async def handle_help_rules(callback: types.CallbackQuery):
    """Обработчик кнопки правил и оферты."""
    message = "<b>📜 Правила использования и Оферта</b>\n\n"
    message += "1️⃣ <b>Общие положения</b>\n"
    message += "• Сервис предоставляется «как есть» без гарантий\n"
    message += "• Мы не несем ответственности за действия пользователей\n\n"
    
    message += "2️⃣ <b>Правила использования</b>\n"
    message += "• Запрещены любые незаконные действия\n"
    message += "• Запрещено использование для спама и рассылок\n"
    message += "• Запрещена передача ключей третьим лицам\n\n"
    
    message += "3️⃣ <b>Условия оплаты</b>\n"
    message += "• Оплаченный период не подлежит возврату\n"
    message += "• Оплата производится в соответствии с выбранным тарифом\n\n"
    
    message += "Продолжая использовать наш сервис, вы автоматически соглашаетесь с указанными условиями."
    
    help_back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к помощи", callback_data="help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
    ])
    
    await bot.send_message(
        callback.from_user.id,
        message,
        reply_markup=help_back_keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "help_vpn_issue")
async def handle_help_vpn_issue(callback: types.CallbackQuery):
    """Обработчик кнопки проблем с VPN."""
    message = "<b>⚠️ Не работает VPN? Решение проблем</b>\n\n"
    message += "Если у вас возникли проблемы с подключением, попробуйте следующие шаги:\n\n"
    
    message += "1️⃣ <b>Основные проверки</b>\n"
    message += "• Проверьте подключение к интернету\n"
    message += "• Убедитесь, что срок действия вашего ключа не истёк\n"
    message += "• Перезапустите приложение VPN\n\n"
    
    message += "2️⃣ <b>Технические решения</b>\n"
    message += "• Попробуйте подключиться через другой сервер\n"
    message += "• Обновите приложение VPN до последней версии\n"
    message += "• Проверьте настройки брандмауэра и антивируса\n\n"
    
    message += "3️⃣ <b>Если ничего не помогает</b>\n"
    message += "• Свяжитесь с нашей технической поддержкой\n"
    message += "• Мы решим вашу проблему в кратчайшие сроки"
    
    help_back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к помощи", callback_data="help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
    ])
    
    await bot.send_message(
        callback.from_user.id,
        message,
        reply_markup=help_back_keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data == "help_contact")
async def handle_help_contact(callback: types.CallbackQuery):
    """Обработчик кнопки связи с поддержкой."""
    message = "<b>📞 Связаться с нами</b>\n\n"
    message += "Наша служба поддержки всегда готова помочь вам с любыми вопросами и проблемами:\n\n"
    
    message += "👨‍💻 <b>Техническая поддержка:</b> @Xguard_SupportBot\n"
    message += "⏱ <b>Время работы:</b> 24/7\n"
    message += "⚡️ <b>Среднее время ответа:</b> 5-10 минут\n\n"
    
    message += "При обращении в поддержку, пожалуйста, опишите вашу проблему максимально подробно."
    
    help_back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать в поддержку", url="https://t.me/Xguard_SupportBot")],
        [InlineKeyboardButton(text="◀️ Назад к помощи", callback_data="help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
    ])
    
    await bot.send_message(
        callback.from_user.id,
        message,
        reply_markup=help_back_keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "help_connection")
async def handle_help_connection(callback: types.CallbackQuery):
    """Обработчик кнопки инструкции по подключению."""
    message = "<b>🔌 Как подключиться к VPN</b>\n\n"
    message += "Следуйте этим простым шагам для настройки VPN на вашем устройстве:\n\n"
    
    message += "1️⃣ <b>Покупка ключа</b>\n"
    message += "• Выберите подходящий тариф в разделе «Купить VPN»\n"
    message += "• Оплатите выбранный тариф\n"
    message += "• Получите ваш личный ключ\n\n"
    
    message += "2️⃣ <b>Установка приложения</b>\n"
    message += "• Скачайте специальное приложение для вашей платформы\n"
    message += "• Установите и запустите приложение\n\n"
    
    message += "3️⃣ <b>Подключение</b>\n"
    message += "• Добавьте ваш ключ в приложение\n"
    message += "• Выберите желаемый сервер\n"
    message += "• Нажмите кнопку подключения\n\n"
    
    message += "Выберите вашу платформу для получения подробной инструкции:"
    
    platforms_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 Android", callback_data="instruction_android"),
            InlineKeyboardButton(text="📱 iOS", callback_data="instruction_ios")
        ],
        [
            InlineKeyboardButton(text="💻 Windows", callback_data="instruction_windows"),
            InlineKeyboardButton(text="💻 macOS", callback_data="instruction_macos")
        ],
        [InlineKeyboardButton(text="◀️ Назад к помощи", callback_data="help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
    ])
    
    await bot.send_message(
        callback.from_user.id,
        message,
        reply_markup=platforms_keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "my_keys")
async def handle_my_keys(callback: types.CallbackQuery):
    """Обработчик кнопки 'Мои активные ключи'."""
    try:
        user_id = callback.from_user.id
        await callback.answer()
        
        # Отправляем сообщение о загрузке
        loading_message = await callback.message.answer("⏳ Загружаем информацию о ваших ключах...")
        
        # Получаем список устройств пользователя
        devices = await get_user_devices(user_id)
        
        if not devices:
            await loading_message.delete()
            await callback.message.answer(
                "У вас нет активных ключей. Нажмите кнопку 'Создать новый ключ', чтобы создать ключ.",
                reply_markup=get_back_to_menu_keyboard()
            )
            return
            
        # Создаем клавиатуру с ключами пользователя
        keyboard = get_my_keys_keyboard(devices)
        
        # Отправляем сообщение с клавиатурой
        await loading_message.delete()
        await callback.message.answer(
            "🔑 <b>Ваши активные ключи:</b>\n\n"
            "Выберите ключ для просмотра подробной информации:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка в обработчике 'Мои активные ключи': {e}")
        logger.error(traceback.format_exc())
        await callback.message.answer(
            "❌ Произошла ошибка при получении ваших ключей. Пожалуйста, попробуйте позже.",
            reply_markup=get_back_to_menu_keyboard()
        )

@dp.callback_query(lambda c: c.data == "affiliate")
async def handle_affiliate(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        await callback.answer()
        
        # Отправляем сообщение о загрузке
        loading_message = await callback.message.answer("⏳ Загружаем информацию...")
        
        # Получаем токен для доступа к API
        token = await get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        try:
            # Получаем данные о пользователе
            response = await async_api_request(
                "GET", 
                f"{MARZBAN_URL}api/telegram_user/{user_id}", 
                headers=headers
            )
            
            if not response or response.get("success") != True:
                logger.error(f"Ошибка при получении данных пользователя: {response}")
                
            user_data = response.get("data", {})
            referral_code = user_data.get("referral_code")
            
            # Если у пользователя нет реферального кода, генерируем его
            if not referral_code:
                try:
                    response = await async_api_request(
                        "POST", 
                        f"{MARZBAN_URL}api/referral/code/{user_id}", 
                        headers=headers
                    )
                    
                    if not response or response.get("success") != True:
                        logger.error(f"Ошибка при генерации реферального кода: {response}")
                        
                    user_data = response.get("data", {})
                    referral_code = user_data.get("referral_code")
                    
                    # Если код все еще не получен, пробуем альтернативный URL
                    if not referral_code:
                        alt_response = await async_api_request(
                            "POST", 
                            f"{MARZBAN_URL}api/telegram_user/{user_id}/generate_code", 
                            headers=headers
                        )
                                                
                        if alt_response and alt_response.get("success") == True:
                            user_data = alt_response.get("data", {})
                            referral_code = user_data.get("referral_code")
                except Exception as e:
                    logger.error(f"Ошибка при генерации реферального кода: {e}")
            
            # Получаем количество рефералов
            ref_count = await get_referral_count(user_id)
            
            # Получаем количество доступных бонусных дней
            bonus_days = await get_referral_bonus_days(user_id)
            
            # Текст сообщения с информацией о партнерской программе
            message_text = (
                "🤝 <b>Партнерская программа</b>\n\n"
                "Приглашайте друзей и получайте бонусы!\n\n"
                "За каждого приглашенного друга вы получаете 7 дней бесплатного использования VPN.\n\n"
                f"👥 Ваши приглашенные: <b>{ref_count}</b>\n"
                f"⏱ Доступные бонусы: <b>{bonus_days} дней</b>\n\n"
            )
            
            # Добавляем информацию о реферальной ссылке
            if referral_code:
                bot_username = (await bot.get_me()).username
                ref_link = f"https://t.me/{bot_username}?start={referral_code}"
                message_text += f"🔗 <b>Ваша реферальная ссылка:</b>\n<code>{ref_link}</code>\n\n"
            
            # Создаем клавиатуру с кнопками
            keyboard_buttons = []

            # Добавляем кнопку для копирования реф.ссылки

            # Если есть доступные бонусы, добавляем кнопку для применения
            if bonus_days > 0:
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text="🎁 Применить бонус к ключу", 
                        callback_data="select_key_for_bonus"
                    )
                ])

            # Добавляем кнопку для возврата в главное меню
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="🔙 Вернуться в меню", 
                    callback_data="menu_main"
                )
            ])

            # Создаем клавиатуру
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            # Отправляем сообщение с клавиатурой - используем answer вместо edit_text
            await callback.message.answer(message_text, reply_markup=keyboard, parse_mode="HTML")
            
        except Exception as e:
            logger.error(f"Ошибка в обработчике партнерской программы: {e}")
            # Используем answer вместо edit_text
            await callback.message.answer(
                "❌ Произошла ошибка при загрузке данных партнерской программы. Пожалуйста, попробуйте позже.",
                reply_markup=get_back_to_menu_keyboard()
            )
        
        # Удаляем сообщение о загрузке
        try:
            await loading_message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение о загрузке: {e}")
            
    except Exception as e:
        logger.error(f"Ошибка в обработчике партнерской программы: {e}")
        traceback.print_exc()
        try:
            # Используем answer вместо edit_text
            await callback.message.answer(
                "❌ Произошла ошибка. Пожалуйста, попробуйте позже.",
                reply_markup=get_back_to_menu_keyboard()
            )
        except:
            pass

@dp.callback_query(lambda c: c.data.startswith("copy_ref_link_"))
async def handle_copy_ref_link(callback: types.CallbackQuery):
    """Обработчик кнопки копирования реферальной ссылки."""
    try:
        referral_code = callback.data.replace("copy_ref_link_", "")
        bot_username = (await bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={referral_code}"
        
        await callback.answer("Ссылка скопирована!")
        await callback.message.answer(f"Ваша реферальная ссылка:\n<code>{referral_link}</code>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при копировании реферальной ссылки: {e}")
        logger.error(traceback.format_exc())
        await callback.answer("Произошла ошибка при копировании ссылки.")

@dp.callback_query(lambda c: c.data == "select_key_for_bonus")
async def select_key_for_bonus(callback: types.CallbackQuery):
    """Обработчик выбора ключа для применения бонуса."""
    try:
        await callback.answer()
        user_id = callback.from_user.id
        
        # Получаем активные бонусы пользователя
        active_bonuses = await get_active_bonuses(user_id)
        
        if not active_bonuses:
            await callback.message.answer("У вас нет доступных бонусов.")
            return
            
        # Берем первый доступный бонус
        bonus_id = active_bonuses[0].get("id")
        
        # Получаем список ключей пользователя
        devices = await get_user_devices(user_id)
        
        if not devices:
            await callback.message.answer("У вас нет активных ключей. Сначала создайте ключ.")
            return
        
        # Создаем клавиатуру с выбором ключей
        keyboard_buttons = []
        
        for device in devices:
            username, vless_link, days_left = device
            
            # Получаем отображаемое имя (первая часть до символа _)
            display_name = username.split('_')[0] if isinstance(username, str) and '_' in username else username
            
            # Форматируем отображение для дней
            days_display = "неизвестно"
            if isinstance(days_left, int):
                days_display = f"{days_left} дней"
            elif days_left == 999:
                days_display = "∞ (бессрочно)"
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"🔑 {display_name} ({days_display})",
                    callback_data=f"apply_bonus_{bonus_id}#{username}"
                )
            ])
        
        # Добавляем кнопку отмены
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="affiliate"
            )
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.answer(
            "Выберите ключ, к которому хотите применить бонус:",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при выборе ключа для бонуса: {e}")
        logger.error(traceback.format_exc())
        await callback.message.answer("❌ Произошла ошибка. Пожалуйста, попробуйте позже или обратитесь в поддержку.")


@dp.callback_query(lambda c: c.data.startswith("apply_bonus_"))
async def apply_bonus_handler(callback: types.CallbackQuery):
    """Обработчик применения бонуса к ключу."""
    try:
        await callback.answer()
        
        # Формат данных: apply_bonus_id#username
        # где id - это ID бонуса, username - имя пользователя Marzban
        bonus_data = callback.data.replace("apply_bonus_", "")
        bonus_id, marzban_username = bonus_data.split("#")
        
        user_id = callback.from_user.id
        
        # Применяем бонус
        result = await apply_bonus_to_key(user_id, int(bonus_id), marzban_username)
        
        if not result.get("success"):
            logger.error(f"Ошибка при применении бонуса: {result.get('error')}")
            await callback.message.answer(
                "❌ Ошибка при применении бонуса. Пожалуйста, попробуйте позже или обратитесь в поддержку.",
                reply_markup=get_back_to_menu_keyboard()
            )
            return
            
        days_added = result.get("days_added", 0)
        new_expire_date = result.get("new_expire_date", "Неизвестно")
        
        await callback.message.answer(
            f"✅ Бонус успешно применен!\n\n"
            f"Вы добавили {days_added} дней к ключу {marzban_username}.\n"
            f"Новая дата окончания: {new_expire_date}",
            reply_markup=get_back_to_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при применении бонуса: {e}")
        logger.error(traceback.format_exc())
        await callback.message.answer(
            "❌ Произошла ошибка. Пожалуйста, попробуйте позже или обратитесь в поддержку.",
            reply_markup=get_back_to_menu_keyboard()
        )

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
        # Получаем информацию о пользователе через API
        url = f"{MARZBAN_URL}api/user/{username}"
        headers = {"Authorization": f"Bearer {token}"}
        
        user_result = await async_api_request("get", url, headers=headers)
        
        if not user_result["success"] or not user_result["data"].get("links"):
            await bot.send_message(
                user_id,
                "❌ Ключ не найден или был удален.",
                reply_markup=get_back_to_menu_keyboard(),
                parse_mode="HTML"
            )
            return await callback.answer()
            
        user_info = user_result["data"]
        vless_link = user_info["links"][0]
        
        # Вычисляем срок действия
        days_left = 0
        created_at = "Неизвестно"
        expire_date = "Неизвестно"
        
        if user_info.get("expire"):
            expire_timestamp = user_info["expire"]
            expire_dt = datetime.fromtimestamp(expire_timestamp)
            days_left = (expire_dt - datetime.now()).days
            expire_date = expire_dt.strftime('%d.%m.%Y')
        
        # Пытаемся получить дату создания из Marzban API, если возможно
        if "created_at" in user_info:
            created_at_value = user_info["created_at"]
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
        # Формируем URL для запроса информации о текущем админе
        url = f"{MARZBAN_URL}api/admin"
        headers = {"Authorization": f"Bearer {token}"}
        
        # Выполняем запрос с помощью нашей универсальной функции
        result = await async_api_request(
            method="get",
            url=url,
            headers=headers
        )
        
        # Проверяем успешность запроса
        return result["success"]
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
            async with DB_POOL.acquire() as conn:
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


# Класс для ограничения скорости запросов (защита от DDoS и брутфорс атак)
class RateLimiter:
    """
    Класс для ограничения скорости запросов от пользователей.
    Помогает защитить от DDoS и брутфорс атак.
    """
    def __init__(self, max_requests=10, time_window=60):
        # max_requests - максимальное количество запросов
        # time_window - временное окно в секундах
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = {}  # {ip: [(timestamp1), (timestamp2), ...]}
        
    def is_rate_limited(self, ip):
        """
        Проверяет, не превышен ли лимит запросов для данного IP.
        Возвращает (is_limited, retry_after), где:
        - is_limited: bool - превышен ли лимит
        - retry_after: int - через сколько секунд можно повторить запрос
        """
        now = datetime.now().timestamp()
        
        # Очищаем старые записи
        if ip in self.requests:
            # Оставляем только те запросы, которые находятся в текущем временном окне
            self.requests[ip] = [ts for ts in self.requests[ip] 
                               if now - ts < self.time_window]
        else:
            self.requests[ip] = []
            
        # Если количество запросов меньше максимального, добавляем новый запрос
        if len(self.requests[ip]) < self.max_requests:
            self.requests[ip].append(now)
            return False, 0
            
        # Вычисляем, через сколько можно повторить запрос
        oldest_request = min(self.requests[ip])
        retry_after = int(self.time_window - (now - oldest_request))
        return True, max(0, retry_after)

# Создаем глобальный экземпляр для ограничения запросов
api_rate_limiter = RateLimiter(max_requests=20, time_window=60)  # 20 запросов в минуту

# Middleware для ограничения скорости запросов
@web.middleware
async def rate_limit_middleware(request, handler):
    """Middleware для ограничения скорости запросов к API."""
    # Проверяем, является ли это API-запросом
    if request.path.startswith('/api/'):
        # Получаем IP клиента
        ip = request.remote
        
        # Проверяем, не превышен ли лимит запросов
        is_limited, retry_after = api_rate_limiter.is_rate_limited(ip)
        
        if is_limited:
            # Если лимит превышен, возвращаем ошибку 429 (Too Many Requests)
            return web.json_response(
                {
                    "success": False,
                    "error": "Слишком много запросов. Пожалуйста, повторите позже.",
                    "retry_after": retry_after
                },
                status=429,
                headers={"Retry-After": str(retry_after)}
            )
    
    # Продолжаем обработку запроса
    return await handler(request)


# Добавляем маршруты для API сообщений
def setup_api_routes(app):
    """Настраивает маршруты API для веб-сервера."""
    # Добавляем middleware для ограничения скорости запросов
    app.middlewares.append(rate_limit_middleware)
    
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
        os.makedirs(static_dir)
    
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
        
        if not DB_POOL:
            logger.error("Не удалось инициализировать пул соединений с базой данных. Бот не может продолжить работу.")
            return
        
        # База данных теперь инициализируется в Marzban
        logger.info("Подключение к базе данных успешно установлено.")
        
        # Не запускаем обработку отложенных бонусов (функция удалена)
    except Exception as db_error:
        logger.error(f"Критическая ошибка при инициализации пула соединений с базой данных: {db_error}")
        logger.error(traceback.format_exc())


async def on_shutdown(bot: Bot):
    """Обработчик события остановки бота."""
    logger.info("Остановка бота...")
    
    # Отключаем вебхук
    await bot.delete_webhook()
    
    # Закрываем соединение с базой данных
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
            pass
        await site.start()
        
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        logger.error(traceback.format_exc())
    finally:
        if 'runner' in locals():
            await runner.cleanup()



async def activate_test_period(user_id: int) -> Optional[str]:
    """Активирует тестовый период для пользователя."""
    try:
        # Получаем токен доступа
        token = await get_access_token()
        if not token:
            return None
        
        # Вместо просто проверки наличия пользователя создаем новый ключ
        # Используем существующую функцию create_vless_user, которая создает ключ на DEFAULT_TEST_PERIOD дней
        vpn_link = await create_vless_user(user_id)
        
        if vpn_link:
            
            # Получаем имя пользователя из ссылки
            try:
                link_parts = vpn_link.split('vless://')[1].split('@')[0]
                marzban_username = link_parts.split('?')[0]  # Попытка выделить имя пользователя
                
                # Устанавливаем связь с пользователем Telegram через API (дополнительная попытка)
                if marzban_username:
                    await link_telegram_user_to_marzban(marzban_username, user_id, token)
            except Exception as e:
                logger.error(f"Ошибка при извлечении имени пользователя из ссылки: {e}")
            
            # Обновляем статус тестового периода через API
            api_base_url = f"{MARZBAN_URL}api"
            user_url = f"{api_base_url}/telegram_user/{user_id}"
            headers = {"Authorization": f"Bearer {token}"}
            update_data = {"test_period": False}  # Отмечаем, что тестовый период использован
            
            result = await async_api_request("put", user_url, headers=headers, json_data=update_data)
            
            if result["success"]:   
                pass
            else:
                logger.error(f"Не удалось обновить статус тестового периода через API: {result['status_code']} {result['error']}")
            
            return vpn_link
            
        logger.error(f"Не удалось создать ключ для тестового периода пользователя {user_id}")
        return None
    except Exception as e:
        logger.error(f"Ошибка при активации тестового периода: {e}")
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
        async with DB_POOL.acquire() as conn:
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
        async with DB_POOL.acquire() as conn:
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
                logger.error(f"Ошибка отправки сообщения пользователь {user_id}: {e}")
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
    """Сохраняет информацию о платеже через API."""
    try:
        payment_obj = payment_data['object']
        payment_method = payment_obj['payment_method']
        user_id = int(payment_obj['metadata']['user_id'])
        
        # Маппинг методов оплаты YooKassa к допустимым значениям API
        payment_method_mapping = {
            'yoo_money': 'yoomoney',
            'bank_card': 'card',
            'sbp': 'sbp',
            'qiwi': 'qiwi',
            'webmoney': 'webmoney',
            'cash': 'cash'
        }
        
        # Определение метода оплаты с использованием маппинга или значения по умолчанию
        method_type = payment_method['type']
        api_payment_method = payment_method_mapping.get(method_type, 'other')
        
        # Формируем данные для сохранения платежа
        payment_save_data = {
            "payment_id": payment_obj['id'],
            "user_id": user_id,
            "amount": float(payment_obj['amount']['value']),
            "income_amount": float(payment_obj['income_amount']['value']) if 'income_amount' in payment_obj else None,
            "status": payment_obj['status'],
            "description": payment_obj['description'],
            "payment_method": api_payment_method,
            "payment_method_details": str(payment_method),
            "payment_metadata": str(payment_obj['metadata']),
            "captured_at": payment_obj.get('captured_at')
        }
        
        # Используем async_api_request для отправки запроса
        result = await async_api_request(
            "post",
            f"{MARZBAN_URL}api/payments/save",
            json_data=payment_save_data
        )
        
        # Проверяем успешность операции
        if result and result.get("status_code") == 200:
            # Если ответ успешный, возвращаем успех
            return {"success": True, "payment_id": payment_obj['id'], "result": result}
        else:
            logger.error(f"Ошибка сохранения платежа {payment_obj['id']}: {result}")
            return {"success": False, "error": "Failed to save payment"}
    
    except Exception as e:
        logger.error(f"Ошибка при сохранении платежа: {e}")
        return {"success": False, "error": str(e)}

# Обработчик вебхука от YooKassa
async def handle_yookassa_webhook(request: web.Request):
    """Обработчик вебхука от YooKassa для обработки платежей."""
    try:
        # Получаем IP-адрес клиента
        client_ip = request.remote
        # Проверяем, что запрос пришел с IP-адреса ЮKassa
        if not is_ip_allowed(client_ip):
            logger.error(f"Запрос с недопустимого IP: {client_ip}")
            return web.json_response({"status": "error", "message": "Invalid IP address"}, status=403)
        
        # Парсим JSON из тела запроса
        data = await request.json()
        
        # Проверяем тип события
        event_type = data.get('event')
        if event_type != 'payment.succeeded':
            return web.json_response({"status": "ignored"})
        
        # Сохраняем информацию о платеже через API
        save_result = await save_payment(data)
        
        if not save_result or not save_result.get("success", False):
            logger.error(f"Ошибка при обработке вебхука платежа: {save_result}")
            return web.json_response({
                "status": "error", 
                "message": save_result.get("error", "Unknown error during payment saving")
            }, status=500)
        
        # Получаем ID платежа и метаданные из данных
        payment_id = data['object']['id']
        metadata = data['object']['metadata']
        user_id = int(metadata.get('user_id'))
        subscription_duration = metadata.get('subscription_duration')
        amount_value = float(data['object']['amount']['value'])
        
        # Преобразуем сумму платежа в целое число (если balance INT)
        payment_amount = int(amount_value)
        
        # Проверяем статус платежа через API ЮKassa
        payment = Payment.find_one(payment_id)
        if payment.status != 'succeeded':
            return web.json_response({"status": "ignored"})
        
        # Выполняем все операции: создание/продление, отправку уведомления
        result = await process_successful_payment(user_id, payment_amount, metadata)
        
        if result and result.get("success", False):
            return web.json_response({"status": "success", "result": result})
        else:
            logger.error(f"Ошибка при обработке платежа {payment_id}: {result.get('error', 'Unknown error')}")
            return web.json_response({"status": "error", "message": result.get("error", "Unknown error")})
    
    except Exception as e:
        logger.error(f"Ошибка в обработчике вебхука: {e}")
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
        
        # Отладочная информация
        logger.critical(f"[PAYMENT DEBUG] Попытка создания пользователя в Marzban API: {MARZBAN_URL}")
        logger.critical(f"[PAYMENT DEBUG] Имя пользователя для создания: {marzban_username}")
        
        # Создаем пользователя через базовую функцию
        result = await create_marzban_user_basic(marzban_username, days, token)
        
        if not result["success"]:
            logger.critical(f"[PAYMENT DEBUG] Ошибка при создании пользователя: {result['error']}")
            return {"success": False, "error": result["error"]}
        
        if not result["links"]:
            return {"success": False, "error": "Пользователь создан, но не удалось получить ссылку"}
        
        vless_link = result["links"][0]
        
        # Устанавливаем связь с пользователем Telegram
        link_success = await link_telegram_user_to_marzban(marzban_username, user_id, token)
        if not link_success:
            logger.warning(f"Не удалось установить связь с Telegram ID {user_id} для пользователя {marzban_username}")
        
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
    """Продлевает существующий VPN ключ пользователя."""
    try:
        token = await get_access_token()
        if not token:
            return {"success": False, "error": "Не удалось получить токен доступа"}
        
        # Сначала получаем информацию о пользователе
        url = f"{MARZBAN_URL}api/user/{username}"
        headers = {"Authorization": f"Bearer {token}"}
        
        user_result = await async_api_request("get", url, headers=headers)
        
        if not user_result["success"]:
            return {"success": False, "error": "Пользователь не найден"}
            
        current_user = user_result["data"]
            
        # Вычисляем новую дату окончания
        now = int(datetime.now().timestamp())
        
        # Если срок уже истек, устанавливаем новую дату от текущего момента
        if "expire" not in current_user or not current_user["expire"] or current_user["expire"] < now:
            new_expire = now + (days * 24 * 3600)
        else:
            # Иначе добавляем дни к текущей дате окончания
            new_expire = current_user["expire"] + (days * 24 * 3600)
        
        # Обновляем пользователя через API
        new_expire_date = datetime.fromtimestamp(new_expire).strftime('%Y-%m-%d %H:%M:%S')
        
        update_data = {"expire": new_expire}
        update_result = await async_api_request("put", url, headers=headers, json_data=update_data)
        
        if not update_result["success"]:
            return {"success": False, "error": f"Не удалось обновить пользователя: {update_result['error']}"}
            
        # Устанавливаем связь с пользователем Telegram через API
        # Получаем ID пользователя Telegram из username (формат: random_12345)
        try:
            telegram_id = int(username.split('_')[-1])
            await link_telegram_user_to_marzban(username, telegram_id, token)
        except Exception as e:
            logger.error(f"Ошибка при установке связи с Telegram пользователем при продлении: {e}")
        
        # Получаем обновленного пользователя для получения ссылки
        updated_result = await async_api_request("get", url, headers=headers)
        link = None
        
        if updated_result["success"] and "links" in updated_result["data"] and updated_result["data"]["links"]:
            link = updated_result["data"]["links"][0]
        
        return {
            "success": True,
            "username": username,
            "new_expire_date": new_expire_date,
            "link": link,
            "days": days
        }
        
    except Exception as e:
        logger.error(f"Ошибка при продлении ключа {username}: {e}")
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}

# Функция для проверки JWT токена Marzban

# Функция для получения списка пользователей с ключами VPN через API
async def get_telegram_users_with_keys():
    """Получает список пользователей Telegram с ключами VPN."""
    try:
        # Формируем URL для запроса к API
        api_base_url = f"{MARZBAN_URL}api"
        url = f"{api_base_url}/telegram_users_with_keys"
        
        # Получаем токен доступа к API
        token = await get_access_token()
        if not token:
            logger.error("Не удалось получить токен для получения списка пользователей")
            return []
            
        # Выполняем запрос к API
        headers = {"Authorization": f"Bearer {token}"}
        params = {"limit": 1000}  # Ограничиваем выборку 1000 пользователями
        
        result = await async_api_request("get", url, headers=headers, params=params)
        
        if result["success"]:
            data = result["data"]
            return data
        else:
            logger.error(f"Ошибка при получении списка пользователей: {result['status_code']} {result['error']}")
            return []
    except Exception as e:
        logger.error(f"Ошибка при получении пользователей с VPN-ключами: {e}")
        logger.error(traceback.format_exc())
        return []

def validate_username(username: str) -> bool:
    """
    Проверяет безопасность имени пользователя.
    Имя должно содержать только буквы, цифры, дефисы и нижние подчеркивания.
    """
    if not username:
        return False
    
    # Проверяем длину (не слишком короткое и не слишком длинное)
    if len(username) < 3 or len(username) > 50:
        return False
    
    # Проверяем допустимые символы (буквы, цифры, дефис, нижнее подчеркивание)
    import re
    pattern = r'^[a-zA-Z0-9_-]+$'
    if not re.match(pattern, username):
        return False
    
    # Проверяем отсутствие SQL-инъекций и других опасных паттернов
    dangerous_patterns = [
        'DROP', 'DELETE', 'UPDATE', 'INSERT', 
        'SELECT', 'UNION', 'OR ', 'AND ', 
        '1=1', '1 = 1', '--', ';', "'", '"'
    ]
    
    upper_username = username.upper()
    for pattern in dangerous_patterns:
        if pattern in upper_username:
            return False
    
    return True


# === Обработчики для реферальных бонусов ===
@dp.callback_query(lambda c: c.data.startswith("apply_bonus_"))
async def apply_bonus_to_key_handler(callback: CallbackQuery):
    """Обрабатывает выбор ключа для применения бонуса."""
    user_id = callback.from_user.id
    
    try:
        # Сразу отправляем уведомление о начале обработки
        await callback.answer("Применяем бонус к ключу...")
        
        # Получаем данные из callback_data
        parts = callback.data.split("_")
        bonus_id = int(parts[2])
        key_name = parts[3]
        
        
        # Получаем информацию о бонусе
        async with DB_POOL.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                query = "SELECT * FROM pending_bonuses WHERE id = %s"
                await cursor.execute(query, (bonus_id,))
                bonus_info = await cursor.fetchone()
                
                if not bonus_info:
                    await callback.answer("Бонус не найден или уже был применен", show_alert=True)
                    return
                
                bonus_days = bonus_info['bonus_days']
                
                # Продлеваем ключ
                extend_result = await extend_marzban_user(key_name, bonus_days)
                
                if extend_result["success"]:
                    # Всегда отправляем новое сообщение вместо редактирования
                    success_message = (
                        f"✅ <b>Бонус успешно применен!</b>\n\n"
                        f"Ваш ключ <code>{key_name}</code> был продлен на {bonus_days} дней.\n"
                        f"Новая дата окончания: {extend_result['new_expire_date']}"
                    )
                    
                    await bot.send_message(
                        user_id,
                        success_message,
                        parse_mode="HTML",
                        reply_markup=get_back_to_menu_keyboard()
                    )
                    
                    # Удаляем бонус из таблицы ожидающих
                    delete_query = "DELETE FROM pending_bonuses WHERE id = %s"
                    await cursor.execute(delete_query, (bonus_id,))
                    await conn.commit()
                    
                else:
                    error_message = f"Ошибка при применении бонуса: {extend_result.get('error', 'Неизвестная ошибка')}"
                    await callback.answer(error_message, show_alert=True)
                    logger.error(f"Ошибка при применении бонуса {bonus_id} к ключу {key_name}: {extend_result.get('error')}")
    except Exception as e:
        logger.error(f"Ошибка при применении бонуса к ключу: {e}")
        logger.error(traceback.format_exc())
        await callback.answer("Произошла ошибка при применении бонуса", show_alert=True)


@dp.callback_query(lambda c: c.data.startswith("cancel_bonus_"))
async def cancel_bonus_handler(callback: CallbackQuery):
    """Обрабатывает отмену применения бонуса."""
    user_id = callback.from_user.id
    
    try:
        # Сразу отправляем уведомление о начале обработки
        await callback.answer("Операция отменена")
        
        # Возвращаем меню партнерской программы
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Вернуться в партнерскую программу", callback_data="affiliate")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main")]
        ])
        
        # Всегда отправляем новое сообщение вместо редактирования
        await bot.send_message(
            user_id,
            "🔄 <b>Вы отменили применение бонуса.</b>\n\n"
            "Вы можете вернуться в партнерскую программу или выбрать другой раздел из главного меню.",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка при отмене бонуса: {e}")
        logger.error(traceback.format_exc())
        await callback.answer("Произошла ошибка при отмене бонуса", show_alert=True)


# Обработчик выбора ключа для применения бонуса
@dp.callback_query(lambda c: c.data.startswith("apply_bonus_"))
async def apply_bonus_to_key_handler(callback: CallbackQuery):
    """Обрабатывает выбор ключа для применения бонуса."""
    user_id = callback.from_user.id
    
    try:
        # Получаем данные из callback_data
        parts = callback.data.split("_")
        bonus_id = int(parts[2])
        key_name = parts[3]
        
        # Получаем информацию о бонусе
        async with DB_POOL.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                query = "SELECT * FROM pending_bonuses WHERE id = %s"
                await cursor.execute(query, (bonus_id,))
                bonus_info = await cursor.fetchone()
                
                if not bonus_info:
                    await callback.answer("Бонус не найден или уже был применен", show_alert=True)
                    return
                
                bonus_days = bonus_info['bonus_days']
                
                # Продлеваем ключ
                extend_result = await extend_marzban_user(key_name, bonus_days)
                
                if extend_result["success"]:
                    # Обновляем сообщение
                    await callback.message.edit_text(
                        f"✅ <b>Бонус успешно применен!</b>\n\n"
                        f"Ваш ключ <code>{key_name}</code> был продлен на {bonus_days} дней.\n"
                        f"Новая дата окончания: {extend_result['new_expire_date']}",
                        parse_mode="HTML",
                        reply_markup=get_back_to_menu_keyboard()
                    )
                    
                    # Удаляем бонус из таблицы ожидающих
                    delete_query = "DELETE FROM pending_bonuses WHERE id = %s"
                    await cursor.execute(delete_query, (bonus_id,))
                    
                    # Обновляем выбранный ключ пользователя
                    update_query = "UPDATE users SET selected_key = %s WHERE id = %s"
                    await cursor.execute(update_query, (key_name, bonus_info['user_id']))
                    
                    await conn.commit()
                    
                else:
                    await callback.answer(
                        f"Ошибка при применении бонуса: {extend_result.get('error', 'Неизвестная ошибка')}",
                        show_alert=True
                    )
                    logger.error(f"Ошибка при применении бонуса {bonus_id} для ключа {key_name}: {extend_result.get('error', 'Неизвестная ошибка')}")
    
    except Exception as e:
        logger.error(f"Ошибка при применении бонуса к ключу: {e}")
        logger.error(traceback.format_exc())
        await callback.answer("Произошла ошибка при применении бонуса", show_alert=True)


async def get_telegram_user(user_id: int):
    """Получение пользователя Telegram через API.
    
    Args:
        user_id: ID пользователя в Telegram
        
    Returns:
        dict: Информация о пользователе или None, если пользователь не найден
    """
    try:
        token = await get_access_token()
        if not token:
            return None
            
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{MARZBAN_URL}api/telegram_user/{user_id}"
        
        response = await async_api_request("GET", url, headers=headers)
        
        if response and response.get("success") == True:
            return response.get("data")
            
        return None
    except Exception as e:
        logger.error(f"Ошибка при получении данных пользователя {user_id}: {e}")
        return None


async def find_user_by_referral_code(referral_code: str) -> Optional[int]:
    """Ищет пользователя Telegram по реферальному коду.
    
    Args:
        referral_code: Реферальный код пользователя
        
    Returns:
        int: ID пользователя Telegram или None, если пользователь не найден
    """
    try:
        token = await get_access_token()
        if not token:
            logger.error("[REFERRAL_DEBUG] Не удалось получить токен доступа для поиска по коду")
            return None
            
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{MARZBAN_URL}api/telegram_user/by_code/{referral_code}"
        
        response = await async_api_request("GET", url, headers=headers)
        
        if response and response.get("success") == True:
            user_data = response.get("data", {})
            user_id = user_data.get("user_id")
            
            if user_id:
                return user_id
            else:
                logger.warning(f"[REFERRAL_LOG] Пользователь найден по коду, но отсутствует user_id: {response}")
        else:
            logger.warning(f"[REFERRAL_LOG] Не удалось найти пользователя по коду {referral_code}: {response}")
            
        return None
    except Exception as e:
        logger.error(f"[REFERRAL_DEBUG] Ошибка при поиске пользователя по коду {referral_code}: {e}")
        logger.error(traceback.format_exc())
        return None


# Функция для получения платежей пользователя
async def get_user_payments(user_id: int, limit: int = 5):
    """Получает список платежей пользователя через API."""
    try:
        # Используем async_api_request для получения данных
        params = {"limit": limit}
        result = await async_api_request(
            "get",
            f"{MARZBAN_URL}api/payments/user/{user_id}",
            params=params
        )
        
        if result and result.get("success", True):
            payments = result.get("data", [])
            return payments
        else:
            logger.warning(f"Не удалось получить платежи пользователя {user_id}")
            return []
    
    except Exception as e:
        logger.error(f"Ошибка запроса платежей: {e}")
        return []

# Функция для получения статистики платежей пользователя
async def get_user_payment_summary(user_id: int):
    """Получает сводку о платежах пользователя через API."""
    try:
        # Используем async_api_request для получения данных
        result = await async_api_request(
            "get",
            f"{MARZBAN_URL}api/payments/user/{user_id}/summary"
        )
        
        if result and result.get("success", True):
            summary = result.get("data", {})
            return summary
        else:
            logger.warning(f"Не удалось получить сводку платежей пользователя {user_id}")
            return None
    
    except Exception as e:
        logger.error(f"Ошибка запроса сводки платежей: {e}")
        return None

@dp.message(Command("my_payments"))
async def cmd_my_payments(message: types.Message):
    """Обработчик команды для просмотра истории платежей пользователя."""
    user_id = message.from_user.id
    
    # Получаем последние платежи пользователя
    payments = await get_user_payments(user_id)
    
    if not payments:
        await message.answer("У вас пока нет платежей в системе.")
        return
    
    # Получаем сводку о платежах
    summary = await get_user_payment_summary(user_id)
    
    # Формируем ответ
    text = "📊 <b>Ваши платежи:</b>\n\n"
    
    for payment in payments:
        status_emoji = "✅" if payment.get("status") == "succeeded" else "⏳"
        payment_date = datetime.fromisoformat(payment.get("created_at").replace("Z", "+00:00"))
        formatted_date = payment_date.strftime("%d.%m.%Y %H:%M")
        
        text += f"{status_emoji} <b>ID платежа:</b> {payment.get('payment_id')}\n"
        text += f"<b>Сумма:</b> {payment.get('amount')} ₽\n"
        text += f"<b>Статус:</b> {payment.get('status')}\n"
        text += f"<b>Дата:</b> {formatted_date}\n"
        if payment.get("description"):
            text += f"<b>Описание:</b> {payment.get('description')}\n"
        text += "\n"
    
    # Добавляем сводную информацию, если доступна
    if summary:
        text += "<b>Общая статистика:</b>\n"
        text += f"Всего платежей: {summary.get('total_payments')}\n"
        text += f"Успешных платежей: {summary.get('successful_payments')}\n"
        text += f"Общая сумма: {summary.get('total_spent')} ₽\n"
    
    await message.answer(text, parse_mode="HTML")


if __name__ == "__main__":
    asyncio.run(main())