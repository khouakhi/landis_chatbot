/**
 * LandIS Assistant — web chat client for POST /api/chat.
 *
 * Adapted from the SOILL chat client. Same-origin API, citations, and read-aloud.
 * **Created:** 29-10-2026 (UK style).
 */

const SESSION_KEY = "landis_chat_session_id";
const params = new URLSearchParams(window.location.search);
const configuredApi = typeof window.LANDIS_CHAT_API === "string" ? window.LANDIS_CHAT_API : "";
const apiBase = (params.get("api") || configuredApi || window.location.origin).replace(/\/$/, "");

const STARTER_PROMPTS = [
  {
    label: "Soil classification",
    text: "How does the soil classification for England and Wales describe major soil groups?",
  },
  {
    label: "Soilscapes",
    text: "What is Soilscapes and what scale of soil information does it show?",
  },
  {
    label: "Using LandIS data",
    text: "What does the LandIS soil information and data policy say about using the data?",
  },
  {
    label: "HOST classes",
    text: "Summarise what HOST soil hydrology classes are used for, in a short table if the sources support it.",
  },
];

const messagesEl = document.getElementById("messages");
const welcomeEl = document.getElementById("welcome");
const startersEl = document.getElementById("starter-prompts");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("message-input");
const statusEl = document.getElementById("status");
const newChatBtn = document.getElementById("new-chat-btn");
const submitBtn = formEl.querySelector('button[type="submit"]');
const usageEl = document.getElementById("usage");
const langButtons = document.querySelectorAll(".lang-switch__btn");
const audienceButtons = document.querySelectorAll("[data-audience]");
const formatButtons = document.querySelectorAll("[data-format]");

const SUPPORTED_LANGS = ["en", "cy"];
const LANG_STORAGE_KEY = "landis_lang";

const state = {
  audience: "general",
  formats: new Set(),
  usage: null,
  busy: false,
  lang: "en",
};

// Strings: web/i18n/en.json (reference) and web/i18n/cy.json (draft Welsh).
let messagesDict = {};
let activeDict = {};

function t(key, vars) {
  let text = activeDict[key];
  if (text === undefined) {
    text = messagesDict[key];
  }
  if (text === undefined) {
    text = key;
  }
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      text = text.replaceAll(`{${name}}`, String(value));
    }
  }
  return text;
}

async function loadMessages(lang) {
  try {
    const response = await fetch(`i18n/${lang}.json`, { cache: "no-cache" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return await response.json();
  } catch (err) {
    console.warn(`LandIS: could not load ${lang} strings`, err);
    return {};
  }
}

function applyStaticText() {
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-title]").forEach((node) => {
    node.title = t(node.dataset.i18nTitle);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((node) => {
    node.setAttribute("aria-label", t(node.dataset.i18nAria));
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    node.placeholder = t(node.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-tip]").forEach((node) => {
    node.dataset.tip = t(node.dataset.i18nTip);
  });
}

function starterPrompts() {
  if (Array.isArray(activeDict.starters)) {
    return activeDict.starters;
  }
  if (Array.isArray(messagesDict.starters)) {
    return messagesDict.starters;
  }
  return STARTER_PROMPTS;
}

function updateLangButtons() {
  langButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.lang === state.lang));
  });
}

function atDailyLimit() {
  return Boolean(state.usage && state.usage.remaining <= 0);
}

function refreshFormState() {
  const limited = atDailyLimit();
  inputEl.disabled = limited;
  submitBtn.disabled = state.busy || limited;
}

