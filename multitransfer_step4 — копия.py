from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional
import re
import asyncio
import json

import requests
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from db import SessionLocal
from models import Invoice as InvoiceModel

DEBUG_DIR_STEP4 = "debug/multitransfer_step4"
WEBHOOK_URL = "https://joker-pay.com/webhook/tips"

# Как в JS-скрипте
CONFIRM_PATH = "/anonymous/multi/multitransfer-qr-processing/v3/anonymous/confirm"

# 20 минут на капчу и ручные действия (как в JS)
CONFIRM_MAX_WAIT_MS = 20 * 60 * 1000

print("[STEP4] *** NEW VERSION: wait_for_response(/confirm) + finish-transfer, без Vision ***")


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


# ============================================================
# PARSING DEEPLINK FROM TEXT
# ============================================================

DEEP_LINK_KEYWORDS = [
    "qr.nspk.ru",
    "SBPQR://",
    "sbpqr://",
    "mcash://",
]

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
# MAIN STEP 4 — логика как в finish_transfer.js
# ============================================================

async def step4_wait_for_deeplink(page: Page, invoice) -> str:
    """
    Финальный шаг (адаптация finish_transfer.js):

      1) Включаем "heartbeat"-лог — раз в 5 секунд пишем текущий URL.
      2) Ждём ответ /confirm (POST на CONFIRM_PATH) через событие "response" + asyncio.Future.
      3) Параллельно (необязательно) ждём перехода на /finish-transfer.
      4) Разбираем JSON, достаём externalData.payload.
      5) Пытаемся вытащить NSPK-URL (qr.nspk.ru и т.п.).
      6) При успехе:
           - пишем в БД (status='created', deeplink),
           - отправляем webhook (status='created').
      7) При любой ошибке:
           - пишем статус 'error' в БД,
           - webhook с status='No Terminals',
           - выбрасываем RuntimeError.
    """

    print(f"[STEP4] → Включили режим ожидания finish-transfer и confirm для invoice={invoice.id}")
    print("Сейчас ты можешь руками проходить капчу / нажимать кнопки.")

    start_ts = time.time()
    done = False

    # --------------------------------------------------------
    # Heartbeat-лог, чтобы было видно, что скрипт жив
    # --------------------------------------------------------
    async def _heartbeat():
        nonlocal done
        while not done:
            elapsed = time.time() - start_ts
            try:
                current_url = page.url
            except Exception:
                current_url = "<unknown>"
            print(f"[STEP4-WAIT] {elapsed:.1f} сек. Текущий URL: {current_url}")
            try:
                await page.wait_for_timeout(5000)
            except Exception:
                await asyncio.sleep(5)

    hb_task = asyncio.create_task(_heartbeat())

    try:
        # ----------------------------------------------------
        # 1️⃣ Ждём ответ confirm (там лежит QR payload)
        # ----------------------------------------------------
        print("[STEP4] Ждём ответа confirm (POST на CONFIRM_PATH)...")

        def _is_confirm_response(resp) -> bool:
            try:
                url = resp.url
                method = resp.request.method
            except Exception:
                return False

            ok = (CONFIRM_PATH in url) and (method.upper() == "POST")
            if ok:
                print(f"[STEP4] 👉 Поймали запрос confirm: {url}")
            return ok

        loop = asyncio.get_running_loop()
        confirm_fut: asyncio.Future = loop.create_future()

        def _on_response(resp) -> None:
            if confirm_fut.done():
                return
            try:
                if _is_confirm_response(resp):
                    confirm_fut.set_result(resp)
            except Exception:
                return

        page.on("response", _on_response)

        try:
            timeout_sec = CONFIRM_MAX_WAIT_MS / 1000.0
            try:
                confirm_resp = await asyncio.wait_for(confirm_fut, timeout=timeout_sec)
            except asyncio.TimeoutError as e:
                done = True
                print(f"[STEP4] ❌ Не дождались ответа confirm: {e}")
                error_msg = "[STEP4] Не дождались ответа confirm (таймаут ожидания)."

                await _save_html(page, "confirm_timeout")
                await _save_screenshot(page, "confirm_timeout")

                _update_local_invoice(
                    invoice,
                    deeplink=None,
                    status="error",
                    error_message=error_msg,
                )
                _send_webhook(
                    invoice,
                    deeplink=None,
                    status="No Terminals",
                    error_reason=error_msg,
                )

                raise RuntimeError(error_msg)
        finally:
            try:
                page.off("response", _on_response)
            except Exception:
                pass

        # ----------------------------------------------------
        # 2️⃣ Параллельно пробуем дождаться URL /finish-transfer (как бонус)
        # ----------------------------------------------------
        try:
            await page.wait_for_url(
                "**/transfer/uzbekistan/finish-transfer",
                timeout=60_000,  # 60 секунд, как в JS
            )
            print("✅ URL сменился на /transfer/uzbekistan/finish-transfer")
        except PlaywrightTimeoutError:
            try:
                current_url = page.url
            except Exception:
                current_url = "<unknown>"
            print(
                "⚠️ Не успели дождаться URL /finish-transfer, но confirm уже есть. "
                f"Текущий URL: {current_url}"
            )

        # ----------------------------------------------------
        # 3️⃣ Разбираем JSON и достаём payload
        # ----------------------------------------------------
        print("[STEP4] Пытаюсь прочитать JSON ответа confirm...")
        raw_text: Optional[str] = None
        data = None

        try:
            # Иногда json() может падать, поэтому сначала text(), потом json.loads
            raw_text = await confirm_resp.text()
            try:
                data = json.loads(raw_text)
            except Exception as e_json:
                print(f"[STEP4] ❌ json.loads(raw_text) упал: {e_json}")
                data = None
        except Exception as e_body:
            print(f"[STEP4] ❌ Не удалось прочитать тело ответа confirm: {e_body}")
            raw_text = None
            data = None

        if data is None and raw_text:
            # Дампим кусок raw_text на всякий случай
            print(
                "[STEP4-DEBUG] Тело ответа confirm (обрезано до 500 символов): "
                f"{raw_text[:500]}"
            )

        print("[STEP4] 📦 Полный ответ confirm (parsed dict):")
        print(data)

        payload_raw = None
        if isinstance(data, dict):
            try:
                payload_raw = (data.get("externalData") or {}).get("payload")
            except Exception:
                payload_raw = None

        if not payload_raw and raw_text:
            # Попробуем найти диплинк в сыром тексте (fallback)
            payload_raw = _extract_deeplink_from_text(raw_text)

        if not payload_raw:
            error_msg = "⚠️ externalData.payload не найден в ответе confirm"
            print(f"[STEP4] {error_msg}")

            await _save_html(page, "confirm_no_payload")
            await _save_screenshot(page, "confirm_no_payload")

            _update_local_invoice(
                invoice,
                deeplink=None,
                status="error",
                error_message=error_msg,
            )
            _send_webhook(
                invoice,
                deeplink=None,
                status="No Terminals",
                error_reason=error_msg,
            )

            raise RuntimeError(error_msg)

        payload_str = str(payload_raw)
        deeplink = _extract_deeplink_from_text(payload_str) or payload_str

        if not any(k in deeplink for k in DEEP_LINK_KEYWORDS):
            print(f"[STEP4] externalData.payload выглядит странно: {payload_str!r}")
            error_msg = (
                "[STEP4] externalData.payload не похож на NSPK/SBP диплинк. "
                f"payload={payload_str!r}"
            )

            await _save_html(page, "confirm_payload_strange")
            await _save_screenshot(page, "confirm_payload_strange")

            _update_local_invoice(
                invoice,
                deeplink=None,
                status="error",
                error_message=error_msg,
            )
            _send_webhook(
                invoice,
                deeplink=None,
                status="No Terminals",
                error_reason=error_msg,
            )

            raise RuntimeError(error_msg)

        # ----------------------------------------------------
        # 4️⃣ Успех: диплинк есть
        # ----------------------------------------------------
        print("🔗 NSPK QR payload (ссылка на QR):")
        print(deeplink)

        await _save_html(page, "finish_success")
        await _save_screenshot(page, "finish_success")

        _update_local_invoice(invoice, deeplink=deeplink, status="created", error_message=None)
        _send_webhook(invoice, deeplink=deeplink, status="created", error_reason=None)

        print(
            f"[DONE] Invoice {getattr(invoice, 'id', '?')} успешно обработан "
            f"(по confirm), диплинк: {deeplink!r}"
        )

        return deeplink

    finally:
        # Останавливаем heartbeat
        done = True
        try:
            hb_task.cancel()
        except Exception:
            pass