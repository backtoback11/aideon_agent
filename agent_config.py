from __future__ import annotations
from typing import List, Optional

# ----------------------
# DATABASE
# ----------------------
# Используется в db.py:
# engine = create_engine(DB_URL, ...)
DB_URL = "sqlite:///./aideon_agent.db"


# ----------------------
# MULTITRANSFER
# ----------------------
MULTITRANSFER_BASE_URL = "https://multitransfer.ru/transfer/uzbekistan"

# таймауты навигации Playwright (в миллисекундах)
NAVIGATION_TIMEOUT_MS = 60_000  # 60 секунд


# ----------------------
# CAPTCHA
# ----------------------
# сколько максимум ждём прохождения капчи (если решает человек)
CAPTCHA_MAX_WAIT_SECONDS = 900   # 15 минут
CAPTCHA_POLL_INTERVAL_SECONDS = 5

# порядок провайдеров капчи (для captcha_solver / captcha_manager)
CAPTCHA_PROVIDERS_ORDER: List[str] = [
    "twocaptcha",
    "anticaptcha",
]

# API-ключи капча-сервисов (пока пустые — будут подхватываться
# либо отсюда, либо из таблицы settings через admin-панель)
TWOCAPTCHA_API_KEY: str = ""      # например: "2CAPTCHA_API_KEY_HERE"
ANTICAPTCHA_API_KEY: str = ""     # например: "ANTICAPTCHA_API_KEY_HERE"


# ----------------------
# HEADERS / USER AGENT
# ----------------------
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ----------------------
# CONCURRENCY (параллельные агенты)
# ----------------------
# сколько инвойсов одновременно обрабатываем в agent.py
MAX_CONCURRENT_INVOICES: int = 10


# ----------------------
# PROXY ROTATION (простейший вариант)
# ----------------------
# Здесь — базовый вариант: список прокси в конфиге.
# Позже можем заменить на чтение из таблицы Proxy.
PROXY_LIST: List[str] = [
    # Примеры:
    # "http://user:pass@45.148.240.152:63030",
    # "socks5://user:pass@45.148.240.152:63031",
]

_proxy_index: int = 0


def get_next_proxy() -> Optional[str]:
    """
    Дает следующий прокси по кругу.
    Если PROXY_LIST пуст — вернёт None, и агент пойдёт без прокси.
    """
    global _proxy_index

    if not PROXY_LIST:
        return None

    proxy = PROXY_LIST[_proxy_index % len(PROXY_LIST)]
    _proxy_index += 1
    return proxy