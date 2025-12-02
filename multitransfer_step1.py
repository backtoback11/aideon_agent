from __future__ import annotations

from datetime import datetime
from pathlib import Path  # ← добавили

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from agent_config import NAVIGATION_TIMEOUT_MS


async def step1_fill_amount_and_open_methods(page: Page, amount: float) -> None:
    """
    Шаг 1:
      - ждём блок 'Сумма отправления'
      - вводим сумму
      - даём форме обновить курс (строка с курсом и пр.)
      - снимаем дамп DOM/скрина после ввода суммы
      - кликаем по строке 'Выберите способ перевода', чтобы открыть список офферов/банков
      - НЕ ждём появления карточек банков/кнопок 'Выбрать' — этим займётся второй шаг
    """
    print(f"[STEP1] → Сумма и открытие списка способов (amount={amount})")

    # Ждём загрузку блока суммы
    await page.get_by_text("Сумма отправления").wait_for(timeout=NAVIGATION_TIMEOUT_MS)
    print("[STEP1] Найден блок 'Сумма отправления'")

    # --- инпут суммы ---
    amount_input = None

    # 1) по placeholder '0 RUB'
    try:
        amount_input = page.get_by_placeholder("0 RUB")
        await amount_input.wait_for(timeout=5000)
        print("[STEP1] Использую инпут по placeholder='0 RUB'")
    except TimeoutError as e:  # на случай, если импортировали не то имя
        raise e
    except PlaywrightTimeoutError:
        print("[STEP1] ⚠ Не нашли placeholder='0 RUB', пробуем fallback...")
        amount_input = None

    # 2) fallback — первый подходящий инпут
    if amount_input is None:
        try:
            amount_input = page.locator(
                "input[name='amount'], "
                "input[type='number'], "
                "input[inputmode='decimal'], "
                "input[type='text']"
            ).first
            await amount_input.wait_for(timeout=5000)
            print("[STEP1] Использую первый numeric/text инпут как сумму (fallback)")
        except PlaywrightTimeoutError:
            raise RuntimeError("[STEP1] Не удалось найти поле для суммы")

    await amount_input.click()
    await amount_input.fill(str(amount))
    print(f"[STEP1] Сумма заполнена: {amount}")

    # ждём пересчёта, чтобы форма «приняла» сумму и добавила строку с курсом
    await page.wait_for_timeout(5000)

    # === DEBUG: дамп после ввода суммы ===
    try:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        # новая структура: debug/multitransfer_step1/...
        debug_dir = Path("debug") / "multitransfer_step1"
        debug_dir.mkdir(parents=True, exist_ok=True)

        html_path = debug_dir / f"debug_step1_after_amount_{ts}.html"
        png_path = debug_dir / f"debug_step1_after_amount_{ts}.png"

        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[STEP1-DEBUG] DOM после ввода суммы сохранён в {html_path}")

        await page.screenshot(path=str(png_path), full_page=True)
        print(f"[STEP1-DEBUG] Скрин после ввода суммы сохранён в {png_path}")
    except Exception as e:
        print(f"[STEP1-DEBUG] Не удалось сохранить дамп после суммы: {e}")

    # --- открываем строку "Выберите способ перевода" ---
    print("[STEP1] Ищу строку 'Выберите способ перевода'...")

    try:
        method_row = page.get_by_text("Выберите способ перевода", exact=False).first
        await method_row.wait_for(timeout=8000)
        await method_row.click()
        print("[STEP1] Клик по 'Выберите способ перевода' выполнен")
    except PlaywrightTimeoutError:
        print("[STEP1] ⚠ Не нашли по чистому тексту, пробуем более общий вариант...")
        try:
            container = page.locator("button, div[role='button']").filter(
                has_text="способ перевода"
            ).first
            await container.wait_for(timeout=8000)
            await container.click()
            print("[STEP1] Клик по контейнеру способа перевода выполнен (fallback)")
        except PlaywrightTimeoutError:
            # здесь дом сохранён уже выше (после суммы), поэтому просто падаем
            raise RuntimeError("[STEP1] Не удалось открыть список способов перевода")

    # даём списку способов/банков прогрузиться, но НИЧЕГО не ждём специфического
    await page.wait_for_timeout(1000)
    print("[STEP1] ✅ Шаг 1 завершён (список способов должен быть открыт, дальше работает STEP2)")