"""Database setup and session management."""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

def get_engine(db_path: str = "data/minibook.db"):
    """Create database engine."""
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", echo=False)

def init_db(db_path: str = "data/minibook.db"):
    """Initialize database and return session maker."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
