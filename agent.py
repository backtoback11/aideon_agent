from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from playwright.async_api import (
    async_playwright,
    Page,
    BrowserContext,
)

from db import SessionLocal
from models import Invoice, Setting
from agent_config import (
    MULTITRANSFER_BASE_URL,
    NAVIGATION_TIMEOUT_MS,
    DEFAULT_USER_AGENT,
    MAX_CONCURRENT_INVOICES,
)

# шаги
from multitransfer_step1 import step1_fill_amount_and_open_methods
from multitransfer_step2 import step2_select_bank
from multitransfer_step3 import step3_fill_recipient_and_sender
from multitransfer_step4 import step4_wait_for_deeplink

# прокси-менеджер (НОВАЯ версия)
from proxy_manager import get_next_proxy_for_launch, mark_proxy_fail, mark_proxy_success

load_dotenv()

WEBHOOK_URL = "https://joker-pay.com/webhook/tips"

# --------------------------------------------
# DEBUG: не закрывать вкладки после обработки
# --------------------------------------------
DEBUG_KEEP_TABS = True  # True → вкладки остаются открытыми для отладки

# Путь к Aideon Helper JS (новый модуль)
HELPER_JS_PATH = Path(__file__).resolve().parent / "browser" / "aideon_helper.js"


# ============================================================
# SETTINGS / STATUS
# ============================================================

def _set_setting(key: str, value: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(Setting).filter(Setting.key == key).first()
        if not row:
            row = Setting(key=key, value=value)
            db.add(row)
        else:
            row.value = value
        db.commit()
    finally:
        db.close()


def _mark_session_status(status: str, message: str = "") -> None:
    now = datetime.utcnow().isoformat(timespec="seconds")
    _set_setting("SESSION_STATUS", status)
    _set_setting("SESSION_MESSAGE", message or "")
    _set_setting("SESSION_UPDATED_AT", now)


# ============================================================
#   ФИНАЛИЗАЦИЯ ОШИБКИ ДО STEP4
# ============================================================

def _finalize_invoice_error_any_step(invoice_id: int, error_message: str) -> None:
    db = SessionLocal()
    inv: Optional[Invoice] = None
    try:
        inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if inv:
            inv.status = "error"
            inv.error_message = error_message
            inv.deeplink = None
            db.commit()
            print(
                f"[AGENT-ERROR] Инвойс id={inv.id} обновлён: "
                f"status=error, error_message={inv.error_message}"
            )
        else:
            print(f"[AGENT-ERROR] Не найден invoice id={invoice_id} для финализации.")
    except Exception as e:
        db.rollback()
        print(f"[AGENT-ERROR] Ошибка записи ошибки в БД для invoice={invoice_id}: {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass

    payload = {
        "invoice_id": invoice_id,
        "invoice_external_id": getattr(inv, "invoice_id", None) if inv else None,
        "amount": float(getattr(inv, "amount", 0) or 0) if inv else 0,
        "currency": getattr(inv, "currency", "643") if inv else "643",
        "deeplink": "",
        "status": "No Terminals",
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "error": error_message,
    }

    print(f"[AGENT-ERROR] POST (No Terminals) → {WEBHOOK_URL}")
    print(f"[AGENT-ERROR] Payload: {payload}")

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"[AGENT-ERROR] Ответ: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[AGENT-ERROR] Webhook error: {e}")


# ============================================================
#   PLAYWRIGHT CONTEXT + ПРОКСИ (АКТУАЛЬНАЯ ВЕРСИЯ)
# ============================================================

async def open_context(play) -> BrowserContext:
    """
    Открываем Chromium-контекст.
    Используем прокси из БД (ProxyLaunchConfig), если он есть и активен.
    """
    proxy_cfg = get_next_proxy_for_launch()

    launch_kwargs: dict = {
        "headless": False,
        "args": [
            f"--user-agent={DEFAULT_USER_AGENT}",
            "--disable-blink-features=AutomationControlled",
        ],
    }

    if proxy_cfg:
        # proxy_cfg: ProxyLaunchConfig(id, label, protocol, server, username, password)
        print(
            "[PROXY] Используем прокси для браузера: "
            f"id={proxy_cfg.id}, label={proxy_cfg.label!r}, "
            f"protocol={proxy_cfg.protocol!r}, server={proxy_cfg.server!r}"
        )

        # ВАЖНО: сюда передаём обычный dict, а не объект ProxyLaunchConfig
        launch_kwargs["proxy"] = {
            "server": proxy_cfg.server,           # напр. "http://45.148.240.152:63030"
            "username": proxy_cfg.username or None,
            "password": proxy_cfg.password or None,
        }
    else:
        print("[PROXY] Активных прокси в БД нет → запускаем без прокси")

    try:
        browser = await play.chromium.launch(**launch_kwargs)
    except Exception as e:
        # если есть прокси — помечаем его как упавший
        if proxy_cfg:
            try:
                mark_proxy_fail(proxy_cfg.id)
            except Exception as ie:
                print(f"[PROXY] Ошибка при mark_proxy_fail: {ie}")
        raise

    # Если браузер успешно поднялся — считаем, что прокси живой
    if proxy_cfg:
        try:
            mark_proxy_success(proxy_cfg.id)
        except Exception as ie:
            print(f"[PROXY] Ошибка при mark_proxy_success: {ie}")

    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=DEFAULT_USER_AGENT,
    )
    context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
    context.set_default_timeout(NAVIGATION_TIMEOUT_MS)
    return context