function renderUsage() {
  const usage = state.usage;
  if (!usage) {
    usageEl.textContent = "";
    usageEl.classList.remove("is-limit");
    inputEl.placeholder = t("input.placeholder");
    refreshFormState();
    return;
  }
  if (atDailyLimit()) {
    // The full friendly message appears in the chat as a notice, so keep this line short.
    usageEl.textContent = t("usage.today", { used: usage.used, limit: usage.limit });
    usageEl.classList.add("is-limit");
    inputEl.placeholder = t("input.limitPlaceholder");
  } else {
    usageEl.textContent = t("usage.today", { used: usage.used, limit: usage.limit });
    usageEl.classList.remove("is-limit");
    inputEl.placeholder = t("input.placeholder");
  }
  refreshFormState();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function renderStarterPrompts() {
  startersEl.innerHTML = "";
  for (const prompt of starterPrompts()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "starter-prompt";
    button.innerHTML = `
      <span class="starter-prompt__label">${escapeHtml(prompt.label)}</span>
      <span class="starter-prompt__text">${escapeHtml(prompt.text)}</span>
    `;
    button.addEventListener("click", () => {
      inputEl.value = prompt.text;
      formEl.requestSubmit();
    });
    startersEl.appendChild(button);
  }
}

async function setLanguage(lang) {
  const code = SUPPORTED_LANGS.includes(lang) ? lang : "en";
  if (Object.keys(messagesDict).length === 0) {
    messagesDict = await loadMessages("en");
  }
  activeDict = code === "en" ? messagesDict : await loadMessages(code);
  if (typeof activeDict._status === "string" && activeDict._status.startsWith("DRAFT")) {
    console.warn(`LandIS: ${code} strings are a draft. Check them with a Welsh speaker before use.`);
  }
  state.lang = code;
  document.documentElement.lang = code === "cy" ? "cy" : "en-GB";
  try {
    window.localStorage.setItem(LANG_STORAGE_KEY, code);
  } catch (_err) {
    // Storage may be blocked inside an iframe. The choice still applies for this page.
  }
  applyStaticText();
  renderStarterPrompts();
  renderUsage();
  updateLangButtons();
}

function chooseInitialLanguage() {
  const fromUrl = params.get("lang");
  if (SUPPORTED_LANGS.includes(fromUrl)) {
    return fromUrl;
  }
  try {
    const stored = window.localStorage.getItem(LANG_STORAGE_KEY);
    if (SUPPORTED_LANGS.includes(stored)) {
      return stored;
    }
  } catch (_err) {
    // Ignore: fall back to English.
  }
  return "en";
}

function getSessionId() {
  return sessionStorage.getItem(SESSION_KEY);
}

function setSessionId(sessionId) {
  if (sessionId) {
    sessionStorage.setItem(SESSION_KEY, sessionId);
  }
}

function clearSession() {
  sessionStorage.removeItem(SESSION_KEY);
}

function escapeHtml(text) {
  const el = document.createElement("span");
  el.textContent = text || "";
  return el.innerHTML;
}

let activeReadButton = null;

function speechSupported() {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

function speechLocale() {
  return state.lang === "cy" ? "cy-GB" : "en-GB";
}

function pickVoice() {
  const voices = window.speechSynthesis.getVoices();
  const locale = speechLocale();
  const prefix = locale.split("-")[0];
  return (
    voices.find((voice) => voice.lang === locale) ||
    voices.find((voice) => voice.lang.startsWith(locale)) ||
    voices.find((voice) => voice.lang.startsWith(`${prefix}-`)) ||
    null
  );
}

if (speechSupported()) {
  window.speechSynthesis.addEventListener("voiceschanged", pickVoice);
}

function plainTextForSpeech(text) {
  let value = (text || "").trim();
  value = value.replace(/\[[^\]]*\]/g, "");
  value = value.replace(/^#{1,6}\s+/gm, "");
  value = value.replace(/\*\*([^*]+)\*\*/g, "$1");
  value = value.replace(/\s+/g, " ");
  return value.trim();
}

function stopReadAloud() {
  if (!speechSupported()) {
    return;
  }
  window.speechSynthesis.cancel();
  if (activeReadButton) {
    activeReadButton.textContent = t("answer.readAloud");
    activeReadButton.setAttribute("aria-pressed", "false");
    activeReadButton = null;
  }
}

function toggleReadAloud(text, button) {
  if (!speechSupported()) {
    button.textContent = t("answer.notSupported");
    window.setTimeout(() => {
      button.textContent = t("answer.readAloud");
    }, 2000);
    return;
  }

  if (activeReadButton === button && button.getAttribute("aria-pressed") === "true") {
    stopReadAloud();
    return;
  }

  stopReadAloud();

  const plain = plainTextForSpeech(text);
  if (!plain) {
    return;
  }

  const utterance = new SpeechSynthesisUtterance(plain);
  utterance.lang = speechLocale();
  const voice = pickVoice();
  if (voice) {
    utterance.voice = voice;
  }

  utterance.onstart = () => {
    activeReadButton = button;
    button.textContent = t("answer.stop");
    button.setAttribute("aria-pressed", "true");
  };

  const resetButton = () => {
    if (activeReadButton === button) {
      activeReadButton = null;
    }
    button.textContent = t("answer.readAloud");
    button.setAttribute("aria-pressed", "false");
  };

  utterance.onend = resetButton;
  utterance.onerror = resetButton;

  window.speechSynthesis.speak(utterance);
}

function formatLocation(source) {
  // Structured database records have no page: show their section only.
  if (!source.location_start) {
    return source.section || "";
  }
  const type = source.location_type || "page";
  const unit = type === "page" ? t("sources.pages") : `${type}s`;
  const span = `${unit} ${source.location_start}–${source.location_end}`;
  return source.section ? `${source.section}, ${span}` : span;
}

function extractCitationLabels(inner) {
  const labels = new Set();
  for (const part of inner.split(/[,;]/)) {
    const match = part.trim().match(/^(\d+)$/);
    if (match) {
      labels.add(Number(match[1]));
    }
  }
  return [...labels].sort((a, b) => a - b);
}

function renderCitationBadges(inner, sources) {
  // Only show a badge for an excerpt that exists. Codes such as 541m are not citations.
  const labels = extractCitationLabels(inner).filter((label) =>
    sources.some((item) => item.label === label),
  );
  if (labels.length === 0) {
    return null;
  }

  return labels
    .map((label) => {
      const source = sources.find((item) => item.label === label);
      const title = source ? sourceDisplayName(source) : t("sources.fallbackLabel", { label });
      return `<button type="button" class="citation-badge" data-source-label="${label}" title="${escapeHtml(title)}">${label}</button>`;
    })
    .join("");
}

function stripMarkdownHeading(line) {
  return (line || "").replace(/^#{1,6}\s+/, "").trim();
}

function stripBulletPrefix(line) {
  return (line || "").replace(/^[-*]\s+/, "").trim();
}

function formatListItem(line, sources) {
  let content = stripMarkdownHeading(stripBulletPrefix(line));

  const numbered = content.match(/^(\d+[.)]\s*)(.+)$/);
  if (numbered) {
    content = numbered[2].trim();
  }

  // Inline "label: detail" inside a bullet — keep as one list item
  // Bullet labels are plain text, so bullets with and without labels look the same.
  const colonSplit = content.match(/^\*\*([^*]+)\*\*:\s+(.+)$/);
  if (colonSplit) {
    return `${formatInline(colonSplit[1].trim(), sources, true)}: ${formatInline(colonSplit[2].trim(), sources, true)}`;
  }

  return formatInline(content, sources, true);
}

function canonicalHttpUrl(value) {
  const trimmed = String(value || "")
    .trim()
    .replace(/[.,;:!?]+$/, "");
  try {
    const url = new URL(trimmed);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return "";
    }
    return url.href;
  } catch (_err) {
    return "";
  }
}

function answerLinkHtml(href, label) {
  const safe = canonicalHttpUrl(href);
  if (!safe) {
    return "";
  }
  const text = String(label || "").trim();
  const shown = canonicalHttpUrl(text) === safe ? safe : text || safe;
  return `<a class="answer-link" href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer">${escapeHtml(shown)}</a>`;
}

function linkifyAnswerText(text) {
  const tokens = [];
  const keep = (html) => {
    const token = `%%LINK${tokens.length}%%`;
    tokens.push(html);
    return token;
  };

  let raw = text || "";
  raw = raw.replace(
    /\[(https?:\/\/[^\]\s]+)\]\s*\(\s*(https?:\/\/[^)\s]+)\s*\)/gi,
    (match, left, right) =>
      canonicalHttpUrl(left) && canonicalHttpUrl(left) === canonicalHttpUrl(right) ? left : match
  );
  raw = raw.replace(
    /(https?:\/\/[^\s<>"'()[\]]+)\s+\(\s*(https?:\/\/[^)\s]+)\s*\)/gi,
    (match, left, right) =>
      canonicalHttpUrl(left) && canonicalHttpUrl(left) === canonicalHttpUrl(right) ? left : match
  );
  raw = raw.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/gi, (match, label, href) => {
    const html = answerLinkHtml(href, label);
    return html ? keep(html) : match;
  });
  raw = raw.replace(/\[(https?:\/\/[^\]\s]+)\]/gi, (match, href) => {
    const html = answerLinkHtml(href, href);
    return html ? keep(html) : match;
  });
  raw = raw.replace(/https?:\/\/[^\s<>"'()[\]]+/gi, (href) => {
    const trimmed = href.replace(/[.,;:!?]+$/, "");
    const tail = href.slice(trimmed.length);
    const html = answerLinkHtml(trimmed, trimmed);
    return html ? keep(html) + tail : href;
  });
  return { raw, tokens };
}

