from fastapi import Request, HTTPException, status, APIRouter
from yookassa import Payment, Configuration
from marzban import MarzbanAPI, UserModify, UserCreate, ProxySettings
from datetime import datetime, timedelta, timezone
import aiomysql
import ipaddress
import logging
import random
from aiogram import Bot
import traceback
import json
import os
from dotenv import load_dotenv
import asyncio

# Загрузка переменных окружения
load_dotenv()

Configuration.account_id = "1022687"  # Замените на ваш shop_id
Configuration.secret_key = "test_FsZWBAm7cB1y7M1d2bv01PfTgYfz65z0QmBi5Bj6NgI"  # Замените на ваш секретный ключ
telegram_bot_token = "7782392038:AAFs12L_n14nEQfW6-Xr9j7Zegun2Qqqiso"
bot = Bot(token=telegram_bot_token)

if not Configuration.account_id or not Configuration.secret_key:
    raise ValueError("Необходимо настроить account_id и secret_key для работы с ЮKassa!")

base_url = "https://xguard.online:8443"
username = "deadfill"
password = "filldead18101990"

router = APIRouter(prefix="/api")

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

def generate_random_string(length=8):
    """Генерирует случайное число заданной длины."""
    min_value = 10 ** (length - 1)  # Минимальное число длины length
    max_value = (10 ** length) - 1   # Максимальное число длины length
    return str(random.randint(min_value, max_value))

# Функция для проверки IP-адреса
def is_ip_allowed(client_ip):
    for ip_range in ALLOWED_IP_RANGES:
        network = ipaddress.ip_network(ip_range)
        if ipaddress.ip_address(client_ip) in network:
            return True
    return False

async def get_access_token():
    try:
        api = MarzbanAPI(base_url=base_url)
        token = await api.get_token(username=username, password=password)
        return token.access_token
    except Exception as e:
            logging.error(f"Ошибка при получении токена: {e}")
    return None

async def create_connection():
    return await aiomysql.connect(
        host='127.0.0.1',
        port=3306,
        user='marzban',  # Замените на ваш MySQL пользователь
        password='filldead18101990',  # Замените на ваш MySQL пароль
        db='marzban'
    )

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
        logging.error(f"Ошибка преобразования даты '{iso_date_str}': {e}")
        # В случае ошибки возвращаем текущее время
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

async def save_payment(payment_data: dict):
    """Сохраняет информацию о платеже в базу данных."""
    try:
        async with await create_connection() as conn:
            async with conn.cursor() as cur:
                payment_obj = payment_data['object']
                payment_method = payment_obj['payment_method']
                user_id = int(payment_obj['metadata']['user_id'])
                
                # Проверяем существование пользователя в базе данных
                await cur.execute("SELECT COUNT(*) FROM telegram_users WHERE user_id = %s", (user_id,))
                user_exists = (await cur.fetchone())[0] > 0
                
                # Если пользователя нет, создаем его автоматически
                if not user_exists:
                    logging.info(f"Пользователь {user_id} не найден, создаем автоматически")
                    await cur.execute(
                        "INSERT INTO telegram_users (user_id, test_period, created_at) VALUES (%s, 1, CURRENT_TIMESTAMP)",
                        (user_id,)
                    )
                    await conn.commit()
                    logging.info(f"Пользователь {user_id} успешно создан")
                
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
                logging.info(f"Платеж {payment_obj['id']} успешно сохранен в базе данных")
    except Exception as e:
        logging.error(f"Ошибка при сохранении платежа: {e}")
        logging.error(traceback.format_exc())

@router.post("/webhook/yookassa")
async def yookassa_webhook(request: Request):
    try:
        # Получаем IP-адрес клиента
        client_ip = request.client.host
        # Проверяем, что запрос пришел с IP-адреса ЮKassa
        if not is_ip_allowed(client_ip):
            logging.error(f"Запрос получен с недопустимого IP-адреса: {client_ip}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid IP address")
        
        # Парсим JSON из тела запроса
        data = await request.json()
        print(data)
        
        # Проверяем тип события
        event_type = data.get('event')
        if event_type != 'payment.succeeded':
            logging.info(f"Получено событие: {event_type}. Ожидалось payment.succeeded.")
            return {"status": "ignored"}
        
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
        
        print(f"Payment ID: {payment_id}, User ID: {user_id}, Amount: {payment_amount}, Subscription Duration: {subscription_duration}")
        
        # Проверяем статус платежа через API ЮKassa
        payment = Payment.find_one(payment_id)
        if payment.status != 'succeeded':
            logging.warning(f"Статус платежа {payment_id} не является succeeded.")
            return {"status": "ignored"}
        
        # Выполняем необходимый код после успешной оплаты
        await process_successful_payment(user_id, payment_amount, metadata)
        return {"status": "success"}
    
    except Exception as e:
        logging.error(f"Ошибка при обработке вебхука: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")

