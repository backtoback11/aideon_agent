from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import requests  # ⬅ отправка вебхуков
from dotenv import load_dotenv  # ⬅ НОВОЕ: загрузка .env при старте процесса

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
    MAX_CONCURRENT_INVOICES,  # ⬅ НОВОЕ: берём лимит из конфига
)

# шаги вынесены в отдельные файлы
from multitransfer_step1 import step1_fill_amount_and_open_methods
from multitransfer_step2 import step2_select_bank
from multitransfer_step3 import step3_fill_recipient_and_sender
from multitransfer_step4 import step4_wait_for_deeplink  # шаг 4 (deeplink + vision/webhook)


# ============================================================
#   ЗАГРУЗКА .env (ключи, настройки и т.д.)
# ============================================================

# .env лежит в корне проекта aideon_agent и НЕ коммитится в git.
# Пример .env:
#   AIDEON_OPENAI_API_KEY=sk-proj-...
#   OPENAI_VISION_MODEL=gpt-4.1-mini
#   OPENAI_VISION_FALLBACK_MODEL=gpt-4.1
load_dotenv()


WEBHOOK_URL = "https://joker-pay.com/webhook/tips"


# ============================================================
#   УТИЛИТЫ ДЛЯ СЕССИИ (settings в БД)
# ============================================================

def _set_setting(key: str, value: str) -> None:
    """Простейший upsert в таблицу settings."""
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
    """
    Записываем статус сессии агента в settings.

    status:
      - ok             — всё норм, агент в рабочем состоянии
      - working        — сейчас обрабатываем инвойс(ы)
      - error          — сессия/страница в ошибке
    """
    now = datetime.utcnow().isoformat(timespec="seconds")

    _set_setting("SESSION_STATUS", status)
    _set_setting("SESSION_MESSAGE", message or "")
    _set_setting("SESSION_UPDATED_AT", now)


# ============================================================
#   ФИНАЛИЗАЦИЯ ИНВОЙСА ПРИ ОШИБКЕ НА ЛЮБОМ ШАГЕ (кроме STEP4)
# ============================================================

def _finalize_invoice_error_any_step(invoice_id: int, error_message: str) -> None:
    """
    Универсальная финализация инвойса при ошибке на ЛЮБОМ шаге (STEP1–STEP3 и др.).

    Делает:
      - пишет в БД: status='error', error_message=<причина>, deeplink=None
      - отправляет webhook с status='No Terminals', пустым deeplink и полем error
    """
    db = SessionLocal()
    inv: Optional[Invoice] = None
    try:
        inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not inv:
            print(f"[AGENT-ERROR] Не найден invoice id={invoice_id} для финализации ошибки.")
        else:
            inv.status = "error"
            inv.error_message = error_message
            inv.deeplink = None

            db.commit()
            print(
                f"[AGENT-ERROR] Инвойс id={inv.id} обновлён: "
                f"status=error, error_message={inv.error_message}"
            )
    except Exception as e:
        db.rollback()
        print(f"[AGENT-ERROR] Ошибка при записи ошибки в БД для invoice={invoice_id}: {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass

    # Webhook с No Terminals
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
        print(f"[AGENT-ERROR] Webhook error (No Terminals) для invoice={invoice_id}: {e}")


# ============================================================
#   PLAYWRIGHT CONTEXT (ОДИН БРАУЗЕР, МНОГО ВКЛАДОК)
# ============================================================

async def open_context(play) -> BrowserContext:
    """
    Создаёт Playwright-контекст без storage_state.
    Для каждого инвойса будет открываться новая вкладка в одном браузере.
    """
    browser = await play.chromium.launch(
        headless=False,
        args=[
            f"--user-agent={DEFAULT_USER_AGENT}",
            "--disable-blink-features=AutomationControlled",
        ],
    )

    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=DEFAULT_USER_AGENT,
    )

    context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
    context.set_default_timeout(NAVIGATION_TIMEOUT_MS)
    return context


