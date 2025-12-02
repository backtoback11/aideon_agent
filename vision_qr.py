from __future__ import annotations

import base64
import re
from typing import Optional

from openai import OpenAI

# ============================================================
#  ЖЁСТКИЙ ХАРДКОД КЛЮЧА (НЕ ЧЕРЕЗ ОКРУЖЕНИЕ)
# ============================================================

HARDCODED_OPENAI_KEY = "___PUT_YOUR_KEY_HERE___"   # <--- ВСТАВЬ СВОЙ КЛЮЧ СЮДА

if HARDCODED_OPENAI_KEY.startswith("___"):
    raise RuntimeError(
        "\n[FATAL] Vision QR: ключ не указан в HARDCODED_OPENAI_KEY.\n"
        "Укажи реальный ключ в начале файла."
    )

# Модели
PRIMARY_MODEL = "gpt-4o-mini"
FALLBACK_MODEL = "gpt-4.1"

# Регэксп на URL
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


# ============================================================
#  OPENAI CLIENT (всегда и только hardcoded key)
# ============================================================

def _get_client() -> Optional[OpenAI]:
    """
    Клиент создаётся ТОЛЬКО через жёстко прошитый ключ.
    Никакие переменные окружения, никакие OPENAI_API_KEY не используются.
    """

    api_key = HARDCODED_OPENAI_KEY

    print(
        "[VISION] Используется OpenAI API key (hardcoded): "
        f"prefix={api_key[:12]}..., len={len(api_key)}"
    )

    try:
        client = OpenAI(api_key=api_key)
        return client
    except Exception as e:
        print(f"[VISION] Ошибка инициализации OpenAI-клиента: {e}")
        return None


# ============================================================
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def _parse_message_content(message) -> str:
    content = message.content

    if isinstance(content, str):
        return content.strip()

    parts = []
    for part in content or []:
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
        elif isinstance(part, str):
            parts.append(part)
    return " ".join(parts).strip()


def _looks_like_safety_refusal(text: str) -> bool:
    lowered = text.lower()
    phrases = [
        "cannot help",
        "can't help",
        "not able to",
        "sorry",
        "не могу",
        "извините",
    ]
    return any(p in lowered for p in phrases)


def _call_vision_once(client: OpenAI, model: str, png_bytes: bytes) -> Optional[str]:
    prompt = (
        "На изображении есть QR-код. Прочитай текст, закодированный в нём "
        "и верни одну строку без пояснений."
    )

    b64 = base64.b64encode(png_bytes).decode("ascii")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }
            ],
            temperature=0.0,
            max_tokens=128,
        )
    except Exception as e:
        print(f"[VISION] Ошибка запроса к OpenAI (model={model}): {e}")
        return None

    try:
        msg = response.choices[0].message
        text = _parse_message_content(msg)

        print(f"[VISION] Ответ модели ({model}): {text!r}")

        if not text:
            return None

        if _looks_like_safety_refusal(text):
            return None

        if text.startswith(("http://", "https://")):
            return text

        m = URL_RE.search(text)
        if m:
            return m.group(0)

        return None

    except Exception as e:
        print(f"[VISION] Ошибка разбора ответа: {e}")
        return None


# ============================================================
#  ОСНОВНАЯ ФУНКЦИЯ
# ============================================================

def extract_qr_deeplink_from_screenshot(png_bytes: bytes) -> Optional[str]:
    client = _get_client()
    if client is None:
        return None

    print(f"[VISION] Основная модель: {PRIMARY_MODEL}")
    url = _call_vision_once(client, PRIMARY_MODEL, png_bytes)
    if url:
        return url

    print(f"[VISION] Fallback-модель: {FALLBACK_MODEL}")
    url = _call_vision_once(client, FALLBACK_MODEL, png_bytes)
    if url:
        return url

    print("[VISION] URL не извлечён ни одной моделью.")
    return None


# ============================================================
#  SELF-TEST
# ============================================================

if __name__ == "__main__":
    print("=== Vision QR self-test (HARDCODED KEY) ===")
    client = _get_client()
    if client:
        print("[R] Клиент успешно инициализирован.")
    else:
        print("[R] Клиент НЕ инициализирован.")