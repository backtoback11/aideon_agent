# aideon_agent/ai/planner.py
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aideon_agent.browser.actions_schema import Action, ScanResult
from aideon_agent.ai.prompts import build_planner_prompt


LLMCall = Callable[[str], Awaitable[str]]


class AIPlanner:
    """
    LLM-планировщик:
    - получает цель + scan + state
    - вызывает LLM-функцию (асинхронную), которая принимает prompt: str и возвращает ответ: str
    - парсит JSON в список Action
    """

    def __init__(self, llm_call: LLMCall, logger: Any = None):
        """
        llm_call: async (prompt: str) -> str
        logger: любой логгер с .info/.warning/.error (может быть None)
        """
        self.llm_call = llm_call
        self.logger = logger

    async def plan(
        self,
        goal: str,
        scan: ScanResult,
        state: Dict[str, Any],
        history: Optional[List[Dict[str, Any]]] = None,
        max_actions: int = 20,
    ) -> List[Action]:
        scan_compact = scan.to_compact_dict()
        prompt = build_planner_prompt(goal, scan_compact, state, history or [])

        if self.logger:
            self.logger.debug("[AIPlanner] Sending prompt to LLM")

        raw = await self.llm_call(prompt)

        if self.logger:
            self.logger.debug(f"[AIPlanner] Raw LLM response: {raw[:400]}...")

        # Попытаемся распарсить JSON даже если модель добавила лишний текст
        json_str = self._extract_json_array(raw)
        if json_str is None:
            if self.logger:
                self.logger.warning("[AIPlanner] JSON array not found in response")
            return []

        try:
            data = json.loads(json_str)
            if not isinstance(data, list):
                if self.logger:
                    self.logger.warning("[AIPlanner] Parsed JSON is not a list")
                return []
        except Exception as e:
            if self.logger:
                self.logger.error(f"[AIPlanner] Failed to parse JSON: {e}")
            return []

        actions: List[Action] = []
        for item in data[:max_actions]:
            if not isinstance(item, dict):
                continue
            if "type" not in item:
                continue
            try:
                actions.append(Action.from_dict(item))
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[AIPlanner] Bad action item skipped: {e}")
                continue

        if self.logger:
            self.logger.info(f"[AIPlanner] Planned {len(actions)} actions for goal '{goal}'")

        return actions

    @staticmethod
    def _extract_json_array(text: str) -> Optional[str]:
        """
        Вырезает первый JSON-массив из текста.
        Например: 'Вот ответ: [ {...}, {...} ] Удачи!' -> '[ {...}, {...} ]'
        """
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return None
        return text[start : end + 1]