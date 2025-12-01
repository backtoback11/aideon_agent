# prmoney_fetcher.py
from __future__ import annotations

from typing import List, Any, Dict

import requests

from prmoney_invoice import invoice_from_prmoney_payload, PrmoneyInvoice

# Для тестового стенда:
# /test1, /test2 и т.д. можно вынести в конфиг при необходимости.
PRMONEY_URL = "https://prmoney.opmall.biz/test1"

# Таймаут запроса к PrMoney в секундах
PRMONEY_TIMEOUT = 10


def _is_pending_status(raw: Dict[str, Any]) -> bool:
    """
    Возвращает True, если инвойс в статусе «ожидает обработки».

    На стороне PrMoney статус может приходить:
      - числом: 0
      - строкой: "0"
      - либо человекочитаемым статусом: "queued", "pending"
    """
    status = raw.get("status")

    # Числовой статус
    if isinstance(status, int):
        return status == 0

    # Строковый статус
    if isinstance(status, str):
        status_lower = status.strip().lower()
        if status_lower in {"0", "queued", "pending", "waiting"}:
            return True

    return False


def fetch_pending_invoices() -> List[PrmoneyInvoice]:
    """
    Тянем список инвойсов из PrMoney и оставляем только:
      - статус "ожидает" (status == 0 / "0" / "queued"/"pending")
      - корректно распарсенные в PrmoneyInvoice.

    Вся логика вытаскивания карты/ФИО/банка/страны находится
    в invoice_from_prmoney_payload(), который должен:
      - брать card_number и holder из payload;
      - подставлять UZUM Bank и Uzbekistan по умолчанию,
        если банк/страна не переданы;
      - остальные данные отправителя — из тестового шаблона.
    """
    try:
        resp = requests.get(PRMONEY_URL, timeout=PRMONEY_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        print(f"[PRMONEY] Ошибка запроса {PRMONEY_URL}: {e}")
        return []

    try:
        data = resp.json()
    except Exception as e:
        print(f"[PRMONEY] Не удалось распарсить JSON от PrMoney: {e}")
        return []

    if not isinstance(data, list):
        print(f"[PRMONEY] Ожидали JSON-список, получили: {type(data)} ({data!r})")
        return []

    result: List[PrmoneyInvoice] = []

    for item in data:
        if not isinstance(item, dict):
            print(f"[PRMONEY] Пропускаем элемент некорректного формата: {item!r}")
            continue

        # Оставляем только «ожидающие» инвойсы
        if not _is_pending_status(item):
            continue

        inv = invoice_from_prmoney_payload(item)
        if inv is None:
            # Можно залогировать сырые данные для дебага маппинга
            print(f"[PRMONEY] Не удалось распарсить инвойс из payload: {item!r}")
            continue

        result.append(inv)

    print(f"[PRMONEY] Получено {len(result)} ожидающих инвойсов")
    return result