from __future__ import annotations

import base64
import os
import time
from enum import Enum
from typing import Optional, Union, List

import requests
import cv2
import numpy as np

try:
    import pytesseract

    _HAS_PYTESSERACT = True
except ImportError:
    _HAS_PYTESSERACT = False


# =========================
# 🔧 Внешние поставщики
# =========================

# Берём ключи из переменных окружения (рекомендуется положить в .env)
RUCAPTCHA_KEY = os.getenv("RUCAPTCHA_KEY", "")
CAPSOLVER_KEY = os.getenv("CAPSOLVER_KEY", "")
TWOCAPTCHA_KEY = os.getenv("TWOCAPTCHA_KEY", "")


class CaptchaType(str, Enum):
    AUTO = "auto"
    IMAGE = "image"      # обычная картинка с текстом
    SLIDER = "slider"    # слайдер-капча


# =========================
# 🔨 Основной метод
# =========================

def solve_captcha(
    image_bytes: bytes,
    captcha_type: Union[CaptchaType, str] = CaptchaType.AUTO,
) -> Optional[Union[str, int]]:
    """
    Универсальный AI-обработчик капчи.

    Параметры:
      image_bytes  — байты картинки капчи
      captcha_type — 'auto' | 'image' | 'slider' (или CaptchaType)

    Возвращает:
      • str  — если это IMAGE-капча (текст / цифры)
      • int  — если это SLIDER-капча (смещение по X)
      • None — если решить не удалось
    """
    # Нормализуем тип
    if isinstance(captcha_type, str):
        try:
            captcha_type = CaptchaType(captcha_type)
        except ValueError:
            print(f"[CAPTCHA] ❌ Неизвестный тип капчи: {captcha_type}")
            return None

    print(f"[CAPTCHA] Начало распознавания… Тип: {captcha_type.value}")

    # --------------------------------------------------------
    # 1) AUTO → пытаемся определить, слайдер или обычная
    # --------------------------------------------------------
    if captcha_type == CaptchaType.AUTO:
        if _is_slider(image_bytes):
            captcha_type = CaptchaType.SLIDER
        else:
            captcha_type = CaptchaType.IMAGE
        print(f"[CAPTCHA] AUTO → определён тип: {captcha_type.value}")

    # --------------------------------------------------------
    # 2) SLIDER-капча
    # --------------------------------------------------------
    if captcha_type == CaptchaType.SLIDER:
        return _solve_slider_chain(image_bytes)

    # --------------------------------------------------------
    # 3) Обычная IMAGE-капча (буквы/цифры)
    # --------------------------------------------------------
    if captcha_type == CaptchaType.IMAGE:
        return _solve_image_chain(image_bytes)

    print("[CAPTCHA] ❌ Тип капчи не поддержан")
    return None


# =========================
# 🔁 Цепочка решения IMAGE-капчи
# =========================

def _solve_image_chain(image_bytes: bytes) -> Optional[str]:
    """
    Последовательность попыток для обычной картинной капчи:

      1) Локальный OpenCV + pytesseract
      2) RuCaptcha
      3) 2Captcha
      4) Capsolver
    """
    print("[CAPTCHA][IMAGE] Пытаемся решить локально (OpenCV + pytesseract)")
    text = _solve_image_local(image_bytes)
    if text:
        print(f"[CAPTCHA][IMAGE] Локально распознано: {text}")
        return text

    # --- RuCaptcha ---
    if RUCAPTCHA_KEY:
        print("[CAPTCHA][IMAGE] Переходим к RuCaptcha API")
        text = _solve_image_rucaptcha(image_bytes)
        if text:
            print(f"[CAPTCHA][IMAGE] RuCaptcha решило: {text}")
            return text
    else:
        print("[CAPTCHA][IMAGE] RUCAPTCHA_KEY не задан, пропускаем RuCaptcha")

    # --- 2Captcha ---
    if TWOCAPTCHA_KEY:
        print("[CAPTCHA][IMAGE] Переходим к 2Captcha API")
        text = _solve_image_2captcha(image_bytes)
        if text:
            print(f"[CAPTCHA][IMAGE] 2Captcha решило: {text}")
            return text
    else:
        print("[CAPTCHA][IMAGE] TWOCAPTCHA_KEY не задан, пропускаем 2Captcha")

    # --- Capsolver ---
    if CAPSOLVER_KEY:
        print("[CAPTCHA][IMAGE] Переходим к Capsolver API")
        text = _solve_image_capsolver(image_bytes)
        if text:
            print(f"[CAPTCHA][IMAGE] Capsolver решило: {text}")
            return text
    else:
        print("[CAPTCHA][IMAGE] CAPSOLVER_KEY не задан, пропускаем Capsolver")

    print("[CAPTCHA][IMAGE] ❌ Все методы провалились")
    return None


