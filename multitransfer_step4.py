from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional
import re
import asyncio

import requests
from playwright.async_api import Page

from db import SessionLocal
from models import Invoice as InvoiceModel

DEBUG_DIR_STEP4 = "debug/multitransfer_step4"
WEBHOOK_URL = "https://joker-pay.com/webhook/tips"

FINAL_SCREEN_MAX_WAIT_SECONDS = 300   # 5 минут на капчу и переход к финалу
QR_APPEAR_DELAY_MS = 2000             # задержка, чтобы QR точно дорисовался

# Ключевые маркеры диплинков, которые нас интересуют
DEEP_LINK_KEYWORDS = [
    "qr.nspk.ru",
    "SBPQR://",
    "sbpqr://",
    "mcash://",
]

print("[STEP4] *** NEW VERSION: network /confirm + console, без Vision ***")


# ============================================================
# DEBUG HELPERS
# ============================================================

def _ensure_debug_dir():
    try:
        os.makedirs(DEBUG_DIR_STEP4, exist_ok=True)
    except Exception:
        pass


async def _save_html(page: Page, label: str):
    """Сохранить HTML для отладки."""
    _ensure_debug_dir()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DEBUG_DIR_STEP4, f"{label}_{ts}.html")
    try:
        html = await page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[STEP4-DEBUG] HTML → {path}")
    except Exception as e:
        print(f"[STEP4-DEBUG] Ошибка сохранения HTML: {e}")


async def _save_screenshot(page: Page, label: str) -> Optional[bytes]:
    """
    Сохранить скрин всей страницы (чисто для отладки).
    """
    _ensure_debug_dir()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DEBUG_DIR_STEP4, f"{label}_{ts}.png")
    try:
        png_bytes = await page.screenshot(path=path, full_page=True)
        print(f"[STEP4-DEBUG] Скрин → {path}")
        return png_bytes
    except Exception as e:
        print(f"[STEP4-DEBUG] Ошибка сохранения скрина: {e}")
        return None


def _save_console_log(messages: list[str], label: str) -> None:
    """Сохранить логи консоли в текстовый файл."""
    if not messages:
        return
    _ensure_debug_dir()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DEBUG_DIR_STEP4, f"{label}_{ts}.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            for i, msg in enumerate(messages, start=1):
                f.write(f"[{i}] {msg}\n")
        print(f"[STEP4-DEBUG] Console log → {path}")
    except Exception as e:
        print(f"[STEP4-DEBUG] Ошибка сохранения console log: {e}")


# ============================================================
# PARSING DEEPLINK FROM TEXT
# ============================================================

_DEEPLINK_URL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\"'<>]+)")


def _extract_deeplink_from_text(text: str) -> Optional[str]:
    """
    Пытаемся вытащить диплинк из произвольной строки:
      - ищем все "scheme://..." куски,
      - фильтруем по ключевым словам (qr.nspk.ru, SBPQR://, mcash://),
      - тримим кавычки/скобки по краям.
    """
    if not text:
        return None

    candidates = _DEEPLINK_URL_RE.findall(text)
    if not candidates:
        return None

    def _clean(url: str) -> str:
        return url.strip().strip("',\"()[]{}")

    for raw in candidates:
        url = _clean(raw)
        if any(k in url for k in DEEP_LINK_KEYWORDS):
            return url

    return None


# ============================================================
# ОЖИДАНИЕ ФИНАЛЬНОГО ЭКРАНА
# ============================================================