# ============================================================
#   ПОЛУЧИТЬ СЛЕДУЮЩИЙ INVOICE
# ============================================================

def get_next_invoice() -> Optional[Invoice]:
    db = SessionLocal()
    try:
        inv = (
            db.query(Invoice)
            .filter(Invoice.status == "queued")
            .order_by(Invoice.id.asc())
            .first()
        )
        if not inv:
            return None
        inv.status = "processing"
        db.commit()
        db.refresh(inv)
        return inv
    finally:
        db.close()


# ============================================================
#   HIGHLIGHT CAPTCHA
# ============================================================

async def highlight_captcha_tab(page: Page, invoice: Invoice) -> None:
    try:
        await page.bring_to_front()
    except Exception:
        pass

    try:
        await page.evaluate(
            """
            (id) => {
                const elId = 'aideon-captcha-banner';
                let el = document.getElementById(elId);
                if (!el) {
                    el = document.createElement('div');
                    el.id = elId;
                    el.style.position = 'fixed';
                    el.style.top = '0';
                    el.style.left = '0';
                    el.style.right = '0';
                    el.style.zIndex = '999999';
                    el.style.background = '#ff3333';
                    el.style.color = '#fff';
                    el.style.padding = '10px 16px';
                    el.style.fontSize = '16px';
                    el.style.fontFamily = 'sans-serif';
                    el.style.textAlign = 'center';
                    document.body.appendChild(el);
                }
                el.textContent = 'Aideon Agent: пройди капчу для инвойса ' + id;
            }
            """,
            invoice.id,
        )
        print(f"[CAPTCHA] Вкладка invoice={invoice.id} подсвечена.")
    except Exception as e:
        print(f"[CAPTCHA] Ошибка показа баннера: {e}")


# ============================================================
#   PROCESS INVOICE
# ============================================================

async def process_invoice(context: BrowserContext, invoice: Invoice) -> None:
    print(f"[PROCESS] Старт обработки invoice={invoice.id}")
    page = await context.new_page()
    print(f"[TAB] Открыта новая вкладка для invoice={invoice.id}")

    # --------------------------------------------
    # НОВОЕ: инжект Aideon Helper JS в вкладку
    # --------------------------------------------
    try:
        if HELPER_JS_PATH.exists():
            helper_js_code = HELPER_JS_PATH.read_text(encoding="utf-8")
            await page.add_init_script(helper_js_code)
            print(f"[AIDEON-HELPER] aideon_helper.js инжектирован для invoice={invoice.id}")
        else:
            print(f"[AIDEON-HELPER] WARN: файл {HELPER_JS_PATH} не найден, helper не подключён")
    except Exception as e:
        print(f"[AIDEON-HELPER] Ошибка инжекта helper JS: {e}")

    db = SessionLocal()
    try:
        inv_db = db.query(Invoice).filter(Invoice.id == invoice.id).first()
        if not inv_db:
            print(f"[ERROR] В БД не найден invoice={invoice.id}.")
            return

        _mark_session_status("working", f"Processing invoice {invoice.id}")

        base_url = MULTITRANSFER_BASE_URL or "https://multitransfer.ru/transfer/uzbekistan"
        print(f"[OPEN] Открываю: {base_url}")
        await page.goto(base_url)

        # STEP 1
        await step1_fill_amount_and_open_methods(page, inv_db.amount)

        # STEP 2
        await step2_select_bank(page, inv_db.recipient_bank)

        # STEP 3
        await step3_fill_recipient_and_sender(page, inv_db)
        print("[FLOW] Шаг 3 завершён — ожидается капча.")

        inv_db.status = "waiting_captcha"
        inv_db.error_message = None
        db.commit()
        print(f"[FLOW] Invoice {inv_db.id} → waiting_captcha")

        await highlight_captcha_tab(page, inv_db)

        # STEP 4
        deeplink = await step4_wait_for_deeplink(page, inv_db)
        print(f"[DONE] STEP4 deeplink: {deeplink!r}")

        _mark_session_status("ok", f"Processed invoice {inv_db.id}")

        if DEBUG_KEEP_TABS:
            print(f"[TAB] Вкладка invoice={invoice.id} оставлена открытой (DEBUG_KEEP_TABS).")
        else:
            try:
                await page.close()
                print(f"[TAB] Закрыта вкладка invoice={invoice.id}")
            except Exception:
                pass

        print(f"[PROCESS] Завершение invoice={invoice.id} (успех).")

    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Ошибка invoice={invoice.id}: {error_msg}")

        if not error_msg.startswith("[STEP4]"):
            _finalize_invoice_error_any_step(invoice.id, error_msg)

        _mark_session_status("error", error_msg)

        if DEBUG_KEEP_TABS:
            print(f"[TAB] Вкладка invoice={invoice.id} НЕ закрыта из-за ошибки (DEBUG_KEEP_TABS).")
        else:
            try:
                await page.close()
                print(f"[TAB] Закрыта вкладка invoice={invoice.id} (ошибка).")
            except Exception:
                pass

        print(f"[PROCESS] Завершение invoice={invoice.id} (ошибка).")
    finally:
        db.close()


