from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.db.crud import get_telegram_user_by_id
from app.db.models import Payment, TelegramUser
from app.db import Session, get_db
from app.models.payment import (
    PaymentSave, 
    PaymentResponse, 
    PaymentFilter, 
    PaymentStatistics,
    PaymentSummary
)
from app.utils.payment_helpers import (
    get_payment_stats_by_period,
    get_user_payments_count,
    get_user_total_spent
)

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.post("/save")
async def save_payment(payment_data: PaymentSave, db: Session = Depends(get_db)):
    """
    Сохранение информации о платеже
    """
    # Проверяем обязательные поля
    required_fields = ["payment_id", "user_id", "amount", "status"]
    for field in required_fields:
        if not getattr(payment_data, field):
            return JSONResponse(
                status_code=400,
                content={"detail": f"Missing required field: {field}"}
            )
    
    # Проверяем существование пользователя
    user = get_telegram_user_by_id(db, payment_data.user_id)
    if not user:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Telegram user with ID {payment_data.user_id} not found"}
        )
    
    # Проверяем существование платежа с таким ID
    existing_payment = db.query(Payment).filter(
        Payment.payment_id == payment_data.payment_id
    ).first()
    
    try:
        if existing_payment:
            # Обновляем существующий платеж
            for key, value in payment_data.dict().items():
                if value is not None:
                    setattr(existing_payment, key, value)
            
            db.commit()
            
            # Возвращаем полную информацию о платеже
            return {
                "payment_id": existing_payment.payment_id,
                "user_id": existing_payment.user_id,
                "amount": float(existing_payment.amount),
                "status": existing_payment.status,
                "created_at": existing_payment.created_at,
                "income_amount": float(existing_payment.income_amount) if existing_payment.income_amount else None,
                "description": existing_payment.description,
                "payment_method": existing_payment.payment_method,
                "payment_method_details": existing_payment.payment_method_details,
                "captured_at": existing_payment.captured_at,
                "payment_metadata": existing_payment.payment_metadata,
                "success": True,
                "message": "Payment updated successfully"
            }
        else:
            # Создаем новый платеж
            new_payment = Payment(**payment_data.dict())
            db.add(new_payment)
            db.commit()
            db.refresh(new_payment)
            
            # Возвращаем полную информацию о платеже
            return {
                "payment_id": new_payment.payment_id,
                "user_id": new_payment.user_id,
                "amount": float(new_payment.amount),
                "status": new_payment.status,
                "created_at": new_payment.created_at,
                "income_amount": float(new_payment.income_amount) if new_payment.income_amount else None,
                "description": new_payment.description,
                "payment_method": new_payment.payment_method,
                "payment_method_details": new_payment.payment_method_details,
                "captured_at": new_payment.captured_at,
                "payment_metadata": new_payment.payment_metadata,
                "success": True,
                "message": "Payment saved successfully"
            }
    except IntegrityError as e:
        db.rollback()
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": f"Database integrity error: {str(e)}"
            }
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": f"Error saving payment: {str(e)}"
            }
        )


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(payment_id: str, db: Session = Depends(get_db)):
    """
    Получение информации о платеже по ID
    """
    payment = db.query(Payment).filter(Payment.payment_id == payment_id).first()
    
    if not payment:
        raise HTTPException(
            status_code=404,
            detail=f"Payment with ID {payment_id} not found"
        )
    
    return {
        "success": True,
        "payment_id": payment.payment_id,
        "user_id": payment.user_id,
        "amount": payment.amount,
        "status": payment.status,
        "payment_method": payment.payment_method,
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
        "description": payment.description,
        "metadata": payment.metadata
    }


@router.get("/user/{user_id}", response_model=List[PaymentResponse])
async def get_user_payments(
    user_id: int,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Получение списка платежей пользователя
    """
    # Проверяем существование пользователя
    user = get_telegram_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail=f"Telegram user with ID {user_id} not found"
        )
    
    # Формируем запрос
    query = db.query(Payment).filter(Payment.user_id == user_id)
    
    if status:
        query = query.filter(Payment.status == status)
    
    if start_date:
        query = query.filter(Payment.created_at >= start_date)
    
    if end_date:
        query = query.filter(Payment.created_at <= end_date)
    
    # Добавляем сортировку и пагинацию
    payments = query.order_by(Payment.created_at.desc()).offset(offset).limit(limit).all()
    
    return payments


@router.get("/statistics", response_model=PaymentStatistics)
async def get_payment_statistics(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    """
    Получение статистики платежей за период
    """
    # Устанавливаем значения по умолчанию, если даты не указаны
    if not start_date:
        start_date = datetime.now() - timedelta(days=30)  # За последние 30 дней
    
    if not end_date:
        end_date = datetime.now()
    
    # Получаем статистику
    stats = await get_payment_stats_by_period(db, start_date, end_date)
    
    return stats


@router.get("/user/{user_id}/summary", response_model=PaymentSummary)
async def get_user_payment_summary(
    user_id: int,
    db: Session = Depends(get_db)
):
    """
    Получение сводки о платежах пользователя
    """
    # Проверяем существование пользователя
    user = get_telegram_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail=f"Telegram user with ID {user_id} not found"
        )
    
    # Получаем количество платежей
    total_payments = await get_user_payments_count(db, user_id)
    successful_payments = await get_user_payments_count(db, user_id, successful_only=True)
    
    # Получаем общую сумму платежей
    total_spent = await get_user_total_spent(db, user_id)
    
    # Получаем последний платеж
    last_payment = db.query(Payment).filter(
        Payment.user_id == user_id
    ).order_by(Payment.created_at.desc()).first()
    
    return {
        "user_id": user_id,
        "total_payments": total_payments,
        "successful_payments": successful_payments,
        "failed_payments": total_payments - successful_payments,
        "total_spent": total_spent,
        "last_payment_date": last_payment.created_at if last_payment else None,
        "last_payment_status": last_payment.status if last_payment else None,
        "last_payment_amount": last_payment.amount if last_payment else None
    }


@router.post("/search", response_model=List[PaymentResponse])
async def search_payments(
    filter_data: PaymentFilter,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Поиск платежей по различным критериям
    """
    query = db.query(Payment)
    
    # Применяем фильтры
    if filter_data.user_id:
        query = query.filter(Payment.user_id == filter_data.user_id)
    
    if filter_data.status:
        query = query.filter(Payment.status == filter_data.status)
    
    if filter_data.payment_method:
        query = query.filter(Payment.payment_method == filter_data.payment_method)
    
    if filter_data.min_amount:
        query = query.filter(Payment.amount >= filter_data.min_amount)
    
    if filter_data.max_amount:
        query = query.filter(Payment.amount <= filter_data.max_amount)
    
    if filter_data.start_date:
        query = query.filter(Payment.created_at >= filter_data.start_date)
    
    if filter_data.end_date:
        query = query.filter(Payment.created_at <= filter_data.end_date)
    
    # Добавляем сортировку и пагинацию
    payments = query.order_by(Payment.created_at.desc()).offset(offset).limit(limit).all()
    
    return payments 