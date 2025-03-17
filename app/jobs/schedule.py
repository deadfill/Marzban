from app import scheduler
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from app.jobs.message_tasks import process_message_tasks
import asyncio
import os
import sys
import logging

# Настройка логгера для отладки
logger = logging.getLogger("uvicorn.error")

# Добавляем корень проекта в путь для импорта
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# scheduler = BackgroundScheduler()  # эта строка создает локальный планировщик, который никогда не запускается

# Функция для выполнения задач отправки сообщений
async def run_message_tasks():
    """Запускает обработку задач отправки сообщений."""
    try:
        await process_message_tasks()
    except Exception as e:
        logger.error(f"Ошибка при выполнении задач отправки сообщений: {e}")
        import traceback
        logger.error(traceback.format_exc())

# Обертка для запуска асинхронной функции в планировщике
def run_message_tasks_wrapper():
    """Обертка для запуска асинхронной функции в планировщике."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_message_tasks())
    except Exception as e:
        logger.error(f"Ошибка в обертке запуска задач сообщений: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        loop.close()

# Добавляем задачи в планировщик каждую минуту (вместо 10 минут) для более частой проверки задач
scheduler.add_job(
    run_message_tasks_wrapper,  # Используем обертку вместо асинхронной функции
    'interval',
    minutes=10,
    id='process_message_tasks',
    replace_existing=True,
    misfire_grace_time=300,
    next_run_time=datetime.now(),
    max_instances=3  # Ограничиваем до 3 одновременных запусков
)