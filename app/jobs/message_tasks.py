import asyncio
from datetime import datetime
import croniter
import os
import requests
import json
from app import logger
from app.db import crud, get_db
from sqlalchemy.orm import Session
from sqlalchemy import text
from dotenv import load_dotenv
import logging

# Настройка дополнительного логирования
logger = logging.getLogger("uvicorn.error")

# Загружаем переменные окружения для доступа к токену бота
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Если BOT_TOKEN не найден, ищем в других переменных
if not BOT_TOKEN:
    # Попробуем получить из TELEGRAM_API_TOKEN (как в Marzban)
    BOT_TOKEN = os.getenv('TELEGRAM_API_TOKEN')
    if BOT_TOKEN:
        logger.info("Используем TELEGRAM_API_TOKEN вместо BOT_TOKEN")
    else:
        logger.error("BOT_TOKEN не найден в переменных окружения! Отправка сообщений не будет работать")

# Покажем первые и последние символы токена для диагностики (не показываем весь токен в целях безопасности)
if BOT_TOKEN:
    token_preview = f"{BOT_TOKEN[:5]}...{BOT_TOKEN[-5:]}" if len(BOT_TOKEN) > 10 else "***"
    logger.info(f"Токен бота доступен: {token_preview}")
    
    # Проверяем соединение с API Telegram
    try:
        # Делаем запрос с подробными логами
        logger.info("Проверка доступа к API Telegram...")
        
        # Проверка сетевого соединения с api.telegram.org
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex(('api.telegram.org', 443))
            if result == 0:
                logger.info("✅ Сетевое соединение с api.telegram.org:443 работает!")
            else:
                logger.error(f"❌ Не удалось подключиться к api.telegram.org:443, код ошибки: {result}")
            sock.close()
        except Exception as e:
            logger.error(f"Ошибка при проверке сетевого соединения: {e}")
        
        # Проверка HTTPS соединения
        logger.info("Выполняем тестовый запрос к API Telegram...")
        test_response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10)
        if test_response.status_code == 200:
            bot_info = test_response.json()
            logger.info(f"Соединение с API Telegram успешно! Бот: @{bot_info['result']['username']}")
        else:
            logger.error(f"Ошибка соединения с API Telegram: HTTP {test_response.status_code} - {test_response.text}")
    except Exception as e:
        logger.error(f"Ошибка при проверке API Telegram: {e}")
else:
    logger.error("Токен бота не обнаружен!")

