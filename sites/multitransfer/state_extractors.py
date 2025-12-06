# aideon_agent/sites/multitransfer/state_extractors.py
from __future__ import annotations

from typing import Any, Dict

from aideon_agent.browser.actions_schema import ScanResult
from aideon_agent.sites.multitransfer.selectors import NO_TERMINALS_MARKERS


def summarize_scan_for_planner(scan: ScanResult) -> Dict[str, Any]:
    """
    Дополнительное сжатие/аннотация данных scan для LLM.
    Например, можно подсчитать, сколько видимых кнопок, есть ли тексты ошибок и т.п.
    """
    buttons = [e for e in scan.elements if e.get("role") == "button" and e.get("visible")]
    inputs = [e for e in scan.elements if e.get("role") == "input" and e.get("visible")]
    links = [e for e in scan.elements if e.get("role") == "link" and e.get("visible")]

    return {
        "url": scan.url,
        "title": scan.title,
        "visible_buttons": [
            {
                "id": b.get("id"),
                "text": b.get("text"),
                "name": b.get("name"),
                "cssSelector": b.get("cssSelector"),
            }
            for b in buttons
        ],
        "visible_inputs": [
            {
                "id": i.get("id"),
                "name": i.get("name"),
                "placeholder": i.get("placeholder"),
                "cssSelector": i.get("cssSelector"),
            }
            for i in inputs
        ],
        "visible_links": [
            {
                "id": l.get("id"),
                "text": l.get("text"),
                "href": l.get("href"),
                "cssSelector": l.get("cssSelector"),
            }
            for l in links
        ],
    }


def detect_no_terminals(scan: ScanResult) -> bool:
    """
    Пытается угадать, что на странице показано сообщение "нет терминалов / нет способов".
    """
    page_text = " ".join(
        (e.get("text") or "") + " " + (e.get("name") or "")
        for e in scan.elements
        if e.get("visible")
    ).lower()

    return any(marker.lower() in page_text for marker in NO_TERMINALS_MARKERS)