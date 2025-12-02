from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_302_FOUND
from pydantic import BaseModel
from dotenv import load_dotenv  # ⬅ НОВОЕ: подхватываем .env

from db import SessionLocal, Base, engine
from models import Invoice, Proxy, Setting

# -------------------------------------------------------------
# ЗАГРУЗКА .env (ключи, конфиги и т.п.)
# -------------------------------------------------------------
# .env лежит в корне проекта aideon_agent и НЕ коммитится.
load_dotenv()

# -------------------------------------------------------------
# БАЗОВЫЕ ПУТИ
# -------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# -------------------------------------------------------------
# ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ
# -------------------------------------------------------------
app = FastAPI(title="Aideon Agent Admin")

# Создаём таблицы, если их нет
Base.metadata.create_all(bind=engine)

# Подключаем статику и шаблоны
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# -------------------------------------------------------------
# КЛЮЧИ НАСТРОЕК
# -------------------------------------------------------------
CAPTCHA_KEYS = ["RUCAPTCHA_KEY", "TWOCAPTCHA_KEY", "CAPSOLVER_KEY"]
SESSION_KEYS = ["SESSION_STATUS", "SESSION_MESSAGE", "SESSION_UPDATED_AT"]

# Флаги воркеров
WORKER_AGENT_KEY = "AGENT_WORKER_ENABLED"
WORKER_PRMONEY_KEY = "PRMONEY_WORKER_ENABLED"


def _db_get_setting(db, key: str) -> str:
    s = db.query(Setting).filter(Setting.key == key).first()
    return s.value if s else ""


def _db_set_setting(db, key: str, value: str) -> None:
    """Универсальная запись / создание настройки."""
    row = db.query(Setting).filter(Setting.key == key).first()
    if not row:
        row = Setting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()


# -------------------------------------------------------------
# РЕДИРЕКТ С КОРНЯ НА /admin
# -------------------------------------------------------------
@app.get("/")
def root():
    return RedirectResponse("/admin", status_code=HTTP_302_FOUND)


# -------------------------------------------------------------
# DASHBOARD
# -------------------------------------------------------------
@app.get("/admin", name="admin_dashboard")
def admin_dashboard(request: Request):
    db = SessionLocal()
    try:
        total = db.query(Invoice).count()
        queued = db.query(Invoice).filter(Invoice.status == "queued").count()
        processing = db.query(Invoice).filter(Invoice.status == "processing").count()
        waiting = db.query(Invoice).filter(Invoice.status == "waiting_captcha").count()
        error = db.query(Invoice).filter(Invoice.status == "error").count()

        proxies = db.query(Proxy).count()

        # статусы сессии агента (обновляются из agent.py)
        session_status = _db_get_setting(db, "SESSION_STATUS") or "unknown"
        session_message = _db_get_setting(db, "SESSION_MESSAGE") or ""
        session_updated_at = _db_get_setting(db, "SESSION_UPDATED_AT") or ""

        # флаги воркеров (1 / 0)
        agent_worker_enabled = (_db_get_setting(db, WORKER_AGENT_KEY) == "1")
        prmoney_worker_enabled = (_db_get_setting(db, WORKER_PRMONEY_KEY) == "1")

        return templates.TemplateResponse(
            "admin/dashboard.html",
            {
                "request": request,
                "active_page": "dashboard",
                "total": total,
                "queued": queued,
                "processing": processing,
                "waiting": waiting,
                "error": error,
                "proxies": proxies,
                "session_status": session_status,
                "session_message": session_message,
                "session_updated_at": session_updated_at,
                # новые поля для управления воркерами
                "agent_worker_enabled": agent_worker_enabled,
                "prmoney_worker_enabled": prmoney_worker_enabled,
            },
        )
    finally:
        db.close()


