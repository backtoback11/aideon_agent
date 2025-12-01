# prmoney_invoice.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any
import json


@dataclass
class PrmoneyInvoice:
    """
    Внутреннее представление инвойса от PrMoney.
    ВАЖНО: id — это внешний ID PrMoney (по нему фильтруем дубли/старье).
    """
    id: int                 # внешний ID (из PrMoney: "id")
    invoice_id: str         # строковый ID (для логов / совместимости)
    amount: float
    currency: str

    client_id: str
    status: int

    card_number: str        # из card_info.card_number
    holder: str             # из card_info.holder


def parse_card_info(raw: str) -> tuple[str, str]:
    """
    card_info приходит строкой JSON:
    "card_info":"{\"card_number\":\"9860...\",\"holder\":\"Xujanazarov Islom\"}"
    """
    if not raw:
        return "", ""
    try:
        data = json.loads(raw)
        return str(data.get("card_number", "")), str(data.get("holder", ""))
    except Exception as e:
        print(f"[PRMONEY] Ошибка парсинга card_info: {e} | raw={raw}")
        return "", ""


def invoice_from_prmoney_payload(item: Dict[str, Any]) -> Optional[PrmoneyInvoice]:
    """
    Преобразуем один объект из JSON PrMoney в PrmoneyInvoice.
    Фильтр:
      - берём только status == 0 (ожидающие платежи)
    """
    try:
        status = int(item.get("status", 0))
        if status != 0:
            # 0 — ожидает, остальные не берём
            return None

        ext_id = int(item["id"])

        card_number, holder = parse_card_info(item.get("card_info", ""))

        return PrmoneyInvoice(
            id=ext_id,
            invoice_id=str(ext_id),
            amount=float(item.get("amount", "0") or 0),
            currency="RUB",  # если валюта появится отдельно — сюда можно подставить
            client_id=item.get("client_id", ""),
            status=status,
            card_number=card_number,
            holder=holder,
        )
    except Exception as e:
        print(f"[PRMONEY] Ошибка сборки invoice: {e} | item={item}")
        return None