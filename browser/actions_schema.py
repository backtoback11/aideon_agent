# aideon_agent/browser/actions_schema.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict


ActionType = Literal["click", "fill", "select", "wait"]


class ScanElementDict(TypedDict, total=False):
    id: Optional[str]
    tag: Optional[str]
    type: Optional[str]
    role: Optional[str]
    name: Optional[str]
    text: Optional[str]
    value: Optional[str]
    cssSelector: Optional[str]
    ariaLabel: Optional[str]
    placeholder: Optional[str]
    href: Optional[str]
    visible: bool
    bbox: Dict[str, float]
    dataset: Dict[str, Any]


class ScanResultDict(TypedDict):
    url: str
    title: str
    elements: List[ScanElementDict]


@dataclass
class TargetRef:
    """Ссылка на элемент, с которым нужно что-то сделать."""
    id: Optional[str] = None
    cssSelector: Optional[str] = None
    text: Optional[str] = None
    role: Optional[str] = None
    name: Optional[str] = None  # label / видимое имя

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TargetRef":
        return cls(
            id=data.get("id"),
            cssSelector=data.get("cssSelector"),
            text=data.get("text"),
            role=data.get("role"),
            name=data.get("name"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "cssSelector": self.cssSelector,
            "text": self.text,
            "role": self.role,
            "name": self.name,
        }


@dataclass
class Action:
    """Одно действие агента на странице."""
    type: ActionType
    target: Optional[TargetRef] = None
    value: Optional[str] = None
    ms: Optional[int] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Action":
        target_data = data.get("target")
        target = TargetRef.from_dict(target_data) if target_data else None
        return cls(
            type=data["type"],
            target=target,
            value=data.get("value"),
            ms=data.get("ms"),
            meta=data.get("meta") or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "target": self.target.to_dict() if self.target else None,
            "value": self.value,
            "ms": self.ms,
            "meta": self.meta,
        }


@dataclass
class ScanResult:
    """Результат scan() от AideonHelper JS."""
    url: str
    title: str
    elements: List[ScanElementDict]

    @classmethod
    def from_dict(cls, data: ScanResultDict) -> "ScanResult":
        return cls(
            url=data["url"],
            title=data["title"],
            elements=list(data.get("elements", [])),
        )

    def to_compact_dict(self, max_elements: int = 120) -> Dict[str, Any]:
        """Компактное представление для передачи в LLM."""
        els = self.elements[:max_elements]
        compact_elements: List[Dict[str, Any]] = []
        for e in els:
            compact_elements.append(
                {
                    "id": e.get("id"),
                    "role": e.get("role"),
                    "tag": e.get("tag"),
                    "name": e.get("name"),
                    "text": e.get("text"),
                    "placeholder": e.get("placeholder"),
                    "type": e.get("type"),
                    "visible": e.get("visible"),
                    "cssSelector": e.get("cssSelector"),
                }
            )
        return {
            "url": self.url,
            "title": self.title,
            "elements": compact_elements,
        }