async def _wait_for_final_screen(page: Page) -> bool:
    """
    Ждём, пока:
      - URL станет .../transfer/uzbekistan/finish-transfer
        ИЛИ
      - на странице появится потенциальный QR-элемент.

    Нужен люфт, чтобы ты успел пройти капчу и нажать кнопку после неё.
    """
    print(f"[STEP4] Ожидание финального экрана (до {FINAL_SCREEN_MAX_WAIT_SECONDS} сек)...")

    start = time.time()
    while time.time() - start < FINAL_SCREEN_MAX_WAIT_SECONDS:
        try:
            url = page.url
        except Exception:
            url = ""

        # 1) финальный URL
        if "/transfer/uzbekistan/finish-transfer" in url:
            print(f"[STEP4] Финальная страница загружена → {url}")
            return True

        # 2) или уже появился QR-элемент (если URL внезапно не меняется)
        try:
            q = page.locator(
                "canvas, "
                "img[src*='qr'], img[src*='QR'], "
                "img[alt*='qr'], img[alt*='QR'], "
                "img[src^='data:image'], img[src^='blob:']"
            ).first
            if await q.count() > 0:
                print("[STEP4] Финальный экран виден (QR-элемент обнаружен).")
                return True
        except Exception:
            pass

        await page.wait_for_timeout(1000)

    print("[STEP4] ⚠ Таймаут ожидания финального экрана.")
    return False


# ============================================================
# ОЖИДАНИЕ ОТВЕТА API С NSPK-ССЫЛКОЙ (через событие response)
# ============================================================

async def _wait_for_nspk_payload(page: Page) -> Optional[str]:
    """
    Ловим ответ от API:
      https://api.multitransfer.ru/.../multitransfer-qr-processing/.../confirm

    Берём externalData.payload – там лежит ссылка вида https://qr.nspk.ru/...
    """
    print("[STEP4] Начинаю слушать network для ответа с NSPK payload...")

    def _is_target_response(response) -> bool:
        try:
            url = response.url
        except Exception:
            return False

        if (
            "api.multitransfer.ru" in url
            and "multitransfer-qr-processing" in url
            and "/confirm" in url
        ):
            return True
        return False

    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def _on_response(response) -> None:
        if fut.done():
            return
        try:
            if _is_target_response(response):
                fut.set_result(response)
        except Exception:
            return

    page.on("response", _on_response)

    try:
        try:
            # Ждём один подходящий ответ не дольше FINAL_SCREEN_MAX_WAIT_SECONDS
            response = await asyncio.wait_for(fut, timeout=FINAL_SCREEN_MAX_WAIT_SECONDS)
        except asyncio.TimeoutError:
            print("[STEP4] Не дождались ответа /confirm (timeout).")
            return None
        except Exception as e:
            print(f"[STEP4] Ошибка ожидания ответа /confirm: {e}")
            return None

        if not response:
            return None

        print(f"[STEP4] Поймали ответ /confirm: {response.url}")

        # Парсим JSON
        try:
            data = await response.json()
        except Exception as e:
            print(f"[STEP4] Ошибка парсинга JSON из /confirm: {e}")
            try:
                text = await response.text()
                print(
                    "[STEP4-DEBUG] Тело ответа /confirm "
                    f"(обрезано до 500 символов): {text[:500]}"
                )
            except Exception:
                pass
            return None

        print(f"[STEP4-DEBUG] JSON /confirm: {data}")

        # Структура ожидается такая:
        # {
        #   "transactionId": "...",
        #   "processingId": "...",
        #   "externalData": {
        #       "payload": "https://qr.nspk.ru/... ?type=02&bank=..."
        #   },
        #   "error": { "code": 0, ... }
        # }
        payload = None
        try:
            external = data.get("externalData") or {}
            payload = external.get("payload")
        except Exception:
            payload = None

        if not payload:
            print("[STEP4] В JSON /confirm не найден externalData.payload")
            return None

        parsed = _extract_deeplink_from_text(str(payload)) or str(payload)
        if parsed and any(k in parsed for k in DEEP_LINK_KEYWORDS):
            print(f"[STEP4] ✓ NSPK payload из /confirm: {parsed}")
            return parsed

        print(f"[STEP4] externalData.payload выглядит странно: {payload!r}")
        return None

    finally:
        try:
            page.off("response", _on_response)
        except Exception:
            pass


# ============================================================
# ОБНОВЛЕНИЕ ЛОКАЛЬНОЙ БД
# ============================================================