function formatInline(text, sources, skipHeadingStrip = false) {
  let raw = text || "";
  if (!skipHeadingStrip) {
    raw = stripMarkdownHeading(raw);
  }

  const linked = linkifyAnswerText(raw);
  let html = escapeHtml(linked.raw);
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\[([^\]]+)\]/g, (match, inner) => {
    const badges = renderCitationBadges(inner, sources);
    return badges || match;
  });
  html = html.replace(/%%LINK(\d+)%%/g, (_match, index) => linked.tokens[Number(index)] || "");
  return html;
}

function plainListText(line) {
  return stripMarkdownHeading(stripBulletPrefix(line))
    .replace(/\*\*/g, "")
    .replace(/\[[^\]]+\]/g, "")
    .trim();
}

/**
 * Section subtitle: bold/plain title ending with a colon, or a short heading-like
 * bullet with no sentence body (common model slip).
 */
function isSectionSubtitleLine(line, nextLine, prevLine) {
  const trimmed = (line || "").trim();
  if (!trimmed || isTableLine(trimmed)) {
    return false;
  }
  if (/^#{1,6}\s+/.test(trimmed) && !/^#{1,6}\s+\d+[.)]/.test(trimmed)) {
    return false; // real ### heading handled separately
  }

  const content = stripMarkdownHeading(stripBulletPrefix(trimmed)).trim();

  // Only a title the model wrote in bold on its own line is a subtitle. A plain
  // line that happens to end with a colon is ordinary text.
  return /^\*\*[^*]+\*\*:?\s*$/.test(content);
}

