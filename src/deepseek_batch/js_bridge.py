from __future__ import annotations

import json
from typing import Any


AUTOMATION_JS = r"""
(function () {
  if (window.__deepseekBatchBot) {
    return;
  }

  const bot = {};

  function textOf(el) {
    if (!el) return "";
    return [
      el.innerText,
      el.textContent,
      el.value,
      el.getAttribute && el.getAttribute("aria-label"),
      el.getAttribute && el.getAttribute("title"),
      el.getAttribute && el.getAttribute("placeholder")
    ].filter(Boolean).join(" ").trim();
  }

  function norm(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 1 && rect.height > 1 &&
      style.visibility !== "hidden" &&
      style.display !== "none" &&
      Number(style.opacity || "1") > 0;
  }

  function enabled(el) {
    if (!el) return false;
    const className = String(el.className || "");
    return !el.disabled &&
      el.getAttribute("aria-disabled") !== "true" &&
      el.getAttribute("disabled") === null &&
      !/(^|\s|--)(disabled)(\s|$)/i.test(className);
  }

  function dispatchInput(el) {
    el.dispatchEvent(new Event("input", { bubbles: true, inputType: "insertText" }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function findComposer() {
    const selectors = [
      "textarea[placeholder='给 DeepSeek 发送消息 '][name='search']",
      "textarea[placeholder='给 DeepSeek 发送消息'][name='search']",
      "textarea[placeholder*='DeepSeek']",
      "textarea[placeholder*='发送消息']",
      "textarea",
      "div[contenteditable='true']",
      "[contenteditable='true']",
      "[role='textbox']",
      "div.ProseMirror",
      "input[type='text']"
    ];
    const seen = new Set();
    for (const selector of selectors) {
      for (const el of Array.from(document.querySelectorAll(selector))) {
        if (seen.has(el) || !visible(el) || !enabled(el)) continue;
        seen.add(el);
        return el;
      }
    }
    return null;
  }

  function setComposerValue(el, value) {
    el.focus();

    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value");
      if (setter && setter.set) {
        setter.set.call(el, value);
      } else {
        el.value = value;
      }
      dispatchInput(el);
      return true;
    }

    const selection = window.getSelection();
    try {
      if (selection) {
        const range = document.createRange();
        range.selectNodeContents(el);
        selection.removeAllRanges();
        selection.addRange(range);
      }
      document.execCommand("insertText", false, value);
    } catch (err) {
      // Fallback below handles editors that reject execCommand.
    }

    if (!norm(textOf(el)).includes(norm(value).slice(0, 80))) {
      el.textContent = value;
    }
    dispatchInput(el);
    return true;
  }

  function buttonLabel(el) {
    return norm([
      textOf(el),
      el.getAttribute && el.getAttribute("aria-label"),
      el.getAttribute && el.getAttribute("title"),
      el.getAttribute && el.getAttribute("data-testid")
    ].filter(Boolean).join(" "));
  }

  function controlLabel(el) {
    return norm(
      (el.getAttribute && (el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("data-testid"))) ||
      el.innerText ||
      el.textContent ||
      el.value ||
      ""
    );
  }

  function findButtonByTerms(terms) {
    const labels = terms.map((term) => norm(term).toLowerCase()).filter(Boolean);
    if (!labels.length) return null;
    const controls = Array.from(document.querySelectorAll(
      "button,[role='button'],[role='menuitem'],[role='option'],[role='radio'],[role='tab'],label"
    ));
    for (const el of controls) {
      if (!visible(el) || !enabled(el)) continue;
      const label = controlLabel(el).toLowerCase();
      if (labels.includes(label)) return el;
    }
    return null;
  }

  function isPressed(el) {
    if (!el) return null;
    const attrs = [
      "aria-pressed",
      "aria-checked",
      "aria-selected",
      "checked",
      "selected",
      "data-checked",
      "data-active",
      "data-selected"
    ];
    for (const attr of attrs) {
      const value = el.getAttribute && el.getAttribute(attr);
      if (value === "true" || value === "checked" || value === "on" || value === "selected") return true;
      if (value === "false" || value === "unchecked" || value === "off") return false;
    }
    const state = el.getAttribute && el.getAttribute("data-state");
    if (state === "checked" || state === "active" || state === "on" || state === "selected") return true;
    if (state === "unchecked" || state === "inactive" || state === "off" || state === "unselected") return false;
    const className = String(el.className || "");
    if (/(^|\s)(active|selected|checked|is-active|is-selected|on)(\s|$)/i.test(className)) return true;
    return null;
  }

  function ensureChatMode(options) {
    options = options || {};
    const mode = String(options.chatMode || "keep");
    if (!mode || mode === "keep") {
      return { ok: true, changed: false, mode, skipped: true };
    }

    const modelTypes = options.chatModeModelTypes || {};
    const modelType = modelTypes[mode];
    if (!modelType) return { ok: false, changed: false, mode, error: "未知对话模式：" + mode };

    const radio = Array.from(document.querySelectorAll("[role='radiogroup'] [role='radio'][data-model-type]"))
      .find((el) => el.getAttribute("data-model-type") === modelType);
    if (!radio || !visible(radio) || !enabled(radio)) {
      return { ok: false, changed: false, mode, error: "未找到对话模式单选项：" + modelType };
    }

    const checked = radio.getAttribute("aria-checked") === "true";
    if (checked) return { ok: true, changed: false, mode, modelType, active: true };

    radio.click();
    return {
      ok: true,
      changed: true,
      mode,
      modelType,
      active: false
    };
  }

  function ensureMode(terms, desired, required) {
    const button = findButtonByTerms(terms);
    if (!button) {
      return {
        ok: !required || !desired,
        changed: false,
        desired,
        warning: "未找到模式按钮：" + terms.join("/")
      };
    }
    const pressed = isPressed(button);
    if (pressed === desired) {
      return { ok: true, changed: false, label: buttonLabel(button), active: pressed, desired };
    }
    if (pressed === null && desired === false) {
      return {
        ok: true,
        changed: false,
        label: buttonLabel(button),
        active: null,
        desired,
        warning: "无法判断模式是否已关闭"
      };
    }
    button.click();
    return { ok: true, changed: true, label: buttonLabel(button), active: pressed, desired };
  }

  function findComposerShell(composer) {
    let cur = composer.parentElement;
    for (let i = 0; i < 4 && cur; i += 1) {
      if (cur.querySelector("[role='button'][aria-pressed]") && cur.querySelector(".ds-icon-button,[role='button']")) {
        return cur;
      }
      cur = cur.parentElement;
    }
    return composer.parentElement;
  }

  function findComposerActionButton(composer, requireEnabled) {
    const shell = findComposerShell(composer);
    if (!shell) return null;

    const controls = Array.from(shell.querySelectorAll("button,[role='button']"))
      .filter((el) => visible(el) && (!requireEnabled || enabled(el)))
      .filter((el) => el.getAttribute("aria-pressed") === null)
      .filter((el) => {
        const label = controlLabel(el).toLowerCase();
        const className = String(el.className || "");
        return label === "send" || label === "发送" || className.includes("ds-icon-button");
      });

    return controls.length ? controls[controls.length - 1] : null;
  }

  function findSendButton(composer) {
    const selectors = [
      "button[type='submit']",
      "button[aria-label*='发送']",
      "button[aria-label*='Send']",
      "[role='button'][aria-label*='发送']",
      "[role='button'][aria-label*='Send']"
    ];
    for (const selector of selectors) {
      const matches = Array.from(document.querySelectorAll(selector))
        .filter((el) => visible(el) && enabled(el));
      if (matches.length) return matches[matches.length - 1];
    }

    return findComposerActionButton(composer, true);
  }

  function clickNewChat(terms) {
    const button = findButtonByTerms(terms || []);
    if (!button) {
      return { ok: false, warning: "未找到新对话按钮" };
    }
    button.click();
    return { ok: true, label: buttonLabel(button) };
  }

  function cleanReply(value) {
    return String(value || "")
      .replace(/\n\s*(复制|Copy|重新生成|Regenerate)\s*$/gi, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function looksLikePrompt(text, prompt) {
    const a = norm(text).toLowerCase();
    const b = norm(prompt).toLowerCase().slice(0, 220);
    return b.length > 20 && a.includes(b) && a.length < b.length + 80;
  }

  function collectFromSelectors(selectors, prompt) {
    const seen = new Set();
    const candidates = [];
    let order = 0;
    for (const selector of selectors) {
      for (const el of Array.from(document.querySelectorAll(selector))) {
        if (seen.has(el) || !visible(el)) continue;
        seen.add(el);
        const text = cleanReply(textOf(el));
        if (text.length < 2 || looksLikePrompt(text, prompt)) continue;
        const rect = el.getBoundingClientRect();
        candidates.push({ text, top: rect.top, length: text.length, order });
        order += 1;
      }
    }
    candidates.sort((a, b) => a.order - b.order);
    return candidates;
  }

  function collectCandidates(prompt) {
    const deepseekMain = collectFromSelectors([
      ".ds-markdown.ds-assistant-message-main-content"
    ], prompt);
    if (deepseekMain.length) return deepseekMain;

    const primary = collectFromSelectors([
      "[data-message-author-role='assistant'] .ds-markdown",
      "[data-role='assistant'] .ds-markdown"
    ], prompt);
    if (primary.length) return primary;

    return collectFromSelectors([
      "[data-message-author-role='assistant']",
      "[data-role='assistant']",
      "[class*='assistant']",
      "[class*='bot']",
      ".ds-markdown",
      "[class*='markdown']",
      "main article",
      "article"
    ], prompt);
  }

  function isGenerating() {
    const terms = ["停止生成", "停止回答", "Stop generating", "Stop responding", "Generating"];
    const lowered = terms.map((term) => term.toLowerCase());
    for (const el of Array.from(document.querySelectorAll("button,[role='button']"))) {
      if (!visible(el)) continue;
      const label = buttonLabel(el).toLowerCase();
      if (lowered.some((term) => label.includes(term))) return true;
    }
    const composer = findComposer();
    if (composer && norm(textOf(composer)) === "") {
      const actionButton = findComposerActionButton(composer, false);
      if (actionButton && enabled(actionButton)) return true;
    }
    return false;
  }

  bot.newChat = function (options) {
    try {
      return clickNewChat((options && options.newChatTerms) || []);
    } catch (err) {
      return { ok: false, error: String(err && err.message ? err.message : err) };
    }
  };

  bot.selectChatMode = function (options) {
    try {
      return ensureChatMode(options || {});
    } catch (err) {
      return { ok: false, error: String(err && err.message ? err.message : err) };
    }
  };

  bot.prepareAndSend = function (prompt, options) {
    try {
      options = options || {};
      const modeResults = [];
      const chatModeResult = ensureChatMode(options);
      modeResults.push({ name: "chatMode", result: chatModeResult });
      if (!chatModeResult.ok) {
        return {
          ok: false,
          error: chatModeResult.error || chatModeResult.warning || "对话模式未切换",
          modeResults
        };
      }

      const searchResult = ensureMode(
        options.searchTerms || [],
        Boolean(options.enableSearch),
        Boolean(options.enableSearch && options.requireSearch)
      );
      modeResults.push({ name: "search", result: searchResult });
      if (!searchResult.ok) {
        return { ok: false, error: searchResult.warning || "网页搜索模式未启用", modeResults };
      }

      const deepThinkResult = ensureMode(
        options.deepThinkTerms || [],
        Boolean(options.enableDeepThink),
        Boolean(options.enableDeepThink && options.requireDeepThink)
      );
      modeResults.push({ name: "deepthink", result: deepThinkResult });
      if (!deepThinkResult.ok) {
        return { ok: false, error: deepThinkResult.warning || "深度思考模式未启用", modeResults };
      }

      const composer = findComposer();
      if (!composer) {
        return { ok: false, error: "未找到可输入提示词的文本框", modeResults };
      }
      setComposerValue(composer, prompt);

      const sendButton = findSendButton(composer);
      if (!sendButton) {
        return { ok: false, error: "未找到发送按钮", modeResults };
      }
      if (!enabled(sendButton)) {
        return { ok: false, error: "发送按钮当前不可用", modeResults };
      }
      sendButton.click();
      return { ok: true, modeResults };
    } catch (err) {
      return { ok: false, error: String(err && err.message ? err.message : err) };
    }
  };

  bot.collectReply = function (prompt) {
    try {
      const candidates = collectCandidates(prompt);
      const latest = candidates.length ? candidates[candidates.length - 1].text : "";
      return {
        ok: true,
        content: latest,
        length: latest.length,
        generating: isGenerating(),
        candidateCount: candidates.length
      };
    } catch (err) {
      return { ok: false, error: String(err && err.message ? err.message : err) };
    }
  };

  window.__deepseekBatchBot = bot;
}());
"""


def js_call(function_name: str, *args: Any) -> str:
    args_json = ", ".join(
        json.dumps(arg, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        for arg in args
    )
    return f"""
(() => {{
{AUTOMATION_JS}
  const result = window.__deepseekBatchBot.{function_name}({args_json});
  return JSON.stringify(result);
}})();
"""


def parse_js_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
            if isinstance(data, dict):
                return data
            return {"ok": False, "error": f"JS 返回值不是对象：{type(data).__name__}"}
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"无法解析 JS 返回值：{exc}"}
    return {"ok": False, "error": f"空或未知 JS 返回值：{value!r}"}