def _update_local_invoice(
    invoice_like,
    deeplink: Optional[str],
    status: str,
    error_message: Optional[str] = None,
) -> None:
    """
    Обновляем локальный инвойс в базе Aideon Agent:
      - сначала ищем по внутреннему id (Invoice.id),
      - если не нашли — по внешнему invoice_id (строка),
      - пишем deeplink, статус и error_message.
    """
    try:
        db = SessionLocal()
    except Exception as e:
        print(f"[STEP4-DB] ❌ Не удалось создать сессию БД: {e}")
        return

    try:
        inv = None

        inv_id = getattr(invoice_like, "id", None)
        inv_ext = getattr(invoice_like, "invoice_id", None)

        if inv_id is not None:
            inv = db.query(InvoiceModel).filter(InvoiceModel.id == inv_id).first()

        if not inv and inv_ext is not None:
            inv = (
                db.query(InvoiceModel)
                .filter(InvoiceModel.invoice_id == str(inv_ext))
                .first()
            )

        if not inv:
            print(
                f"[STEP4-DB] ⚠ Не найден инвойс ни по id={inv_id}, "
                f"ни по invoice_id={inv_ext}"
            )
            return

        inv.deeplink = deeplink
        inv.status = status
        inv.error_message = error_message

        db.commit()
        print(
            f"[STEP4-DB] ✔ Обновлён инвойс id={inv.id}: "
            f"status={inv.status}, deeplink={inv.deeplink or '—'}, "
            f"error_message={inv.error_message or '—'}"
        )
    except Exception as e:
        db.rollback()
        print(f"[STEP4-DB] ❌ Ошибка при обновлении инвойса: {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass


# ============================================================
# WEBHOOK
# ============================================================

def _send_webhook(
    invoice,
    deeplink: Optional[str],
    status: str,
    error_reason: Optional[str] = None,
):
    """
    Отправка постбека на joker-pay.com.

    status:
      - "created"      — диплинк успешно получен
      - "No Terminals" — диплинк не получен (нет QR / сети / консоли)
    """
    payload = {
        "invoice_id": getattr(invoice, "id", None),
        "invoice_external_id": getattr(invoice, "invoice_id", None),
        "amount": float(getattr(invoice, "amount", 0) or 0),
        "currency": getattr(invoice, "currency", "RUB"),
        "deeplink": deeplink or "",
        "status": status,
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }

    if error_reason:
        payload["error"] = error_reason

    print(f"[STEP4] POST → {WEBHOOK_URL}")
    print(f"[STEP4] Payload: {payload}")

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"[STEP4] Ответ: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[STEP4] Webhook error: {e}")


# ============================================================
# MAIN STEP 4 — Весь пайплайн
# ============================================================

