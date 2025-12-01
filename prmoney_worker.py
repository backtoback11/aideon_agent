from __future__ import annotations

import json
import time
from typing import Optional

from sqlalchemy.exc import IntegrityError

from db import SessionLocal
from models import Invoice, Setting
from prmoney_fetcher import fetch_pending_invoices  # уже есть в проекте


PRMONEY_POLL_INTERVAL_SEC = 5  # каждые 5 секунд опрашиваем /test1
PRMONEY_LAST_ID_KEY = "PRMONEY_LAST_ID"  # ключ в таблице settings


# ============================================================
# helpers для settings
# ============================================================

def _get_setting(db, key: str) -> Optional[str]:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else None


def _set_setting(db, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if not row:
        row = Setting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()


# ============================================================
# парсинг card_info
# ============================================================

def _parse_card_info(card_info_raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """
    card_info хранится строкой JSON, пример:
      "{\"card_number\":\"9860046602662507\",\"holder\":\"Xujanazarov Islom\"}"

    Возвращаем (card_number, holder).
    """
    if not card_info_raw:
        return None, None

    try:
        obj = json.loads(card_info_raw)
        card_number = obj.get("card_number")
        holder = obj.get("holder")
        return card_number, holder
    except Exception as e:
        print(f"[PRMONEY] Ошибка парсинга card_info='{card_info_raw}': {e}")
        return None, None


def _split_holder(holder: Optional[str]) -> tuple[str, str]:
    """
    holder типа 'Xujanazarov Islom' → (last_name, first_name).
    Если формат странный — просто всё в last_name.
    """
    if not holder:
        return "", ""

    parts = holder.strip().split()
    if len(parts) >= 2:
        last_name = " ".join(parts[:-1])
        first_name = parts[-1]
    else:
        last_name = holder.strip()
        first_name = ""

    return last_name, first_name


# ============================================================
# маппинг PrMoney → Invoice
# ============================================================

def _create_invoice_from_prmoney(db, pr_inv) -> None:
    """
    pr_inv — это объект из fetch_pending_invoices() с полями:
      id, client_id, amount, status, card_info, ...
    Мы создаём запись в таблице invoices со статусом queued.

    ВАЖНО:
      - card_info: JSON с полями card_number и holder.
      - банк по умолчанию: UZUM Bank (если не придёт другой).
      - страна по умолчанию: Uzbekistan (если не придёт другая).
      - данные отправителя — жёсткий шаблон.
    """

    # --- карта и держатель ---
    card_number, holder = _parse_card_info(getattr(pr_inv, "card_info", None))
    last_name, first_name = _split_holder(holder)

    invoice_id = str(pr_inv.id)  # используем id PrMoney как внешний invoice_id

    # Проверка на дубль по invoice_id (на всякий случай, помимо PRMONEY_LAST_ID)
    existing = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
    if existing:
        print(f"[PRMONEY] Инвойс invoice_id={invoice_id} уже есть в БД, пропускаю.")
        return

    # --- страна и банк получателя ---
    # На будущее: если PrMoney начнёт присылать свои поля, подхватим их.
    recipient_country = getattr(pr_inv, "country", None) or "Uzbekistan"
    recipient_bank = getattr(pr_inv, "bank", None) or "UZUM Bank"

    # --- получатель ---
    recipient_card_number = card_number or "UNKNOWN"

    # если holder есть, то first/last уже разнесены; если нет — подстрахуемся
    recipient_first_name = first_name or (holder or "Unknown")
    recipient_last_name = last_name or ""

    if holder:
        recipient_name = holder
    else:
        # fallback: склейка из first/last
        full_parts = [recipient_first_name, recipient_last_name]
        recipient_name = " ".join(p for p in full_parts if p).strip() or "Unknown"

    recipient_requisites = (
        f"Карта: {recipient_card_number}, "
        f"Держатель: {holder or 'Unknown'}, "
        f"Банк: {recipient_bank}, "
        f"Страна: {recipient_country}"
    )

    # --- отправитель — жёсткий шаблон, как обсуждали ---
    sender_first_name = "Иван"
    sender_last_name = "Иванов"
    sender_middle_name = None

    sender_passport_type = "rf_national"
    sender_passport_series = "0000"
    sender_passport_number = "000000"
    sender_passport_country = "Россия"
    sender_passport_issue_date = "01.01.2020"

    sender_birth_date = "01.01.1990"
    sender_birth_country = "Россия"
    sender_birth_place = "Москва"

    sender_registration_country = "Россия"
    sender_registration_place = "Москва"
    sender_phone = "+79990000000"

    sender_name = f"{sender_last_name} {sender_first_name}".strip()

    inv = Invoice(
        invoice_id=invoice_id,
        amount=float(pr_inv.amount),
        currency="RUB",

        # получатель — новые поля
        recipient_country=recipient_country,
        recipient_bank=recipient_bank,
        recipient_card_number=recipient_card_number,
        recipient_first_name=recipient_first_name,
        recipient_last_name=recipient_last_name,

        # получатель — legacy
        recipient_name=recipient_name,
        recipient_requisites=recipient_requisites,

        # отправитель — новые поля
        sender_first_name=sender_first_name,
        sender_last_name=sender_last_name,
        sender_middle_name=sender_middle_name,
        sender_passport_type=sender_passport_type,
        sender_passport_series=sender_passport_series,
        sender_passport_number=sender_passport_number,
        sender_passport_country=sender_passport_country,
        sender_passport_issue_date=sender_passport_issue_date,
        sender_birth_date=sender_birth_date,
        sender_birth_country=sender_birth_country,
        sender_birth_place=sender_birth_place,
        sender_registration_country=sender_registration_country,
        sender_registration_place=sender_registration_place,
        sender_phone=sender_phone,

        # отправитель — legacy
        sender_name=sender_name,

        callback_url=None,
        status="queued",
    )

    try:
        db.add(inv)
        db.commit()
        print(f"[PRMONEY] ✔ Создан инвойс id={inv.id} (invoice_id={invoice_id}) amount={inv.amount}")
    except IntegrityError as e:
        db.rollback()
        print(f"[PRMONEY] ⚠ Инвойс invoice_id={invoice_id} уже существует (IntegrityError): {e}")
    except Exception as e:
        db.rollback()
        print(f"[PRMONEY] ❌ Ошибка при создании инвойса invoice_id={invoice_id}: {e}")


# ============================================================
# один цикл опроса
# ============================================================

def _poll_prmoney_once(db) -> None:
    last_id_str = _get_setting(db, PRMONEY_LAST_ID_KEY) or "0"
    try:
        last_id = int(last_id_str)
    except ValueError:
        last_id = 0

    print(f"\n[PRMONEY] === Опрос /test1 (последний id={last_id}) ===")

    try:
        invoices = fetch_pending_invoices()
    except Exception as e:
        print(f"[PRMONEY] ❌ Ошибка запроса к PrMoney: {e}")
        return

    if not invoices:
        print("[PRMONEY] Нет инвойсов от PrMoney.")
        return

    # фильтруем только status == 0 и только id > last_id
    new_items = [
        inv for inv in invoices
        if getattr(inv, "status", None) == 0 and getattr(inv, "id", 0) > last_id
    ]

    if not new_items:
        print("[PRMONEY] Нет новых инвойсов для обработки.")
        return

    # сортируем по id по возрастанию
    new_items.sort(key=lambda x: x.id)

    max_id = last_id
    for pr_inv in new_items:
        print(
            f"[PRMONEY] Новый инвойс: id={pr_inv.id}, "
            f"amount={pr_inv.amount}, status={pr_inv.status}, "
            f"card_info={getattr(pr_inv, 'card_info', None)}"
        )
        _create_invoice_from_prmoney(db, pr_inv)
        if pr_inv.id > max_id:
            max_id = pr_inv.id

    if max_id > last_id:
        _set_setting(db, PRMONEY_LAST_ID_KEY, str(max_id))
        print(f"[PRMONEY] Обновлён {PRMONEY_LAST_ID_KEY} → {max_id}")


# ============================================================
# основной цикл воркера
# ============================================================

def run_prmoney_worker(poll_interval_sec: int = PRMONEY_POLL_INTERVAL_SEC) -> None:
    """
    Бесконечный цикл:
      - тянем список инвойсов с PrMoney (/test1),
      - фильтруем только новые и статус=0,
      - кладём их в таблицу invoices со статусом queued,
      - запоминаем последний обработанный id в settings.
    """
    print("[PrMoneyWorker] Запущен воркер PrMoney (poll /test1)")

    while True:
        db = SessionLocal()
        try:
            _poll_prmoney_once(db)
        except Exception as e:
            print(f"[PrMoneyWorker] ❌ Ошибка верхнего уровня: {e}")
        finally:
            db.close()

        time.sleep(poll_interval_sec)


if __name__ == "__main__":
    # Запуск напрямую: python prmoney_worker.py
    run_prmoney_worker()