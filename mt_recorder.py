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
# СНИМОК СОСТОЯНИЯ MULTITRANSFER (stepsData, 'Способ перевода', офферы)
# ------------------------------------------------------------

async def snapshot_mt_state(page: Page, session_id: str, label: str) -> None:
    """
    Читает с клиента:
      - stepsData из localStorage
      - amount / currencyAmount / amountOk
      - наличие кликабельного "Способ перевода"
      - наличие офферов / 'Выбрать'
      - текст 'нет доступных способов'
    """
    _ensure_log_dir()
    js = """
    () => {
      const info = {
        stepsRaw: null,
        amountOk: false,
        amount: null,
        currencyAmount: null,
        methodLabelFound: false,
        methodClickable: false,
        methodRect: null,
        offersCount: 0,
        hasOfferButton: false,
        hasNoOffersText: false,
      };

      // --- 1) localStorage.stepsData ---
      try {
        const raw = window.localStorage.getItem("stepsData");
        if (raw) {
          info.stepsRaw = raw.length > 2000 ? raw.slice(0, 2000) + "...(truncated)" : raw;

          const data = JSON.parse(raw);
          const stepsData =
            data && data.state && data.state.stepsData
              ? data.state.stepsData
              : null;

          const s1 = stepsData ? (stepsData["1"] || stepsData[1] || null) : null;

          if (s1) {
            const aRaw = String(s1.amount ?? "").replace(",", ".").trim();
            const caRaw = String(s1.currencyAmount ?? "").replace(",", ".").trim();

            const a = parseFloat(aRaw || "0") || 0;
            const ca = parseFloat(caRaw || "0") || 0;

            info.amount = a;
            info.currencyAmount = ca;
            if (a > 0 && ca > 0) {
              info.amountOk = true;
            }
          }
        }
      } catch (e) {
        // ignore
      }

      // --- 2) ищем 'Способ перевода' / 'Выберите способ...' ---
      try {
        const allNodes = Array.from(document.querySelectorAll("*"));
        const labelNodes = allNodes.filter((el) => {
          const t = (el.textContent || "").toLowerCase();
          return (
            t.includes("способ перевода") ||
            t.includes("способ оплаты") ||
            t.includes("выберите способ")
          );
        });

        if (labelNodes.length > 0) {
          info.methodLabelFound = true;

          let clickable = null;
          for (const labelEl of labelNodes) {
            let candidate = labelEl.closest(
              "button, [role='button'], a, .css-1cban0a, .css-1thsucp"
            );
            if (!candidate) continue;

            const style = window.getComputedStyle(candidate);
            const rect = candidate.getBoundingClientRect();

            const disabled =
              candidate.disabled === true ||
              candidate.getAttribute("aria-disabled") === "true" ||
              style.pointerEvents === "none" ||
              style.cursor === "not-allowed" ||
              parseFloat(style.opacity || "1") < 0.5;

            if (disabled) continue;
            if (rect.width <= 0 || rect.height <= 0) continue;

            clickable = candidate;
            info.methodClickable = true;
            info.methodRect = {
              x: rect.x,
              y: rect.y,
              width: rect.width,
              height: rect.height,
            };
            break;
          }
        }
      } catch (e) {
        // ignore
      }

      // --- 3) список офферов / кнопки 'Выбрать' ---
      try {
        const buttons = Array.from(document.querySelectorAll("button, [role='button']"));
        const offers = buttons.filter((el) => {
          const t = (el.textContent || "").toLowerCase();
          return t.includes("выбрать");
        });
        info.offersCount = offers.length;
        info.hasOfferButton = offers.length > 0;
      } catch (e) {
        // ignore
      }

      // --- 4) текст про отсутствие доступных способов ---
      try {
        const bodyText = (document.body.innerText || "").toLowerCase();
        if (
          bodyText.includes("нет доступных способов") ||
          bodyText.includes("нет доступных офферов") ||
          bodyText.includes("нет доступных предложений")
        ) {
          info.hasNoOffersText = true;
        }
      } catch (e) {
        // ignore
      }

      return info;
    }
    """

    try:
        data = await page.evaluate(js)
    except Exception as e:
        data = {"evaluate_error": str(e)}

    # Краткий лог в консоль
    print(
        "[MT-STATE] "
        f"amountOk={data.get('amountOk')}, "
        f"amount={data.get('amount')}, "
        f"currencyAmount={data.get('currencyAmount')}, "
        f"methodLabelFound={data.get('methodLabelFound')}, "
        f"methodClickable={data.get('methodClickable')}, "
        f"methodRect={data.get('methodRect')}, "
        f"offersCount={data.get('offersCount')}, "
        f"hasOfferButton={data.get('hasOfferButton')}, "
        f"hasNoOffersText={data.get('hasNoOffersText')}"
    )

    # Полный JSON на диск
    fname = os.path.join(LOG_DIR, f"mt_state_{session_id}_{label}_{_ts()}.json")
    print(f"[MT-STATE] Сохраняю состояние Multitransfer → {fname}")
    try:
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[RECORDER] Ошибка записи MT state: {e}")