function subtitleLabel(line) {
  let content = stripMarkdownHeading(stripBulletPrefix(line)).trim();
  const bold = content.match(/^\*\*([^*]+)\*\*:?\s*$/);
  if (bold) {
    content = bold[1].trim();
  } else {
    content = content.replace(/\*\*/g, "").replace(/:\s*$/, "").trim();
  }
  if (!content.endsWith(":")) {
    content = `${content}:`;
  }
  return content;
}

function formatSubtitle(line, sources) {
  const label = subtitleLabel(line);
  const core = label.endsWith(":") ? label.slice(0, -1) : label;
  return `<p class="answer-subtitle"><strong>${formatInline(core, sources, true)}:</strong></p>`;
}

function isListLine(line) {
  return /^[-*]\s+/.test((line || "").trim());
}

function formatStructuredListBlock(lines, sources) {
  let html = "";
  let listOpen = false;

  const closeList = () => {
    if (listOpen) {
      html += "</ul>";
      listOpen = false;
    }
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const next = lines[i + 1];
    const prev = lines[i - 1];

    if (/^#{1,6}\s+/.test(line) && !/^#{1,6}\s+\d+[.)]/.test(line)) {
      closeList();
      html += `<h3 class="answer-heading">${formatInline(stripMarkdownHeading(line), sources, true)}</h3>`;
      continue;
    }

    if (isSectionSubtitleLine(line, next, prev)) {
      closeList();
      html += formatSubtitle(line, sources);
      continue;
    }

    if (isListLine(line)) {
      if (!listOpen) {
        html += '<ul class="answer-list">';
        listOpen = true;
      }
      html += `<li>${formatListItem(line, sources)}</li>`;
      continue;
    }

    closeList();
    const lone = line.trim();
    if (/^\*\*[^*]+\*\*:?\s*$/.test(lone)) {
      html += formatSubtitle(line, sources);
    } else {
      html += `<p class="answer-paragraph">${formatInline(stripMarkdownHeading(line), sources, true)}</p>`;
    }
  }

  closeList();
  return html;
}

function isTableSeparator(line) {
  const trimmed = (line || "").trim();
  if (!trimmed.includes("|") || !/-{2,}/.test(trimmed)) {
    return false;
  }
  return /^[\s|:-]+$/.test(trimmed);
}

function isTableRow(line) {
  const trimmed = (line || "").trim();
  if (!trimmed || isTableSeparator(trimmed)) {
    return false;
  }
  const pipes = (trimmed.match(/\|/g) || []).length;
  return pipes >= 1 && /\|/.test(trimmed.slice(1, -1) || trimmed);
}

function isTableLine(line) {
  return isTableSeparator(line) || isTableRow(line);
}

function parseTableCells(line) {
  let trimmed = (line || "").trim();
  if (trimmed.startsWith("|")) {
    trimmed = trimmed.slice(1);
  }
  if (trimmed.endsWith("|")) {
    trimmed = trimmed.slice(0, -1);
  }
  return trimmed.split("|").map((cell) => cell.trim());
}

function formatTableRow(cells) {
  return `| ${cells.map((cell) => cell || "").join(" | ")} |`;
}

/**
 * Models often emit a whole Markdown table on one line, joining rows with
 * "||" or "| |", sometimes after an intro sentence on the same line.
 * Rebuild proper newline-separated rows from the header width.
 */
