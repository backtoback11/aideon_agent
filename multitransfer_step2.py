from __future__ import annotations

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from agent_config import NAVIGATION_TIMEOUT_MS


# === Обёртка под старое имя, чтобы agent.py не ломался ===
async def step2_select_bank(page: Page, bank_name: str = "UZUM BANK") -> None:
    """
    Старое публичное API, которое ждёт agent.py.
    Внутри просто вызывает новый STEP2.
    """
    await step2_choose_bank_and_continue(page, bank_name=bank_name)


# === Новый STEP2, адаптированный с рабочего Node-скрипта ===
async def step2_choose_bank_and_continue(page: Page, bank_name: str = "UZUM BANK") -> None:
    """
    Новый STEP2 — адаптация рабочего Node.js-скрипта:

      1) Ждём появление карточек банков: div[role="button"][aria-label]
      2) Логируем найденные карточки (aria-label)
      3) Ищем карточку bank_name (по умолчанию 'UZUM BANK') по aria-label*="UZUM BANK" (case-insensitive)
      4) Ховерим и кликаем по карточке
         + при наличии дочернего .choose кликаем и по нему
      5) Ждём появления и активации кнопки "Продолжить" (#pay)
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

    # 2) Ищем карточку нужного банка
    # Используем case-insensitive селектор ([attr*=value i]), как в браузере
    uzum_selector = f'div[role="button"][aria-label*="{bank_name}" i]'
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

    # 3) Если внутри карточки есть кнопка ".choose" — кликаем и по ней
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