# ============================================================
#   ГЛАВНЫЙ ЦИКЛ АГЕНТА — ТИХАЯ ВЕРСИЯ
# ============================================================

async def run_agent():
    async with async_playwright() as play:
        context = await open_context(play)

        base_url = MULTITRANSFER_BASE_URL or "https://multitransfer.ru/transfer/uzbekistan"
        print(f"[AGENT] Открываю базовую вкладку: {base_url}")
        try:
            base_page = await context.new_page()

            # Инжект helper JS и в базовую форму (для будущего использования)
            try:
                if HELPER_JS_PATH.exists():
                    helper_js_code = HELPER_JS_PATH.read_text(encoding="utf-8")
                    await base_page.add_init_script(helper_js_code)
                    print("[AIDEON-HELPER] helper JS инжектирован в базовую вкладку")
                else:
                    print(f"[AIDEON-HELPER] WARN: файл {HELPER_JS_PATH} не найден для базовой вкладки")
            except Exception as e:
                print(f"[AIDEON-HELPER] Ошибка инжекта helper JS в базовую вкладку: {e}")

            await base_page.goto(base_url)
            _mark_session_status("ok", "Base form opened")
        except Exception as e:
            print(f"[AGENT] ⚠ Ошибка открытия базовой формы: {e}")
            _mark_session_status("error", str(e))

        print("[AGENT] Запущен. Жду queued-инвойсы...")

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_INVOICES)
        tasks: set[asyncio.Task] = set()
        last_idle_log: Optional[datetime] = None

        async def _runner(inv: Invoice):
            print(f"[RUNNER] Жду слот для invoice={inv.id}")
            async with semaphore:
                print(f"[RUNNER] Слот получен → invoice={inv.id}")
                await process_invoice(context, inv)
            print(f"[RUNNER] Слот освобождён → invoice={inv.id}")

        while True:
            done = {t for t in tasks if t.done()}
            if done:
                print(f"[AGENT] Завершено задач: {len(done)}")
                tasks -= done

            found_new = False

            while len(tasks) < MAX_CONCURRENT_INVOICES:
                invoice = get_next_invoice()
                if not invoice:
                    break

                found_new = True
                print(f"[QUEUE] Взяли invoice={invoice.id} в обработку")
                t = asyncio.create_task(_runner(invoice), name=f"invoice-{invoice.id}")
                tasks.add(t)

            if tasks:
                active = [t.get_name() for t in tasks]
                print(f"[AGENT] Активных задач: {len(tasks)} / {MAX_CONCURRENT_INVOICES} → {active}")
                _mark_session_status("working", f"{len(tasks)} active")
                await asyncio.sleep(1)
                continue

            now = datetime.utcnow()
            if not found_new:
                if not last_idle_log or (now - last_idle_log).total_seconds() >= 60:
                    print("[AGENT] Idle. Нет queued-инвойсов.")
                    last_idle_log = now

            _mark_session_status("ok", "Idle")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run_agent())