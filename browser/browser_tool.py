# aideon_agent/browser/browser_tool.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, Page

from aideon_agent.browser.actions_schema import Action, ScanResult


class BrowserTool:
    """
    Обёртка над Playwright для Aideon Agent:
    - старт/стоп браузера
    - инжект aideon_helper.js
    - scan() / perform() / get_state() / screenshot()
    """

    def __init__(
        self,
        helper_js_path: str,
        headless: bool = True,
        proxy: Optional[Dict[str, str]] = None,
        slow_mo: Optional[int] = None,
        logger: Any = None,
    ):
        """
        proxy пример:
        {
            "server": "http://157.22.14.232:64962",
            "username": "user",
            "password": "pass"
        }
        """
        self.helper_js_path = helper_js_path
        self.headless = headless
        self.proxy = proxy
        self.slow_mo = slow_mo
        self.logger = logger

        self._pw = None
        self._browser = None
        self._context = None
        self.page: Optional[Page] = None

    async def __aenter__(self) -> "BrowserTool":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self) -> None:
        if self.logger:
            self.logger.info("[BrowserTool] Starting browser...")

        self._pw = await async_playwright().start()

        launch_kwargs: Dict[str, Any] = {
            "headless": self.headless,
        }
        if self.proxy:
            launch_kwargs["proxy"] = {
                "server": self.proxy["server"],
                "username": self.proxy.get("username") or None,
                "password": self.proxy.get("password") or None,
            }
        if self.slow_mo:
            launch_kwargs["slow_mo"] = self.slow_mo

        self._browser = await self._pw.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context()
        self.page = await self._context.new_page()

        helper_js = Path(self.helper_js_path).read_text(encoding="utf-8")
        await self.page.add_init_script(helper_js)

        if self.logger:
            self.logger.info("[BrowserTool] Browser started, helper injected")

    async def goto(self, url: str, wait_until: str = "networkidle", timeout: int = 60000) -> None:
        if not self.page:
            raise RuntimeError("BrowserTool: page is not initialized")
        if self.logger:
            self.logger.info(f"[BrowserTool] GOTO {url}")
        await self.page.goto(url, wait_until=wait_until, timeout=timeout)

    async def scan(self) -> ScanResult:
        if not self.page:
            raise RuntimeError("BrowserTool: page is not initialized")

        if self.logger:
            self.logger.debug("[BrowserTool] SCAN")

        data = await self.page.evaluate(
            "() => window.AideonHelper && window.AideonHelper.scan()"
        )
        if not data:
            raise RuntimeError("AideonHelper.scan() returned nothing")

        return ScanResult.from_dict(data)

    async def perform(self, action: Action) -> Dict[str, Any]:
        if not self.page:
            raise RuntimeError("BrowserTool: page is not initialized")

        payload = action.to_dict()
        if self.logger:
            self.logger.info(f"[BrowserTool] PERFORM {json.dumps(payload, ensure_ascii=False)}")

        res = await self.page.evaluate(
            "(action) => window.AideonHelper && window.AideonHelper.perform(action)",
            payload,
        )
        return res or {"ok": False, "error": "No response from helper"}

    async def perform_many(self, actions: List[Action]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for a in actions:
            res = await self.perform(a)
            results.append(res)
            # небольшая пауза, чтобы UI успевал обновляться
            if a.type in ("click", "fill", "select") and self.page:
                await self.page.wait_for_timeout(200)
        return results

    async def get_state(self) -> Dict[str, Any]:
        if not self.page:
            raise RuntimeError("BrowserTool: page is not initialized")

        if self.logger:
            self.logger.debug("[BrowserTool] GET_STATE")

        res = await self.page.evaluate(
            "() => window.AideonHelper && window.AideonHelper.getState()"
        )
        return res or {}

    async def screenshot(self, path: str, full_page: bool = True) -> None:
        if not self.page:
            raise RuntimeError("BrowserTool: page is not initialized")

        if self.logger:
            self.logger.info(f"[BrowserTool] SCREENSHOT -> {path}")
        await self.page.screenshot(path=path, full_page=full_page)

    async def close(self) -> None:
        if self.logger:
            self.logger.info("[BrowserTool] Closing browser...")
        try:
            if self._context:
                await self._context.close()
        finally:
            self._context = None

        try:
            if self._browser:
                await self._browser.close()
        finally:
            self._browser = None

        if self._pw:
            await self._pw.stop()
            self._pw = None

        self.page = None
        if self.logger:
            self.logger.info("[BrowserTool] Closed")