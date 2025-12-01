from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from db import SessionLocal
from models import Proxy


def get_next_proxy_from_db() -> Optional[str]:
    """
    Возвращает строку адреса прокси (address) из БД
    с минимальным fail_count и самым старым last_used_at.
    """
    db: Session = SessionLocal()
    try:
        stmt = (
            select(Proxy)
            .where(Proxy.is_active == True)  # noqa: E712
            .order_by(
                Proxy.fail_count.asc(),
                Proxy.last_used_at.is_(None).desc(),
                Proxy.last_used_at.asc(),
            )
            .limit(1)
        )
        result = db.execute(stmt).scalars().first()
        if not result:
            return None

        result.last_used_at = datetime.datetime.utcnow()
        db.commit()
        db.refresh(result)
        return result.address
    finally:
        db.close()


def mark_proxy_fail(address: str):
    """Увеличиваем счётчик ошибок у конкретного прокси."""
    db: Session = SessionLocal()
    try:
        proxy = db.query(Proxy).filter(Proxy.address == address).first()
        if not proxy:
            return
        proxy.fail_count += 1
        db.commit()
    finally:
        db.close()


def reset_proxy_fail(address: str):
    """Сбрасываем счётчик ошибок (если прокси успешно отработал)."""
    db: Session = SessionLocal()
    try:
        proxy = db.query(Proxy).filter(Proxy.address == address).first()
        if not proxy:
            return
        proxy.fail_count = 0
        db.commit()
    finally:
        db.close()