# Инициализация бота (замените 'YOUR_TELEGRAM_BOT_TOKEN' на токен вашего бота)
async def process_successful_payment(user_id: int, payment_amount: int, payment_data: dict):
    """Обработка успешного платежа."""
    action = payment_data.get("action")
    days = int(payment_data.get("days", 0))
    amount = float(payment_data.get("amount", 0))
    
    if action == "new_key":
        token = await get_access_token()
        if not token:
            logging.error("Не удалось получить токен для создания ключа")
            return
            
        try:
            marzban_username = f"{generate_random_string()}_{user_id}"
            
            # Создаем нового пользователя в Marzban
            new_user = UserCreate(
                username=marzban_username,
                proxies={"vless": ProxySettings(flow="xtls-rprx-vision")},
                inbounds={'vless': ['VLESS TCP REALITY']},
                expire=int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
            )
            
            api = MarzbanAPI(base_url=base_url)
            await api.add_user(new_user, token)
            
            # Получаем информацию о созданном пользователе
            user_info = await api.get_user(marzban_username, token)
            vless_link = user_info.links[0]
            
            # Отправляем сообщение пользователю с данными подключения
            await bot.send_message(
                user_id,
                f"✅ <b>Ваш новый ключ создан!</b>\n\n"
                f"🔑 Имя ключа: <code>{marzban_username.split('_')[0]}</code>\n"
                f"📅 Срок действия: <b>{days}</b> дней\n\n"
                f"Ваша ссылка для подключения:\n<code>{vless_link}</code>",
                parse_mode="HTML"
            )
            
        except Exception as e:
            logging.error(f"Ошибка при создании нового ключа: {e}")
            await bot.send_message(
                user_id,
                "❌ Произошла ошибка при создании ключа. Пожалуйста, обратитесь в поддержку.",
                parse_mode="HTML"
            )
    else:
        # Обработка продления существующего ключа
        username = payment_data.get("username")
        token = await get_access_token()
        if not token:
            logging.error("Не удалось получить токен для продления ключа")
            return
            
        try:
            api = MarzbanAPI(base_url=base_url)
            
            # Получаем текущую информацию о пользователе
            user_info = await api.get_user(username, token)
            current_expire = user_info.expire
            
            # Рассчитываем новую дату истечения
            current_time = datetime.now(timezone.utc).timestamp()
            if current_expire < current_time:
                new_expire = int(current_time + timedelta(days=days).total_seconds())
            else:
                new_expire = int(current_expire + timedelta(days=days).total_seconds())
            
            # Обновляем срок действия ключа
            await api.modify_user(
                username=username,
                user=UserModify(expire=new_expire),
                token=token
            )
            
            # Отправляем сообщение пользователю
            new_expire_date = datetime.fromtimestamp(new_expire, tz=timezone.utc).strftime("%d.%m.%Y")
            await bot.send_message(
                user_id,
                f"✅ <b>Ваш ключ успешно продлен!</b>\n\n"
                f"🔑 Ключ: <code>{username.split('_')[0]}</code>\n"
                f"📅 Добавлено дней: <b>{days}</b>\n"
                f"📆 Действует до: <b>{new_expire_date}</b>",
                parse_mode="HTML"
            )
            
            logging.info(f"Успешно продлен ключ {username} на {days} дней")
            
        except Exception as e:
            logging.error(f"Ошибка при продлении ключа {username}: {e}")
            await bot.send_message(
                user_id,
                "❌ Произошла ошибка при продлении ключа. Пожалуйста, обратитесь в поддержку.",
                parse_mode="HTML"
            )