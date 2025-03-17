from fastapi import APIRouter
from . import (
    admin, 
    core, 
    node, 
    subscription, 
    system, 
    user_template, 
    user,
    home,
    message,
    telegram_user,
    referral,
    payment
)

api_router = APIRouter()

routers = [
    admin.router,
    core.router,
    node.router,
    subscription.router,
    system.router,
    user_template.router,
    user.router,
    home.router,
    message.router,
    telegram_user.router,
    referral.router,
    payment.router,
]

for router in routers:
    api_router.include_router(router)

__all__ = ["api_router"]