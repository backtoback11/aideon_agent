# aideon_agent/ai/prompts.py
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def build_planner_prompt(
    goal: str,
    scan_compact: Dict[str, Any],
    state: Dict[str, Any],
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Формирует текст промпта для LLM-планировщика.
    На выходе модель должна вернуть ТОЛЬКО JSON-массив действий.
    """
    history = history or []

    system_instructions = (
        "Ты — модуль планирования действий для браузерного агента Aideon.\n"
        "Твоя задача: на основе структуры элементов страницы и цели вернуть список действий в формате JSON.\n\n"
        "Ограничения:\n"
        "- НЕ выполняй действия сам, только планируй.\n"
        "- Возвращай ТОЛЬКО JSON-массив без комментариев.\n"
        "- Каждый элемент массива — объект с полями: type, target, value (опционально), ms (для wait).\n"
        "- Допустимые type: 'click', 'fill', 'select', 'wait'.\n"
        "- target может содержать: id, cssSelector, text, role, name.\n"
        "- Если нужно просто подождать, используй действие {\"type\": \"wait\", \"ms\": 500}.\n"
        "- Если нужный элемент не найден, верни пустой массив [].\n"
    )

    user_payload = {
        "goal": goal,
        "scan": scan_compact,
        "state": state,
        "history": history,
        "expected_action_schema": {
            "type": "Action[]",
            "item": {
                "type": "string (click|fill|select|wait)",
                "target": {
                    "id": "string | null",
                    "cssSelector": "string | null",
                    "text": "string | null",
                    "role": "string | null",
                    "name": "string | null",
                },
                "value": "string | null",
                "ms": "number | null",
            },
        },
    }

    return system_instructions + "\nДанные пользователя (JSON):\n" + json.dumps(
        user_payload, ensure_ascii=False, indent=2
    ) + "\n\nВерни только JSON-массив действий."