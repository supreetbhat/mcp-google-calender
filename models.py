# models.py
from sqlalchemy import Column, Integer, String
from database import Base

class TokenStorage(Base):
    __tablename__ = "token_storage"

    # We'll only ever have one row, with id=1
    id = Column(Integer, primary_key=True, default=1)
    refresh_token = Column(String, nullable=False)