function expandCollapsedTableLine(line) {
  let trimmed = (line || "").trim();
  if ((trimmed.match(/\|/g) || []).length < 6 || !/-{2,}/.test(trimmed)) {
    return line;
  }

  // Peel off leading prose before the table starts
  const tableStartMatch = trimmed.match(/\|\s*[^|]+\s*\|\s*[^|]+\s*\|/);
  let prefix = "";
  if (tableStartMatch && tableStartMatch.index > 0) {
    prefix = trimmed.slice(0, tableStartMatch.index).trim();
    trimmed = trimmed.slice(tableStartMatch.index).trim();
  }

  const sepMatch = trimmed.match(
    /\|[\t ]*[-:]{2,}[\t :|-]*(?:\|[\t ]*[-:]{2,}[\t :|-]*)*\|?/
  );
  if (!sepMatch || sepMatch.index == null) {
    return line;
  }

  const before = trimmed.slice(0, sepMatch.index).trim();
  let after = trimmed.slice(sepMatch.index + sepMatch[0].length).trim();
  if (!before || !after) {
    return line;
  }

  const headerCells = parseTableCells(before.startsWith("|") ? before : `| ${before}`);
  while (headerCells.length > 1 && !headerCells[headerCells.length - 1]) {
    headerCells.pop();
  }
  const colCount = headerCells.length;
  if (colCount < 2) {
    return line;
  }

  const separator = formatTableRow(Array(colCount).fill("---"));
  const rows = [formatTableRow(headerCells), separator];

  let rowStrings = [];
  if (/\|\|/.test(after)) {
    rowStrings = after.split(/\|\|/).map((part) => {
      let row = part.trim();
      if (!row) {
        return "";
      }
      if (!row.startsWith("|")) {
        row = `| ${row}`;
      }
      if (!row.endsWith("|")) {
        row = `${row} |`;
      }
      return row;
    }).filter(Boolean);
  } else {
    const bodyCells = parseTableCells(after.startsWith("|") ? after : `| ${after}`);
    const cleaned = [];
    for (let i = 0; i < bodyCells.length; i += 1) {
      const atRowStart = cleaned.length % colCount === 0;
      if (atRowStart && !bodyCells[i] && i + 1 < bodyCells.length && bodyCells[i + 1]) {
        continue;
      }
      cleaned.push(bodyCells[i]);
    }
    for (let i = 0; i < cleaned.length; i += colCount) {
      const chunk = cleaned.slice(i, i + colCount);
      if (chunk.every((cell) => !cell)) {
        continue;
      }
      while (chunk.length < colCount) {
        chunk.push("");
      }
      rowStrings.push(formatTableRow(chunk));
    }
  }

  for (const row of rowStrings) {
    const cells = parseTableCells(row);
    if (cells.every((cell) => !cell)) {
      continue;
    }
    while (cells.length < colCount) {
      cells.push("");
    }
    rows.push(formatTableRow(cells.slice(0, colCount)));
  }

  if (rows.length < 3) {
    return line;
  }

  const table = rows.join("\n");
  return prefix ? `${prefix}\n\n${table}` : table;
}

function formatMarkdownTable(lines, sources) {
  const dataLines = lines.filter((line) => !isTableSeparator(line));
  if (dataLines.length === 0) {
    return "";
  }

  const header = parseTableCells(dataLines[0]);
  const bodyRows = dataLines.slice(1).map(parseTableCells);
  const colCount = Math.max(
    header.length,
    ...bodyRows.map((row) => row.length),
    1
  );

  const pad = (cells) => {
    const next = cells.slice(0, colCount);
    while (next.length < colCount) {
      next.push("");
    }
    return next;
  };

  let html = '<div class="answer-table-wrap"><table class="answer-table"><thead><tr>';
  for (const cell of pad(header)) {
    html += `<th>${formatInline(cell, sources, true)}</th>`;
  }
  html += "</tr></thead><tbody>";
  for (const row of bodyRows) {
    html += "<tr>";
    for (const cell of pad(row)) {
      html += `<td>${formatInline(cell, sources, true)}</td>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table></div>";
  return html;
}

function isTableBlock(lines) {
  if (lines.length < 2) {
    return false;
  }
  if (!lines.every((line) => isTableLine(line))) {
    return false;
  }
  return lines.some(isTableSeparator) || lines.filter(isTableRow).length >= 2;
}

function preprocessAnswerText(text) {
  let value = (text || "").trim().replace(/\r\n/g, "\n");
  value = value.replace(/\s+(?=####\s+\d+[.)])/g, "\n");
  value = value.replace(/^####\s+(\d+[.)]\s*)/gm, "- ");
  value = value.replace(/^####\s+/gm, "- ");
  // Expand jammed single-line pipe tables before block parsing
  value = value
    .split("\n")
    .map((line) => expandCollapsedTableLine(line))
    .join("\n");
  return value;
}

function formatAssistantContent(text, sources) {
  const normalised = preprocessAnswerText(text);
  if (!normalised) {
    return "";
  }

  const rawBlocks = normalised.split(/\n\s*\n/);
  const blocks = [];

  for (const block of rawBlocks) {
    const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
    if (lines.length === 0) {
      continue;
    }
    if (
      blocks.length > 0 &&
      isTableBlock(blocks[blocks.length - 1]) &&
      lines.every((line) => isTableLine(line))
    ) {
      blocks[blocks.length - 1] = blocks[blocks.length - 1].concat(lines);
      continue;
    }
    blocks.push(lines);
  }

  const htmlParts = [];

  for (const lines of blocks) {
    if (lines.length === 1 && /^#{1,6}\s+/.test(lines[0]) && !/^#{1,6}\s+\d+[.)]/.test(lines[0])) {
      htmlParts.push(
        `<h3 class="answer-heading">${formatInline(stripMarkdownHeading(lines[0]), sources, true)}</h3>`
      );
      continue;
    }

    if (lines.length === 1 && /^\*\*[^*]+\*\*:?\s*$/.test(lines[0].trim())) {
      htmlParts.push(formatSubtitle(lines[0], sources));
      continue;
    }

    if (isTableBlock(lines)) {
      htmlParts.push(formatMarkdownTable(lines, sources));
      continue;
    }

    // Mixed heading / subtitle / bullet blocks
    if (
      lines.some((line) => isListLine(line) || /^#{1,6}\s+/.test(line)) ||
      lines.some((line, idx) => isSectionSubtitleLine(line, lines[idx + 1], lines[idx - 1]))
    ) {
      htmlParts.push(formatStructuredListBlock(lines, sources));
      continue;
    }

    const paragraph = lines
      .map((line) => formatInline(stripMarkdownHeading(stripBulletPrefix(line)), sources, true))
      .join(" ");
    htmlParts.push(`<p class="answer-paragraph">${paragraph}</p>`);
  }

  return htmlParts.join("");
}

function sourceDisplayName(source) {
  return source.title || source.filename || t("sources.fallbackName");
}

function buildSourcesPanel(sources) {
  const panel = document.createElement("details");
  panel.className = "sources-panel";

  const summary = document.createElement("summary");
  summary.className = "sources-panel__title";
  summary.textContent = t("sources.title", { count: sources.length });
  panel.appendChild(summary);

  const list = document.createElement("ol");
  list.className = "sources-panel__list";

  for (const source of sources) {
    const item = document.createElement("li");
    item.className = "sources-panel__item";
    item.dataset.sourceLabel = String(source.label);

    const title = document.createElement("div");
    title.className = "sources-panel__filename";
    title.textContent = `${source.label}. ${sourceDisplayName(source)}`;

    const meta = document.createElement("div");
    meta.className = "sources-panel__meta";
    if (source.public_url) {
      meta.innerHTML = `${escapeHtml(formatLocation(source))} · <a class="sources-panel__link" href="${escapeHtml(source.public_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("sources.viewPublic"))}</a>`;
    } else {
      meta.textContent = formatLocation(source);
    }

    item.append(title, meta);

    const preview = document.createElement("p");
    preview.className = "sources-panel__preview";
    preview.textContent = source.preview || "";

    item.appendChild(preview);
    list.appendChild(item);
  }

  panel.appendChild(list);
  return panel;
}