# ============================================================
#   ВЗЯТЬ СЛЕДУЮЩИЙ INVOICE
# ============================================================

def get_next_invoice() -> Optional[Invoice]:
    """
    Берём первый queued-invoice и ставим ему status=processing.
    """
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
#   ПОДСВЕТКА ВКЛАДКИ ДЛЯ КАПЧИ
# ============================================================

async def highlight_captcha_tab(page: Page, invoice: Invoice) -> None:
    """
    Поднимаем вкладку на передний план и показываем красный баннер,
    чтобы оператор сразу видел, где проходить капчу.
    """
    try:
        await page.bring_to_front()
    except Exception as e:
        print(f"[CAPTCHA] Не удалось поднять вкладку invoice={invoice.id}: {e}")

    try:
        await page.evaluate(
            """
            (invoiceId) => {
                const id = 'aideon-captcha-banner';
                let el = document.getElementById(id);
                if (!el) {
                    el = document.createElement('div');
                    el.id = id;
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
                    el.style.boxShadow = '0 2px 6px rgba(0,0,0,0.3)';
                    document.body.appendChild(el);
                }
                el.textContent = 'Aideon Agent: пройди капчу для инвойса ' + invoiceId;
            }
            """,
            invoice.id,
        )
        print(f"[CAPTCHA] Вкладка для invoice={invoice.id} подсвечена (баннер капчи).")
    except Exception as e:
        print(f"[CAPTCHA] Не удалось показать баннер для invoice={invoice.id}: {e}")


# ============================================================
#   ОБРАБОТКА ОДНОГО INVOICE В ОТДЕЛЬНОЙ ВКЛАДКЕ
# ============================================================

async def process_invoice(context: BrowserContext, invoice: Invoice) -> None:
    """
    Полный пайплайн обработки одного инвойса:

      1. Открываем форму Узбекистана в НОВОЙ вкладке.
      2. Шаг 1: ввод суммы + раскрытие списка способов перевода.
      3. Шаг 2: выбор оффера/банка (по invoice.recipient_bank).
      4. Шаг 3: заполняем форму получателя/отправителя + галочка + 'Продолжить'.
         → на этом этапе появляется капча, оператор проходит её вручную.
         → помечаем invoice.status = 'waiting_captcha' и подсвечиваем вкладку.
      5. Шаг 4: ждём финальный экран с QR/СБП-НСПК, вытаскиваем диплинк.
         ВАЖНО: step4 сам шлёт вебхук и управляет финальным статусом/диплинком.
      6. При успехе вкладку не закрываем (остаётся для оператора),
         при ошибке — вкладку закрываем, чтобы не копить мусор.
    """
    page: Page = await context.new_page()
    print(f"[TAB] Открыта новая вкладка для invoice={invoice.id}")

    db = SessionLocal()
    try:
        # подгружаем актуальный объект из БД (на случай, если сессия изменилась)
        inv_db = db.query(Invoice).filter(Invoice.id == invoice.id).first()
        if not inv_db:
            print(f"[ERROR] В БД не найден invoice id={invoice.id}, прекращаем обработку.")
            return

        _mark_session_status("working", f"Processing invoice {invoice.id}")

        base_url = MULTITRANSFER_BASE_URL or "https://multitransfer.ru/transfer/uzbekistan"
        print(f"[OPEN] Открываю: {base_url}")
        await page.goto(base_url)

        # STEP 1 — сумма + открыть список способов
        await step1_fill_amount_and_open_methods(page, inv_db.amount)

        # STEP 2 — выбор оффера/банка
        await step2_select_bank(page, inv_db.recipient_bank)

        # STEP 3 — форма получателя и отправителя + галочка + 'Продолжить'
        await step3_fill_recipient_and_sender(page, inv_db)

        print(
            "[FLOW] Шаг 3 завершён, ожидается капча. "
            "Оператор должен пройти капчу и довести поток до экрана с QR/ссылкой."
        )

        # Помечаем в БД, что теперь ждём капчу
        inv_db.status = "waiting_captcha"
        inv_db.error_message = None
        db.commit()
        print(f"[FLOW] Invoice {inv_db.id} помечен как waiting_captcha.")

        # Подсветка вкладки для оператора (баннер + bring_to_front)
        await highlight_captcha_tab(page, inv_db)

        # STEP 4 — ждём экран с диплинком/QR и вытаскиваем ссылку
        # ВАЖНО: step4 сам управляет диплинком/статусом (через свою логику и/или вебхук).
        deeplink = await step4_wait_for_deeplink(page, inv_db)

        # Здесь мы НИЧЕГО не меняем в БД —
        # считаем, что step4 или обработчик вебхука уже сделал всё нужное.
        print(f"[DONE] Invoice {inv_db.id} успешно обработан, диплинк из STEP4: {deeplink!r}")
        _mark_session_status("ok", f"Processed invoice {inv_db.id} with deeplink")

        # Вкладку при успехе оставляем открытой — вдруг оператору надо что-то досмотреть.
        print(f"[TAB] Вкладка для invoice={invoice.id} остаётся открытой после успешной обработки.")

    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Ошибка обработки invoice={invoice.id}: {error_msg}")

        # STEP4 уже сам шлёт вебхук и ставит статусы/ошибку,
        # поэтому здесь не дублируем для ошибок, начинающихся с "[STEP4]"
        if not error_msg.startswith("[STEP4]"):
            _finalize_invoice_error_any_step(invoice.id, error_message=error_msg)

        _mark_session_status("error", f"Error while processing invoice {invoice.id}: {error_msg}")

        # При любой ошибке вкладку закрываем, чтобы не засорять браузер
        try:
            await page.close()
            print(f"[TAB] Вкладка для invoice={invoice.id} закрыта из-за ошибки.")
        except Exception as e_close:
            print(f"[TAB] Не удалось закрыть вкладку invoice={invoice.id}: {e_close}")
    finally:
        db.close()