# -------------------------------------------------------------
# ИНВОЙСЫ
# -------------------------------------------------------------
@app.get("/admin/invoices", name="invoices_list")
def invoices_list(request: Request):
    """
    Список инвойсов — для контроля очереди.
    """
    db = SessionLocal()
    try:
        invoices = (
            db.query(Invoice)
            .order_by(Invoice.id.desc())
            .limit(100)
            .all()
        )
        return templates.TemplateResponse(
            "admin/invoices_list.html",
            {
                "request": request,
                "active_page": "invoices",
                "invoices": invoices,
            },
        )
    finally:
        db.close()


@app.get("/admin/invoices/create", name="invoices_create_form")
def invoices_create_form(request: Request):
    """
    Форма создания инвойса (все поля забиваем руками как временный шаблон).
    """
    return templates.TemplateResponse(
        "admin/invoices_create.html",
        {
            "request": request,
            "active_page": "invoices",
        },
    )


@app.post("/admin/invoices/create", name="invoices_create")
def invoices_create(
    request: Request,
    # --- базовые поля инвойса ---
    invoice_id: str = Form(...),
    amount: float = Form(...),
    currency: str = Form("RUB"),
    callback_url: str | None = Form(None),

    # --- ПОЛУЧАТЕЛЬ ---
    recipient_country: str = Form(...),
    recipient_bank: str = Form(...),
    recipient_card_number: str = Form(...),
    recipient_first_name: str = Form(...),
    recipient_last_name: str = Form(...),
    recipient_requisites: str | None = Form(None),

    # --- ОТПРАВИТЕЛЬ ---
    sender_first_name: str = Form(...),
    sender_last_name: str = Form(...),
    sender_middle_name: str | None = Form(None),

    sender_passport_type: str = Form("rf_national"),
    sender_passport_series: str = Form(...),
    sender_passport_number: str = Form(...),
    sender_passport_country: str = Form("Россия"),
    sender_passport_issue_date: str = Form(...),

    sender_birth_date: str = Form(...),
    sender_birth_country: str = Form("Россия"),
    sender_birth_place: str = Form(...),

    sender_registration_country: str = Form("Россия"),
    sender_registration_place: str = Form(...),

    sender_phone: str = Form(...),
):
    """
    Создание нового инвойса в статусе queued.
    Все поля маппим на детализированную модель Invoice.
    """
    db = SessionLocal()
    try:
        # ФИО получателя для легаси-полей
        recipient_full_name = f"{recipient_first_name} {recipient_last_name}".strip()

        # ФИО отправителя (легаси sender_name)
        sender_full_name_parts = [sender_last_name, sender_first_name]
        if sender_middle_name:
            sender_full_name_parts.append(sender_middle_name)
        sender_full_name = " ".join(p for p in sender_full_name_parts if p)

        # Легаси-реквизиты — номер карты + банк + доп. текст, если есть
        base_requisites = f"Карта: {recipient_card_number}, Банк: {recipient_bank}"
        if recipient_requisites:
            recipient_requisites_full = base_requisites + f", {recipient_requisites.strip()}"
        else:
            recipient_requisites_full = base_requisites

        inv = Invoice(
            invoice_id=invoice_id.strip(),
            amount=float(amount),
            currency=currency.strip(),

            # получатель — новые поля
            recipient_country=recipient_country.strip(),
            recipient_bank=recipient_bank.strip(),
            recipient_card_number=recipient_card_number.strip(),
            recipient_first_name=recipient_first_name.strip(),
            recipient_last_name=recipient_last_name.strip(),

            # получатель — legacy-поля
            recipient_name=recipient_full_name,
            recipient_requisites=recipient_requisites_full,

            # отправитель — новые поля
            sender_first_name=sender_first_name.strip(),
            sender_last_name=sender_last_name.strip(),
            sender_middle_name=sender_middle_name.strip() if sender_middle_name else None,
            sender_passport_type=sender_passport_type.strip(),
            sender_passport_series=sender_passport_series.strip(),
            sender_passport_number=sender_passport_number.strip(),
            sender_passport_country=sender_passport_country.strip(),
            sender_passport_issue_date=sender_passport_issue_date.strip(),
            sender_birth_date=sender_birth_date.strip(),
            sender_birth_country=sender_birth_country.strip(),
            sender_birth_place=sender_birth_place.strip(),
            sender_registration_country=sender_registration_country.strip(),
            sender_registration_place=sender_registration_place.strip(),
            sender_phone=sender_phone.strip(),

            # отправитель — legacy-поле
            sender_name=sender_full_name,

            callback_url=callback_url.strip() if callback_url else None,
            status="queued",
        )

        db.add(inv)
        db.commit()

        return RedirectResponse("/admin/invoices", status_code=HTTP_302_FOUND)

    finally:
        db.close()


