from datetime import datetime
from typing import List, Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app import logger
from app.db import Session, get_db
from app.dependencies import validate_dates
from app.models.admin import Admin
from app.models.telegram_user import (
    TelegramUserCreate,
    TelegramUserModify,
    TelegramUserResponse,
    TelegramUsersResponse,
)
from app.utils import responses
from app.db.models import TelegramUser, User
import httpx
from app.models.user import UserResponse

router = APIRouter(tags=["TelegramUser"], prefix="/api", responses={401: responses._401})


# CRUD функции
def get_telegram_user(db: Session, user_id: int) -> Optional[TelegramUserResponse]:
    """Получить пользователя Telegram по user_id"""
    from app.db.models import TelegramUser
    
    telegram_user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
    if not telegram_user:
        return None
    
    return TelegramUserResponse.model_validate(telegram_user)


def get_telegram_users(db: Session, skip: int = 0, limit: int = 100, 
                      search: str = None, admin: Union[List[str], None] = None) -> tuple:
    """Получить список пользователей Telegram с пагинацией и фильтрацией"""
    from app.db.models import TelegramUser, User
    
    query = db.query(TelegramUser)
    
    # Поиск по имени пользователя или ID
    if search:
        # Проверяем, является ли поисковый запрос числом (для поиска по ID)
        try:
            user_id = int(search)
            query = query.filter(TelegramUser.user_id == user_id)
        except ValueError:
            # Если не число, ищем по имени пользователя (username, first_name, last_name)
            query = query.filter(
                (TelegramUser.username.ilike(f"%{search}%")) |
                (TelegramUser.first_name.ilike(f"%{search}%")) |
                (TelegramUser.last_name.ilike(f"%{search}%"))
            )
    
    # Фильтрация по администратору (если указан)
    if admin:
        query = query.join(TelegramUser.marzban_users).join(User.admin).filter(Admin.username.in_(admin))
    
    # Получаем общее количество записей
    total = query.count()
    
    # Применяем пагинацию
    users = query.order_by(TelegramUser.created_at.desc()).offset(skip).limit(limit).all()
    
    return users, total


