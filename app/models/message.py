from pydantic import BaseModel, validator
from typing import Optional, List, Any
from datetime import datetime
import croniter

class MessageTaskBase(BaseModel):
    task_type: str
    cron_expression: str
    message_text: str
    
    @validator("cron_expression")
    def validate_cron_expression(cls, v):
        try:
            croniter.croniter(v, datetime.now())
        except Exception as e:
            raise ValueError(f"Invalid cron expression: {e}")
        return v

class MessageTaskCreate(MessageTaskBase):
    pass

class MessageTaskResponse(MessageTaskBase):
    id: int
    is_active: bool
    created_at: datetime
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class MessageResponse(BaseModel):
    success: bool
    message: str

class SendMessageRequest(BaseModel):
    user_ids: List[Any] = []
    all_users: bool = False
    message: str 