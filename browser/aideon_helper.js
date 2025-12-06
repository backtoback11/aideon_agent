// aideon_agent/browser/aideon_helper.js
// Универсальный JS-хелпер, который живет внутри страницы.
// Даёт API: window.AideonHelper.scan/perform/getState

(function () {
  if (window.AideonHelper) return;

  function getElementLabel(el) {
    if (!el) return null;

    // <label for="id">
    if (el.id) {
      const label = document.querySelector(`label[for="${el.id}"]`);
      if (label) return label.innerText.trim();
    }

    // <label><input ...>Текст</label>
    const labelWrap = el.closest("label");
    if (labelWrap) return labelWrap.innerText.trim();

    const aria = el.getAttribute("aria-label");
    if (aria) return aria.trim();

    const placeholder = el.getAttribute("placeholder");
    if (placeholder) return placeholder.trim();

    const title = el.getAttribute("title");
    if (title) return title.trim();

    const tag = el.tagName.toLowerCase();
    if (tag === "button" || tag === "a") {
      return el.innerText.trim();
    }

    return null;
  }

  function getCssSelector(el) {
    if (!(el instanceof Element)) return null;
    const path = [];
    let cur = el;
    let depth = 0;

    while (cur && cur.nodeType === 1 && depth < 6) {
      let selector = cur.nodeName.toLowerCase();
      if (cur.id) {
        selector += `#${cur.id}`;
        path.unshift(selector);
        break;
      } else {
        let sib = cur;
        let nth = 1;
        while ((sib = sib.previousElementSibling)) {
          if (sib.nodeName === cur.nodeName) nth++;
        }
        selector += `:nth-of-type(${nth})`;
      }
      path.unshift(selector);
      cur = cur.parentElement;
      depth++;
    }
    return path.join(" > ");
  }

  function isVisible(el) {
    if (!(el instanceof Element)) return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const vw = window.innerWidth || document.documentElement.clientWidth;
    return (
      rect.bottom >= 0 &&
      rect.right >= 0 &&
      rect.top <= vh &&
      rect.left <= vw
    );
  }

  function serializeElement(el, roleOverride) {
    const rect = el.getBoundingClientRect();
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute("type") || null;

    const role =
      roleOverride ||
      el.getAttribute("data-aideon-role") ||
      el.getAttribute("role") ||
      (tag === "button"
        ? "button"
        : tag === "a"
        ? "link"
        : tag === "input"
        ? "input"
        : tag === "select"
        ? "select"
        : "unknown");

    const label = getElementLabel(el);

    return {
      id: el.id || null,
      tag,
      type,
      role,
      name: label,
      text: el.innerText ? el.innerText.trim() : null,
      value:
        tag === "input" || tag === "textarea" || tag === "select"
          ? el.value
          : null,
      cssSelector: getCssSelector(el),
      ariaLabel: el.getAttribute("aria-label") || null,
      placeholder: el.getAttribute("placeholder") || null,
      href: el.getAttribute("href") || null,
      visible: isVisible(el),
      bbox: {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
      },
      dataset: { ...el.dataset },
    };
  }

  function scan() {
    const elements = [];

    const buttons = Array.from(
      document.querySelectorAll(
        "button, [role='button'], input[type='submit'], input[type='button']"
      )
    );
    buttons.forEach((btn) => {
      elements.push(serializeElement(btn, "button"));
    });

    const inputs = Array.from(
      document.querySelectorAll("input, textarea, select")
    );
    inputs.forEach((inp) => {
      elements.push(serializeElement(inp));
    });

    const links = Array.from(document.querySelectorAll("a[href]"));
    links.forEach((a) => {
      elements.push(serializeElement(a, "link"));
    });

    return {
      url: window.location.href,
      title: document.title,
      elements,
    };
  }

  function findElementBySelectorOrId(target) {
    if (!target) return null;

    if (target.cssSelector) {
      const byCss = document.querySelector(target.cssSelector);
      if (byCss) return byCss;
    }

    if (target.id) {
      const byId = document.getElementById(target.id);
      if (byId) return byId;
    }

    if (target.text) {
      const lower = target.text.toLowerCase();
      const candidates = Array.from(
        document.querySelectorAll(
          "button, [role='button'], a, input[type='submit'], input[type='button']"
        )
      );
      const found = candidates.find(
        (el) =>
          el.innerText &&
          el.innerText.trim().toLowerCase().includes(lower)
      );
      if (found) return found;
    }

    if (target.name) {
      const byName = document.querySelector(
        `input[name='${target.name}'], textarea[name='${target.name}'], select[name='${target.name}']`
      );
      if (byName) return byName;
    }

    return null;
  }

  async function perform(action) {
    const result = { ok: false, error: null };

    try {
      if (!action || !action.type) {
        result.error = "No action.type specified";
        return result;
      }

      if (action.type === "wait") {
        const ms = Number(action.ms || 1000);
        await new Promise((res) => setTimeout(res, ms));
        result.ok = true;
        return result;
      }

      const el = findElementBySelectorOrId(action.target || {});
      if (!el) {
        result.error = "Element not found";
        return result;
      }

      if (action.type === "click") {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.click();
        result.ok = true;
        return result;
      }

      if (action.type === "fill") {
        const text = action.value != null ? String(action.value) : "";
        el.focus();
        el.value = "";
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.value = text;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        result.ok = true;
        return result;
      }

      if (action.type === "select") {
        const optVal = action.value != null ? String(action.value) : "";
        const options = Array.from(el.options || []);
        const match = options.find(
          (o) =>
            o.value === optVal ||
            o.text.trim().toLowerCase() === optVal.toLowerCase()
        );
        if (match) {
          el.value = match.value;
          el.dispatchEvent(new Event("change", { bubbles: true }));
          result.ok = true;
        } else {
          result.error = "Option not found";
        }
        return result;
      }

      result.error = "Unknown action type: " + action.type;
      return result;
    } catch (e) {
      result.error = String(e);
      return result;
    }
  }

  function getState() {
    // Общий state по умолчанию. Под конкретный сайт можно доопределять.
    const balanceEl =
      document.querySelector("[data-balance], .balance, .BalanceAmount") ||
      null;

    return {
      url: window.location.href,
      title: document.title,
      timestamp: Date.now(),
      hintBalance: balanceEl ? balanceEl.innerText.trim() : null,
    };
  }

  window.AideonHelper = {
    scan,
    perform,
    getState,
  };
})();