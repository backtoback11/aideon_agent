from __future__ import annotations

import time
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from agent_config import NAVIGATION_TIMEOUT_MS


async def step1_fill_amount_and_open_methods(page: Page, amount: float) -> None:
    """
    Новый STEP1 — адаптация рабочего Node.js-скрипта под Python:

      1) Вводим сумму в поле '0 RUB'
      2) Ждём ~3 секунды, пока фронт всё пересчитает
      3) Находим блок 'Способ перевода' по селектору div.css-1cban0a
      4) Ждём, пока он станет кликабельным (pointer-events / opacity)
      5) Кликаем по нему (Playwright click + программный click() в JS-контексте)
      6) На этом STEP1 заканчивается — меню способов должно быть открыто,
         дальше работает STEP2 (выбор банка и 'Продолжить')
    """

    if amount is None or float(amount) <= 0:
        raise RuntimeError(f"[STEP1] Некорректная сумма: {amount!r}")

    print(f"[STEP1] → Сумма и выбор способа (amount={amount})")

    # Дождёмся базовой загрузки DOM
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    except Exception:
        pass

    # 1) Вводим сумму в инпут (как в референсном скрипте: placeholder '0 RUB')
    amount_input = None
    placeholders = ["0 RUB", "0₽", "0 ₽", "Введите сумму"]

    for ph in placeholders:
        try:
            locator = page.get_by_placeholder(ph).first
            await locator.wait_for(timeout=3_000)
            amount_input = locator
            print(f"[STEP1] Использую инпут по placeholder={ph!r}")
            break
        except PlaywrightTimeoutError:
            continue

    if amount_input is None:
        raise RuntimeError("[STEP1] Не нашли поле суммы по известным placeholder'ам")

    await amount_input.click()
    try:
        await amount_input.fill("")
    except Exception:
        try:
            await amount_input.press("Control+A")
            await amount_input.press("Backspace")
        except Exception:
            pass

    try:
        if int(amount) == amount:
            text_amount = str(int(amount))
        else:
            text_amount = str(amount)
    except Exception:
        text_amount = str(amount)

    print(f"[STEP1] Ручной (type) ввод суммы: {text_amount!r}")
    await amount_input.type(text_amount, delay=120)
    print(f"[STEP1] Сумма заполнена: {text_amount}")

    # 2) Даём фронту время пересчитать курсы, комиссии и т.д. (как в JS: 3 секунды)
    try:
        await page.wait_for_timeout(3_000)
    except Exception:
        pass

    # 3) Новый блок "Способ перевода" — div.css-1cban0a с текстом "Способ перевода"
    print("[STEP1] Ждём появления блока выбора способа перевода (div.css-1cban0a)…")
    method_block = page.locator("div.css-1cban0a", has_text="Способ перевода").first

    try:
        await method_block.wait_for(state="visible", timeout=NAVIGATION_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise RuntimeError(
            "[STEP1] Не нашли видимый блок 'Способ перевода' (div.css-1cban0a)."
        )

    try:
        await method_block.scroll_into_view_if_needed()
    except Exception:
        pass

    print("[STEP1] Нашли блок 'Способ перевода' (div.css-1cban0a)")

    # Необязательная подсветка блока (как в Node-скрипте)
    try:
        method_handle = await method_block.element_handle()
        if method_handle:
            await page.evaluate(
                "(el) => { try { el.style.outline = '2px solid red'; } catch (e) {} }",
                method_handle,
            )
    except Exception:
        pass

    # 4) Ждём, пока блок станет кликабельным (pointer-events != none, opacity > 0.5)
    print("[STEP1] Ждём, пока блок 'Способ перевода' станет кликабельным…")
    try:
        await page.wait_for_function(
            """
            () => {
              const el = document.querySelector('div.css-1cban0a');
              if (!el) return false;
              const s = window.getComputedStyle(el);
              return s.pointerEvents !== 'none' && parseFloat(s.opacity || '1') > 0.5;
            }
            """,
            timeout=30_000,
        )
    except PlaywrightTimeoutError:
        raise RuntimeError(
            "[STEP1] Блок 'Способ перевода' так и не стал кликабельным (pointer-events/opacity)."
        )

    print("[STEP1] Блок 'Способ перевода' кликабелен, кликаю…")

    # 5) Обычный клик Playwright
    await method_block.click(force=True)

    # + программный .click() в JS-контексте (как в Node-скрипте)
    try:
        await page.evaluate(
            """
            () => {
              const el = document.querySelector('div.css-1cban0a');
              if (el && typeof el.click === 'function') {
                el.click();
              }
            }
            """
        )
    except Exception:
        pass

    try:
        await page.wait_for_timeout(800)
    except Exception:
        pass

    try:
        current_url = page.url
    except Exception:
        current_url = "<unknown>"

    print(f"[STEP1] Клик по блоку 'Способ перевода' отправлен. Текущий URL: {current_url!r}")
    print("[STEP1] ✅ STEP1 завершён: сумма введена, меню способов должно быть открыто. Дальше — STEP2.")


async def step2_choose_bank_and_continue(page: Page, bank_name: str = "UZUM BANK") -> None:
    """
    Новый STEP2 — адаптация оставшейся части Node.js-скрипта:

      1) Ждём появление карточек банков: div[role="button"][aria-label]
      2) Логируем найденные карточки (aria-label)
      3) Ищем карточку bank_name (по умолчанию 'UZUM BANK') по aria-label*="UZUM BANK"
      4) Ховерим и кликаем по карточке
         + при наличии дочернего .choose кликаем и по нему
      5) Ждём появления и активации кнопки "Продолжить" (#pay, !disabled)
      6) Кликаем по "Продолжить"
      7) Ждём перехода на /transfer/uzbekistan/sender-details
    """

    print(f"[STEP2] → Выбор банка/оффера (bank={bank_name!r})")

    # 1) Ждём появления карточек банков
    print("[STEP2] Жду появления банковских карточек (div[role='button'][aria-label])…")
    try:
        await page.wait_for_function(
            """
            () => {
              const cards = document.querySelectorAll('div[role="button"][aria-label]');
              return cards.length > 0;
            }
            """,
            timeout=30_000,
        )
    except PlaywrightTimeoutError:
        try:
            body_sample = await page.evaluate(
                "() => (document.body.innerText || '').slice(0, 800)"
            )
        except Exception:
            body_sample = "<unavailable>"

        raise RuntimeError(
            "[STEP2] Не дождались банковских карточек после открытия 'Способ перевода'. "
            f"Фрагмент текста страницы: {body_sample!r}"
        )

    bank_buttons = page.locator('div[role="button"][aria-label]')
    bank_count = await bank_buttons.count()
    print(f"[STEP2] Найдено карточек банков: {bank_count}")

    max_log = min(bank_count, 12)
    for i in range(max_log):
        aria = await bank_buttons.nth(i).get_attribute("aria-label")
        print(f"[STEP2]   Card[{i}] aria-label={aria!r}")

    # 2) Ищем карточку нужного банка по aria-label*="UZUM BANK"
    uzum_selector = f'div[role="button"][aria-label*="{bank_name}"]'
    uzum_card = page.locator(uzum_selector).first

    try:
        await uzum_card.wait_for(state="visible", timeout=10_000)
    except PlaywrightTimeoutError:
        raise RuntimeError(
            f"[STEP2] Не нашли видимую карточку банка по селектору {uzum_selector!r}"
        )

    try:
        await uzum_card.scroll_into_view_if_needed()
    except Exception:
        pass

    print(f"[STEP2] Карточка банка {bank_name!r} найдена, ховерим и кликаем…")

    try:
        await uzum_card.hover()
        await page.wait_for_timeout(500)
    except Exception:
        pass

    await uzum_card.click(force=True)
    print(f"[STEP2] Клик по карточке {bank_name!r} выполнен.")

    # 3) Если внутри карточки есть кнопка ".choose" — кликаем и по ней (как в Node-скрипте)
    choose_btn = uzum_card.locator(".choose")
    try:
        if await choose_btn.is_visible():
            print("[STEP2] Кнопка '.choose' видна, кликаем по ней…")
            await choose_btn.click(force=True)
            print("[STEP2] Клик по '.choose' выполнен.")
        else:
            print("[STEP2] Кнопка '.choose' не видна, ограничились кликом по карточке.")
    except Exception:
        print("[STEP2] Ошибка при проверке/клике по '.choose', продолжаю дальше.")

    # 4) Ждём появления и активации кнопки 'Продолжить' (#pay)
    print('[STEP2] Жду появления кнопки "Продолжить" (#pay)…')
    continue_btn = page.locator("#pay")

    try:
        await continue_btn.wait_for(state="visible", timeout=30_000)
    except PlaywrightTimeoutError:
        raise RuntimeError("[STEP2] Кнопка 'Продолжить' (#pay) не появилась.")

    print('[STEP2] Кнопка "Продолжить" видна, жду пока станет активной (не disabled)…')
    try:
        await page.wait_for_function(
            """
            () => {
              const btn = document.querySelector('#pay');
              if (!btn) return false;
              return !btn.hasAttribute('disabled');
            }
            """,
            timeout=60_000,
        )
    except PlaywrightTimeoutError:
        raise RuntimeError(
            "[STEP2] Кнопка 'Продолжить' (#pay) так и не стала активной (disabled не снят)."
        )

    try:
        await continue_btn.scroll_into_view_if_needed()
    except Exception:
        pass

    print('[STEP2] Кнопка "Продолжить" активна, кликаю…')
    await continue_btn.click(force=True)

    # 5) Ждём перехода на sender-details
    print("[STEP2] Жду перехода на страницу sender-details…")
    try:
        await page.wait_for_url("**/transfer/uzbekistan/sender-details**", timeout=60_000)
    except PlaywrightTimeoutError:
        try:
            cur_url = page.url
        except Exception:
            cur_url = "<unknown>"

        raise RuntimeError(
            "[STEP2] Не дождались перехода на '/transfer/uzbekistan/sender-details' "
            f"после нажатия 'Продолжить'. Текущий URL: {cur_url!r}"
        )

    try:
        cur_url = page.url
    except Exception:
        cur_url = "<unknown>"

    print(f"[STEP2] ✅ Перешли на страницу sender-details: {cur_url!r}")
    print("[STEP2] ✅ STEP2 завершён: банк выбран, 'Продолжить' нажата, мы на форме отправителя.")