def _solve_image_local(image_bytes: bytes) -> Optional[str]:
    """
    Локальное распознавание картинной капчи (text) через OpenCV + pytesseract.

    Возвращает:
      • строку, если удалось распознать
      • None, если не получилось или pytesseract не установлен
    """
    if not _HAS_PYTESSERACT:
        print("[CAPTCHA][local] pytesseract не установлен, пропускаем локальное решение")
        return None

    try:
        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            print("[CAPTCHA][local] Не удалось декодировать изображение")
            return None

        # Бинаризация / повышение контраста
        _, th = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        text = pytesseract.image_to_string(th, config="--psm 7")
        text = text.strip().replace(" ", "")

        # Минимальная длина результата
        if len(text) >= 3:
            return text

        print(f"[CAPTCHA][local] Слишком короткий текст: '{text}'")

    except Exception as e:
        print(f"[CAPTCHA][local] Ошибка: {e}")

    return None


def _solve_image_rucaptcha(image_bytes: bytes) -> Optional[str]:
    """
    RuCaptcha / rucaptcha.com — классический API (очень похож на 2Captcha).
    """
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        create_resp = requests.post(
            "http://rucaptcha.com/in.php",
            data={
                "key": RUCAPTCHA_KEY,
                "method": "base64",
                "body": b64,
                "json": 1,
            },
            timeout=30,
        ).json()

        if create_resp.get("status") != 1:
            print("[CAPTCHA][rucaptcha] create error:", create_resp)
            return None

        captcha_id = create_resp["request"]

        # Ожидаем результат
        for _ in range(20):
            res = requests.get(
                "http://rucaptcha.com/res.php",
                params={
                    "key": RUCAPTCHA_KEY,
                    "action": "get",
                    "id": captcha_id,
                    "json": 1,
                },
                timeout=30,
            ).json()

            if res.get("status") == 1:
                return res.get("request")

            if res.get("request") in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
                time.sleep(5)
                continue

            print("[CAPTCHA][rucaptcha] error:", res)
            return None

    except Exception as e:
        print(f"[CAPTCHA][rucaptcha] Ошибка: {e}")

    return None


def _solve_image_2captcha(image_bytes: bytes) -> Optional[str]:
    """
    2Captcha — аналог RuCaptcha, но свой ключ и домен.
    """
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        create_resp = requests.post(
            "http://2captcha.com/in.php",
            data={
                "key": TWOCAPTCHA_KEY,
                "method": "base64",
                "body": b64,
                "json": 1,
            },
            timeout=30,
        ).json()

        if create_resp.get("status") != 1:
            print("[CAPTCHA][2captcha] create error:", create_resp)
            return None

        captcha_id = create_resp["request"]

        for _ in range(20):
            res = requests.get(
                "http://2captcha.com/res.php",
                params={
                    "key": TWOCAPTCHA_KEY,
                    "action": "get",
                    "id": captcha_id,
                    "json": 1,
                },
                timeout=30,
            ).json()

            if res.get("status") == 1:
                return res.get("request")

            if res.get("request") in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
                time.sleep(5)
                continue

            print("[CAPTCHA][2captcha] error:", res)
            return None

    except Exception as e:
        print(f"[CAPTCHA][2captcha] Ошибка: {e}")

    return None


