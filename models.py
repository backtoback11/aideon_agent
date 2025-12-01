from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Text,
    DateTime,
    Boolean,
)
from sqlalchemy.sql import func

# ВАЖНО: db.py лежит в том же каталоге
from db import Base


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)

    # бизнес-ID со стороны платёжной системы
    invoice_id = Column(String(255), unique=True, index=True, nullable=False)

    # финансы
    amount = Column(Float, nullable=False)
    currency = Column(String(16), nullable=False, default="RUB")

    # -----------------------------
    # ПОЛУЧАТЕЛЬ (детализированный)
    # -----------------------------
    # Страна получателя (как и раньше)
    recipient_country = Column(String(64), nullable=False)

    # Банк получателя (название из Multitransfer)
    recipient_bank = Column(String(255), nullable=False)

    # Номер карты получателя (9860 0466 0266 2507 и т.п.)
    recipient_card_number = Column(String(32), nullable=False)

    # Имя / Фамилия получателя
    recipient_first_name = Column(String(255), nullable=False)
    recipient_last_name = Column(String(255), nullable=False)

    # ЛЕГАСИ-ПОЛЯ (оставлены для совместимости, можно маппить в них ФИО/реквизиты)
    recipient_name = Column(String(255), nullable=False)
    recipient_requisites = Column(Text, nullable=False)

    # -----------------------------
    # ОТПРАВИТЕЛЬ (детализированный)
    # -----------------------------

    # Имя / Фамилия / Отчество отправителя
    sender_first_name = Column(String(255), nullable=False)
    sender_last_name = Column(String(255), nullable=False)
    sender_middle_name = Column(String(255), nullable=True)

    # Паспортные данные
    # Пример: "Паспорт РФ" / "Национальный"
    sender_passport_type = Column(String(64), nullable=False, default="Национальный")
    sender_passport_series = Column(String(32), nullable=False)
    sender_passport_number = Column(String(32), nullable=False)

    # Страна выдачи паспорта, дата выдачи
    sender_passport_country = Column(String(64), nullable=False, default="Россия")
    # Дата строкой "26.08.2021" — так проще прокинуть в форму 1-в-1
    sender_passport_issue_date = Column(String(32), nullable=False)

    # Дата рождения и место рождения
    # "01.08.2007", "Камышин", "Россия"
    sender_birth_date = Column(String(32), nullable=False)
    sender_birth_country = Column(String(64), nullable=False, default="Россия")
    sender_birth_place = Column(String(255), nullable=False)

    # Регистрация
    sender_registration_country = Column(String(64), nullable=False, default="Россия")
    sender_registration_place = Column(String(255), nullable=False)

    # Телефон отправителя (как и раньше, но теперь обязателен по смыслу)
    sender_phone = Column(String(64), nullable=True)

    # ЛЕГАСИ: агрегированное имя отправителя, если где-то ещё используется
    sender_name = Column(String(255), nullable=True)

    # URL, куда отдать результат (deeplink / ошибка)
    callback_url = Column(String(500), nullable=True)

    # queued / processing / waiting_captcha /
    # captcha_solved / processed / error
    status = Column(String(32), nullable=False, default="queued", index=True)

    # результат работы агента
    deeplink = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    # таймстемпы
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} invoice_id={self.invoice_id} status={self.status}>"


class Proxy(Base):
    """
    Прокси для агента.
    Пример:
      host = "45.148.240.152"
      port_http = 63030
      port_socks5 = 63031
      username = "ULCJDDCg"
      password = "WdE3AkhE"
    """
    __tablename__ = "proxies"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(255), nullable=True)  # удобное название

    host = Column(String(255), nullable=False)
    port_http = Column(Integer, nullable=True)
    port_socks5 = Column(Integer, nullable=True)

    username = Column(String(255), nullable=True)
    password = Column(String(255), nullable=True)

    # http / socks5
    protocol = Column(String(16), nullable=False, default="http")

    is_active = Column(Boolean, nullable=False, default=True)

    last_used_at = Column(DateTime(timezone=True), nullable=True)
    success_count = Column(Integer, nullable=False, default=0)
    fail_count = Column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<Proxy id={self.id} {self.protocol}://{self.host} active={self.is_active}>"


class Setting(Base):
    """
    Универсальная таблица настроек (ключ-значение).
    Используем для API-ключей капча-сервисов и прочих конфигов.
    """
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(128), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Setting {self.key}={self.value!r}>"