async def step4_wait_for_deeplink(page: Page, invoice) -> str:
    """
    Финальный шаг:
      1) подписываемся на консоль браузера и собираем все сообщения,
      2) параллельно запускаем ожидание network-ответа /confirm с NSPK-ссылкой,
      3) ждём, пока ты пройдёшь капчу и откроется финальная страница /finish-transfer,
      4) сначала пробуем диплинк из /confirm,
      5) если нет — пробуем диплинк из console.log,
      6) если всё ещё нет — пишем ошибку (без Vision),
      7) при успехе — шлём диплинк вебхуком (status='created') и обновляем локальную БД,
      8) при любой ошибке — вебхук с status='No Terminals' и статус инвойса 'error'.
    """
    print(f"[STEP4] → Ожидание финального экрана для invoice={invoice.id}")

    # --------------------------------------------------------
    # 0. Подписка на console.log
    # --------------------------------------------------------
    console_messages: list[str] = []
    console_deeplink: Optional[str] = None

    def _on_console(msg) -> None:
        nonlocal console_deeplink
        try:
            text = msg.text()
        except Exception:
            text = ""
        if not text:
            return

        console_messages.append(text)

        if console_deeplink is None:
            dl = _extract_deeplink_from_text(text)
            if dl:
                console_deeplink = dl
                print(f"[STEP4-CONSOLE] Найден диплинк в консоли: {dl}")

    page.on("console", _on_console)

    # --------------------------------------------------------
    # 1. Фон: ждём NSPK из network /confirm
    # --------------------------------------------------------
    nspk_task = asyncio.create_task(_wait_for_nspk_payload(page))

    # --------------------------------------------------------
    # 2. Ждём финальную страницу / QR
    # --------------------------------------------------------
    ok = await _wait_for_final_screen(page)
    if not ok:
        if not nspk_task.done():
            nspk_task.cancel()

        await _save_html(page, "final_timeout")
        _save_console_log(console_messages, "console_final_timeout")

        error_msg = (
            "[STEP4] Не дождались финальной страницы с QR "
            "(скорее всего, капча не пройдена или поток не завершён)."
        )

        _update_local_invoice(invoice, deeplink=None, status="error", error_message=error_msg)
        _send_webhook(invoice, deeplink=None, status="No Terminals", error_reason=error_msg)

        raise RuntimeError(error_msg)

    # Чутка ждём, чтобы всё дорисовалось
    await page.wait_for_timeout(QR_APPEAR_DELAY_MS)

    # Дамп финальной HTML для отладки
    await _save_html(page, "final_page_loaded")

    # --------------------------------------------------------
    # 3. Дождаться (немного) результат NSPK-задачи
    # --------------------------------------------------------
    deeplink_from_network: Optional[str] = None
    try:
        if not nspk_task.done():
            deeplink_from_network = await asyncio.wait_for(nspk_task, timeout=5)
        else:
            deeplink_from_network = nspk_task.result()
    except asyncio.TimeoutError:
        print("[STEP4] NSPK-пейлоад по сети не успел за 5 секунд после финальной страницы.")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[STEP4] Ошибка фоновой задачи NSPK: {e}")

    # Сохраняем логи консоли (независимо от исхода)
    _save_console_log(console_messages, "console_after_final")

    # --------------------------------------------------------
    # 4. Если есть диплинк из сети — используем его и выходим
    # --------------------------------------------------------
    if deeplink_from_network:
        deeplink = deeplink_from_network
        print(f"[STEP4] ✓ Диплинк получен из network-response /confirm: {deeplink}")

        await _save_html(page, "qr_found_network")
        await _save_screenshot(page, "final_network")

        _send_webhook(invoice, deeplink=deeplink, status="created")
        _update_local_invoice(invoice, deeplink=deeplink, status="created", error_message=None)

        print(
            f"[DONE] Invoice {getattr(invoice, 'id', '?')} успешно обработан "
            f"(по network /confirm), диплинк: {deeplink!r}"
        )
        return deeplink

    # --------------------------------------------------------
    # 5. Если диплинк нашли в консоли — используем его
    # --------------------------------------------------------
    if console_deeplink:
        deeplink = console_deeplink
        print(f"[STEP4] ✓ Диплинк найден в консоли: {deeplink}")

        await _save_html(page, "qr_found_console")
        await _save_screenshot(page, "final_console")

        _send_webhook(invoice, deeplink=deeplink, status="created")
        _update_local_invoice(invoice, deeplink=deeplink, status="created", error_message=None)

        print(
            f"[DONE] Invoice {getattr(invoice, 'id', '?')} успешно обработан "
            f"(по консоли), диплинк: {deeplink!r}"
        )
        return deeplink

    # --------------------------------------------------------
    # 6. Нет диплинка ни из сети, ни из консоли → ошибка
    # --------------------------------------------------------
    await _save_html(page, "deeplink_not_found")
    await _save_screenshot(page, "deeplink_not_found")

    error_msg = (
        "[STEP4] Не удалось извлечь диплинк ни из ответа /confirm, "
        "ни из console.log (Vision временно отключён)."
    )

    _update_local_invoice(invoice, deeplink=None, status="error", error_message=error_msg)
    _send_webhook(invoice, deeplink=None, status="No Terminals", error_reason=error_msg)

    raise RuntimeError(error_msg)