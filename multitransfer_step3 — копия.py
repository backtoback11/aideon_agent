from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

# Папка для дампов третьего шага
DEBUG_DIR_STEP3 = "debug/multitransfer_step3"


def _ensure_step3_debug_dir() -> None:
    """Гарантируем, что папка для дампов STEP3 существует."""
    try:
        os.makedirs(DEBUG_DIR_STEP3, exist_ok=True)
    except Exception as e:
        print(f"[STEP3-DEBUG] Не удалось создать папку {DEBUG_DIR_STEP3}: {e}")


async def _save_step3_html(page: Page, label: str) -> None:
    """
    Сохранение HTML для третьего шага:
    debug/multitransfer_step3/{label}_step3_YYYYMMDD_HHMMSS.html
    """
    _ensure_step3_debug_dir()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    html_path = os.path.join(DEBUG_DIR_STEP3, f"{label}_step3_{ts}.html")

    try:
        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[STEP3-DEBUG] HTML страницы сохранён в {html_path}")
    except Exception as e:
        print(f"[STEP3-DEBUG] Не удалось сохранить HTML: {e}")


# ============================================================
# АВТО-КЛИК ПРОСТОЙ CHECKBOX-КАПЧИ (SmartCaptcha)
# ============================================================

async def _try_solve_checkbox_captcha(
    page: Page,
    timeout_ms: int = 10_000,
) -> bool:
    """
    Пытаемся автоматически кликнуть по чекбокс-капче SmartCaptcha.

    Актуальная вёрстка:
      - iframe backend-а
      - iframe с чекбоксом:
          <iframe
              data-testid="checkbox-iframe"
              title="SmartCaptcha checkbox widget"
              src="https://smartcaptcha.yandexcloud.net/checkbox...."
          ></iframe>

    Поэтому:
      1) целимся строго в iframe[data-testid="checkbox-iframe"],
      2) внутри него ищем .CheckboxCaptcha-Button и кликаем.
    """
    print("[CAPTCHA] Пытаюсь автоматически кликнуть по checkbox-капче (SmartCaptcha)...")

    try:
        # Берём iframe именно чекбокса, чтобы не ловить strict mode по двум iframe
        frame_locator = page.frame_locator("iframe[data-testid='checkbox-iframe']")
        checkbox = frame_locator.locator(".CheckboxCaptcha-Button")

        # ждём появления кнопки в iframe
        await checkbox.wait_for(timeout=timeout_ms)

        # небольшая пауза как в JS
        try:
            await page.wait_for_timeout(1000)
        except Exception:
            pass

        await checkbox.click()
        print("[CAPTCHA] ✅ Checkbox-капча кликнута автоматически.")
        return True

    except PlaywrightTimeoutError:
        print("[CAPTCHA] ⏱ iframe[data-testid='checkbox-iframe'] или .CheckboxCaptcha-Button не появились за отведённое время, продолжаем без авто-клика.")
        return False
    except Exception as e:
        print(f"[CAPTCHA] ⚠ Ошибка при попытке кликнуть капчу: {e}")
        try:
            await _save_step3_html(page, label="captcha_error")
        except Exception:
            pass
        return False


# ============================================================
# ПОПАП ПОСЛЕ КАПЧИ С КНОПКОЙ "ПРОДОЛЖИТЬ"
# ============================================================