# ------------------------------------------------------------
# СНИМОК ДЕРЕВА КЛИКАБЕЛЬНЫХ ЭЛЕМЕНТОВ
# ------------------------------------------------------------

async def snapshot_clickable_tree(page: Page, session_id: str, label: str) -> None:
    """
    Собираем все кликабельные элементы:
      - button / a
      - [role="button"]
      - [onclick]
      - cursor: pointer
      - tabIndex >= 0

    С координатами, видимостью, текстом и классами.
    """
    _ensure_log_dir()

    js = """
    () => {
      const res = [];
      const all = Array.from(document.querySelectorAll("*"));

      for (const el of all) {
        try {
          const style = window.getComputedStyle(el);
          const rect = el.getBoundingClientRect();

          const clickable =
            el.tagName === "BUTTON" ||
            el.tagName === "A" ||
            el.getAttribute("role") === "button" ||
            el.hasAttribute("onclick") ||
            style.cursor === "pointer" ||
            el.tabIndex >= 0;

          if (!clickable) continue;

          const visible =
            rect.width > 0 &&
            rect.height > 0 &&
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            parseFloat(style.opacity || "1") > 0.05;

          const text = (el.textContent || "")
            .replace(/\\s+/g, " ")
            .trim();

          res.push({
            tag: el.tagName,
            id: el.id || null,
            classes: el.className || "",
            role: el.getAttribute("role") || null,
            tabIndex: el.tabIndex,
            text: text.slice(0, 200),
            visible,
            rect: {
              x: rect.x,
              y: rect.y,
              width: rect.width,
              height: rect.height,
            },
            styles: {
              display: style.display,
              visibility: style.visibility,
              opacity: style.opacity,
              pointerEvents: style.pointerEvents,
              cursor: style.cursor,
              zIndex: style.zIndex,
            },
            dataAttrs: Array.from(el.attributes)
              .filter(a => a.name.startsWith("data-"))
              .map(a => ({ name: a.name, value: a.value })),
          });
        } catch (e) {
          // ignore отдельные элементы
        }
      }

      // Чтобы не улететь в мегабайты — ограничим до 500 элементов
      return res.slice(0, 500);
    }
    """

    try:
        data = await page.evaluate(js)
    except Exception as e:
        data = {"evaluate_error": str(e)}

    fname = os.path.join(LOG_DIR, f"clickable_{session_id}_{label}_{_ts()}.json")
    print(f"[CLICKABLE] Сохраняю дерево кликабельных элементов → {fname}")
    try:
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[RECORDER] Ошибка записи clickable tree: {e}")


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
        print("  1) Заполнить форму, пройти все шаги, капчу и т.п. ВРУЧНУЮ.")
        print("  2) Когда дойдёшь до интересного места (после ввода суммы, после клика")
        print("     по 'Способ перевода', после открытия списка банков и т.д.),")
        print("     вернись в терминал и нажми Enter.")
        print("  3) Я сделаю четыре снапшота:")
        print("       - snapshot_window_state (window.*)")
        print("       - snapshot_mt_state (stepsData, 'Способ перевода', офферы)")
        print("       - snapshot_clickable_tree (все кликабельные элементы)")
        print("       - HTML + скрин страницы")
        print("  4) Чтобы закончить — закрой окно браузера или нажми Ctrl+C.\n")

        try:
            idx = 1
            loop = asyncio.get_running_loop()
            while True:
                # ждём Enter в терминале, чтобы сделать “контрольный снимок”
                await loop.run_in_executor(
                    None,
                    lambda: input(
                        f"[RECORDER] Snapshot #{idx}. Нажми Enter (или Ctrl+C для выхода)... "
                    ),
                )

                label = f"manual_{idx:03d}"
                print(f"[RECORDER] Делаю snapshot #{idx} текущего состояния страницы...")

                await snapshot_window_state(page, session_id, label=label)
                await snapshot_mt_state(page, session_id, label=label)
                await snapshot_clickable_tree(page, session_id, label=label)
                await snapshot_page_html_and_screenshot(page, session_id, label=label)

                print(f"[RECORDER] Snapshot #{idx} завершён.\n")
                idx += 1

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