# Функция для отправки сообщения через API бота
async def send_telegram_message(chat_id, text):
    """Отправляет сообщение через API Telegram бота."""
    try:
        # Проверяем наличие токена для API
        if not BOT_TOKEN:
            logger.error(f"BOT_TOKEN не задан! Невозможно отправить сообщение для {chat_id}")
            return False
            
        # Отправка через HTTP API Telegram
        logger.info(f"Отправка сообщения пользователю {chat_id} через HTTP API")
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        
        # Форматируем данные для запроса
        data = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        
        logger.info(f"URL: {url[:30]}..., данные: {{chat_id: {chat_id}, text_length: {len(text)}}}")
        
        try:
            # Добавляем таймаут для избежания зависания при проблемах с сетью
            response = requests.post(url, json=data, timeout=15)
            
            # Проверяем ответ API
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get('ok'):
                    logger.info(f"✅ Сообщение успешно отправлено пользователю {chat_id}")
                    return True
                else:
                    logger.error(f"❌ API вернул ошибку: {resp_json.get('description')}")
                    # Проверяем особые случаи ошибок
                    if "blocked" in resp_json.get('description', '').lower():
                        logger.error(f"Пользователь {chat_id} заблокировал бота или никогда с ним не взаимодействовал")
                    if "chat not found" in resp_json.get('description', '').lower():
                        logger.error(f"Чат с ID {chat_id} не найден")
                    return False
            elif response.status_code == 401:
                logger.error(f"❌ Неверный токен бота. Проверьте токен: {token_preview}")
                return False
            elif response.status_code == 400:
                logger.error(f"❌ Некорректный запрос: {response.text}")
                return False
            elif response.status_code == 403:
                logger.error(f"❌ Пользователь {chat_id} заблокировал бота")
                return False
            else:
                logger.error(f"❌ Ошибка отправки сообщения HTTP {response.status_code}: {response.text}")
                return False
        except requests.exceptions.Timeout:
            logger.error(f"❌ Таймаут соединения с API Telegram")
            return False
        except requests.exceptions.ConnectionError:
            logger.error(f"❌ Ошибка соединения с API Telegram. Проверьте доступ к сети из контейнера.")
            return False
    except Exception as e:
        logger.error(f"❌ Исключение при отправке сообщения для {chat_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

async def process_message_tasks():
    """Обрабатывает CRON задачи отправки сообщений"""
    try:
        logger.info("Начинаем обработку задач сообщений")
        
        db = next(get_db())
        now = datetime.now()
        
        # Получаем все активные задачи
        tasks = crud.get_active_message_tasks(db)
        logger.info(f"Найдено {len(tasks)} активных задач сообщений")
        
        # Создаем список задач для асинхронного выполнения
        processing_tasks = []
        
        for task in tasks:
            # Проверяем, нужно ли выполнить задачу
            if task.next_run and now >= task.next_run:
                logger.info(f"Запланирована задача сообщений {task.id} типа {task.task_type}")
                
                # Получаем следующее время запуска
                cron = croniter.croniter(task.cron_expression, now)
                next_run_time = cron.get_next(datetime)
                
                # Добавляем задачу в список для асинхронного выполнения
                if task.task_type.startswith('expiration_'):
                    processing_tasks.append(process_expiration_notification(db, task))
                else:
                    processing_tasks.append(process_broadcast_notification(db, task))
                
                # Обновляем время следующего запуска
                crud.update_message_task_run_time(db, task.id, now, next_run_time)
        
        # Если есть задачи для выполнения, запускаем их параллельно
        if processing_tasks:
            logger.info(f"Обрабатываем {len(processing_tasks)} задач сообщений параллельно")
            await asyncio.gather(*processing_tasks)
            logger.info("Все задачи сообщений успешно обработаны")
                
    except Exception as e:
        logger.error(f"Ошибка обработки задач сообщений: {e}")
        import traceback
        logger.error(traceback.format_exc())

async def process_expiration_notification(db: Session, task):
    """Обрабатывает уведомления об истечении подписки"""
    try:
        # Определяем количество дней до истечения
        days = 0
        if task.task_type == "expiration_7days":
            days = 7
        elif task.task_type == "expiration_3days":
            days = 3
        elif task.task_type == "expiration_1day":
            days = 1
        
        if days == 0:
            return
        
        # Получаем пользователей с истечением через days дней
        now = datetime.now()
        expiring_users = crud.get_users_by_expiration_days(db, days)
        logger.info(f"Найдено {len(expiring_users)} пользователей с истечением через {days} дней")
        
        # Получаем Telegram ID из таблицы telegram_users
        telegram_users = []
        try:
            # Используем text() для явного объявления SQL-запроса
            sql_query = text("SELECT user_id FROM telegram_users")
            result = db.execute(sql_query)
            telegram_users = [row[0] for row in result.fetchall()]
            logger.info(f"Получено {len(telegram_users)} ID из таблицы telegram_users")
        except Exception as e:
            logger.error(f"Ошибка при получении списка Telegram ID из базы данных: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        # Отправляем сообщение каждому пользователю
        sent_count = 0
        error_count = 0
        no_telegram_id_count = 0
        invalid_id_count = 0
        
        # Сначала отправляем уведомление пользователям с истекающими подписками,
        # у которых есть Telegram ID в таблице admin
        for user in expiring_users:
            # Проверяем стандартный путь - через admin
            if user.admin and user.admin.telegram_id:
                telegram_id = user.admin.telegram_id
                
                try:
                    # Проверяем и преобразуем строковый ID в числовой, если нужно
                    try:
                        # Пытаемся преобразовать в число, если это строка
                        if isinstance(telegram_id, str) and telegram_id.isdigit():
                            telegram_id = int(telegram_id)
                        
                        # Проверка валидности ID (должен быть числом)
                        if not isinstance(telegram_id, (int, str)):
                            invalid_id_count += 1
                            continue
                    except ValueError:
                        invalid_id_count += 1
                        continue
                    
                    # Используем новую функцию отправки через API
                    success = await send_telegram_message(telegram_id, task.message_text)
                    if success:
                        sent_count += 1
                    else:
                        error_count += 1
                except Exception as e:
                    error_count += 1
            else:
                no_telegram_id_count += 1
        
        # Теперь отправляем уведомление всем пользователям из таблицы telegram_users
        # (они могут дублироваться с пользователями выше)
        for telegram_id in telegram_users:
            try:
                # Проверяем и преобразуем строковый ID в числовой, если нужно
                try:
                    # Пытаемся преобразовать в число, если это строка
                    if isinstance(telegram_id, str) and telegram_id.isdigit():
                        telegram_id = int(telegram_id)
                    
                    # Проверка валидности ID (должен быть числом)
                    if not isinstance(telegram_id, (int, str)):
                        invalid_id_count += 1
                        continue
                except ValueError:
                    invalid_id_count += 1
                    continue
                
                # Используем новую функцию отправки через API
                success = await send_telegram_message(telegram_id, task.message_text)
                if success:
                    sent_count += 1
                else:
                    error_count += 1
            except Exception as e:
                error_count += 1
        
        logger.info(f"Результат отправки уведомлений об истечении: отправлено {sent_count}, ошибок {error_count}")
                    
    except Exception as e:
        logger.error(f"Ошибка обработки уведомлений об истечении: {e}")
        import traceback
        logger.error(traceback.format_exc())

async def process_broadcast_notification(db: Session, task):
    """Отправляет сообщение всем пользователям"""
    try:
        # Вместо получения пользователей из таблицы User, получаем Telegram ID из таблицы telegram_users
        # Запрос к таблице telegram_users напрямую
        telegram_users = []
        try:
            # Используем text() для явного объявления SQL-запроса
            sql_query = text("SELECT user_id FROM telegram_users")
            result = db.execute(sql_query)
            telegram_users = [row[0] for row in result.fetchall()]
            logger.info(f"Найдено {len(telegram_users)} пользователей в таблице telegram_users")
        except Exception as e:
            logger.error(f"Ошибка при получении списка Telegram ID из базы данных: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        # Получаем также обычных пользователей (для логирования)
        users = db.query(crud.User).all()
        logger.info(f"Найдено {len(users)} пользователей в таблице users")
        
        # Отправляем сообщение каждому пользователю из telegram_users
        sent_count = 0
        error_count = 0
        invalid_id_count = 0
        
        for telegram_id in telegram_users:
            try:
                # Проверяем и преобразуем строковый ID в числовой, если нужно
                try:
                    # Пытаемся преобразовать в число, если это строка
                    if isinstance(telegram_id, str) and telegram_id.isdigit():
                        telegram_id = int(telegram_id)
                    
                    # Проверка валидности ID (должен быть числом)
                    if not isinstance(telegram_id, (int, str)):
                        invalid_id_count += 1
                        continue
                except ValueError:
                    invalid_id_count += 1
                    continue
                
                # Используем новую функцию отправки через API
                success = await send_telegram_message(telegram_id, task.message_text)
                if success:
                    sent_count += 1
                else:
                    error_count += 1
            except Exception as e:
                error_count += 1
        
        logger.info(f"Результат массовой рассылки: отправлено {sent_count}, ошибок {error_count}")
                    
    except Exception as e:
        logger.error(f"Ошибка обработки массовой рассылки: {e}")
        import traceback
        logger.error(traceback.format_exc()) 