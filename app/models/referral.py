from datetime import datetime
from typing import Dict, List, Optional, Union, Any
from pydantic import BaseModel, Field, validator


# Базовые модели ответов
class BaseResponse(BaseModel):
    status: str = "success"
    message: Optional[str] = None


# Модели для реферальных кодов
class CreateReferralCodeResponse(BaseResponse):
    data: Dict[str, str] = {"referral_code": ""}


# Модели для применения реферальных кодов
class ApplyReferralResponse(BaseResponse):
    data: Dict[str, Any]


# Модели для структуры рефералов
class ReferralUserInfo(BaseModel):
    id: int
    user_id: int
    username: Optional[str] = None
    created_at: str


class ReferralStructureResponse(BaseResponse):
    data: List[Dict[str, Any]] = []


# Модели для работы с бонусами
class ReferralBonusInfo(BaseModel):
    id: int
    amount: float
    bonus_type: str = "days"
    is_applied: bool = False
    created_at: str
    applied_at: Optional[str] = None
    expires_at: Optional[str] = None


class ReferralBonusResponse(BaseResponse):
    data: List[Dict[str, Any]] = []


class ReferralBonusCreate(BaseModel):
    amount: float = Field(..., gt=0, description="Размер бонуса (количество дней)")
    bonus_type: str = "days"
    valid_days: Optional[int] = Field(90, gt=0, description="Срок действия бонуса в днях (по умолчанию 90)")

    @validator('amount')
    def amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Размер бонуса должен быть положительным числом')
        return v 