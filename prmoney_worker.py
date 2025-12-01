# prmoney_worker.py
from __future__ import annotations

import asyncio

from playwright.async_api import Browser

from processed_store import ProcessedStore
from prmoney_fetcher import fetch_pending_invoices
from prmoney_invoice import PrmoneyInvoice

# Когда будем подключать реальный мульти-трансфер — раскомментируем:
# from multitransfer_step1 import step1_fill_amount_and_open_methods
# from multitransfer_step2 import step2_fill_recipient
# from multitransfer_step3 import step3_confirm_and_go
# from multitransfer_step4 import step4_wait_for_deeplink


async def process_invoice(browser: Browser, inv: PrmoneyInvoice, store: ProcessedStore) -> None:
    """
    Обработка ОДНОГО инвойса PrMoney.

    Здесь место для интеграции с твоим Multitransfer-потоком:
      1) открыть страницу multitransfer,
      2) step1: выставить сумму,
      3) step2: заполнить получателя (holder + card_number),
      4) step3: согласие/кнопки,
      5) step4: дождаться диплинка (Vision) и он сам улетит вебхуком.
    """
    print(
        f"\n[WORKER] → Начинаем обработку external_id={inv.id}, "
        f"amount={inv.amount}, card={inv.card_number}, holder={inv.holder}"
    )

    # отмечаем, что инвойс в обработке — чтобы не взять повторно
    store.mark_processing(inv.id)

    # Открываем отдельную вкладку под этот инвойс
    page = await browser.new_page()

    try:
        # TODO: здесь подключаем твой существующий сценарий multitransfer.
        # Ниже пример скелета — по факту ты подставишь свои URL и шаги.

        # await page.goto("https://multitransfer.ru/transfer/uzbekistan", wait_until="load")
        # await step1_fill_amount_and_open_methods(page, inv.amount)
        # await step2_fill_recipient(page, full_name=inv.holder, card_number=inv.card_number)
        # await step3_confirm_and_go(page)
        # deeplink = await step4_wait_for_deeplink(page, inv)

        # Временный мок, чтобы не ломать существующий агент:
        print(f"[WORKER] (mock) Обрабатываю invoice={inv.id}, пока без реального multitransfer-потока.")
        deeplink = None  # сюда позже придёт реальный диплинк

        # Если дошли сюда без исключений — считаем инвойс успешно обработанным
        store.mark_done(inv.id)
        print(f"[WORKER] ✔ invoice={inv.id} отмечен как обработанный (external_id={inv.id}).")

    except Exception as e:
        print(f"[WORKER] ❌ Ошибка обработки invoice={inv.id}: {e}")
        store.mark_failed(inv.id)
    finally:
        # Закрываем вкладку
        try:
            await page.close()
        except Exception:
            pass


async def prmoney_loop(browser: Browser, poll_interval_sec: int = 3) -> None:
    """
    Основной цикл воркера:
      - каждые poll_interval_sec секунд тянем список инвойсов из PrMoney,
      - берём только новые (ещё не processed и не в processing),
      - для каждого нового инвойса создаём отдельную async-задачу.

    Обработка инвойсов идёт параллельно, но без повторов.
    """
    store = ProcessedStore()

    while True:
        print("\n[WORKER] === ЗАПРОС К PRMONEY /test1 ===")
        invoices = fetch_pending_invoices()

        for inv in invoices:
            # фильтр старых/дублей
            if not store.is_new(inv.id):
                continue

            print(f"[WORKER] Найден новый инвойс external_id={inv.id}, ставим в очередь обработки.")
            # отдельная задача под каждый инвойс
            asyncio.create_task(process_invoice(browser, inv, store))

        # пауза до следующего опроса
        await asyncio.sleep(poll_interval_sec)