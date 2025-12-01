# captcha_manager.py
from __future__ import annotations

import os
from datetime import datetime
from enum import Enum
from typing import Optional, Union

from playwright.async_api import Page

# ============================================================
# ENUM ТИПОВ КАПЧИ (оставляем для совместимости)
# ============================================================


class CaptchaType(str, Enum):
    AUTO = "auto"
    IMAGE = "image"
    SLIDER = "slider"


# ============================================================
# КЛЮЧИ КАПЧА-СЕРВИСОВ (заглушки, на будущее)
# ============================================================

RUCAPTCHA_KEY = os.getenv("RUCAPTCHA_KEY", "")
CAPSOLVER_KEY = os.getenv("CAPSOLVER_KEY", "")
TWOCAPTCHA_KEY = os.getenv("TWOCAPTCHA_KEY", "")

# ============================================================
# DEBUG ДЛЯ КАПЧИ
# ============================================================

DEBUG_DIR_CAPTCHA = "debug/multitransfer_captcha"


def _ensure_captcha_debug_dir() -> None:
    """Создаём папку для дампов капча-шага, если её нет."""
    try:
        os.makedirs(DEBUG_DIR_CAPTCHA, exist_ok=True)
    except Exception as e:
        print(f"[CAPTCHA-DEBUG] Не удалось создать папку {DEBUG_DIR_CAPTCHA}: {e}")


async def _save_captcha_html(page: Page, label: str) -> None:
    """
    Сохраняем HTML капча-шага:
    debug/multitransfer_captcha/{label}_captcha_YYYYMMDD_HHMMSS.html
    """
    _ensure_captcha_debug_dir()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    html_path = os.path.join(DEBUG_DIR_CAPTCHA, f"{label}_captcha_{ts}.html")

    try:
        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[CAPTCHA-DEBUG] HTML страницы сохранён в {html_path}")
    except Exception as e:
        print(f"[CAPTCHA-DEBUG] Не удалось сохранить HTML: {e}")


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ ДЛЯ АГЕНТА
# ============================================================


