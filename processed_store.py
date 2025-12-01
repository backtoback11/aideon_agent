# processed_store.py
from __future__ import annotations

import json
import os
from typing import Set

STORE_PATH = "processed_invoices.json"


class ProcessedStore:
    """
    Простое файловое хранилище:
      - processed: какие external_id мы уже успешно обработали
      - processing: какие прямо сейчас в работе (чтоб не взять повторно)
    """

    def __init__(self):
        self.processed: Set[int] = set()
        self.processing: Set[int] = set()
        self._load()

    # -------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(STORE_PATH):
            return
        try:
            with open(STORE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.processed = set(data.get("processed", []))
            print(f"[STORE] Загружено обработанных инвойсов: {len(self.processed)}")
        except Exception as e:
            print(f"[STORE] Ошибка загрузки {STORE_PATH}: {e}")

    # -------------------------------------------------
    def save(self) -> None:
        try:
            with open(STORE_PATH, "w", encoding="utf-8") as f:
                json.dump({"processed": list(self.processed)}, f, indent=2, ensure_ascii=False)
            print(f"[STORE] Сохранено обработанных инвойсов: {len(self.processed)}")
        except Exception as e:
            print(f"[STORE] Ошибка сохранения {STORE_PATH}: {e}")

    # -------------------------------------------------
    def is_new(self, external_id: int) -> bool:
        """
        Можно ли брать инвойс в работу?
        - не в processed
        - не в processing
        """
        return external_id not in self.processed and external_id not in self.processing

    # -------------------------------------------------
    def mark_processing(self, external_id: int) -> None:
        self.processing.add(external_id)

    # -------------------------------------------------
    def mark_done(self, external_id: int) -> None:
        self.processing.discard(external_id)
        self.processed.add(external_id)
        self.save()

    # -------------------------------------------------
    def mark_failed(self, external_id: int) -> None:
        """
        На случай ошибки обработки:
        убираем из processing, но НЕ добавляем в processed,
        чтобы можно было повторно попытаться позже.
        """
        self.processing.discard(external_id)