async def _click_post_captcha_continue_popup(
    page: Page,
    timeout_ms: int = 20_000,
) -> bool:
    """
    После успешного чекбокса SmartCaptcha появляется попап
    с синей кнопкой "Продолжить" и спиннером — именно он
    запускает финальный запрос и ожидание формирования QR.

    - ждём появления диалога/оверлея,
    - ищем внутри кнопку "Продолжить" и кликаем.

    Если ничего не нашли — просто логируем и возвращаем False,
    сценарий дальше не ломаем.
    """
    print("[CAPTCHA] Ищу попап после капчи с кнопкой 'Продолжить'...")

    try:
        # Основная попытка — по роли dialog
        dialog = page.get_by_role("dialog")
        has_dialog = False

        try:
            await dialog.wait_for(timeout=timeout_ms)
            if await dialog.count() > 0:
                has_dialog = True
        except PlaywrightTimeoutError:
            has_dialog = False

        if not has_dialog:
            # Резервный вариант — типичные классы MUI / alertdialog
            dialog = page.locator(".MuiDialog-root, .MuiDialog-paper, [role='alertdialog']")
            try:
                await dialog.first.wait_for(timeout=2000)
                if await dialog.count() == 0:
                    print("[CAPTCHA] Попап 'Продолжить' после капчи не найден (нет диалога).")
                    return False
            except PlaywrightTimeoutError:
                print("[CAPTCHA] Попап 'Продолжить' после капчи не найден (timeout по диалогу).")
                return False

        dlg = dialog.first

        # Ищем кнопку "Продолжить" внутри диалога
        btn = dlg.get_by_role("button", name="Продолжить").first
        try:
            await btn.wait_for(timeout=5000)
        except PlaywrightTimeoutError:
            print("[CAPTCHA] В попапе после капчи не нашли кнопку 'Продолжить' (timeout).")
            return False

        await btn.scroll_into_view_if_needed()
        await btn.click()
        print("[CAPTCHA] ✅ Нажата кнопка 'Продолжить' в попапе после капчи.")

        # Небольшая пауза, чтобы фронт успел отправить запрос и запустить спиннер
        try:
            await page.wait_for_timeout(1500)
        except Exception:
            pass

        return True

    except Exception as e:
        print(f"[CAPTCHA] ⚠ Ошибка при обработке попапа после капчи: {e}")
        try:
            await _save_step3_html(page, label="post_captcha_popup_error")
        except Exception:
            pass
        return False


def _normalize_date_for_multitransfer(value: Optional[str]) -> Optional[str]:
    """
    Приводим дату к формату ДД.ММ.ГГГГ, который, скорее всего, ожидает форма.
    Поддерживаем несколько входных форматов:
      - YYYY-MM-DD
      - YYYY-MM-DD HH:MM:SS
      - DD.MM.YYYY
      - DD-MM-YYYY
      - YYYY.MM.DD
    Если ничего не подошло — возвращаем исходную строку.
    """
    if not value:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    # Если уже похоже на ДД.ММ.ГГГГ — просто возвращаем
    try:
        dt = datetime.strptime(raw, "%d.%m.%Y")
        return dt.strftime("%d.%m.%Y")
    except ValueError:
        pass

    # Пробуем типичные внутренние форматы
    candidates = [
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d-%m-%Y",
        "%Y.%m.%d",
    ]

    for fmt in candidates:
        try:
            dt = datetime.strptime(raw, fmt)
            norm = dt.strftime("%d.%m.%Y")
            print(f"[STEP3-DATE] Нормализовали дату '{raw}' по формату '{fmt}' → '{norm}'")
            return norm
        except ValueError:
            continue

    # Ничего не подошло — возвращаем как есть
    print(f"[STEP3-DATE] Не удалось нормализовать дату '{raw}', оставляем как есть")
    return raw


async def _fill_by_label_or_name(
    page: Page,
    label_text: Optional[str],
    name_attr: Optional[str],
    value: Optional[str],
) -> None:
    """
    Универсальный хелпер:
      1) пробуем заполнить по видимому label (get_by_label)
      2) если не получилось — по input[name=...]
    """
    if not value:
        return

    # 1) по label
    if label_text:
        try:
            el = page.get_by_label(label_text)
            await el.wait_for(timeout=100)
            await el.fill(str(value))
            print(f"[STEP3] Заполнили по label '{label_text}' значением '{value}'")
            return
        except PlaywrightTimeoutError:
            print(f"[STEP3] ⚠ Не нашли поле по label '{label_text}', пробуем по name…")
        except Exception as e:
            print(f"[STEP3] ⚠ Ошибка при заполнении по label '{label_text}': {e}")

    # 2) fallback — по name
    if name_attr:
        sel = f"input[name='{name_attr}']"
        try:
            await page.fill(sel, str(value))
            print(f"[STEP3] Заполнили {sel} значением '{value}' (fallback)")
        except Exception as e:
            print(f"[STEP3] ⚠ Не удалось заполнить {sel}: {e}")


