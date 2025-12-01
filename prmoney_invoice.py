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
    status: int             # нормализованный статус (число, для внутренней логики)

    card_number: str        # card_number из payload / card_info
    holder: str             # holder из payload / card_info


def parse_card_info(raw: Any) -> tuple[str, str]:
    """
    Парсинг card_info:
      - может прийти строкой JSON:
        "card_info":"{\"card_number\":\"9860...\",\"holder\":\"Xujanazarov Islom\"}"
      - или уже dict-объектом.

    Возвращает (card_number, holder).
    """
    if not raw:
        return "", ""

    data: Dict[str, Any]

    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception as e:
            print(f"[PRMONEY] Ошибка парсинга card_info: {e} | raw={raw}")
            return "", ""
    else:
        # Неизвестный формат
        return "", ""

    card_number = str(data.get("card_number", "") or "")
    holder = str(data.get("holder", "") or "")

    return card_number, holder


def normalize_status(raw_status: Any) -> int:
    """
    Нормализуем статус в int для внутренней логики.
    Если status не приводится к int:
      - ставим 0 для статусов вида "queued"/"pending"/"waiting"/"0"
      - иначе -1 (неизвестный/неожидающий).
    """
    try:
        return int(raw_status)
    except (TypeError, ValueError):
        s = str(raw_status).strip().lower()
        if s in {"0", "queued", "pending", "waiting"}:
            return 0
        return -1


def invoice_from_prmoney_payload(item: Dict[str, Any]) -> Optional[PrmoneyInvoice]:
    """
    Преобразуем один объект из JSON PrMoney в PrmoneyInvoice.

    ВНИМАНИЕ:
      - Фильтрация по «ожидающим» статусам теперь делается
        в fetch_pending_invoices() (prmoney_fetcher.py).
      - Здесь мы только аккуратно парсим и нормализуем данные.

    Карта/ФИО:
      - сначала берём card_number и holder из корня payload (новый формат),
      - если их нет, пробуем распарсить card_info (старый формат).
    """
    try:
        ext_id = int(item["id"])

        # Статус
        raw_status = item.get("status", 0)
        status = normalize_status(raw_status)

        # Сумма
        raw_amount = item.get("amount", "0") or 0
        amount = float(raw_amount)

        # Валюта: если есть в payload — используем, иначе RUB по умолчанию
        currency = str(item.get("currency") or item.get("currency_code") or "RUB")

        client_id = str(item.get("client_id", "") or "")

        # --- Карта / ФИО ---

        # Новый формат: card_number и holder в корне payload
        card_number = str(item.get("card_number", "") or "")
        holder = str(item.get("holder", "") or "")

        # Если не пришли в корне — пробуем старое поле card_info
        if (not card_number or not holder) and item.get("card_info"):
            parsed_number, parsed_holder = parse_card_info(item.get("card_info"))
            if not card_number:
                card_number = parsed_number
            if not holder:
                holder = parsed_holder

        # Фолбэки, чтобы в таблице не было пустых значений
        if not card_number:
            card_number = "UNKNOWN"
        if not holder:
            holder = "Unknown"

        # Если даже после всех попыток у нас UNKNOWN/Unknown —
        # логируем сырой payload, чтобы увидеть реальную структуру.
        if card_number == "UNKNOWN" or holder == "Unknown":
            try:
                print(
                    "[PRMONEY] ⚠ Не удалось вытащить card_number/holder, сырой payload:\n"
                    + json.dumps(item, ensure_ascii=False, indent=2)
                )
            except Exception as log_e:
                print(f"[PRMONEY] ⚠ Не удалось залогировать payload: {log_e} | item={item}")

        return PrmoneyInvoice(
            id=ext_id,
            invoice_id=str(ext_id),
            amount=amount,
            currency=currency,
            client_id=client_id,
            status=status,
            card_number=card_number,
            holder=holder,
        )
    except Exception as e:
        print(f"[PRMONEY] Ошибка сборки invoice: {e} | item={item}")
        return None