def create_telegram_user(db: Session, user: TelegramUserCreate) -> TelegramUserResponse:
    """Создать нового пользователя Telegram"""
    from app.db.models import TelegramUser
    
    db_user = TelegramUser(
        user_id=user.user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        test_period=user.test_period
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return TelegramUserResponse.model_validate(db_user)


def update_telegram_user(db: Session, telegram_user, user: TelegramUserModify) -> TelegramUserResponse:
    """Обновить данные пользователя Telegram"""
    update_data = user.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(telegram_user, key, value)
    
    db.commit()
    db.refresh(telegram_user)
    
    return TelegramUserResponse.model_validate(telegram_user)


def remove_telegram_user(db: Session, telegram_user):
    """Удалить пользователя Telegram"""
    db.delete(telegram_user)
    db.commit()


# Зависимость для проверки существования пользователя
def get_validated_telegram_user(user_id: int, db: Session = Depends(get_db)):
    """Проверяет существование пользователя Telegram и возвращает его"""
    from app.db.models import TelegramUser
    
    telegram_user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
    if telegram_user is None:
        raise HTTPException(
            status_code=404, detail=f"Telegram user with ID {user_id} not found"
        )
    return telegram_user


# API эндпоинты
@router.post("/telegram_user", response_model=TelegramUserResponse, responses={400: responses._400, 409: responses._409})
def add_telegram_user(
    new_user: TelegramUserCreate,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """
    Добавить нового пользователя Telegram
    
    - **user_id**: ID пользователя в Telegram
    - **username**: Имя пользователя в Telegram (опционально)
    - **first_name**: Имя пользователя (опционально)
    - **last_name**: Фамилия пользователя (опционально)
    - **test_period**: Флаг использования тестового периода (по умолчанию False)
    """
    try:
        dbuser = create_telegram_user(db, new_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Telegram user already exists")
    
    user = TelegramUserResponse.model_validate(dbuser)
    logger.info(f'New telegram user "{dbuser.user_id}" added')
    return user


@router.get("/telegram_user/{user_id}", response_model=TelegramUserResponse, responses={404: responses._404})
def get_telegram_user_by_id(
    telegram_user = Depends(get_validated_telegram_user)
):
    """Получить информацию о пользователе Telegram по ID"""
    return TelegramUserResponse.model_validate(telegram_user)


@router.put("/telegram_user/{user_id}", response_model=TelegramUserResponse, responses={400: responses._400, 404: responses._404})
def modify_telegram_user(
    user_id: int,
    modified_user: TelegramUserModify,
    db: Session = Depends(get_db),
    telegram_user = Depends(get_validated_telegram_user),
    admin: Admin = Depends(Admin.get_current),
):
    """
    Изменить существующего пользователя Telegram
    
    - **username**: Имя пользователя в Telegram (опционально)
    - **first_name**: Имя пользователя (опционально)
    - **last_name**: Фамилия пользователя (опционально)
    - **test_period**: Флаг использования тестового периода (опционально)
    """
    updated_user = update_telegram_user(db, telegram_user, modified_user)
    
    logger.info(f'Telegram user "{user_id}" modified')
    return updated_user


@router.delete("/telegram_user/{user_id}", responses={404: responses._404})
def remove_telegram_user_by_id(
    user_id: int,
    db: Session = Depends(get_db),
    telegram_user = Depends(get_validated_telegram_user),
    admin: Admin = Depends(Admin.get_current),
):
    """Удалить пользователя Telegram"""
    remove_telegram_user(db, telegram_user)
    
    logger.info(f'Telegram user "{user_id}" deleted')
    return {"detail": "Telegram user successfully deleted"}


@router.get("/telegram_users", response_model=TelegramUsersResponse, responses={400: responses._400})
def get_all_telegram_users(
    offset: int = 0,
    limit: int = 100,
    search: str = None,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """
    Получить список всех пользователей Telegram
    
    - **offset**: Смещение для пагинации (опционально)
    - **limit**: Максимальное количество пользователей для возврата (опционально)
    - **search**: Поиск по имени пользователя или ID (опционально)
    """
    users, count = get_telegram_users(
        db=db,
        skip=offset,
        limit=limit,
        search=search,
        admin=[admin.username] if not admin.is_sudo else None
    )
    
    return {
        "users": [TelegramUserResponse.model_validate(user) for user in users],
        "total": count
    }


@router.get("/telegram_users_with_keys", response_model=List[dict], responses={400: responses._400})
async def get_telegram_users_with_keys(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
    limit: int = Query(1000, description="Максимальное количество записей для возврата"),
    telegram_id: Optional[int] = Query(None, description="ID пользователя Telegram для фильтрации")
):
    """
    Получить список всех пользователей Telegram с их VPN-ключами
    
    Возвращает расширенный список пользователей, включая информацию о связанных VPN-ключах Marzban и ссылках для подключения
    
    - **limit**: Максимальное количество пользователей для возврата
    - **telegram_id**: ID пользователя Telegram для фильтрации результатов (опционально)
    """

    
    # Получаем всех пользователей с загрузкой связанных Marzban-пользователей
    query = db.query(TelegramUser).options(
        joinedload(TelegramUser.marzban_users)
    )
    
    # Фильтрация по telegram_id, если указан
    if telegram_id is not None:
        query = query.filter(TelegramUser.user_id == telegram_id)
    
    # Фильтрация по администратору (если это не суперпользователь)
    if not admin.is_sudo:
        query = query.join(TelegramUser.marzban_users).join(User.admin).filter(Admin.username == admin.username)
    
    users = query.limit(limit).all()
    
    # Получаем все ссылки пользователей через API напрямую
    from app import xray
    
    result = []
    for user in users:
        if user.marzban_users:
            for marzban_user in user.marzban_users:
                # Для каждого пользователя получаем его ссылки через API
                # Создаем объект UserResponse, который генерирует ссылки
                user_links = []
                try:
                    # Создаем UserResponse объект, который автоматически генерирует ссылки
                    user_response = UserResponse.from_orm(marzban_user)
                    user_links = user_response.links if hasattr(user_response, 'links') else []
                except Exception as e:
                    print(f"Ошибка при получении ссылок для пользователя {marzban_user.username}: {e}")
                
                # Безопасно обрабатываем поле expire, которое может быть либо datetime, либо уже int
                expire_value = None
                if marzban_user.expire:
                    if hasattr(marzban_user.expire, 'timestamp'):
                        # Если это объект datetime
                        expire_value = int(marzban_user.expire.timestamp())
                    elif isinstance(marzban_user.expire, (int, float)):
                        # Если это уже число
                        expire_value = int(marzban_user.expire)
                    else:
                        # Для других неожиданных типов
                        print(f"Неизвестный тип expire для пользователя {marzban_user.username}: {type(marzban_user.expire)}")
                
                result.append({
                    'telegram_id': user.user_id,
                    'telegram_username': user.username,
                    'first_name': user.first_name,
                    'last_name': user.last_name, 
                    'created_at': user.created_at.isoformat() if user.created_at else None,
                    'test_period': user.test_period,
                    'marzban_username': marzban_user.username,
                    'status': marzban_user.status,
                    'expire': expire_value,
                    'data_limit': marzban_user.data_limit,
                    'used_traffic': marzban_user.used_traffic,
                    'links': user_links  # Добавляем ссылки, полученные через UserResponse
                })
        else:
            # Пользователь без VPN-ключей
            result.append({
                'telegram_id': user.user_id,
                'telegram_username': user.username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'test_period': user.test_period,
                'marzban_username': None,
                'status': None,
                'expire': None,
                'data_limit': None,
                'used_traffic': None,
                'links': []  # Пустой список ссылок
            })
    
    return result 