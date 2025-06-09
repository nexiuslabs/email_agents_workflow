from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class TaskOut(BaseModel):
    id: int
    user_id: int
    mail_id: str
    title: str
    detail: str
    due_at: Optional[datetime]
    is_done: bool
    created_at: datetime

    class Config:
        orm_mode = True