# -------------------------------------------------------------
# ПРОКСИ
# -------------------------------------------------------------
@app.get("/admin/proxies")
def list_proxies(request: Request):
    db = SessionLocal()
    try:
        proxies = db.query(Proxy).order_by(Proxy.id.asc()).all()
        return templates.TemplateResponse(
            "admin/proxies.html",
            {
                "request": request,
                "active_page": "proxies",
                "proxies": proxies,
            },
        )
    finally:
        db.close()


@app.post("/admin/proxies/add")
def add_proxy(
    host: str = Form(...),
    port_http: int | None = Form(None),
    port_socks5: int | None = Form(None),
    username: str | None = Form(None),
    password: str | None = Form(None),
    protocol: str = Form("http"),
    label: str | None = Form(None),
    is_active: str | None = Form(None),  # чекбокс приходит как "on"
):
    db = SessionLocal()
    try:
        proxy = Proxy(
            host=host.strip(),
            port_http=port_http,
            port_socks5=port_socks5,
            username=username or None,
            password=password or None,
            protocol=protocol,
            label=label or host,
            is_active=True if is_active else False,
        )
        db.add(proxy)
        db.commit()

        return RedirectResponse("/admin/proxies", status_code=HTTP_302_FOUND)
    finally:
        db.close()


@app.post("/admin/proxies/{proxy_id}/toggle")
def toggle_proxy(proxy_id: int):
    db = SessionLocal()
    try:
        proxy = db.query(Proxy).filter(Proxy.id == proxy_id).first()
        if not proxy:
            raise HTTPException(404, "Proxy not found")

        proxy.is_active = not proxy.is_active
        db.commit()

        return RedirectResponse("/admin/proxies", status_code=HTTP_302_FOUND)
    finally:
        db.close()


@app.post("/admin/proxies/{proxy_id}/delete")
def delete_proxy(proxy_id: int):
    db = SessionLocal()
    try:
        proxy = db.query(Proxy).filter(Proxy.id == proxy_id).first()
        if proxy:
            db.delete(proxy)
            db.commit()

        return RedirectResponse("/admin/proxies", status_code=HTTP_302_FOUND)
    finally:
        db.close()


# -------------------------------------------------------------
# НАСТРОЙКИ (API-ключи капч)
# -------------------------------------------------------------
@app.get("/admin/settings")
def settings_page(request: Request):
    db = SessionLocal()
    try:
        values = {k: _db_get_setting(db, k) for k in CAPTCHA_KEYS}

        return templates.TemplateResponse(
            "admin/settings.html",
            {
                "request": request,
                "active_page": "settings",
                "settings": values,
            },
        )
    finally:
        db.close()


@app.post("/admin/settings")
def save_settings(
    rucaptcha_key: str = Form(""),
    twocaptcha_key: str = Form(""),
    capsolver_key: str = Form(""),
):
    mapping = {
        "RUCAPTCHA_KEY": rucaptcha_key.strip(),
        "TWOCAPTCHA_KEY": twocaptcha_key.strip(),
        "CAPSOLVER_KEY": capsolver_key.strip(),
    }

    db = SessionLocal()
    try:
        for key, value in mapping.items():
            row = db.query(Setting).filter(Setting.key == key).first()

            if not row:
                row = Setting(key=key, value=value)
                db.add(row)
            else:
                row.value = value

        db.commit()

        return RedirectResponse("/admin/settings", status_code=HTTP_302_FOUND)
    finally:
        db.close()


