from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.models import Payment, TelegramUser


async def get_user_payments_count(db: Session, user_id: int, successful_only: bool = False) -> int:
    """
    Получение количества платежей пользователя
    
    Args:
        db: Сессия SQLAlchemy
        user_id: ID пользователя Telegram
        successful_only: Учитывать только успешные платежи
        
    Returns:
        Количество платежей
    """
    query = db.query(Payment).filter(Payment.user_id == user_id)
    if successful_only:
        query = query.filter(Payment.status == "succeeded")
    return query.count()


async def get_user_total_spent(db: Session, user_id: int) -> Decimal:
    """
    Получение общей суммы успешных платежей пользователя
    
    Args:
        db: Сессия SQLAlchemy
        user_id: ID пользователя Telegram
        
    Returns:
        Общая сумма платежей
    """
    result = db.query(func.sum(Payment.amount)).filter(
        Payment.user_id == user_id,
        Payment.status == "succeeded"
    ).scalar()
    return result if result else Decimal('0.00')


async def get_payment_stats_by_period(
    db: Session, 
    start_date: datetime, 
    end_date: datetime
) -> Dict[str, Any]:
    """
    Получение статистики платежей за период
    
    Args:
        db: Сессия SQLAlchemy
        start_date: Начальная дата
        end_date: Конечная дата
        
    Returns:
        Словарь со статистикой платежей
    """
    # Общее количество платежей
    total_count = db.query(Payment).filter(
        Payment.created_at.between(start_date, end_date)
    ).count()
    
    # Общая сумма платежей
    total_sum = db.query(func.sum(Payment.amount)).filter(
        Payment.created_at.between(start_date, end_date),
        Payment.status == "succeeded"
    ).scalar() or Decimal('0.00')
    
    # Статистика по методам оплаты
    payment_methods_stats = {}
    payment_methods = db.query(
        Payment.payment_method, 
        func.count(Payment.payment_id).label('count'),
        func.sum(Payment.amount).label('total')
    ).filter(
        Payment.created_at.between(start_date, end_date),
        Payment.status == "succeeded"
    ).group_by(Payment.payment_method).all()
    
    for method, count, total in payment_methods:
        if method:
            payment_methods_stats[method] = {
                "count": count,
                "total": total
            }
    
    return {
        "total_count": total_count,
        "total_sum": total_sum,
        "payment_methods": payment_methods_stats,
        "period": {
            "start": start_date,
            "end": end_date
        }
    }


async def check_user_payment_eligibility(
    db: Session, 
    user_id: int, 
    min_payments: int = 0,
    min_total_spent: Decimal = Decimal('0.00')
) -> Dict[str, Any]:
    """
    Проверка соответствия пользователя критериям по платежам
    
    Args:
        db: Сессия SQLAlchemy
        user_id: ID пользователя Telegram
        min_payments: Минимальное количество успешных платежей
        min_total_spent: Минимальная сумма успешных платежей
        
    Returns:
        Словарь с результатами проверки
    """
    # Проверяем существование пользователя
    user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
    if not user:
        return {
            "eligible": False,
            "error": f"User with ID {user_id} not found"
        }
    
    # Получаем количество успешных платежей
    payments_count = await get_user_payments_count(db, user_id, successful_only=True)
    
    # Получаем общую сумму успешных платежей
    total_spent = await get_user_total_spent(db, user_id)
    
    # Проверяем соответствие критериям
    eligible = payments_count >= min_payments and total_spent >= min_total_spent
    
    return {
        "eligible": eligible,
        "user_id": user_id,
        "payments_count": payments_count,
        "total_spent": total_spent,
        "min_payments_required": min_payments,
        "min_total_spent_required": min_total_spent
    }


async def get_top_paying_users(
    db: Session, 
    limit: int = 10, 
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """
    Получение списка пользователей с наибольшей суммой платежей
    
    Args:
        db: Сессия SQLAlchemy
        limit: Максимальное количество пользователей
        start_date: Начальная дата (опционально)
        end_date: Конечная дата (опционально)
        
    Returns:
        Список пользователей с суммой платежей
    """
    query = db.query(
        Payment.user_id,
        func.sum(Payment.amount).label('total_spent')
    ).filter(
        Payment.status == "succeeded"
    )
    
    if start_date:
        query = query.filter(Payment.created_at >= start_date)
    
    if end_date:
        query = query.filter(Payment.created_at <= end_date)
    
    query = query.group_by(Payment.user_id).order_by(func.sum(Payment.amount).desc()).limit(limit)
    
    results = []
    for user_id, total_spent in query.all():
        # Получаем информацию о пользователе
        user = db.query(TelegramUser).filter(TelegramUser.user_id == user_id).first()
        
        if user:
            results.append({
                "user_id": user_id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "total_spent": total_spent,
                "payments_count": await get_user_payments_count(db, user_id, successful_only=True)
            })
    
    return results


async def get_payment_status_distribution(
    db: Session,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Получение распределения платежей по статусам
    
    Args:
        db: Сессия SQLAlchemy
        start_date: Начальная дата (опционально)
        end_date: Конечная дата (опционально)
        
    Returns:
        Словарь с распределением платежей по статусам
    """
    query = db.query(
        Payment.status,
        func.count(Payment.payment_id).label('count'),
        func.sum(Payment.amount).label('total')
    )
    
    if start_date:
        query = query.filter(Payment.created_at >= start_date)
    
    if end_date:
        query = query.filter(Payment.created_at <= end_date)
    
    query = query.group_by(Payment.status)
    
    results = {}
    for status, count, total in query.all():
        results[status] = {
            "count": count,
            "total": total or Decimal('0.00')
        }
    
    return results 