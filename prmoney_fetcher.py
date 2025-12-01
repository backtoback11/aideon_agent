# prmoney_fetcher.py
from __future__ import annotations

from typing import List

import requests

from prmoney_invoice import invoice_from_prmoney_payload, PrmoneyInvoice

PRMONEY_URL = "https://prmoney.opmall.biz/test1"


def fetch_pending_invoices() -> List[PrmoneyInvoice]:
    """
    Тянем список инвойсов из PrMoney и оставляем только:
      - status == 0
      - корректно распарсенные.
    """
    try:
        resp = requests.get(PRMONEY_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[PRMONEY] Ошибка запроса {PRMONEY_URL}: {e}")
        return []

    if not isinstance(data, list):
        print(f"[PRMONEY] Ожидали JSON-список, получили: {type(data)}")
        return []

    result: List[PrmoneyInvoice] = []
    for item in data:
        inv = invoice_from_prmoney_payload(item)
        if inv:
            result.append(inv)

    print(f"[PRMONEY] Получено {len(result)} ожидающих инвойсов")
    return result