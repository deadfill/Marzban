from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from enum import Enum


class PaymentStatus(str, Enum):
    """Статусы платежей"""
    PENDING = "pending"
    WAITING_FOR_CAPTURE = "waiting_for_capture"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentMethod(str, Enum):
    """Методы оплаты"""
    BANK_CARD = "bank_card"
    YOOMONEY = "yoomoney"
    QIWI = "qiwi"
    WEBMONEY = "webmoney"
    SBP = "sbp"
    CASH = "cash"
    OTHER = "other"
    CARD = "card"
    CRYPTO = "crypto"


class PaymentBase(BaseModel):
    """Базовая модель для платежей"""
    model_config = ConfigDict(from_attributes=True)
    
    amount: Decimal = Field(..., description="Сумма платежа", gt=0)
    income_amount: Optional[Decimal] = Field(None, description="Полученная сумма (за вычетом комиссий)")
    status: PaymentStatus = Field(..., description="Статус платежа")
    description: Optional[str] = Field(None, description="Описание платежа")
    payment_method: Optional[PaymentMethod] = Field(None, description="Метод оплаты")
    payment_method_details: Optional[str] = Field(None, description="Детали метода оплаты в JSON формате")
    payment_metadata: Optional[str] = Field(None, description="Метаданные платежа в JSON формате")


class PaymentSave(BaseModel):
    """Модель для сохранения платежа"""
    payment_id: str = Field(..., description="Уникальный идентификатор платежа")
    user_id: int = Field(..., description="ID пользователя Telegram")
    amount: Decimal = Field(..., description="Сумма платежа", gt=0)
    status: PaymentStatus = Field(..., description="Статус платежа")
    income_amount: Optional[Decimal] = Field(None, description="Полученная сумма (за вычетом комиссий)")
    description: Optional[str] = Field(None, description="Описание платежа")
    payment_method: Optional[PaymentMethod] = Field(None, description="Метод оплаты")
    payment_method_details: Optional[str] = Field(None, description="Детали метода оплаты в JSON формате")
    payment_metadata: Optional[str] = Field(None, description="Метаданные платежа в JSON формате")
    captured_at: Optional[datetime] = Field(None, description="Дата успешного списания средств")
    
    @field_validator('payment_id')
    @classmethod
    def validate_payment_id(cls, v: str) -> str:
        if not v:
            raise ValueError("ID платежа не может быть пустым")
        return v


class PaymentUpdate(BaseModel):
    """Модель для обновления платежа"""
    status: Optional[PaymentStatus] = None
    income_amount: Optional[Decimal] = None
    captured_at: Optional[datetime] = None
    payment_method_details: Optional[str] = None
    payment_metadata: Optional[str] = None


class PaymentResponse(BaseModel):
    """Модель для отображения платежа"""
    payment_id: str
    user_id: int
    amount: float
    status: str
    created_at: datetime
    income_amount: Optional[float] = None
    description: Optional[str] = None
    payment_method: Optional[str] = None
    payment_method_details: Optional[str] = None
    captured_at: Optional[datetime] = None
    payment_metadata: Optional[str] = None
    success: Optional[bool] = None
    message: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class PaymentFilter(BaseModel):
    """Фильтр для получения платежей"""
    user_id: Optional[int] = None
    status: Optional[PaymentStatus] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    payment_method: Optional[PaymentMethod] = None


class PaymentMethodStats(BaseModel):
    """Статистика по методу оплаты"""
    count: int = Field(..., description="Количество платежей")
    total: Decimal = Field(..., description="Общая сумма платежей")


class PaymentStatisticsPeriod(BaseModel):
    """Период статистики платежей"""
    start: datetime = Field(..., description="Начальная дата")
    end: datetime = Field(..., description="Конечная дата")


class PaymentStatistics(BaseModel):
    """Статистика платежей"""
    total_count: int = Field(..., description="Общее количество платежей")
    total_sum: Decimal = Field(..., description="Общая сумма платежей")
    total_amount: Decimal = Field(..., description="Общая сумма платежей (устаревшее)")
    successful_count: int = Field(..., description="Количество успешных платежей")
    successful_amount: Decimal = Field(..., description="Сумма успешных платежей")
    average_amount: Decimal = Field(..., description="Средняя сумма платежа")
    period_start: datetime = Field(..., description="Начало периода (устаревшее)")
    period_end: datetime = Field(..., description="Конец периода (устаревшее)")
    payment_methods: Dict[str, PaymentMethodStats] = Field(..., description="Статистика по методам оплаты")
    period: PaymentStatisticsPeriod = Field(..., description="Период статистики")


class PaymentSummary(BaseModel):
    """Сводка о платежах пользователя"""
    user_id: int = Field(..., description="ID пользователя Telegram")
    total_payments: int = Field(..., description="Общее количество платежей")
    total_amount: Decimal = Field(..., description="Общая сумма платежей")
    successful_payments: int = Field(..., description="Количество успешных платежей")
    failed_payments: int = Field(..., description="Количество неуспешных платежей")
    total_spent: Decimal = Field(..., description="Общая сумма успешных платежей")
    last_payment_date: Optional[datetime] = Field(None, description="Дата последнего платежа")
    last_payment_status: Optional[str] = Field(None, description="Статус последнего платежа")
    last_payment_amount: Optional[Decimal] = Field(None, description="Сумма последнего платежа")
    payment_methods: Dict[str, int] = Field(..., description="Количество платежей по методам оплаты") 