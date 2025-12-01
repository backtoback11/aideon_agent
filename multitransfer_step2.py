from __future__ import annotations

import os
from datetime import datetime
from typing import Optional, List

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from agent_config import NAVIGATION_TIMEOUT_MS

# Папка для дампов второго шага
DEBUG_DIR = "debug/multitransfer_step2"


def _ensure_debug_dir() -> None:
    """Гарантируем, что папка для дампов существует."""
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
    except Exception as e:
        print(f"[STEP2-DEBUG] Не удалось создать папку {DEBUG_DIR}: {e}")


async def _save_step2_html(page: Page, label: str) -> None:
    """
    Сохранение только HTML для второго шага
    в debug/multitransfer_step2/{label}_step2_YYYYMMDD_HHMMSS.html
    """
    _ensure_debug_dir()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    html_path = os.path.join(DEBUG_DIR, f"{label}_step2_{ts}.html")

    try:
        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[STEP2-DEBUG] HTML страницы сохранён в {html_path}")
    except Exception as e:
        print(f"[STEP2-DEBUG] Не удалось сохранить HTML: {e}")


async def step2_select_bank(page: Page, bank_name: Optional[str]) -> None:
    """
    Шаг 2:
      - ждём прогрузку списка
      - ищем банк по любому из вариантов текста
      - если нашли → кликаем по банку
      - затем нажимаем первую кнопку 'Продолжить'
      - затем ждём появления формы получателя
    """
    bank_name = (bank_name or "").strip()
    print(f"[STEP2] → Выбор банка/оффера (bank={bank_name!r})")

    await page.wait_for_timeout(4000)
    await _save_step2_html(page, label="before_select")

    if not bank_name:
        raise RuntimeError(
            "[STEP2] BANK_NOT_SPECIFIED: В инвойсе не указан банк (recipient_bank пустой)"
        )

    search_variants: List[str] = [bank_name]
    parts = bank_name.split()
    for p in parts:
        if len(p) >= 3 and p not in search_variants:
            search_variants.append(p)

    print(f"[STEP2] Буду искать банк по вариантам текста: {search_variants}")

    clicked = False

    # ----------- 2 волны попыток поиска -----------
    for attempt in (1, 2):
        if clicked:
            break

        if attempt == 2:
            print("[STEP2] Вторая попытка — страница могла долго грузиться…")
            await page.wait_for_timeout(4000)
            await _save_step2_html(page, label="retry_before_select")

        for text_variant in search_variants:
            try:
                print(f"[STEP2] Попытка #{attempt}: ищу '{text_variant}'…")

                candidate = page.get_by_text(text_variant, exact=False).first
                await candidate.wait_for(timeout=12000)
                await candidate.scroll_into_view_if_needed()
                print(f"[STEP2] Найден текст '{text_variant}', ищем контейнер…")

                container = candidate

                btn = candidate.locator("xpath=ancestor-or-self::button[1]")
                if await btn.count() > 0:
                    container = btn.first
                else:
                    btn_role = candidate.locator("xpath=ancestor-or-self::*[@role='button'][1]")
                    if await btn_role.count() > 0:
                        container = btn_role.first
                    else:
                        fallback = candidate.locator(
                            "xpath=ancestor-or-self::*[self::li or self::div or self::a][1]"
                        )
                        if await fallback.count() > 0:
                            container = fallback.first

                if await container.count() == 0:
                    print(f"[STEP2] ⚠ Не нашли кликабельный контейнер для '{text_variant}'")
                    continue

                await container.scroll_into_view_if_needed()
                await container.click()
                print(f"[STEP2] ✅ Кликнули банк '{text_variant}'")
                clicked = True
                break

            except PlaywrightTimeoutError:
                print(f"[STEP2] ⚠ '{text_variant}' не найден (таймаут)")
            except Exception as e:
                print(f"[STEP2] ⚠ Ошибка при обработке '{text_variant}': {e}")

    # ----------- Банк НЕ найден -----------
    if not clicked:
        print(f"[STEP2] ❌ BANK_NOT_FOUND: {bank_name!r}")
        await _save_step2_html(page, label="bank_not_found")
        raise RuntimeError(
            f"[STEP2] BANK_NOT_FOUND: Банк {bank_name!r} не найден среди вариантов. "
            "Требуется заменить реквизиты получателя."
        )

    # ----------- Нажимаем кнопку ПРОДОЛЖИТЬ -----------
    print("[STEP2] Клик по банку выполнен, ищу кнопку 'Продолжить'…")

    try:
        await page.wait_for_timeout(1600)
        await _save_step2_html(page, label="before_continue")

        continue_btn = page.get_by_role("button", name="Продолжить").first
        await continue_btn.wait_for(timeout=12000)
        await continue_btn.scroll_into_view_if_needed()
        await continue_btn.click()

        print("[STEP2] ✅ Нажали 'Продолжить'")
    except PlaywrightTimeoutError:
        await _save_step2_html(page, label="continue_not_found")
        raise RuntimeError("[STEP2] Не найдена кнопка 'Продолжить' после выбора банка")
    except Exception as e:
        await _save_step2_html(page, label="continue_click_error")
        raise RuntimeError(f"[STEP2] Ошибка при клике по 'Продолжить': {e}")

    # ----------- Ждём форму получателя -----------
    print("[STEP2] Ожидаю форму получателя…")

    try:
        await page.wait_for_selector(
            "input[name='transfer_beneficiaryAccountNumber']",
            timeout=NAVIGATION_TIMEOUT_MS,
        )
        print("[STEP2] ✅ Форма получателя загружена")
    except PlaywrightTimeoutError:
        await _save_step2_html(page, label="after_continue_error")
        raise RuntimeError(
            "[STEP2] После 'Продолжить' не появилась форма получателя. "
            "Возможно баг сайта / редирект / ошибка DOM."
        )