# -------------------------------------------------------------
# УПРАВЛЕНИЕ ВОРКЕРАМИ (AGENT / PRMONEY)
# -------------------------------------------------------------
@app.post("/admin/workers/toggle_agent", name="toggle_agent_worker")
def toggle_agent_worker():
    """
    Включение/выключение основного агента.
    workers.py должен сам смотреть на флаг AGENT_WORKER_ENABLED.
    """
    db = SessionLocal()
    try:
        cur = _db_get_setting(db, WORKER_AGENT_KEY)
        new_val = "0" if cur == "1" else "1"
        _db_set_setting(db, WORKER_AGENT_KEY, new_val)
        return RedirectResponse("/admin", status_code=HTTP_302_FOUND)
    finally:
        db.close()


@app.post("/admin/workers/toggle_prmoney", name="toggle_prmoney_worker")
def toggle_prmoney_worker():
    """
    Включение/выключение PrMoney-воркера.
    workers.py должен сам смотреть на флаг PRMONEY_WORKER_ENABLED.
    """
    db = SessionLocal()
    try:
        cur = _db_get_setting(db, WORKER_PRMONEY_KEY)
        new_val = "0" if cur == "1" else "1"
        _db_set_setting(db, WORKER_PRMONEY_KEY, new_val)
        _db_set_setting(db, "SESSION_MESSAGE", "")  # опционально: чистим сообщение
        return RedirectResponse("/admin", status_code=HTTP_302_FOUND)
    finally:
        db.close()


# -------------------------------------------------------------
# CALLBACK ДЛЯ ПОЛУЧЕНИЯ DEEPLINK ПО ИНВОЙСУ
# -------------------------------------------------------------

class InvoiceDeeplinkPayload(BaseModel):
    """
    Payload постбека от воркера / внешней системы с диплинком.
    Ожидается примерно такой JSON:
    {
        "invoice_id": 65,
        "invoice_external_id": "2640797",
        "amount": 6000,
        "currency": "643",
        "deeplink": "https://qr.nspk.ru/...",
        "status": "created",
        "created_at": "2025-12-01T21:15:22"
    }
    """
    invoice_id: int
    invoice_external_id: str
    amount: float
    currency: str
    deeplink: str
    status: str
    created_at: str


@app.post("/callbacks/invoice/deeplink", name="invoice_deeplink_callback")
def invoice_deeplink_callback(payload: InvoiceDeeplinkPayload):
    """
    Принимаем постбек с диплинком:
      - пытаемся найти инвойс по внутреннему ID (invoice_id),
      - если не нашли, ищем по внешнему invoice_external_id,
      - обновляем deeplink и статус.
    """
    db = SessionLocal()
    try:
        # 1) ищем по внутреннему ID (Invoice.id)
        invoice = db.query(Invoice).filter(Invoice.id == payload.invoice_id).first()

        # 2) если не нашли — пробуем по внешнему invoice_id (строка)
        if not invoice:
            invoice = (
                db.query(Invoice)
                .filter(Invoice.invoice_id == str(payload.invoice_external_id))
                .first()
            )

        if not invoice:
            print(
                f"[CALLBACK] Не найден инвойс ни по id={payload.invoice_id}, "
                f"ни по invoice_id={payload.invoice_external_id}"
            )
            raise HTTPException(status_code=404, detail="Invoice not found")

        # Обновляем диплинк и статус
        invoice.deeplink = payload.deeplink
        invoice.status = payload.status or "created"

        db.commit()
        db.refresh(invoice)

        print(
            f"[CALLBACK] Обновлён инвойс id={invoice.id}: "
            f"status={invoice.status}, deeplink={invoice.deeplink}"
        )

        return {"ok": True}
    finally:
        db.close()