async def _select_country_by_label(page: Page, label_text: str, country: Optional[str]) -> None:
    """
    Для дропдаунов 'Страна рождения', 'Страна регистрации' и т.п.
    Стратегия:
      - кликаем по блоку под заголовком label_text
      - выбираем пункт с текстом country
    """
    if not country:
        return

    print(f"[STEP3] Пытаюсь выбрать страну '{country}' для '{label_text}'")

    try:
        label_el = page.get_by_text(label_text, exact=True).first
        await label_el.wait_for(timeout=2000)

        # контейнер с самим дропдауном (строка со стрелкой)
        container = label_el.locator("xpath=following-sibling::*[1]")
        if await container.count() == 0:
            container = label_el.locator(
                "xpath=ancestor::*[self::div or self::label][1]/following-sibling::*[1]"
            )

        await container.first.scroll_into_view_if_needed()
        await container.first.click()
        print(f"[STEP3] Открыл дропдаун для '{label_text}'")

        # ищем опцию
        try:
            option = page.get_by_role("option", name=country, exact=False).first
            await option.wait_for(timeout=8000)
        except PlaywrightTimeoutError:
            option = page.get_by_text(country, exact=False).first
            await option.wait_for(timeout=8000)

        await option.scroll_into_view_if_needed()
        await option.click()
        print(f"[STEP3] ✅ В дропдауне '{label_text}' выбрана страна '{country}'")
    except PlaywrightTimeoutError:
        print(f"[STEP3] ⚠ Timeout при выборе страны '{country}' для '{label_text}'")
    except Exception as e:
        print(f"[STEP3] ⚠ Ошибка при выборе страны '{country}' для '{label_text}': {e}")


# ============================================================
# Попапы с ошибками ("Понятно"/"ОК"/"Закрыть")
# ============================================================

ERROR_POPUP_BUTTON_TEXTS = [
    "Понятно",
    "ОК",
    "Ок",
    "Ok",
    "OK",
    "Хорошо",
    "Закрыть",
]


async def close_error_popups(page: Page, max_attempts: int = 3) -> bool:
    """
    Пытаемся найти и закрыть попапы с ошибками:
      - ищем диалоги/модалки,
      - логируем текст,
      - нажимаем кнопку 'Понятно' / 'ОК' / 'Закрыть' и т.п.

    Возвращает True, если хотя бы один попап был закрыт.
    """
    closed_any = False

    for attempt in range(max_attempts):
        # Ищем диалог/модалку
        dialog = page.get_by_role("dialog")
        dialog_count = 0
        try:
            dialog_count = await dialog.count()
        except Exception:
            dialog_count = 0

        if dialog_count == 0:
            # Дополнительная эвристика: MUI-диалоги
            dialog = page.locator(".MuiDialog-root, .MuiDialog-paper")
            try:
                dialog_count = await dialog.count()
            except Exception:
                dialog_count = 0

        if dialog_count == 0:
            # Попапов больше нет
            break

        dlg = dialog.nth(0)

        # Логируем текст диалога
        try:
            dlg_text = await dlg.inner_text()
            print(f"[STEP3-POPUP] Найден диалог/попап, текст:\n{dlg_text}")
        except Exception:
            dlg_text = ""
            print("[STEP3-POPUP] Найден диалог/попап, но текст прочитать не удалось.")

        # Ищем кнопку внутри диалога
        btn = None
        for label in ERROR_POPUP_BUTTON_TEXTS:
            candidate = dlg.get_by_role("button", name=label)
            try:
                if await candidate.count() > 0:
                    btn = candidate.nth(0)
                    break
            except Exception:
                continue

        if not btn:
            # fallback: любой button с подходящим текстом
            all_buttons = dlg.get_by_role("button")
            try:
                btn_count = await all_buttons.count()
            except Exception:
                btn_count = 0

            for i in range(btn_count):
                b = all_buttons.nth(i)
                try:
                    txt = (await b.inner_text()).strip()
                except Exception:
                    continue
                if txt in ERROR_POPUP_BUTTON_TEXTS:
                    btn = b
                    break

        if not btn:
            print("[STEP3-POPUP] Не нашли кнопку 'Понятно/ОК/Закрыть' в диалоге.")
            break

        # Кликаем по кнопке
        try:
            print("[STEP3-POPUP] Жмём кнопку закрытия попапа…")
            await btn.scroll_into_view_if_needed()
            await btn.click()
            closed_any = True
            await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"[STEP3-POPUP] Ошибка при клике по кнопке попапа: {e}")
            break

    if closed_any:
        print("[STEP3-POPUP] Один или несколько попапов были закрыты автоматически.")
    else:
        print("[STEP3-POPUP] Попапы с ошибками не найдены (или не были закрыты).")

    return closed_any


