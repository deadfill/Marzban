from datetime import datetime
import random
import string
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import logger
from app.db import get_db
from app.db.models import TelegramUser, ReferralBonus, Payment
from app.models.admin import Admin
from app.models.telegram_user import (
    ReferralBonusCreate,
    ReferralBonusResponse,
    TelegramUserResponse
)
from app.utils import responses

router = APIRouter(tags=["Referral"], prefix="/api", responses={401: responses._401})


def generate_referral_code(length=8):
    """Генерирует случайный реферальный код"""
    letters = string.ascii_letters + string.digits
    return ''.join(random.choice(letters) for i in range(length))


def get_telegram_user_by_code(db: Session, referral_code: str) -> Optional[TelegramUser]:
    """Получить пользователя Telegram по реферальному коду"""
    return db.query(TelegramUser).filter(TelegramUser.referral_code == referral_code).first()


@router.post("/referral/code/{user_id}", response_model=TelegramUserResponse, responses={404: responses._404})
def generate_referral_code_for_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Генерирует или обновляет реферальный код пользователя"""
    telegram_user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
    if not telegram_user:
        raise HTTPException(status_code=404, detail="Пользователь Telegram не найден")
    
    # Генерируем код, если его нет
    if not telegram_user.referral_code:
        # Генерируем уникальный код
        while True:
            code = generate_referral_code()
            existing_user = get_telegram_user_by_code(db, code)
            if not existing_user:
                telegram_user.referral_code = code
                break
    
    db.commit()
    db.refresh(telegram_user)
    return telegram_user


@router.post("/referral/apply", response_model=TelegramUserResponse, responses={400: responses._400, 404: responses._404})
def apply_referral_code(
    body: dict = Body(...),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Применяет реферальный код к пользователю"""
    # Получаем параметры из тела запроса
    logger.info(f"Получено тело запроса: {body}")
    
    user_id = body.get("user_id")
    referrer_code = body.get("referrer_code")
    auto_bonus_days = body.get("auto_bonus_days")
    
    logger.info(f"Применение реферального кода. user_id={user_id}, referrer_code={referrer_code}, auto_bonus_days={auto_bonus_days}, тип auto_bonus_days: {type(auto_bonus_days)}")
    
    # Проверяем обязательные параметры
    if not user_id:
        logger.error("Отсутствует обязательный параметр user_id")
        raise HTTPException(status_code=422, detail={"user_id": "Field required"})
    if not referrer_code:
        logger.error("Отсутствует обязательный параметр referrer_code")
        raise HTTPException(status_code=422, detail={"referrer_code": "Field required"})
    
    # Проверяем существование пользователя
    user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
    if not user:
        logger.error(f"Пользователь не найден: user_id={user_id}")
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    # Проверяем, что у пользователя еще нет реферера
    if user.referrer_id:
        logger.warning(f"У пользователя {user_id} уже есть реферер")
        raise HTTPException(status_code=400, detail="У пользователя уже есть реферер")
    
    # Находим пользователя по реферальному коду
    referrer = get_telegram_user_by_code(db, referrer_code)
    if not referrer:
        logger.error(f"Реферальный код не найден: {referrer_code}")
        raise HTTPException(status_code=404, detail="Реферальный код не найден")
    
    logger.info(f"Найден реферер: user_id={referrer.user_id}, id={referrer.id}")
    
    # Проверяем, что пользователь не пытается использовать свой собственный код
    if referrer.id == user.id:
        logger.warning(f"Пользователь {user_id} пытается использовать собственный код")
        raise HTTPException(status_code=400, detail="Нельзя использовать собственный реферальный код")
    
    # Устанавливаем реферера для пользователя
    user.referrer_id = referrer.id
    logger.info(f"Установлен реферер {referrer.id} для пользователя {user_id}")
    
    # Если указано количество дней бонуса, создаем бонус для реферера
    try:
        # Преобразуем auto_bonus_days в число
        bonus_days = int(auto_bonus_days) if auto_bonus_days is not None else 0
        logger.info(f"Преобразованное значение auto_bonus_days: {bonus_days}")
        
        if bonus_days > 0:
            logger.info(f"Создаем бонус {bonus_days} дней для реферера {referrer.id}")
            
            # Создаем бонус для реферера
            referrer_bonus = ReferralBonus(
                telegram_user_id=referrer.id,
                amount=bonus_days,
                is_applied=False,
                created_at=datetime.utcnow()
            )
            db.add(referrer_bonus)
            logger.info(f"Бонус успешно создан для реферера {referrer.id}")
        else:
            logger.warning(f"auto_bonus_days не положительное число: {auto_bonus_days}")
    except Exception as e:
        logger.error(f"Ошибка при создании бонуса: {e}")
    
    db.commit()
    db.refresh(user)
    return user