def _solve_image_capsolver(image_bytes: bytes) -> Optional[str]:
    """
    Capsolver — AI-сервис, умеющий и текстовые капчи.
    """
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        payload = {
            "clientKey": CAPSOLVER_KEY,
            "task": {
                "type": "ImageToTextTask",
                "body": b64,
            },
        }

        create_resp = requests.post(
            "https://api.capsolver.com/createTask",
            json=payload,
            timeout=30,
        ).json()

        task_id = create_resp.get("taskId")
        if not task_id:
            print("[CAPTCHA][capsolver] create error:", create_resp)
            return None

        for _ in range(20):
            res = requests.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": CAPSOLVER_KEY, "taskId": task_id},
                timeout=30,
            ).json()

            if res.get("status") == "ready":
                solution = res.get("solution", {})
                return solution.get("text")

            time.sleep(3)

    except Exception as e:
        print(f"[CAPTCHA][capsolver] Ошибка: {e}")

    return None


# =========================
# 🔁 Цепочка решения SLIDER-капчи
# =========================

def _solve_slider_chain(image_bytes: bytes) -> Optional[int]:
    """
    Цепочка для слайдер-капчи:

      1) Локальный OpenCV (поиск смещения)
      2) Capsolver (если есть ключ)
    """
    print("[CAPTCHA][SLIDER] Пытаемся решить локально")
    shift = _solve_slider_local(image_bytes)
    if shift is not None:
        print(f"[CAPTCHA][SLIDER] Локально найден shift={shift}")
        return shift

    if CAPSOLVER_KEY:
        print("[CAPTCHA][SLIDER] Переходим к Capsolver (координаты)")
        shift = _solve_slider_capsolver(image_bytes)
        if shift is not None:
            print(f"[CAPTCHA][SLIDER] Capsolver вернул shift={shift}")
            return shift
    else:
        print("[CAPTCHA][SLIDER] CAPSOLVER_KEY не задан, пропускаем Capsolver")

    print("[CAPTCHA][SLIDER] ❌ Слайдер не удалось решить")
    return None


def _solve_slider_local(image_bytes: bytes) -> Optional[int]:
    """
    Простейший локальный метод для слайдер-капчи:
      • ищем самый крупный контур (как вырез пазла)
      • берём его X как смещение.

    Это эвристика, может не работать на всех сайтах.
    """
    try:
        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            print("[CAPTCHA][slider_local] Не удалось декодировать изображение")
            return None

        edges = cv2.Canny(img, 50, 200)
        cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not cnts:
            print("[CAPTCHA][slider_local] Контуры не найдены")
            return None

        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)

        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            # простая эвристика под пазл
            if 20 < w < img.shape[1] * 0.8 and 20 < h < img.shape[0] * 0.8:
                return x

        return None

    except Exception as e:
        print(f"[CAPTCHA][slider_local] Ошибка: {e}")
        return None


def _solve_slider_capsolver(image_bytes: bytes) -> Optional[int]:
    """
    Capsolver для слайдер-капчи (ImageToCoordinatesTask).
    Возвращаем X-координату первой найденной точки.
    """
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        create_payload = {
            "clientKey": CAPSOLVER_KEY,
            "task": {
                "type": "ImageToCoordinatesTask",
                "body": b64,
            },
        }

        create_resp = requests.post(
            "https://api.capsolver.com/createTask",
            json=create_payload,
            timeout=30,
        ).json()

        task_id = create_resp.get("taskId")
        if not task_id:
            print("[CAPTCHA][capsolver_slider] create error:", create_resp)
            return None

        for _ in range(20):
            res = requests.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": CAPSOLVER_KEY, "taskId": task_id},
                timeout=30,
            ).json()

            if res.get("status") == "ready":
                coords: List[dict] = res.get("solution", {}).get("coordinates", [])
                if coords:
                    return int(coords[0].get("x", 0))
                return None

            time.sleep(2)

    except Exception as e:
        print(f"[CAPTCHA][capsolver_slider] Ошибка: {e}")

    return None


# =========================
# 🔍 Определение типа (slider / image)
# =========================

def _is_slider(image_bytes: bytes) -> bool:
    """
    Примитивный детектор slider-капчи по геометрии/яркости.
    Это эвристика, не строгая, но для AUTO-режима достаточно.
    """
    try:
        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return False

        h, w = img.shape[:2]

        # slider часто широкий и невысокий
        if w > 250 and h < 150:
            return True

        # Простой анализ средней яркости – slider часто светло-серый
        avg_color = img.mean()
        if 120 < avg_color < 210:
            return True

    except Exception as e:
        print(f"[CAPTCHA][detect_slider] Ошибка: {e}")

    return False