function buildFollowUpQuestions(questions) {
  const section = document.createElement("section");
  section.className = "follow-up";

  const heading = document.createElement("h3");
  heading.className = "follow-up__title";
  heading.textContent = t("followUp.title");
  section.appendChild(heading);

  const list = document.createElement("div");
  list.className = "follow-up__list";

  for (const question of questions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "follow-up__button";
    button.textContent = question;
    button.addEventListener("click", () => {
      inputEl.value = question;
      formEl.requestSubmit();
    });
    list.appendChild(button);
  }

  section.appendChild(list);
  return section;
}

function submitQuestion(message) {
  appendMessage("user", message);
  inputEl.value = "";
  return sendMessage(message);
}

function appendStatusMessage(text) {
  const div = document.createElement("div");
  div.className = "message status";
  div.innerHTML = `
    <ul class="status-steps">
      <li>${escapeHtml(text)}</li>
    </ul>
  `;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

const THUMB_UP_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
const THUMB_DOWN_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg>';

const REASON_OPTIONS = [
  ["incorrect", "feedback.reason.incorrect"],
  ["not_answered", "feedback.reason.notAnswered"],
  ["too_technical", "feedback.reason.tooTechnical"],
  ["too_simple", "feedback.reason.tooSimple"],
];

function feedbackButton(rating, label) {
  const button = el("button", "message__action");
  button.type = "button";
  button.innerHTML = `${rating === "up" ? THUMB_UP_SVG : THUMB_DOWN_SVG}${escapeHtml(label)}`;
  button.setAttribute("aria-label", label);
  button.title = label;
  return button;
}

function feedbackRecord(content, sources, meta, rating, reason) {
  return {
    rating,
    reason: reason || null,
    question: (meta && meta.question) || "",
    answer: content || "",
    audience: (meta && meta.audience) || "general",
    formats: (meta && meta.formats) || [],
    lang: (meta && meta.lang) || "en",
    cited_chunk_ids: (sources || []).map((source) => source.chunk_id).filter(Boolean),
    response_ms: meta && Number.isFinite(meta.responseMs) ? meta.responseMs : null,
  };
}

async function saveFeedback(record) {
  try {
    const response = await fetch(`${apiBase}/api/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(record),
      credentials: "include",
    });
    return response.ok;
  } catch (_err) {
    return false;
  }
}

/**
 * Thumbs up saves at once. Thumbs down asks for an optional reason, then saves
 * once (with the reason, or without it after "Skip"). Each answer is rated once.
 */
function attachFeedback({ content, sources, meta, helpfulBtn, notHelpfulBtn, area }) {
  let rated = false;

  const lockRatings = () => {
    helpfulBtn.disabled = true;
    notHelpfulBtn.disabled = true;
  };

  const showThanks = (text) => {
    area.innerHTML = "";
    area.appendChild(el("p", "feedback-reasons__note", text));
  };

  helpfulBtn.addEventListener("click", async () => {
    if (rated) {
      return;
    }
    rated = true;
    lockRatings();
    helpfulBtn.classList.add("is-active");
    const ok = await saveFeedback(feedbackRecord(content, sources, meta, "up", null));
    showThanks(ok ? t("feedback.thanks") : t("feedback.failed"));
  });

  notHelpfulBtn.addEventListener("click", () => {
    if (rated) {
      return;
    }
    rated = true;
    lockRatings();
    notHelpfulBtn.classList.add("is-active");

    const panel = el("div", "feedback-reasons");
    panel.appendChild(el("p", "feedback-reasons__title", t("feedback.reasonTitle")));
    const list = el("div", "feedback-reasons__list");

    const choose = async (reason) => {
      panel.querySelectorAll("button").forEach((button) => {
        button.disabled = true;
      });
      const ok = await saveFeedback(feedbackRecord(content, sources, meta, "down", reason));
      showThanks(ok ? t("feedback.thanks") : t("feedback.failed"));
    };

    for (const [reason, key] of REASON_OPTIONS) {
      const button = el("button", "chip", t(key));
      button.type = "button";
      button.addEventListener("click", () => choose(reason));
      list.appendChild(button);
    }
    const skip = el("button", "chip", t("feedback.skip"));
    skip.type = "button";
    skip.addEventListener("click", () => choose(null));
    list.appendChild(skip);

    panel.appendChild(list);
    area.innerHTML = "";
    area.appendChild(panel);
  });
}

function appendNotice(text) {
  hideWelcome();
  const div = el("div", "message notice", text);
  div.setAttribute("role", "status");
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function appendMessage(role, content, sources, suggestedQuestions, meta) {
  hideWelcome();

  const div = document.createElement("article");
  div.className = `message ${role}`;

  if (role === "assistant") {
    const body = document.createElement("div");
    body.className = "message__body";
    body.innerHTML = formatAssistantContent(content, sources || []);
    div.appendChild(body);

    body.querySelectorAll(".citation-badge").forEach((badge) => {
      badge.addEventListener("click", () => {
        const label = badge.getAttribute("data-source-label");
        const target = div.querySelector(
          `.sources-panel__item[data-source-label="${label}"]`
        );
        const sourcesPanel = div.querySelector(".sources-panel");
        if (sourcesPanel && !sourcesPanel.open) {
          sourcesPanel.open = true;
        }
        if (target) {
          target.scrollIntoView({ behavior: "smooth", block: "nearest" });
          target.classList.add("is-highlighted");
          window.setTimeout(() => target.classList.remove("is-highlighted"), 1200);
        }
      });
    });

    const actions = document.createElement("div");
    actions.className = "message__actions";

    if (meta && Number.isFinite(meta.responseMs)) {
      actions.appendChild(
        el("span", "message__meta", t("answer.answeredIn", { seconds: (meta.responseMs / 1000).toFixed(1) }))
      );
    }

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "message__action";
    copyBtn.textContent = t("answer.copy");
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(content || "");
        copyBtn.textContent = t("answer.copied");
        window.setTimeout(() => {
          copyBtn.textContent = t("answer.copy");
        }, 1500);
      } catch (_err) {
        copyBtn.textContent = t("answer.copyFailed");
      }
    });

    const readBtn = document.createElement("button");
    readBtn.type = "button";
    readBtn.className = "message__action";
    readBtn.textContent = t("answer.readAloud");
    readBtn.setAttribute("aria-pressed", "false");
    readBtn.setAttribute("aria-label", t("answer.readAloudLabel"));
    readBtn.addEventListener("click", () => {
      toggleReadAloud(content, readBtn);
    });

    const helpfulBtn = feedbackButton("up", t("feedback.helpful"));
    const notHelpfulBtn = feedbackButton("down", t("feedback.notHelpful"));

    actions.append(copyBtn, readBtn, helpfulBtn, notHelpfulBtn);
    div.appendChild(actions);

    const feedbackArea = el("div", "feedback");
    div.appendChild(feedbackArea);
    attachFeedback({ content, sources, meta, helpfulBtn, notHelpfulBtn, area: feedbackArea });

    if (sources && sources.length > 0) {
      div.appendChild(buildSourcesPanel(sources));
    }

    if (suggestedQuestions && suggestedQuestions.length > 0) {
      div.appendChild(buildFollowUpQuestions(suggestedQuestions));
    }
  } else if (role === "error") {
    div.textContent = content;
  } else {
    div.textContent = content;
  }

  messagesEl.appendChild(div);
  if (role === "assistant") {
    scrollToMessageStart(div);
  } else {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function scrollToMessageStart(messageEl) {
  const containerTop = messagesEl.getBoundingClientRect().top;
  const messageTop = messageEl.getBoundingClientRect().top;
  messagesEl.scrollTop += messageTop - containerTop;
}

function hideWelcome() {
  if (welcomeEl) {
    welcomeEl.hidden = true;
  }
}

function showWelcome() {
  messagesEl.innerHTML = "";
  if (welcomeEl) {
    welcomeEl.hidden = false;
  }
}

function setStatus(text) {
  statusEl.textContent = text;
}

async function sendMessage(message) {
  if (atDailyLimit()) {
    appendNotice(t("usage.limitReached", { limit: state.usage.limit }));
    setStatus(t("status.dailyLimit"));
    return;
  }

  const payload = {
    message,
    audience: state.audience,
    formats: [...state.formats],
    lang: state.lang,
  };
  const sessionId = getSessionId();
  if (sessionId) {
    payload.session_id = sessionId;
  }

  state.busy = true;
  refreshFormState();
  setStatus(t("status.connecting"));
  const pendingStatus = appendStatusMessage(t("status.searching"));
  const started = performance.now();

  try {
    const response = await fetch(`${apiBase}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "include",
    });

    if (response.status === 429) {
      // Daily limit: the server did not call the model. Show a friendly notice.
      const body = await response.json().catch(() => ({}));
      const detail = body.detail || {};
      pendingStatus.remove();
      state.usage = {
        used: detail.used ?? state.usage?.limit ?? 0,
        limit: detail.limit ?? state.usage?.limit ?? 0,
        remaining: 0,
      };
      renderUsage();
      appendNotice(t("usage.limitReached", { limit: state.usage.limit }));
      setStatus(t("status.dailyLimit"));
      return;
    }

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`HTTP ${response.status}: ${detail}`);
    }

    const responseMs = Math.round(performance.now() - started);
    const data = await response.json();
    pendingStatus.remove();
    setSessionId(data.session_id);
    if (data.usage) {
      state.usage = data.usage;
      renderUsage();
    }

    if (data.error) {
      appendMessage("error", data.answer || data.error);
      setStatus(t("status.couldNotAnswer"));
    } else {
      appendMessage("assistant", data.answer, data.sources, data.suggested_questions, {
        question: message,
        audience: payload.audience,
        formats: payload.formats,
        lang: payload.lang,
        responseMs,
      });
      const publicCount = (data.sources || []).filter((s) => s.public_url).length;
      let statusText = data.sources?.length
        ? t("status.answered", { count: data.sources.length })
        : t("status.answerReady");
      if (publicCount > 0) {
        statusText += ` ${t("status.publicLinks", { count: publicCount })}`;
      }
      setStatus(statusText);
    }
  } catch (err) {
    pendingStatus.remove();
    appendMessage("error", err.message || String(err));
    setStatus(t("status.requestFailed"));
  } finally {
    state.busy = false;
    refreshFormState();
    inputEl.focus();
  }
}

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = inputEl.value.trim();
  if (!message) {
    return;
  }

  await submitQuestion(message);
});