# ============================================================
# MAIN STEP 3
# ============================================================

async def step3_fill_recipient_and_sender(page: Page, invoice) -> None:
    """
    Шаг 3: форма sender-details.
    Заполняем все данные получателя и отправителя,
    ставим галочку согласия, жмём 'Продолжить'.
    Капча обрабатывается:
      - либо автоматически (checkbox-капча + попап через _try_solve_checkbox_captcha / _click_post_captcha_continue_popup),
      - либо вручную/на STEP4, где ждём /confirm.
    """
    print(f"[STEP3] → Заполняю данные получателя и отправителя для invoice={invoice.invoice_id}")

    # Дамп формы ДО заполнения
    await _save_step3_html(page, label="before_fill")

    # ---------- ПОЛУЧАТЕЛЬ ----------
    try:
        await _fill_by_label_or_name(
            page,
            label_text="Номер карты получателя",
            name_attr="transfer_beneficiaryAccountNumber",
            value=invoice.recipient_card_number,
        )
        await _fill_by_label_or_name(
            page,
            label_text="Имя получателя",
            name_attr="beneficiary_firstName",
            value=invoice.recipient_first_name,
        )
        await _fill_by_label_or_name(
            page,
            label_text="Фамилия получателя",
            name_attr="beneficiary_lastName",
            value=invoice.recipient_last_name,
        )
    except Exception as e:
        await _save_step3_html(page, label="error_fill_recipient")
        raise RuntimeError(f"[STEP3] Ошибка при заполнении данных получателя: {e}")

    # ---------- ОТПРАВИТЕЛЬ ----------
    try:
        # ФИО
        await _fill_by_label_or_name(
            page,
            label_text="Имя",
            name_attr="sender_firstName",
            value=invoice.sender_first_name,
        )
        await _fill_by_label_or_name(
            page,
            label_text="Фамилия",
            name_attr="sender_lastName",
            value=invoice.sender_last_name,
        )
        middle_name = getattr(invoice, "sender_middle_name", None)
        if middle_name:
            await _fill_by_label_or_name(
                page,
                label_text="Отчество",
                name_attr="sender_middleName",
                value=middle_name,
            )

        # Паспорт: серия + номер + дата выдачи
        await _fill_by_label_or_name(
            page,
            label_text="Серия паспорта",
            name_attr=None,  # rely on label, name мог измениться
            value=getattr(invoice, "sender_passport_series", None),
        )
        await _fill_by_label_or_name(
            page,
            label_text="Номер паспорта",
            name_attr=None,
            value=getattr(invoice, "sender_passport_number", None),
        )

        passport_issue_raw = getattr(invoice, "sender_passport_issue_date", None)
        passport_issue_norm = _normalize_date_for_multitransfer(passport_issue_raw)
        await _fill_by_label_or_name(
            page,
            label_text="Дата выдачи паспорта",
            name_attr=None,
            value=passport_issue_norm,
        )

        # Дата рождения
        birth_raw = invoice.sender_birth_date
        birth_norm = _normalize_date_for_multitransfer(birth_raw)
        await _fill_by_label_or_name(
            page,
            label_text="Дата рождения",
            name_attr="birthDate",
            value=birth_norm,
        )

        # Страна/место рождения
        await _select_country_by_label(
            page,
            label_text="Страна рождения",
            country=getattr(invoice, "sender_birth_country", None),
        )
        await _fill_by_label_or_name(
            page,
            label_text="Место рождения",
            name_attr="birthPlaceAddress_full",
            value=invoice.sender_birth_place,
        )

        # Страна/место регистрации
        await _select_country_by_label(
            page,
            label_text="Страна регистрации",
            country=getattr(invoice, "sender_registration_country", None),
        )
        await _fill_by_label_or_name(
            page,
            label_text="Место регистрации",
            name_attr="registrationAddress_full",
            value=invoice.sender_registration_place,
        )

        # Телефон
        await _fill_by_label_or_name(
            page,
            label_text="Телефон",
            name_attr="phoneNumber",
            value=invoice.sender_phone,
        )

    except Exception as e:
        await _save_step3_html(page, label="error_fill_sender")
        raise RuntimeError(f"[STEP3] Ошибка при заполнении данных отправителя: {e}")

    print("[STEP3] ✅ Попытались заполнить все поля получателя и отправителя.")
    await _save_step3_html(page, label="after_fill")

    # ---------- ГАЛОЧКА СОГЛАСИЯ ----------
    print("[STEP3] Пытаюсь поставить галочку согласия...")

    checkbox_clicked = False

    try:
        # 1) По тексту "подтверждаю"
        try:
            consent_text = page.get_by_text("подтверждаю", exact=False).first
            await consent_text.wait_for(timeout=1500)
            container = consent_text.locator(
                "xpath=ancestor::*[self::label or self::div][1]"
            )
            if await container.count() == 0:
                container = consent_text
            await container.first.scroll_into_view_if_needed()
            await container.first.click()
            checkbox_clicked = True
            print("[STEP3] ✅ Галочка установлена через текст 'подтверждаю'")
        except PlaywrightTimeoutError:
            print("[STEP3] ⚠ Не нашли текст согласия с 'подтверждаю', пробую первый чекбокс…")

        # 2) fallback — первый чекбокс
        if not checkbox_clicked:
            checkbox = page.locator("input[type='checkbox']").first
            if await checkbox.count() > 0:
                await checkbox.scroll_into_view_if_needed()
                await checkbox.click()
                checkbox_clicked = True
                print("[STEP3] ✅ Галочка установлена через первый input[type='checkbox']")
    except Exception as e:
        print(f"[STEP3] ⚠ Не удалось корректно кликнуть по галочке согласия: {e}")
        await _save_step3_html(page, label="checkbox_error")

    if not checkbox_clicked:
        print("[STEP3] ⚠ Галочку согласия не нашли/не поставили. Возможно, она необязательна или DOM другой.")

    # ---------- КНОПКА "ПРОДОЛЖИТЬ" ----------
    print("[STEP3] Пытаюсь нажать кнопку 'Продолжить' для перехода к капче...")

    try:
        # Небольшая пауза, чтобы форма успела провалидироваться
        await page.wait_for_timeout(200)

        await _save_step3_html(page, label="before_continue")

        continue_btn = page.get_by_role("button", name="Продолжить").first

        try:
            await continue_btn.wait_for(timeout=15000)
        except PlaywrightTimeoutError:
            print("[STEP3] ⚠ Кнопка 'Продолжить' не появилась вовремя, пробуем кликнуть всё равно.")

        await continue_btn.scroll_into_view_if_needed()
        await continue_btn.click()
        print("[STEP3] Кнопка 'Продолжить' кликнута, проверяю попапы...")

        # Дадим фронту время показать возможный попап
        await page.wait_for_timeout(1500)

        # Пытаемся закрыть попапы с ошибками
        popup_closed = await close_error_popups(page)

        if popup_closed:
            print("[STEP3] После закрытия попапа повторно жмём 'Продолжить'...")
            try:
                await continue_btn.click()
            except Exception as e:
                print(f"[STEP3] ⚠ Ошибка при повторном клике по 'Продолжить': {e}")

        await _save_step3_html(page, label="after_continue")

        print("[STEP3] ✅ Нажата кнопка 'Продолжить' — дальше должен быть шаг с капчей/проверкой")

        # ---------- ПРОБА АВТО-КАПЧИ + ПОПАП ----------
        try:
            solved = await _try_solve_checkbox_captcha(page)
            if solved:
                print("[STEP3] ✅ CAPTCHA (checkbox) решена автоматически на STEP3.")

                popup_ok = await _click_post_captcha_continue_popup(page)
                if popup_ok:
                    print("[STEP3] ✅ Попап после капчи обработан (клик 'Продолжить').")
                else:
                    print("[STEP3] Попап после капчи не найден/не обработан — дальше STEP4 ждёт /confirm.")
            else:
                print("[STEP3] CAPTCHA (checkbox) не найдена или не решена автоматически — дальше STEP4 ждёт /confirm.")
        except Exception as e:
            print(f"[STEP3] ⚠ Ошибка при автоклике капчи/попапа: {e}")

    except PlaywrightTimeoutError:
        await _save_step3_html(page, label="continue_not_found")
        raise RuntimeError("[STEP3] Не удалось найти кнопку 'Продолжить' на форме отправителя/получателя")
    except Exception as e:
        await _save_step3_html(page, label="continue_click_error")
        raise RuntimeError(f"[STEP3] Ошибка при клике по кнопке 'Продолжить': {e}")