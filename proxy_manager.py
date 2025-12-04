from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from db import SessionLocal
from models import Proxy


@dataclass
class ProxyLaunchConfig:
    """
    Конфиг прокси для запуска браузера / Playwright.

    server: строка вида "http://host:port" или "socks5://host:port"
    """
    id: int
    label: str
    protocol: str
    server: str
    username: Optional[str]
    password: Optional[str]


def _choose_port(proxy: Proxy) -> Optional[int]:
    """
    Выбираем порт в зависимости от протокола:
      - если protocol начинается с "socks" → берём port_socks5, иначе port_http
      - fallback: если основной пустой, берём другой
    """
    protocol = (proxy.protocol or "http").lower()

    if protocol.startswith("socks"):
        return proxy.port_socks5 or proxy.port_http

    return proxy.port_http or proxy.port_socks5


# ============================================================
#  ВЫБОР ПРОКСИ ДЛЯ ЗАПУСКА БРАУЗЕРА (НОВАЯ ВЕРСИЯ)
# ============================================================

def get_next_proxy_for_launch() -> Optional[ProxyLaunchConfig]:
    """
    Возвращает ProxyLaunchConfig для прокси с минимальным fail_count
    и самым "старым" last_used_at среди активных (is_active = True).

    Если активных прокси нет или у записи некорректные host/port — вернёт None.
    """
    db: Session = SessionLocal()
    try:
        stmt = (
            select(Proxy)
            .where(Proxy.is_active.is_(True))
            .order_by(
                Proxy.fail_count.asc(),
                # сначала те, у кого last_used_at = NULL
                Proxy.last_used_at.is_(None).desc(),
                Proxy.last_used_at.asc(),
                Proxy.id.asc(),
            )
            .limit(1)
        )

        proxy: Proxy | None = db.execute(stmt).scalars().first()
        if not proxy:
            print("[PROXY] Активных прокси в БД не найдено")
            return None

        port = _choose_port(proxy)
        protocol = (proxy.protocol or "http").lower()

        if not proxy.host or not port:
            print(
                f"[PROXY] Прокси id={proxy.id} имеет пустой host/port "
                f"(host={proxy.host}, port={port}) — пропускаю"
            )
            return None

        server = f"{protocol}://{proxy.host}:{port}"
        label = proxy.label or f"{proxy.host}:{port}"

        # отмечаем использование
        proxy.last_used_at = datetime.utcnow()
        db.add(proxy)
        db.commit()
        db.refresh(proxy)

        cfg = ProxyLaunchConfig(
            id=proxy.id,
            label=label,
            protocol=protocol,
            server=server,
            username=(proxy.username or None),
            password=(proxy.password or None),
        )

        print(
            f"[PROXY] Выбран прокси id={cfg.id} "
            f"({cfg.protocol} {cfg.server}, label={cfg.label})"
        )

        return cfg

    finally:
        db.close()


# ============================================================
#  СТАРЫЙ ИНТЕРФЕЙС (ДЛЯ СОВМЕСТИМОСТИ)
# ============================================================

def get_next_proxy_from_db() -> Optional[ProxyLaunchConfig]:
    """
    СТАРОЕ ИМЯ ФУНКЦИИ.
    Сейчас просто обёртка над get_next_proxy_for_launch().
    Оставлено для совместимости, если где-то ещё вызывается.
    """
    return get_next_proxy_for_launch()


# ============================================================
#  ОТМЕТКА ОШИБКИ / УСПЕХА ПРОКСИ
# ============================================================

def mark_proxy_fail(proxy_id: int, reason: str | None = None) -> None:
    """
    Увеличиваем счётчик ошибок у конкретного прокси.
    Простая логика: после 3 фейлов автоматически деактивируем прокси.
    """
    db: Session = SessionLocal()
    try:
        proxy: Proxy | None = db.get(Proxy, proxy_id)
        if not proxy:
            print(f"[PROXY] mark_proxy_fail: прокси id={proxy_id} не найден")
            return

        proxy.fail_count = (proxy.fail_count or 0) + 1

        # пример логики: после 3 ошибок можно деактивировать
        if proxy.fail_count >= 3:
            proxy.is_active = False

        proxy.last_used_at = datetime.utcnow()
        db.add(proxy)
        db.commit()

        print(
            f"[PROXY] FAIL id={proxy.id}, fail_count={proxy.fail_count}, "
            f"is_active={proxy.is_active}, reason={reason}"
        )
    finally:
        db.close()


def mark_proxy_success(proxy_id: int) -> None:
    """
    Сбрасываем счётчик ошибок и снова активируем прокси.
    Можно дергать после успешной сессии.
    """
    db: Session = SessionLocal()
    try:
        proxy: Proxy | None = db.get(Proxy, proxy_id)
        if not proxy:
            print(f"[PROXY] mark_proxy_success: прокси id={proxy_id} не найден")
            return

        proxy.fail_count = 0
        proxy.is_active = True
        proxy.last_used_at = datetime.utcnow()
        db.add(proxy)
        db.commit()

        print(f"[PROXY] SUCCESS id={proxy.id}: fail_count=0, is_active=True")
    finally:
        db.close()


def reset_proxy_fail(proxy_id: int) -> None:
    """
    Старое имя для того же действия, что mark_proxy_success().
    Оставлено для совместимости.
    """
    mark_proxy_success(proxy_id)