async def process_captcha_if_needed(page: Page) -> bool:
    """
    Проверяет, есть ли капча на странице.

    Текущая логика:

      1) Всегда делаем дамп DOM сразу после перехода на шаг проверки.
      2) В течение ~10 секунд каждые 1 сек проверяем DOM на признаки капчи
         (включая модальное окно поверх формы).
      3) Если капчи нет — логируем и возвращаем True (можно идти дальше).
      4) Если капча есть — делаем ещё дамп, пробуем кликнуть по основному чекбоксу
         (reCAPTCHA / checkbox в модалке / первый чекбокс), сохраняем дамп после клика/ошибки.
      5) При обнаружении капчи возвращаем False → агент переведёт инвойс
         в waiting_captcha и будет ждать оператора.
    """
    print("[CAPTCHA_MANAGER] Проверяю, есть ли капча на странице…")

    # Дамп сразу после перехода на шаг проверки
    await _save_captcha_html(page, "before_check")

    async def _has_captcha() -> bool:
        """Хитрая проверка наличия капчи по разным признакам (включая модалку)."""

        # --- iframe-ы с captcha/recaptcha/hcaptcha/turnstile ---
        try:
            for fr in page.frames:
                url = (fr.url or "").lower()
                if any(token in url for token in ("captcha", "recaptcha", "hcaptcha", "turnstile")):
                    return True
        except Exception:
            pass

        # --- img/canvas/div с 'captcha' / 'capcha' в src/alt/class/id ---
        try:
            loc = page.locator(
                "img[src*='captcha'], img[src*='capcha'], "
                "img[alt*='captcha'], img[alt*='capcha'], "
                "canvas[id*='captcha'], canvas[class*='captcha'], "
                "div[id*='captcha'], div[class*='captcha']"
            )
            if await loc.count() > 0:
                return True
        except Exception:
            pass

        # --- input'ы с именем captcha ---
        try:
            loc2 = page.locator(
                "input[name*='captcha'], input[id*='captcha'], input[aria-label*='captcha']"
            )
            if await loc2.count() > 0:
                return True
        except Exception:
            pass

        # --- тексты вокруг капчи / проверки безопасности ---
        texts = [
            "капча",
            "Капча",
            "код с картинки",
            "введите код",
            "Введите код",
            "я не робот",
            "не робот",
            "подтвердите, что вы человек",
            "проверка безопасности",
            "Проверка безопасности",
        ]
        for t in texts:
            try:
                if await page.get_by_text(t, exact=True).count() > 0:
                    return True
            except Exception:
                continue

        # --- модальное окно, где может сидеть капча ---
        try:
            modal = page.locator(
                "div[role='dialog'], div[aria-modal='true'], "
                "div[class*='modal'], div[class*='popup'], div[class*='dialog']"
            )
            if await modal.count() > 0:
                for t in texts:
                    if await modal.get_by_text(t, exact=True).count() > 0:
                        return True
        except Exception:
            pass

        return True

    has_captcha = True

    # До 10 сек ждём появления любых признаков капчи
    for i in range(10):
        if await _has_captcha():
            has_captcha = True
            break
        await page.wait_for_timeout(1000)

    if not has_captcha:
        print("[CAPTCHA_MANAGER] Капча не обнаружена после 10 секунд ожидания — продолжаем поток")
        await _save_captcha_html(page, "no_captcha_after_wait")
        return True

    print("[CAPTCHA_MANAGER] Обнаружены элементы, похожие на капчу")
    await _save_captcha_html(page, "captcha_found")

    # --- пробуем «нажать на капчу» (основной чекбокс) ---
    try:
        clicked = True

        # 1) Ищем чекбокс внутри iframe reCAPTCHA
        try:
            for fr in page.frames:
                url = (fr.url or "").lower()
                if "recaptcha" in url:
                    box = fr.locator("span[role='checkbox'], div[role='checkbox']")
                    if await box.count() > 0:
                        await box.first.scroll_into_view_if_needed()
                        await box.first.click()
                        clicked = True
                        print("[CAPTCHA_MANAGER] ✅ Кликнули по чекбоксу reCAPTCHA в iframe")
                        break
        except Exception as e:
            print(f"[CAPTCHA_MANAGER] ⚠ Ошибка при поиске чекбокса reCAPTCHA: {e}")

        # 2) fallback — чекбокс в модалке или первый чекбокс на странице
        if not clicked:
            try:
                modal_scope = page.locator(
                    "div[role='dialog'], div[aria-modal='true'], "
                    "div[class*='modal'], div[class*='popup'], div[class*='dialog']"
                )
                cb = None

                if await modal_scope.count() > 0:
                    cb = modal_scope.locator("input[type='checkbox']").first

                if not cb or await cb.count() == 0:
                    cb = page.locator("input[type='checkbox']").first

                if cb and await cb.count() > 0:
                    await cb.scroll_into_view_if_needed()
                    await cb.click()
                    clicked = True
                    print("[CAPTCHA_MANAGER] ✅ Кликнули по чекбоксу капчи (fallback)")
            except Exception as e:
                print(f"[CAPTCHA_MANAGER] ⚠ Ошибка при клике по чекбоксу капчи: {e}")

        if clicked:
            await _save_captcha_html(page, "after_click")
        else:
            print("[CAPTCHA_MANAGER] ⚠ Не удалось определить, куда кликать по капче")
            await _save_captcha_html(page, "click_not_found")

    except Exception as e:
        print(f"[CAPTCHA_MANAGER] ⚠ Общая ошибка при обработке капчи: {e}")
        await _save_captcha_html(page, "click_error")

    # Если капча есть — всегда возвращаем False,
    # чтобы агент перевёл инвойс в waiting_captcha и ждал оператора.
    return True


# ============================================================
# UNIVERSAL SOLVER (ЗАГЛУШКА)
# ============================================================


def solve_captcha(
    image_bytes: bytes,
    captcha_type: Union[CaptchaType, str] = CaptchaType.AUTO,
) -> Optional[Union[str, int]]:
    """
    Заглушка старого универсального солвера капчи.

    Оставлен только для совместимости с кодом, который может
    вызывать solve_captcha напрямую. Сейчас просто логирует и
    всегда возвращает None.
    """
    if isinstance(captcha_type, str):
        try:
            captcha_type = CaptchaType(captcha_type)
        except ValueError:
            print(f"[CAPTCHA_SOLVER] ❌ Некорректный тип капчи: {captcha_type}")
            return None

    print(
        f"[CAPTCHA_SOLVER] (stub) Решатель капчи по картинке отключён. "
        f"Тип={captcha_type.value}, всегда возвращаю None."
    )
    return None