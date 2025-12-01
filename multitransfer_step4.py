from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import Optional

import requests
from playwright.async_api import Page
import importlib.util

DEBUG_DIR_STEP4 = "debug/multitransfer_step4"
WEBHOOK_URL = "https://joker-pay.com/webhook/tips"

FINAL_SCREEN_MAX_WAIT_SECONDS = 300   # 5 минут на капчу и переход к финалу
QR_APPEAR_DELAY_MS = 2000             # задержка, чтобы QR точно дорисовался


# ============================================================
# ЗАГРУЗКА vision_qr (устойчиво к любому способу запуска)
# ============================================================

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
VISION_PATH = os.path.join(CURRENT_DIR, "vision_qr.py")

# пробуем обычный импорт, если модуль уже в sys.path
try:
    from vision_qr import extract_qr_deeplink_from_screenshot  # type: ignore
    print("[STEP4] vision_qr импортирован как обычный модуль.")
except ImportError:
    print("[STEP4] Обычный импорт vision_qr не удался, пробую загрузить по пути:", VISION_PATH)

    if not os.path.exists(VISION_PATH):
        raise RuntimeError(
            f"[STEP4] Не найден vision_qr.py по пути: {VISION_PATH}. "
            f"Положи vision_qr.py рядом с multitransfer_step4.py."
        )

    spec = importlib.util.spec_from_file_location("vision_qr", VISION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("[STEP4] Не удалось создать spec для vision_qr.py")

    vision_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vision_module)  # type: ignore[attr-defined]

    try:
        extract_qr_deeplink_from_screenshot = vision_module.extract_qr_deeplink_from_screenshot  # type: ignore[attr-defined]
        print("[STEP4] vision_qr успешно загружен через importlib.util.")
    except AttributeError:
        raise RuntimeError(
            "[STEP4] В vision_qr.py не найдена функция "
            "`extract_qr_deeplink_from_screenshot`."
        )


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
    Сохранить скрин всей страницы и вернуть PNG-байты
    (для Vision) + оставить файл для отладки.
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
# WEBHOOK
# ============================================================

def _send_webhook(invoice, deeplink: str):
    payload = {
        "invoice_id": getattr(invoice, "id", None),
        "invoice_external_id": getattr(invoice, "invoice_id", None),
        "amount": float(getattr(invoice, "amount", 0) or 0),
        "currency": getattr(invoice, "currency", "RUB"),
        "deeplink": deeplink,
        "status": "created",
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }

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
      1) ждём, пока ты пройдёшь капчу и откроется финальная страница /finish-transfer,
      2) даём 2 секунды на дорисовку QR,
      3) делаем фуллскрин,
      4) отправляем его в GPT-Vision и вытаскиваем URL,
      5) шлём диплинк вебхуком.
    """
    print(f"[STEP4] → Ожидание финального экрана для invoice={invoice.id}")

    ok = await _wait_for_final_screen(page)
    if not ok:
        await _save_html(page, "final_timeout")
        raise RuntimeError(
            "[STEP4] Не дождались финальной страницы с QR "
            "(скорее всего, капча не пройдена или поток не завершён)."
        )

    # Чутка ждём, чтобы QR точно успел прорендериться
    await page.wait_for_timeout(QR_APPEAR_DELAY_MS)

    # Дамп финальной HTML для отладки
    await _save_html(page, "final_page_loaded")

    # Скриншот для Vision
    png_bytes = await _save_screenshot(page, "fullpage")
    if not png_bytes:
        raise RuntimeError("[STEP4] Не удалось сделать скрин для Vision.")

    # Вызов GPT-Vision
    deeplink = extract_qr_deeplink_from_screenshot(png_bytes)

    if not deeplink:
        await _save_html(page, "vision_fail")
        raise RuntimeError("[STEP4] Vision не смогла извлечь URL из QR-кода.")

    print(f"[STEP4] ✓ Диплинк получен из Vision: {deeplink}")

    await _save_html(page, "qr_found")

    _send_webhook(invoice, deeplink)

    return deeplink