from __future__ import annotations

import os
import json
import asyncio
from datetime import datetime

from playwright.async_api import async_playwright, Page, BrowserContext

# Куда складываем всё
LOG_DIR = "debug/mt_recorder"

# Базовый URL (можешь подправить, если нужно другой GEO)
BASE_URL = "https://multitransfer.ru/transfer/uzbekistan"


def _ensure_log_dir() -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass


def _ts() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")


# ------------------------------------------------------------
# ЛОГИРОВАНИЕ КОНСОЛИ
# ------------------------------------------------------------

def attach_console_logger(page: Page, session_id: str) -> None:
    """
    Логируем все сообщения консоли в один файл.
    """
    console_log_path = os.path.join(LOG_DIR, f"console_{session_id}.log")

    def _on_console(msg):
        try:
            text = msg.text()
        except Exception:
            text = ""

        line = f"[{_ts()}] [{msg.type}] {text}\n"
        print("[CONSOLE]", line.strip())

        try:
            with open(console_log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            print(f"[RECORDER] Ошибка записи console log: {e}")

    page.on("console", _on_console)


# ------------------------------------------------------------
# ЛОГИРОВАНИЕ СЕТИ (api.multitransfer.ru)
# ------------------------------------------------------------

def attach_network_logger(page: Page, session_id: str) -> None:
    """
    Логируем все ответы от api.multitransfer.ru.
    Каждый ответ — отдельный JSON-файл.
    """
    async def _handle_response(response):
        url = response.url
        if "api.multitransfer.ru" not in url:
            return

        entry = {
            "timestamp": _ts(),
            "url": url,
        }

        try:
            entry["status"] = response.status
        except Exception:
            entry["status"] = None

        try:
            headers = await response.all_headers()
        except Exception:
            headers = {}
        entry["headers"] = headers

        # Пытаемся понять, JSON или нет
        body_saved = False
        try:
            ct = headers.get("content-type", "") or headers.get("Content-Type", "")
            if "application/json" in ct.lower():
                try:
                    data = await response.json()
                    entry["json"] = data
                    body_saved = True
                except Exception as e_json:
                    entry["json_error"] = str(e_json)

            if not body_saved:
                try:
                    txt = await response.text()
                    entry["text"] = txt[:5000]  # чтобы не раздувать файл
                except Exception as e_txt:
                    entry["text_error"] = str(e_txt)
        except Exception as e:
            entry["body_error"] = str(e)

        # Пишем в файл
        fname = os.path.join(
            LOG_DIR,
            f"response_{session_id}_{_ts()}.json",
        )
        print(f"[NET] Логирую ответ → {fname}")
        try:
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[RECORDER] Ошибка записи response log: {e}")

    # Важно: заворачиваем в create_task, чтобы не блокировать Playwright
    page.on("response", lambda resp: asyncio.create_task(_handle_response(resp)))


# ------------------------------------------------------------
# СНИМОК window.* (ключей глобального состояния)
# ------------------------------------------------------------

async def snapshot_window_state(page: Page, session_id: str, label: str) -> None:
    """
    Делаем небольшой снимок JS-окружения:
      - Object.keys(window)
      - пытаемся снять ключи популярных глобалов (app, store, __NUXT__ и т.п.)
    """
    _ensure_log_dir()
    out = {
        "timestamp": _ts(),
        "label": label,
        "url": page.url,
    }

    script = """
    () => {
      const res = {};
      try {
        const keys = Object.keys(window);
        res.windowKeys = keys;

        const candidates = [
          'app', 'store', '__NUXT__', '__INITIAL_STATE__',
          '__VUE_DEVTOOLS_GLOBAL_HOOK__', 'transfer', 'stepper'
        ];

        res.globals = {};
        for (const k of candidates) {
          try {
            const v = window[k];
            if (v !== undefined) {
              if (v === null) {
                res.globals[k] = null;
              } else if (typeof v === 'object') {
                // Берём только верхний уровень ключей, чтобы не улететь в рекурсию
                res.globals[k] = {
                  __type: Object.prototype.toString.call(v),
                  keys: Object.keys(v).slice(0, 50),
                };
              } else {
                res.globals[k] = {
                  __type: typeof v,
                  value: String(v).slice(0, 500),
                };
              }
            }
          } catch (e) {
            res.globals[k] = { __error: String(e) };
          }
        }
      } catch (e) {
        res.error = String(e);
      }
      return res;
    }
    """
    try:
        data = await page.evaluate(script)
        out["data"] = data
    except Exception as e:
        out["evaluate_error"] = str(e)

    fname = os.path.join(LOG_DIR, f"window_snapshot_{session_id}_{label}_{_ts()}.json")
    print(f"[SNAPSHOT] Сохраняю snapshot window → {fname}")
    try:
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[RECORDER] Ошибка записи snapshot: {e}")


# ------------------------------------------------------------
# СНИМОК HTML + СКРИН
# ------------------------------------------------------------

async def snapshot_page_html_and_screenshot(page: Page, session_id: str, label: str) -> None:
    _ensure_log_dir()
    ts = _ts()

    # HTML
    html_path = os.path.join(LOG_DIR, f"page_{session_id}_{label}_{ts}.html")
    try:
        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[SNAPSHOT] HTML → {html_path}")
    except Exception as e:
        print(f"[SNAPSHOT] Ошибка сохранения HTML: {e}")

    # Screenshot
    png_path = os.path.join(LOG_DIR, f"page_{session_id}_{label}_{ts}.png")
    try:
        await page.screenshot(path=png_path, full_page=True)
        print(f"[SNAPSHOT] PNG → {png_path}")
    except Exception as e:
        print(f"[SNAPSHOT] Ошибка сохранения PNG: {e}")


# ------------------------------------------------------------
# ОСНОВНОЙ РАННЕР
# ------------------------------------------------------------

async def main():
    _ensure_log_dir()

    session_id = _ts()
    print(f"[RECORDER] Старт сессии логирования: {session_id}")
    print("[RECORDER] Скрипт НИЧЕГО не заполняет сам.")
    print("[RECORDER] Ты просто работаешь в браузере, как обычно,")
    print("           а я пишу логи консоли, сети и снапшоты.")
    print()
    print(f"[RECORDER] Логи будут в папке: {LOG_DIR}")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context: BrowserContext = await browser.new_context(
            viewport={"width": 1366, "height": 768},
        )
        page: Page = await context.new_page()

        attach_console_logger(page, session_id)
        attach_network_logger(page, session_id)

        print(f"[RECORDER] Открываю {BASE_URL} ...")
        await page.goto(BASE_URL)

        print("\n[RECORDER] 🔴 Теперь ты можешь:")
        print("  1) Заполнить форму, пройти все шаги, капчу и т.п.")
        print("  2) Когда дойдёшь до интересного места (например, finish-transfer),")
        print("     просто НИЧЕГО не делай, а вернись в терминал и нажми Enter.")
        print("  3) Я сделаю snapshot (window, HTML, скрин) и продолжу ждать.")
        print("  4) Чтобы закончить — закрой окно браузера или нажми Ctrl+C.\n")

        try:
            while True:
                # ждём Enter в терминале, чтобы сделать “контрольный снимок”
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: input("[RECORDER] Нажми Enter, чтобы сделать snapshot (или Ctrl+C для выхода)... "),
                )

                print("[RECORDER] Делаю snapshot текущего состояния страницы...")
                await snapshot_window_state(page, session_id, label="manual")
                await snapshot_page_html_and_screenshot(page, session_id, label="manual")

        except KeyboardInterrupt:
            print("\n[RECORDER] Остановлено пользователем (Ctrl+C).")
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    print(f"[RECORDER] Сессия {session_id} завершена. Логи в {LOG_DIR}")


if __name__ == "__main__":
    asyncio.run(main())