from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.user import UserResponse


class TelegramUserBase(BaseModel):
    """Базовая модель для телеграм пользователя"""
    user_id: int = Field(..., description="Telegram ID пользователя")
    username: Optional[str] = Field(None, description="Username пользователя в Telegram")
    first_name: Optional[str] = Field(None, description="Имя пользователя")
    last_name: Optional[str] = Field(None, description="Фамилия пользователя")
    test_period: bool = Field(False, description="Был ли использован тестовый период")
    referral_code: Optional[str] = Field(None, description="Реферальный код пользователя")


class TelegramUserCreate(TelegramUserBase):
    """Модель для создания нового телеграм пользователя"""
    referrer_code: Optional[str] = Field(None, description="Реферальный код пригласившего пользователя")


class TelegramUserModify(BaseModel):
    """Модель для изменения данных телеграм пользователя"""
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    test_period: Optional[bool] = None
    referral_code: Optional[str] = None


class BonusType(str, Enum):
    """Тип бонуса для реферальной системы"""
    DAYS = "days"


class ReferralBonusBase(BaseModel):
    """Базовая модель для реферального бонуса"""
    amount: float = Field(..., description="Размер бонуса в днях")
    expires_at: Optional[datetime] = Field(None, description="Срок действия бонуса")


class ReferralBonusCreate(ReferralBonusBase):
    """Модель для создания реферального бонуса"""
    telegram_user_id: int = Field(..., description="ID пользователя Telegram, которому начисляется бонус")


class ReferralBonusResponse(ReferralBonusBase):
    """Модель для получения данных о реферальном бонусе"""
    id: int
    telegram_user_id: int
    bonus_type: str = "days"
    is_applied: bool
    created_at: datetime
    applied_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class PaymentResponse(BaseModel):
    """Модель для отображения платежа"""
    payment_id: str
    user_id: int
    amount: float
    income_amount: Optional[float] = None
    status: str
    description: Optional[str] = None
    payment_method: Optional[str] = None
    payment_method_details: Optional[str] = None
    created_at: datetime
    captured_at: Optional[datetime] = None
    payment_metadata: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class TelegramUserResponse(TelegramUserBase):
    """Модель для получения данных телеграм пользователя"""
    id: int
    created_at: datetime
    marzban_users: Optional[List[UserResponse]] = None
    payments: Optional[List[PaymentResponse]] = None
    referrer_id: Optional[int] = None
    referrals: Optional[List['TelegramUserResponse']] = None
    referral_bonuses: Optional[List[ReferralBonusResponse]] = None
    model_config = ConfigDict(from_attributes=True)


class TelegramUsersResponse(BaseModel):
    """Модель для получения списка телеграм пользователей"""
    users: List[TelegramUserResponse]
    total: int


# Вспомогательные функции для работы с БД через SQLAlchemy
async def get_telegram_user(db, user_id: int) -> Optional[TelegramUserResponse]:
    """Получить пользователя Telegram по user_id"""
    from app.db.models import TelegramUser
    
    telegram_user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
    if not telegram_user:
        return None
    
    return TelegramUserResponse.model_validate(telegram_user)


async def get_telegram_users(db, skip: int = 0, limit: int = 100) -> TelegramUsersResponse:
    """Получить список пользователей Telegram с пагинацией"""
    from app.db.models import TelegramUser
    
    total = db.query(TelegramUser).count()
    users = db.query(TelegramUser).order_by(TelegramUser.created_at.desc()).offset(skip).limit(limit).all()
    
    return TelegramUsersResponse(
        users=[TelegramUserResponse.model_validate(user) for user in users],
        total=total
    )


async def create_telegram_user(db, user: TelegramUserCreate) -> TelegramUserResponse:
    """Создать нового пользователя Telegram"""
    from app.db.models import TelegramUser
    
    db_user = TelegramUser(
        user_id=user.user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        test_period=user.test_period,
        referral_code=user.referral_code
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return TelegramUserResponse.model_validate(db_user)


async def update_telegram_user(db, user_id: int, user: TelegramUserModify) -> Optional[TelegramUserResponse]:
    """Обновить данные пользователя Telegram"""
    from app.db.models import TelegramUser
    
    db_user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
    if not db_user:
        return None
    
    update_data = user.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_user, key, value)
    
    db.commit()
    db.refresh(db_user)
    
    return TelegramUserResponse.model_validate(db_user)


# Функция для использования в боте, когда он работает в отдельном контейнере
def create_connection_function(db_config):
    """
    Создаёт функцию для подключения к базе данных.
    
    Пример использования:
    
    db_config = {
        'host': 'db',
        'port': 3306,
        'user': 'marzban',
        'password': 'marzban',
        'db': 'marzban'
    }
    
    get_db = create_connection_function(db_config)
    
    # Затем в вашем обработчике:
    @dp.message(Command("stats"))
    async def send_stats(message: types.Message):
        with get_db() as db:
            users = await get_telegram_users(db)
            await message.answer(f"Всего пользователей: {users.total}")
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    DATABASE_URL = f"mysql+pymysql://{db_config['user']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['db']}"
    
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def get_db():
        db = SessionLocal()
        try:
            return db
        finally:
            db.close()
    
    return get_db 