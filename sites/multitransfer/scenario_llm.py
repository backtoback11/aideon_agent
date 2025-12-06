# aideon_agent/sites/multitransfer/scenario_llm.py
from __future__ import annotations

import asyncio
from typing import Any

from aideon_agent.browser.browser_tool import BrowserTool
from aideon_agent.browser.actions_schema import Action, ScanResult
from aideon_agent.ai import AIPlanner
from aideon_agent.sites.multitransfer.selectors import NEXT_BUTTON_TEXTS
from aideon_agent.sites.multitransfer.state_extractors import (
    summarize_scan_for_planner,
    detect_no_terminals,
)


class MultitransferScenario:
    """
    Сценарий для работы с Multitransfer на основе BrowserTool + AIPlanner.

    Идея:
    - стабильные шаги (заполнение суммы / форм) можно оставить в старых step1/step3 модулях,
      а здесь постепенно переносить/дополнять.
    - выбор оффера / банка / обработку нестандартных экранов делаем через LLM-планировщик.
    """

    def __init__(self, planner: AIPlanner, logger: Any = None):
        self.planner = planner
        self.logger = logger

    async def run_invoice(self, bt: BrowserTool, invoice: Any) -> None:
        """
        Высокоуровневая точка входа для обработки одного инвойса.
        Предполагаем, что:
        - браузер уже открыт
        - страница Multitransfer уже на шаге, где можно вводить сумму
        """
        # 1. Сумма + переход к выбору метода
        await self._step_amount_and_methods(bt, invoice)

        # 2. Выбор банка/оффера через LLM-планировщик
        await self._step_select_bank_llm(bt, invoice)

        # 3. Заполнение форм получателя/отправителя — пока можно использовать старый step3
        # Здесь зашит "hook" для постепенной миграции.
        await self._step_fill_forms_legacy(bt, invoice)

        # 4. Ожидание диплинка — пока оставляем старый step4 (network/console)
        await self._step_wait_deeplink_legacy(bt, invoice)

    async def _step_amount_and_methods(self, bt: BrowserTool, invoice: Any) -> None:
        """
        Упрощённая обёртка вокруг текущей логики step1:
        сейчас оставляем тебе возможность вызывать старый код,
        позже сюда можно перенести реализацию через Action/scan.
        """
        if self.logger:
            self.logger.info("[MT-Scenario] STEP1: amount + open methods (legacy hook)")

        # Временный вариант: просто сделать scan для дебага
        scan = await bt.scan()
        if self.logger:
            self.logger.debug(
                f"[MT-Scenario] STEP1 SCAN: {scan.url} elements={len(scan.elements)}"
            )

        # Здесь ты можешь вызвать свой текущий multitransfer_step1.fill_amount(...)
        # либо постепенно переписать на Action-стиль.
        # Сейчас оставляем заглушку, чтобы не ломать существующий поток.
        # Пример Action-стиля (потом заменить на реальные элементы):
        # actions = [
        #     Action(type="fill", target=TargetRef(name="Сумма отправления"), value=str(invoice.amount)),
        #     Action(type="click", target=TargetRef(text="Продолжить")),
        # ]
        # await bt.perform_many(actions)

    async def _step_select_bank_llm(self, bt: BrowserTool, invoice: Any) -> None:
        """
        Выбор нужного банка/оффера через LLM-планировщик.
        """
        if self.logger:
            self.logger.info("[MT-Scenario] STEP2: select bank via LLM")

        # 1. scan + state
        scan = await bt.scan()
        state = await bt.get_state()
        scan_summary = summarize_scan_for_planner(scan)

        if detect_no_terminals(scan):
            raise RuntimeError("[STEP2] BANK_NOT_FOUND: no terminals / methods")

        goal = (
            f"Выбери оффер/способ перевода с банком '{invoice.recipient_bank}' "
            f"и нажми кнопку продолжения (например: {', '.join(NEXT_BUTTON_TEXTS)}). "
            "Не нажимай окончательное подтверждение платежа, только переход к следующему шагу."
        )

        history = [
            {
                "step": "step2",
                "comment": "Initial bank selection",
                "invoice_id": getattr(invoice, "invoice_id", None),
            }
        ]

        # 2. план от LLM
        actions = await self.planner.plan(goal=goal, scan=scan, state=scan_summary, history=history)

        if not actions:
            if self.logger:
                self.logger.warning("[MT-Scenario] STEP2: LLM returned no actions")
            # fallback: здесь можно вызвать старый multitransfer_step2
            return

        # 3. выполнение
        results = await bt.perform_many(actions)
        if self.logger:
            self.logger.info(f"[MT-Scenario] STEP2: executed {len(results)} actions")

    async def _step_fill_forms_legacy(self, bt: BrowserTool, invoice: Any) -> None:
        """
        Хук под твой существующий multitransfer_step3.
        Пока здесь просто логируем scan, позже можно переписать на Action/LLM.
        """
        if self.logger:
            self.logger.info("[MT-Scenario] STEP3: fill recipient/sender forms (legacy hook)")

        scan = await bt.scan()
        if self.logger:
            self.logger.debug(
                f"[MT-Scenario] STEP3 SCAN: {scan.url} elements={len(scan.elements)}"
            )

        # На этом месте можно:
        # - либо вызвать текущий multitransfer_step3.fill_recipient_and_sender(...)
        # - либо постепенно переносить в Action/LLM-логику.

    async def _step_wait_deeplink_legacy(self, bt: BrowserTool, invoice: Any) -> None:
        """
        Хук под твой существующий multitransfer_step4 (ожидание диплинка).
        Здесь можно просто дать времени устаканиться шагам и затем передать управление старому коду.
        """
        if self.logger:
            self.logger.info("[MT-Scenario] STEP4: wait deeplink (legacy hook)")

        # Небольшой wait для стабилизации UI
        if bt.page:
            await bt.page.wait_for_timeout(1500)

        # Здесь можно оставить вызов multitransfer_step4.wait_for_deeplink(...)
        # или переписать на анализ network/console прямо из BrowserTool.
        # Сейчас ничем не ломаем существующий код.