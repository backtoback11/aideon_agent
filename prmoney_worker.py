# prmoney_worker.py
from __future__ import annotations

import asyncio

from processed_store import ProcessedStore
from prmoney_fetcher import fetch_pending_invoices
from prmoney_invoice import PrmoneyInvoice


# ============================================================
# 1) Обработчик НОВОГО инвойса (здесь только наша БД / админка)
# ============================================================

async def handle_new_invoice(inv: PrmoneyInvoice) -> None:
    """
    Здесь мы НИЧЕГО не знаем про Multitransfer и браузер.

    Задача только одна:
      - записать инвойс из PrMoney в нашу систему (БД),
      - чтобы он появился в разделе «Инвойсы» в админке
        и ждал, пока Aideon Agent его заберёт.

    Примерная логика (псевдокод):

        from models import Invoice
        Invoice.create(
            external_id=inv.id,
            provider="prmoney",
            client_id=inv.client_id,
            amount=inv.amount,
            card_number=inv.card_number,
            holder=inv.holder,
            status="waiting_agent",  # наш внутренний статус
        )

    Сейчас оставляю как заглушку с print — ты подставишь реальную запись в БД.
    """
    print(
        f"[PRMONEY_HANDLER] Новый инвойс: external_id={inv.id}, "
        f"amount={inv.amount}, card={inv.card_number}, holder={inv.holder}"
    )
    # TODO: заменить на реальную вставку в БД (раздел «Инвойсы»).


# ============================================================
# 2) Обработка одного инвойса: только учёт и вызов handler'a
# ============================================================

async def _process_single_invoice(
    inv: PrmoneyInvoice,
    store: ProcessedStore,
) -> None:
    """
    Локальная задача для одного инвойса:
      - помечаем как "в обработке", чтобы не взять повторно,
      - вызываем handle_new_invoice (запись в БД),
      - по результату отмечаем done/failed в ProcessedStore.

    НИКАКОГО браузера, капчи или QR здесь нет.
    """
    print(
        f"\n[PRMONEY_WORKER] → Обработка external_id={inv.id}, "
        f"amount={inv.amount}, card={inv.card_number}, holder={inv.holder}"
    )

    # отмечаем, что этот инвойс мы уже взяли
    store.mark_processing(inv.id)

    try:
        await handle_new_invoice(inv)
        store.mark_done(inv.id)
        print(f"[PRMONEY_WORKER] ✔ external_id={inv.id} отмечен как обработанный (saved to DB).")
    except Exception as e:
        print(f"[PRMONEY_WORKER] ❌ Ошибка обработки external_id={inv.id}: {e}")
        store.mark_failed(inv.id)


# ============================================================
# 3) Основной цикл воркера PrMoney
# ============================================================

async def prmoney_loop(poll_interval_sec: int = 3) -> None:
    """
    Основной цикл воркера PrMoney.

    Делает следующее:
      - каждые poll_interval_sec секунд запрашивает список инвойсов у PrMoney (fetch_pending_invoices),
      - фильтрует только НОВЫЕ (через ProcessedStore),
      - для каждого нового инвойса создаёт отдельную async-задачу
        _process_single_invoice(...).

    ВАЖНО:
      - никакого Multitransfer и Playwright внутри этого файла;
      - этот воркер — только "мост" между PrMoney и нашей БД.
    """
    store = ProcessedStore()

    print("[PRMONEY_WORKER] Старт цикла опроса PrMoney...")

    while True:
        print("\n[PRMONEY_WORKER] === Запрос к PrMoney /test1 ===")
        try:
            invoices = fetch_pending_invoices()
        except Exception as e:
            print(f"[PRMONEY_WORKER] ❌ Ошибка при fetch_pending_invoices: {e}")
            invoices = []

        for inv in invoices:
            # фильтр старых/дубликатов
            if not store.is_new(inv.id):
                continue

            print(f"[PRMONEY_WORKER] Новый инвойс external_id={inv.id} → ставим в обработку.")
            # отдельная async-задача для каждого инвойса
            asyncio.create_task(_process_single_invoice(inv, store))

        # пауза до следующего опроса
        await asyncio.sleep(poll_interval_sec)