# ============================================================
#   ГЛАВНЫЙ ЦИКЛ АГЕНТА (ПАРАЛЛЕЛЬНАЯ ОБРАБОТКА)
# ============================================================

async def run_agent():
    async with async_playwright() as play:
        context = await open_context(play)

        # Базовая форма в отдельной вкладке (по желанию — для ручных действий/логина)
        base_url = MULTITRANSFER_BASE_URL or "https://multitransfer.ru/transfer/uzbekistan"
        print(f"[AGENT] Открываю базовую страницу формы: {base_url}")
        try:
            base_page = await context.new_page()
            await base_page.goto(base_url)
            _mark_session_status("ok", f"Base form opened: {base_url}")
        except Exception as e:
            print(f"[AGENT] ⚠ Не удалось открыть базовую страницу: {e}")
            _mark_session_status("error", f"Cannot open base form: {e}")

        print("[AGENT] Запущен. Жду инвойсы в статусе 'queued'...")

        # Семафор для ограничения числа одновременных инвойсов
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_INVOICES)
        tasks: set[asyncio.Task] = set()

        async def _runner(inv: Invoice):
            """Обёртка, которая учитывает семафор и пул задач."""
            async with semaphore:
                await process_invoice(context, inv)

        while True:
            # чистим завершённые таски
            done_tasks = {t for t in tasks if t.done()}
            if done_tasks:
                tasks -= done_tasks

            # добираем новые инвойсы, пока есть свободные слоты
            while len(tasks) < MAX_CONCURRENT_INVOICES:
                invoice = get_next_invoice()
                if not invoice:
                    break

                print(f"[QUEUE] Берём invoice={invoice.id} в обработку (параллельно).")
                task = asyncio.create_task(_runner(invoice), name=f"invoice-{invoice.id}")
                tasks.add(task)

            # если задач нет — просто ждём и снова опрашиваем БД
            if not tasks:
                await asyncio.sleep(5)
            else:
                # если задачи есть — даём им поработать и снова проверяем очередь
                await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(run_agent())