from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.db import get_db, crud
from app.models.message import MessageTaskCreate, MessageTaskResponse, MessageResponse, SendMessageRequest
from typing import List, Optional, Dict, Any
import logging
import requests
from decouple import config
from app import scheduler

logger = logging.getLogger("uvicorn.error")
router = APIRouter(tags=["Messages"], prefix="/api/messages")

# Получаем параметры для API бота из переменных окружения
BOT_API_URL = config("BOT_API_URL", default="http://127.0.0.1:8000")
BOT_API_TOKEN = config("BOT_API_TOKEN", default="")

# Проверка конфигурации бота
if not BOT_API_TOKEN:
    logger.warning("BOT_API_TOKEN не настроен. Отправка сообщений будет недоступна.")

# Функция для отправки сообщения через API бота
async def send_message_via_bot_api(text: str, all_users: bool = True, user_ids: List[int] = None, image_data: bytes = None):
    """Отправляет сообщение через HTTP API бота"""
    try:
        url = f"{BOT_API_URL}/api/send_message"
        headers = {"Authorization": f"Bearer {BOT_API_TOKEN}"}
        
        # Подготовка данных запроса
        data = {
            "text": text,
            "all_users": all_users,
            "user_ids": user_ids or []
        }
        
        # Если есть изображение, отправляем multipart запрос
        if image_data:
            # Важно: установите правильное имя файла с расширением
            files = {'image': ('image.jpg', image_data, 'image/jpeg')}
            response = requests.post(url, headers=headers, data=data, files=files)
        else:
            response = requests.post(url, headers=headers, json=data)
        
        # Обработка ответа
        if response.status_code == 200:
            result = response.json()
            return True, result.get('message', 'Сообщение отправлено')
        else:
            error_msg = f"Ошибка API бота: {response.status_code} - {response.text}"
            logger.error(error_msg)
            return False, error_msg
            
    except Exception as e:
        error_msg = f"Исключение при отправке сообщения через API бота: {e}"
        logger.error(error_msg)
        return False, error_msg

@router.post("/send", response_model=MessageResponse)
async def send_message(
    message: str = Form(...),
    all_users: bool = Form(True),
    user_ids: Optional[List[int]] = Form(None),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    """Отправить сообщение (и картинку) пользователям через API бота"""
    if not BOT_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
                          detail="Bot API is not configured")

    image_data = None
    if image:
        # Читаем данные изображения
        image_data = await image.read()
    
    # Отправляем сообщение через API бота
    success, message_result = await send_message_via_bot_api(
        text=message,
        all_users=all_users,
        user_ids=user_ids,
        image_data=image_data
    )
    
    if success:
        return MessageResponse(success=True, message=message_result)
    else:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                          detail=message_result)

@router.get("/tasks", response_model=List[MessageTaskResponse])
async def get_message_tasks(
    db: Session = Depends(get_db)
):
    """Получить список всех задач отправки сообщений"""
    return crud.get_message_tasks(db)

@router.get("/system-cron-jobs")
async def get_system_cron_jobs():
    """Получить список всех системных CRON задач из планировщика"""
    try:
        # Получаем все задачи из планировщика
        jobs = []
        for job in scheduler.get_jobs():
            job_info = {
                "id": job.id,
                "name": job.name,
                "func": str(job.func),
                "trigger": str(job.trigger),
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "args": str(job.args) if job.args else None,
                "kwargs": str(job.kwargs) if job.kwargs else None,
                "misfire_grace_time": job.misfire_grace_time,
                "max_instances": job.max_instances,
                "type": str(type(job.trigger)),
            }
            
            # Если это интервальный триггер, добавляем интервал
            if hasattr(job.trigger, 'interval'):
                job_info["interval_seconds"] = job.trigger.interval.total_seconds()
            
            # Если это cron триггер, добавляем cron выражение
            if hasattr(job.trigger, 'fields'):
                try:
                    cron_fields = []
                    for field in job.trigger.fields:
                        if hasattr(field, 'name') and hasattr(field, 'expressions'):
                            expressions = []
                            for expr in field.expressions:
                                if hasattr(expr, 'value'):
                                    expressions.append(str(expr.value))
                            cron_fields.append(f"{field.name}: {','.join(expressions)}")
                    job_info["cron_fields"] = cron_fields
                except Exception as e:
                    job_info["cron_error"] = str(e)
            
            jobs.append(job_info)
        
        return {
            "jobs": jobs,
            "count": len(jobs),
            "scheduler_running": scheduler.running
        }
    except Exception as e:
        logger.error(f"Ошибка при получении системных CRON задач: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Ошибка при получении системных CRON задач: {str(e)}"
        )

@router.post("/tasks", response_model=MessageTaskResponse)
async def create_message_task(
    task: MessageTaskCreate,
    db: Session = Depends(get_db)
):
    """Создать новую задачу отправки сообщений"""
    try:
        return crud.create_message_task(
            db=db,
            task_type=task.task_type,
            cron_expression=task.cron_expression,
            message_text=task.message_text
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/tasks/{task_id}/toggle", response_model=MessageTaskResponse)
async def toggle_message_task(
    task_id: int,
    db: Session = Depends(get_db)
):
    """Включить/выключить задачу отправки сообщений"""
    task = crud.toggle_message_task(db, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task

@router.delete("/tasks/{task_id}", response_model=MessageResponse)
async def delete_message_task(
    task_id: int,
    db: Session = Depends(get_db)
):
    """Удалить задачу отправки сообщений"""
    result = crud.delete_message_task(db, task_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return MessageResponse(success=True, message="Task deleted successfully")