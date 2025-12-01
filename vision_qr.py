from __future__ import annotations

import base64
import os
import re
from typing import Optional

from openai import OpenAI

# ============================================================
#  НАСТРОЙКИ
# ============================================================

# Ключ и модели читаем из переменных окружения.
# Примеры (НЕ коммитить реальные ключи в репозиторий!):
#   export OPENAI_API_KEY="sk-..."             # реальный ключ только в окружении
#   export OPENAI_VISION_MODEL="gpt-4o-mini"
#   export OPENAI_VISION_FALLBACK_MODEL="gpt-4.1"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
PRIMARY_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini")
FALLBACK_MODEL = os.environ.get("OPENAI_VISION_FALLBACK_MODEL", "gpt-4.1")

# Регэксп для вытаскивания первого URL из текста
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


# ============================================================
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def _get_client() -> Optional[OpenAI]:
    """
    Возвращает OpenAI-клиент или None, если ключ не задан.
    """
    if not OPENAI_API_KEY:
        print(
            "[VISION] OPENAI_API_KEY не задан. "
            "Укажи его в переменных окружения (export OPENAI_API_KEY='...')."
        )
        return None

    # Небольшой дебаг, чтобы видеть, какой ключ реально используется (без утечки)
    try:
        print(
            f"[VISION] Используется OPENAI_API_KEY: "
            f"prefix={str(OPENAI_API_KEY)[:12]}..., len={len(OPENAI_API_KEY)}"
        )
    except Exception:
        # На всякий случай, если вдруг ключ не строка
        pass

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        return client
    except Exception as e:
        print(f"[VISION] Ошибка инициализации OpenAI-клиента: {e}")
        return None


def _parse_message_content(message) -> str:
    """
    Безопасно достаём текст из ответа нового SDK:
    content может быть строкой или массивом частей.
    """
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
    """
    Простейшая эвристика: модель отказалась помогать (safety-фильтр).
    """
    lowered = text.lower()
    phrases = [
        "не могу помочь",
        "не могу с этим помочь",
        "cannot help",
        "can't help",
        "i’m not able to",
        "i am not able to",
        "sorry, i can’t",
        "sorry, i cannot",
        "извините, я не могу",
    ]
    return any(p in lowered for p in phrases)


def _call_vision_once(
    client: OpenAI,
    model: str,
    png_bytes: bytes,
) -> Optional[str]:
    """
    Один вызов GPT-Vision с максимально нейтральным промптом.
    Возвращает URL или None.
    """

    # ВАЖНО: никаких слов "оплата/банк/СБП/НСПК" и т.п. в промпте.
    prompt = (
        "На изображении есть один QR-код.\n"
        "Твоя задача: прочитать текст, закодированный в этом QR-коде, "
        "и вернуть ровно одну строку — этот текст.\n"
        "Не добавляй никаких пояснений, комментариев, предупреждений или форматирования.\n"
        "Просто верни строку с тем, что зашито в QR-коде."
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
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}"
                            },
                        },
                    ],
                }
            ],
            max_tokens=128,
            temperature=0.0,
        )
    except Exception as e:
        print(f"[VISION] Ошибка запроса к OpenAI (model={model}): {e}")
        return None

    try:
        msg = response.choices[0].message
        text = _parse_message_content(msg)
        print(f"[VISION] Ответ модели ({model}): {text!r}")

        if not text:
            print("[VISION] Ответ пустой.")
            return None

        # Если явно похоже на отказ по безопасности — считаем, что модель не помогла
        if _looks_like_safety_refusal(text):
            print("[VISION] Похоже, сработал safety-отказ модели.")
            return None

        # Если это чистый URL — отлично
        if text.startswith("http://") or text.startswith("https://"):
            return text

        # Иначе вытаскиваем первый URL из текста (на случай лишних символов)
        m = URL_RE.search(text)
        if m:
            return m.group(0)

        print("[VISION] В ответе не найден URL.")
        return None

    except Exception as e:
        print(f"[VISION] Ошибка разбора ответа OpenAI: {e}")
        return None


# ============================================================
#  ОСНОВНАЯ ФУНКЦИЯ
# ============================================================

def extract_qr_deeplink_from_screenshot(png_bytes: bytes) -> Optional[str]:
    """
    Высокоуровневая функция:

      1) инициализируем OpenAI-клиент,
      2) пробуем основную модель (PRIMARY_MODEL),
      3) если она не вернула URL (в т.ч. из-за safety-отказа),
         пробуем fallback-модель (FALLBACK_MODEL),
      4) возвращаем URL или None.
    """
    client = _get_client()
    if client is None:
        return None

    # 1) Основная модель
    print(f"[VISION] Запрос к основной модели: {PRIMARY_MODEL}")
    url = _call_vision_once(client, PRIMARY_MODEL, png_bytes)
    if url:
        return url

    # 2) Fallback-модель (если отличается от основной)
    if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
        print(f"[VISION] Пробую fallback-модель: {FALLBACK_MODEL}")
        url = _call_vision_once(client, FALLBACK_MODEL, png_bytes)
        if url:
            return url

    print("[VISION] Не удалось получить URL ни основной, ни fallback-моделью.")
    return None