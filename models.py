# models.py
from sqlalchemy import Column, Integer, String
from database import Base

class TokenStorage(Base):
    __tablename__ = "token_storage"

    id = Column(Integer, primary_key=True, default=1)
    refresh_token = Column(String, nullable=False)