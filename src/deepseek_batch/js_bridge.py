from __future__ import annotations

import json
from typing import Any


AUTOMATION_JS = r"""
(function () {
  const BOT_VERSION = "2026-05-14-busy-detect-3";
  if (window.__deepseekBatchBot && window.__deepseekBatchBot.version === BOT_VERSION) {
    return;
  }

  const bot = { version: BOT_VERSION };

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

  function isAlreadyNewChat() {
    const assistantSelectors = [
      ".ds-markdown.ds-assistant-message-main-content",
      "[data-message-author-role='assistant'] .ds-markdown",
      "[data-role='assistant'] .ds-markdown"
    ].join(",");
    return document.querySelectorAll(assistantSelectors).length === 0;
  }

  function findButtonByLooseLabel(terms) {
    const labels = terms.map((term) => norm(term).toLowerCase()).filter(Boolean);
    if (!labels.length) return null;
    const controls = Array.from(document.querySelectorAll(
      "button,[role='button'],[role='menuitem'],[role='option'],[role='radio'],[role='tab'],a"
    ));
    for (const el of controls) {
      if (!visible(el) || !enabled(el)) continue;
      const label = controlLabel(el).toLowerCase();
      if (label && labels.some((term) => label.includes(term))) return el;
    }
    return null;
  }

  function findButtonNearLabelText(terms) {
    const labels = terms.map((term) => norm(term).toLowerCase()).filter(Boolean);
    if (!labels.length) return null;
    const all = Array.from(document.querySelectorAll("body *"));
    for (const el of all) {
      const own = norm(el.innerText || el.textContent || "").toLowerCase();
      if (!own || !labels.some((term) => own === term)) continue;
      let cur = el;
      for (let depth = 0; depth < 4 && cur; depth += 1) {
        if (cur.tagName === "BUTTON" || (cur.getAttribute && cur.getAttribute("role") === "button")) {
          if (visible(cur) && enabled(cur)) return cur;
        }
        cur = cur.parentElement;
      }
      const sibling = el.parentElement && el.parentElement.querySelector("button,[role='button']");
      if (sibling && visible(sibling) && enabled(sibling)) return sibling;
    }
    return null;
  }

  function clickNewChat(terms) {
    const termList = terms || [];
    if (isAlreadyNewChat()) {
      return { ok: true, label: "(already-new-chat)", skipped: true };
    }
    let button = findButtonByTerms(termList);
    if (!button) button = findButtonByLooseLabel(termList);
    if (!button) button = findButtonNearLabelText(termList);
    if (!button) {
      return { ok: false, warning: "未找到新对话按钮" };
    }
    button.click();
    return { ok: true, label: buttonLabel(button) };
  }

  function cleanReply(value) {
    const text = String(value || "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
    const jsonPayload = extractJsonPayload(text);
    if (jsonPayload !== null) return jsonPayload;
    return stripCodeFence(text)
      .replace(/^(json|JSON)\s*\n(复制|Copy)\s*\n(下载|Download)\s*\n+/, "")
      .replace(/\n\s*(复制|Copy|下载|Download|重新生成|Regenerate)\s*$/gi, "")
      .trim();
  }

  function stripCodeFence(value) {
    const text = String(value || "").trim();
    const match = text.match(/^```(?:json|JSON)?\s*\n([\s\S]*?)\n```$/);
    return match ? match[1].trim() : text;
  }

  function parsesJson(value) {
    try {
      JSON.parse(value);
      return true;
    } catch (err) {
      return false;
    }
  }

  function findFirstBalanced(text, start) {
    const stack = [];
    let inString = false;
    let escaped = false;
    for (let pos = start; pos < text.length; pos += 1) {
      const ch = text[pos];
      if (inString) {
        if (escaped) {
          escaped = false;
        } else if (ch === "\\") {
          escaped = true;
        } else if (ch === "\"") {
          inString = false;
        }
        continue;
      }
      if (ch === "\"") { inString = true; continue; }
      if (ch === "{") { stack.push("}"); continue; }
      if (ch === "[") { stack.push("]"); continue; }
      if (ch === "}" || ch === "]") {
        if (!stack.length || stack[stack.length - 1] !== ch) return -1;
        stack.pop();
        if (!stack.length) return pos + 1;
      }
    }
    return -1;
  }

  function extractJsonPayload(value) {
    const text = stripCodeFence(value);
    if (!text) return null;
    if (parsesJson(text)) return text;

    let best = null;
    for (let start = 0; start < text.length; start += 1) {
      const first = text[start];
      if (first !== "{" && first !== "[") continue;
      const end = findFirstBalanced(text, start);
      if (end <= start) continue;
      const candidate = text.slice(start, end).trim();
      if (parsesJson(candidate)) {
        if (!best || candidate.length > best.length) best = candidate;
      }
    }
    return best;
  }

  function elementText(el) {
    return String((el && (el.innerText || el.textContent)) || "").trim();
  }

  function latestCodeBlockText(el) {
    if (!el || !el.querySelectorAll) return "";
    const blocks = Array.from(el.querySelectorAll("pre code, pre"))
      .filter((node) => visible(node))
      .map((node) => elementText(node))
      .filter(Boolean);
    if (!blocks.length) return "";
    for (let i = blocks.length - 1; i >= 0; i -= 1) {
      const stripped = blocks[i].trim();
      if (stripped.startsWith("{") || stripped.startsWith("[")) return blocks[i];
    }
    const isJustLanguageLabel = (text) => /^(json|JSON|javascript|js|ts|typescript|html|css|python|bash|sh|yaml|yml|xml|sql|md|markdown|java|go|rust|c|c\+\+|cpp)$/i.test(text.trim());
    const meaningful = blocks.filter((text) => !isJustLanguageLabel(text));
    if (meaningful.length) return meaningful[meaningful.length - 1];
    return blocks[blocks.length - 1];
  }

  function readableReplyText(el) {
    const codeText = latestCodeBlockText(el);
    if (codeText) return codeText;

    const clone = el.cloneNode(true);
    for (const node of Array.from(clone.querySelectorAll("button,[role='button'],.ds-icon-button"))) {
      node.remove();
    }
    return elementText(clone);
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
        const text = cleanReply(readableReplyText(el));
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
    return collectFromSelectors([
      ".ds-markdown.ds-assistant-message-main-content",
      "[data-message-author-role='assistant'] .ds-markdown",
      "[data-role='assistant'] .ds-markdown"
    ], prompt);
  }

  const SERVER_BUSY_PHRASES = ["服务器繁忙", "Server busy"];
  const PRIMARY_BUBBLE_SELECTORS = [".ds-message"];
  const FALLBACK_BUBBLE_SELECTORS = [
    ".ds-assistant-message-main-content",
    ".ds-message-content",
    "[data-message-author-role]",
    "[data-role='assistant']",
    "[data-role='user']"
  ];
  const CONVERSATION_AREA_SELECTORS = [
    ".ds-virtual-list",
    ".ds-message",
    ".ds-markdown",
    ".ds-assistant-message-main-content",
    ".ds-message-content",
    "[data-message-author-role]",
    "[data-role='assistant']",
    "[data-role='user']"
  ];

  function textHasBusyPhrase(text, mode) {
    if (!text) return false;
    for (const phrase of SERVER_BUSY_PHRASES) {
      if (mode === "startsWith") {
        if (text.startsWith(phrase)) return true;
      } else {
        if (text.includes(phrase)) return true;
      }
    }
    return false;
  }

  function elementMatchesAncestor(el, selectors) {
    if (!el || typeof el.closest !== "function") return false;
    for (const selector of selectors) {
      if (el.closest(selector)) return true;
    }
    return false;
  }

  function listVisibleBubbles() {
    const seen = new Set();
    const list = [];
    const tryCollect = (selectors) => {
      for (const selector of selectors) {
        for (const el of Array.from(document.querySelectorAll(selector))) {
          if (seen.has(el) || !visible(el)) continue;
          seen.add(el);
          list.push(el);
        }
      }
    };
    tryCollect(PRIMARY_BUBBLE_SELECTORS);
    if (list.length === 0) tryCollect(FALLBACK_BUBBLE_SELECTORS);
    list.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
    return list;
  }

  function countMessageBubbles() {
    return listVisibleBubbles().length;
  }

  function hasFreshBusyMessage(baselineBubbleCount, currentBubbleCount, prompt) {
    if (typeof baselineBubbleCount !== "number" || typeof currentBubbleCount !== "number") return false;
    const delta = currentBubbleCount - baselineBubbleCount;
    if (delta <= 0) return false;
    const bubbles = listVisibleBubbles();
    if (bubbles.length === 0) return false;
    const fresh = bubbles.slice(-delta);
    for (const el of fresh) {
      const text = norm(el.innerText || el.textContent || "");
      if (!text || text.length > 200) continue;
      if (prompt && looksLikePrompt(text, prompt)) continue;
      if (textHasBusyPhrase(text, "startsWith")) return true;
    }
    return false;
  }

  function hasBusyToast() {
    const all = document.querySelectorAll("body *");
    for (const el of all) {
      if (!visible(el) || elementMatchesAncestor(el, CONVERSATION_AREA_SELECTORS)) continue;
      const own = norm(el.innerText || el.textContent || "");
      if (!own || own.length > 40) continue;
      if (textHasBusyPhrase(own, "startsWith")) return true;
    }
    return false;
  }

  function isServerBusy(baselineBubbleCount, currentBubbleCount, prompt) {
    if (hasFreshBusyMessage(baselineBubbleCount, currentBubbleCount, prompt)) return true;
    return hasBusyToast();
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

  function textSummary(value, limit) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    const max = limit || 360;
    if (text.length <= max) {
      return { length: text.length, sample: text };
    }
    const edge = Math.max(80, Math.floor(max / 2));
    return {
      length: text.length,
      sample: text.slice(0, edge) + " ... " + text.slice(-edge)
    };
  }

  function rectInfo(el) {
    if (!el || !el.getBoundingClientRect) return null;
    const rect = el.getBoundingClientRect();
    return {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      top: Math.round(rect.top),
      left: Math.round(rect.left)
    };
  }

  function attrsOf(el) {
    const names = [
      "id",
      "class",
      "role",
      "aria-label",
      "aria-pressed",
      "aria-checked",
      "aria-selected",
      "aria-expanded",
      "aria-disabled",
      "title",
      "name",
      "type",
      "placeholder",
      "data-testid",
      "data-role",
      "data-message-author-role",
      "data-model-type",
      "data-state"
    ];
    const attrs = {};
    for (const name of names) {
      const value = el.getAttribute && el.getAttribute(name);
      if (value !== null && value !== undefined && value !== "") attrs[name] = value;
    }
    if (el.attributes) {
      for (const attr of Array.from(el.attributes)) {
        if (attr.name.startsWith("data-") && attrs[attr.name] === undefined) {
          attrs[attr.name] = attr.value;
        }
      }
    }
    return attrs;
  }

  function selectorPart(el) {
    const tag = String(el.tagName || "node").toLowerCase();
    const classes = String(el.className || "")
      .split(/\s+/)
      .filter((item) => /^[A-Za-z0-9_-]+$/.test(item))
      .slice(0, 3)
      .map((item) => "." + item)
      .join("");

    let nth = 1;
    let cur = el;
    while ((cur = cur.previousElementSibling)) {
      if (cur.tagName === el.tagName) nth += 1;
    }
    return tag + classes + ":nth-of-type(" + nth + ")";
  }

  function cssPath(el) {
    if (!el || !el.tagName) return "";
    const parts = [];
    let cur = el;
    for (let depth = 0; cur && cur.nodeType === 1 && depth < 8; depth += 1) {
      parts.unshift(selectorPart(cur));
      if (cur === document.body || cur === document.documentElement) break;
      cur = cur.parentElement;
    }
    return parts.join(" > ");
  }

  function ancestorChain(el, limit) {
    const chain = [];
    let cur = el ? el.parentElement : null;
    while (cur && chain.length < (limit || 6)) {
      chain.push({
        tag: String(cur.tagName || "").toLowerCase(),
        selector: cssPath(cur),
        attrs: attrsOf(cur),
        rect: rectInfo(cur),
        text: textSummary(elementText(cur), 220)
      });
      cur = cur.parentElement;
    }
    return chain;
  }

  function elementProbe(el, textLimit) {
    const label = controlLabel(el);
    return {
      tag: String((el && el.tagName) || "").toLowerCase(),
      selector: cssPath(el),
      attrs: attrsOf(el),
      visible: visible(el),
      enabled: enabled(el),
      rect: rectInfo(el),
      label: label.length > 300 ? label.slice(0, 300) + "..." : label,
      text: textSummary(elementText(el), textLimit || 360),
      ancestors: ancestorChain(el, 5)
    };
  }

  function shallowTree(el, depth, maxDepth) {
    if (!el || depth > maxDepth) return null;
    const children = Array.from(el.children || []).slice(0, 12);
    return {
      tag: String(el.tagName || "").toLowerCase(),
      attrs: attrsOf(el),
      rect: rectInfo(el),
      text: textSummary(elementText(el), 180),
      children: children.map((child) => shallowTree(child, depth + 1, maxDepth)).filter(Boolean)
    };
  }

  function nearestShell(el) {
    let cur = el;
    for (let i = 0; i < 7 && cur; i += 1) {
      const hasMarkdown = cur.querySelector && cur.querySelector(".ds-markdown,[data-message-author-role='assistant'],[data-role='assistant']");
      const hasControls = cur.querySelector && cur.querySelector("button,[role='button']");
      if (hasMarkdown && hasControls) return cur;
      cur = cur.parentElement;
    }
    return el ? el.parentElement : null;
  }

  function queryCount(selector) {
    try {
      return document.querySelectorAll(selector).length;
    } catch (err) {
      return -1;
    }
  }

  function collectElements(selector, limit, textLimit) {
    return Array.from(document.querySelectorAll(selector))
      .slice(0, limit || 40)
      .map((el) => elementProbe(el, textLimit));
  }

  function probeAssistantMarkdown(el) {
    const shell = nearestShell(el);
    return {
      node: elementProbe(el, 520),
      shell: shell ? elementProbe(shell, 520) : null,
      shellTree: shell ? shallowTree(shell, 0, 2) : null,
      codeBlocks: Array.from(el.querySelectorAll("pre code, pre, code"))
        .slice(0, 20)
        .map((node) => elementProbe(node, 520)),
      shellControls: shell
        ? Array.from(shell.querySelectorAll("button,[role='button']"))
            .slice(0, 40)
            .map((node) => elementProbe(node, 220))
        : []
    };
  }

  bot.probeLayout = function () {
    try {
      const selectorCounts = [
        ".ds-markdown.ds-assistant-message-main-content",
        "[data-message-author-role='assistant']",
        "[data-message-author-role='assistant'] .ds-markdown",
        "[data-role='assistant']",
        "[data-role='assistant'] .ds-markdown",
        "[role='radiogroup'] [role='radio'][data-model-type]",
        "textarea",
        "[contenteditable='true']",
        "[role='textbox']",
        "button",
        "[role='button']",
        "pre",
        "pre code",
        "code",
        "details",
        "[class*='think']",
        "[class*='reason']",
        "[class*='search']"
      ].map((selector) => ({ selector, count: queryCount(selector) }));

      const assistantNodes = Array.from(document.querySelectorAll(
        ".ds-markdown.ds-assistant-message-main-content," +
        "[data-message-author-role='assistant'] .ds-markdown," +
        "[data-role='assistant'] .ds-markdown"
      )).slice(-5);

      return {
        ok: true,
        botVersion: BOT_VERSION,
        generatedAt: new Date().toISOString(),
        url: window.location.href,
        title: document.title,
        viewport: {
          width: window.innerWidth,
          height: window.innerHeight,
          devicePixelRatio: window.devicePixelRatio
        },
        body: document.body ? elementProbe(document.body, 220) : null,
        selectorCounts,
        chatModeRadios: collectElements("[role='radiogroup'] [role='radio'][data-model-type]", 20, 180),
        composerCandidates: collectElements(
          "textarea,[contenteditable='true'],[role='textbox'],div.ProseMirror,input[type='text']",
          30,
          220
        ),
        controls: collectElements(
          "button,[role='button'],[role='menuitem'],[role='option'],[role='radio'],[role='tab'],label",
          140,
          220
        ),
        assistantMarkdowns: assistantNodes.map((el) => probeAssistantMarkdown(el)),
        codeBlocks: collectElements("pre code, pre, code", 80, 520),
        detailsBlocks: collectElements("details,summary", 40, 360)
      };
    } catch (err) {
      return { ok: false, error: String(err && err.message ? err.message : err) };
    }
  };

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
      const baselineBubbleCount = countMessageBubbles();
      sendButton.click();
      return { ok: true, modeResults, baselineBubbleCount };
    } catch (err) {
      return { ok: false, error: String(err && err.message ? err.message : err) };
    }
  };

  bot.collectReply = function (prompt, baselineBubbleCount) {
    try {
      const candidates = collectCandidates(prompt);
      const latest = candidates.length ? candidates[candidates.length - 1].text : "";
      const candidateCount = candidates.length;
      const bubbleCount = countMessageBubbles();
      const baseline = typeof baselineBubbleCount === "number" ? baselineBubbleCount : null;
      return {
        ok: true,
        content: latest,
        length: latest.length,
        generating: isGenerating(),
        serverBusy: isServerBusy(baseline, bubbleCount, prompt),
        candidateCount,
        bubbleCount,
        baselineBubbleCount: baseline
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