newChatBtn.addEventListener("click", () => {
  stopReadAloud();
  clearSession();
  showWelcome();
  setStatus(t("status.newConversation"));
  inputEl.focus();
});

audienceButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.audience = button.dataset.audience;
    audienceButtons.forEach((other) => {
      other.setAttribute("aria-pressed", String(other === button));
      other.classList.toggle("is-selected", other === button);
    });
  });
});

formatButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const tag = button.dataset.format;
    if (state.formats.has(tag)) {
      state.formats.delete(tag);
    } else {
      state.formats.add(tag);
    }
    button.setAttribute("aria-pressed", String(state.formats.has(tag)));
  });
});

langButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setLanguage(button.dataset.lang);
  });
});

async function refreshUsage() {
  try {
    const response = await fetch(`${apiBase}/api/usage`, { credentials: "include" });
    if (response.ok) {
      state.usage = await response.json();
      renderUsage();
    }
  } catch (_err) {
    // The counter is shown only when the API answers. Chat still works without it.
  }
}

async function bootstrap() {
  await setLanguage(chooseInitialLanguage());
  await refreshUsage();

  const isLocalDev =
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1";

  const existingSession = getSessionId();
  if (existingSession) {
    setStatus(t("status.sessionRestored", { id: existingSession.slice(0, 8) }));
  } else if (isLocalDev) {
    setStatus(t("status.localClient", { api: `${apiBase}/api/chat` }));
  }

  inputEl.focus();
}

bootstrap();