@router.post("/referral/bonus", response_model=ReferralBonusResponse, responses={400: responses._400, 404: responses._404})
def create_referral_bonus(
    bonus: ReferralBonusCreate,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Создает новый реферальный бонус для пользователя"""
    # Проверяем существование пользователя
    user = db.query(TelegramUser).filter(TelegramUser.id == bonus.telegram_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    # Проверяем, что размер бонуса положительный
    if bonus.amount <= 0:
        raise HTTPException(status_code=400, detail="Размер бонуса должен быть положительным числом")
    
    # Создаем новый бонус
    new_bonus = ReferralBonus(
        telegram_user_id=bonus.telegram_user_id,
        amount=bonus.amount,
        is_applied=False,
        created_at=datetime.utcnow(),
        expires_at=bonus.expires_at
    )
    
    db.add(new_bonus)
    db.commit()
    db.refresh(new_bonus)
    return new_bonus


@router.put("/referral/bonus/{bonus_id}/apply", response_model=ReferralBonusResponse, responses={404: responses._404, 400: responses._400})
def apply_referral_bonus(
    bonus_id: int,
    marzban_username: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Применяет реферальный бонус к пользователю"""
    # Находим бонус
    bonus = db.query(ReferralBonus).filter(ReferralBonus.id == bonus_id).first()
    if not bonus:
        raise HTTPException(status_code=404, detail="Бонус не найден")
    
    # Проверяем, что бонус еще не применен
    if bonus.is_applied:
        raise HTTPException(status_code=400, detail="Бонус уже применен")
    
    # Проверяем, что бонус не истек
    if bonus.expires_at and bonus.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Срок действия бонуса истек")
    
    # Проверяем, указан ли пользователь Marzban для применения бонуса
    if not marzban_username:
        # Проверяем, есть ли у пользователя Telegram хотя бы один пользователь Marzban
        telegram_user = db.query(TelegramUser).filter(TelegramUser.id == bonus.telegram_user_id).first()
        if not telegram_user or not telegram_user.marzban_users:
            raise HTTPException(status_code=400, detail="Не указан пользователь Marzban для применения бонуса")
        
        # Используем первого найденного пользователя Marzban
        marzban_user = telegram_user.marzban_users[0]
    else:
        # Ищем указанного пользователя Marzban
        from app.db.models import User
        marzban_user = db.query(User).filter(User.username == marzban_username).first()
        if not marzban_user:
            raise HTTPException(status_code=404, detail=f"Пользователь Marzban {marzban_username} не найден")
        
        # Проверяем, что пользователь Marzban привязан к данному пользователю Telegram
        telegram_user = db.query(TelegramUser).filter(TelegramUser.id == bonus.telegram_user_id).first()
        if marzban_user.telegram_user_id != telegram_user.id:
            raise HTTPException(status_code=400, detail=f"Пользователь Marzban {marzban_username} не принадлежит данному пользователю Telegram")
    
    # Применяем бонус: добавляем дни к сроку действия аккаунта
    days_to_add = int(bonus.amount)
    
    # Если у пользователя есть срок действия, продлеваем его
    if marzban_user.expire:
        # Получаем текущую дату истечения срока действия
        from datetime import datetime, timezone
        current_time = datetime.now(timezone.utc).timestamp()
        
        # Если аккаунт просрочен, устанавливаем срок от текущего времени
        if marzban_user.expire < current_time:
            new_expire = current_time + (days_to_add * 86400)  # 86400 секунд в сутках
        else:
            # Иначе просто добавляем дни к существующему сроку
            new_expire = marzban_user.expire + (days_to_add * 86400)
        
        marzban_user.expire = new_expire
    else:
        # Если у пользователя нет срока действия, устанавливаем новый
        from datetime import datetime, timezone
        current_time = datetime.now(timezone.utc).timestamp()
        marzban_user.expire = current_time + (days_to_add * 86400)
    
    # Отмечаем бонус как примененный
    bonus.is_applied = True
    bonus.applied_at = datetime.utcnow()
    
    db.commit()
    db.refresh(bonus)
    return bonus


@router.get("/referral/bonuses/{user_id}", response_model=List[ReferralBonusResponse], responses={404: responses._404})
def get_user_bonuses(
    user_id: int,
    applied_only: bool = False,
    active_only: bool = False,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Получает список бонусов пользователя"""
    # Проверяем существование пользователя
    user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    # Формируем запрос с фильтрами
    query = db.query(ReferralBonus).filter(ReferralBonus.telegram_user_id == user.id)
    
    if applied_only:
        query = query.filter(ReferralBonus.is_applied == True)
    
    if active_only:
        # Только активные бонусы (не истекшие и не примененные)
        query = query.filter(
            (ReferralBonus.expires_at.is_(None) | (ReferralBonus.expires_at > datetime.utcnow())) &
            (ReferralBonus.is_applied == False)
        )
    
    bonuses = query.all()
    return bonuses


@router.get("/referral/structure/{user_id}", response_model=List[TelegramUserResponse], responses={404: responses._404})
def get_referral_structure(
    user_id: int,
    level: int = 1,  # Глубина получения структуры (1 - только прямые рефералы)
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Получает реферальную структуру пользователя"""
    # Проверяем существование пользователя
    user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    # Получаем только прямых рефералов (уровень 1)
    # Для более глубоких уровней потребуется рекурсивная логика
    referrals = db.query(TelegramUser).filter(TelegramUser.referrer_id == user.id).all()
    
    return referrals


@router.post("/referral/auto-bonus", response_model=ReferralBonusResponse, responses={400: responses._400, 404: responses._404})
def create_referral_auto_bonus(
    user_id: int,
    days: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Автоматически создает бонус в днях для пользователя"""
    # Проверяем существование пользователя
    user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    if days <= 0:
        raise HTTPException(status_code=400, detail="Количество дней должно быть положительным числом")
    
    # Создаем новый бонус для пользователя
    new_bonus = ReferralBonus(
        telegram_user_id=user.id,
        amount=days,
        is_applied=False,
        created_at=datetime.utcnow()
    )
    
    db.add(new_bonus)
    db.commit()
    db.refresh(new_bonus)
    return new_bonus


@router.get("/telegram_user/by_code/{referral_code}", response_model=TelegramUserResponse, responses={404: responses._404})
def get_telegram_user_by_referral_code(
    referral_code: str,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Получает пользователя Telegram по реферальному коду"""
    telegram_user = get_telegram_user_by_code(db, referral_code)
    if not telegram_user:
        raise HTTPException(status_code=404, detail="Пользователь с указанным реферальным кодом не найден")
    
    return telegram_user 