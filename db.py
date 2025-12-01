from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# ВАЖНО: без точки, т.к. мы не внутри пакета
from agent_config import DB_URL


# Создаём движок БД
engine = create_engine(
    DB_URL,
    echo=False,
    future=True,
)

# Сессии для работы с БД
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)

# Базовый класс моделей
Base = declarative_base()