# models.py
from sqlalchemy import Column, Integer, String, UniqueConstraint
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    
    # This is the unique API key we will give to the client
    api_key = Column(String, unique=True, index=True, nullable=False)
    
    # This is the permanent token we store to act on their behalf
    refresh_token = Column(String, nullable=False)

    __table_args__ = (UniqueConstraint('email'), UniqueConstraint('api_key'))