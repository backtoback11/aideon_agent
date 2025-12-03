# multitransfer_telemetry.py
# Полный сбор JS-состояния, storage, network, console для Multitransfer.
# Новая версия:
#  - сама открывает страницу
#  - ничего не ждёт через input()
#  - автоматически логирует всё, пока ты руками проходишь шаги 1–3.

import json
import asyncio
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Page

BASE_DIR = Path("debug/multitransfer_telemetry")
BASE_DIR.mkdir(parents=True, exist_ok=True)

# Сколько секунд живёт сессия телеметрии (можно увеличить)
TELEMETRY_DURATION_SEC = 300  # 5 минут
SAMPLING_INTERVAL_SEC = 5     # как часто делаем снапшот storage/window


def ts() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


async def save_json(obj, path: Path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        print(f"[SAVE] OK → {path}")
    except Exception as e:
        print(f"[SAVE] ERROR {path}: {e}")


async def dump_storage(page: Page, label: str):
    """Снять снапшот localStorage + sessionStorage."""
    try:
        local = await page.evaluate(
            "() => Object.fromEntries(Object.entries(localStorage))"
        )
    except Exception as e:
        print(f"[STORAGE] localStorage error: {e}")
        local = {"__error__": str(e)}

    await save_json(local, BASE_DIR / f"{label}_localstorage_{ts()}.json")

    try:
        session = await page.evaluate(
            "() => Object.fromEntries(Object.entries(sessionStorage))"
        )
    except Exception as e:
        print(f"[STORAGE] sessionStorage error: {e}")
        session = {"__error__": str(e)}

    await save_json(session, BASE_DIR / f"{label}_sessionstorage_{ts()}.json")


async def dump_window_vars(page: Page, label: str):
    """
    Снять снапшот window.* (обрезаем большие объекты).
    Это поможет увидеть redux-сторы, глобальные состояния и т.п.
    """
    js = """
    () => {
        const out = {};
        for (let k of Object.keys(window)) {
            try {
                const val = window[k];
                if (val === null) continue;
                if (typeof val === "function") continue;

                if (typeof val === "object") {
                    // чтобы не улететь в мегабайты
                    try {
                        out[k] = JSON.stringify(val, null, 2).slice(0, 50000);
                    } catch(e) {
                        out[k] = "[unserializable object]";
                    }
                } else {
                    out[k] = val;
                }
            } catch(e) {}
        }
        return out;
    }
    """
    try:
        windump = await page.evaluate(js)
    except Exception as e:
        print(f"[WINDOW] dump error: {e}")
        windump = {"__error__": str(e)}

    await save_json(windump, BASE_DIR / f"{label}_window_dump_{ts()}.json")


async def collect_network_response(response, storage: list):
    """Перехват нужных сетевых ответов (commissions, uifields, transfers, confirm...)."""
    try:
        request = response.request
        url = request.url

        interesting = [
            "commissions",
            "uifields",
            "services",
            "create",
            "confirm",
            "directions",
            "transfers",
        ]

        if not any(key in url for key in interesting):
            return

        try:
            body = await response.text()
        except Exception:
            body = "<unreadable>"

        item = {
            "url": url,
            "method": request.method,
            "status": response.status,
            "headers": dict(response.headers),
            "body": body,
            "timestamp": ts(),
        }
        storage.append(item)
        print(f"[NETWORK] captured: {url}")
    except Exception as e:
        print(f"[NETWORK] handler error: {e}")


def safe_url_tag(url: str) -> str:
    """Сделать короткий тег из URL для имени файла."""
    if not url:
        return "no_url"
    clean = (
        url.replace("https://", "")
        .replace("http://", "")
        .replace("/", "_")
        .replace("?", "_")
        .replace("&", "_")
        .replace("=", "-")
    )
    return clean[:80]


async def periodic_sampler(page: Page):
    """
    Периодический сбор storage/window, пока ты руками кликаешь по шагам.
    """
    total_ticks = TELEMETRY_DURATION_SEC // SAMPLING_INTERVAL_SEC
    print(
        f"[SAMPLER] Запускаю периодический сбор снапшотов: "
        f"{total_ticks} тиков каждые {SAMPLING_INTERVAL_SEC} сек."
    )

    for i in range(1, total_ticks + 1):
        await asyncio.sleep(SAMPLING_INTERVAL_SEC)
        try:
            url = page.url
        except Exception:
            url = ""

        label = f"tick{i}_{safe_url_tag(url)}"
        print(f"[SAMPLER] tick #{i}, url={url}")
        await dump_storage(page, label)
        await dump_window_vars(page, label)

    print("[SAMPLER] Завершил периодический сбор снапшотов.")


async def navigation_sniffer(page: Page):
    """
    Дополнительно делаем снапшот при каждой навигации main frame.
    Это поможет поймать моменты:
      - после ввода суммы / получения commissions
      - после выбора банка / uifields
      - при переходе на sender-details
    """

    async def _on_nav(frame):
        if frame != page.main_frame:
            return
        try:
            url = frame.url
        except Exception:
            url = ""
        label = f"nav_{safe_url_tag(url)}"
        print(f"[NAV] Основной фрейм навигирован → {url}")
        await dump_storage(page, label)
        await dump_window_vars(page, label)

    page.on("framenavigated", lambda frame: asyncio.create_task(_on_nav(frame)))


async def run():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()

        page = await context.new_page()

        # --- LOG FILE ---
        console_log_path = BASE_DIR / f"console_{ts()}.log"
        console_f = open(console_log_path, "w", encoding="utf-8")

        def _on_console(msg):
            line = f"{datetime.utcnow().isoformat()} [{msg.type.upper()}] {msg.text}\n"
            console_f.write(line)
            console_f.flush()

        page.on("console", _on_console)

        # --- NETWORK CAPTURE ---
        network_data = []

        context.on(
            "response",
            lambda resp: asyncio.create_task(
                collect_network_response(resp, network_data)
            ),
        )

        # --- Навигация + автоснапшоты на каждом переходе ---
        await navigation_sniffer(page)

        # 1. Открываем страницу сразу
        start_url = "https://multitransfer.ru/transfer/uzbekistan"
        print(f"[START] Открываю {start_url}")
        await page.goto(start_url, wait_until="load")

        print("\n========== ИНСТРУКЦИИ ==========\n")
        print("1) В браузере (который открылся) сделай все шаги руками:")
        print("   - STEP1: введи сумму, дождись пересчёта курса.")
        print("   - STEP2: открой способы, выбери UZUM Bank, дождись формы sender-details.")
        print("   - STEP3: можешь заполнить/не заполнять — главное, чтобы форма загрузилась.")
        print("")
        print(
            f"Скрипт сам каждые {SAMPLING_INTERVAL_SEC} сек снимает снапшоты "
            f"и реагирует на навигацию.\n"
        )
        print(
            f"Через ~{TELEMETRY_DURATION_SEC} секунд ("
            f"{TELEMETRY_DURATION_SEC // 60} мин) он завершит сбор и закроет браузер."
        )
        print("Если хочешь закончить раньше — просто закрой окно браузера или прерви скрипт (Ctrl+C).\n")
        print("================================\n")

        # Запускаем периодический сбор
        sampler_task = asyncio.create_task(periodic_sampler(page))

        # Ждём завершения сбора
        try:
            await sampler_task
        except asyncio.CancelledError:
            pass

        # Финальный дамп network
        await save_json(network_data, BASE_DIR / f"network_{ts()}.json")

        console_f.close()
        await browser.close()

        print("\n📁 Все данные успешно собраны.")
        print(f"Папка: {BASE_DIR.resolve()}")


if __name__ == "__main__":
    asyncio.run(run())