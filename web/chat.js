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

function pickEnglishVoice() {
  const voices = window.speechSynthesis.getVoices();
  return (
    voices.find((voice) => voice.lang === "en-GB") ||
    voices.find((voice) => voice.lang.startsWith("en-GB")) ||
    voices.find((voice) => voice.lang.startsWith("en-")) ||
    null
  );
}

if (speechSupported()) {
  window.speechSynthesis.addEventListener("voiceschanged", pickEnglishVoice);
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
    activeReadButton.textContent = "Read aloud";
    activeReadButton.setAttribute("aria-pressed", "false");
    activeReadButton = null;
  }
}

function toggleReadAloud(text, button) {
  if (!speechSupported()) {
    button.textContent = "Not supported";
    window.setTimeout(() => {
      button.textContent = "Read aloud";
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
  utterance.lang = "en-GB";
  const voice = pickEnglishVoice();
  if (voice) {
    utterance.voice = voice;
  }

  utterance.onstart = () => {
    activeReadButton = button;
    button.textContent = "Stop";
    button.setAttribute("aria-pressed", "true");
  };

  const resetButton = () => {
    if (activeReadButton === button) {
      activeReadButton = null;
    }
    button.textContent = "Read aloud";
    button.setAttribute("aria-pressed", "false");
  };

  utterance.onend = resetButton;
  utterance.onerror = resetButton;

  window.speechSynthesis.speak(utterance);
}

function formatLocation(source) {
  const type = source.location_type || "page";
  const span = `${type}s ${source.location_start}–${source.location_end}`;
  return source.section ? `${source.section}, ${span}` : span;
}

function extractCitationLabels(inner) {
  const labels = new Set();
  for (const part of inner.split(/[,;]/)) {
    const match = part.trim().match(/^(\d+)/);
    if (match) {
      labels.add(Number(match[1]));
    }
  }
  return [...labels].sort((a, b) => a - b);
}

function renderCitationBadges(inner, sources) {
  const labels = extractCitationLabels(inner);
  if (labels.length === 0) {
    return null;
  }

  return labels
    .map((label) => {
      const source = sources.find((item) => item.label === label);
      const title = source ? sourceDisplayName(source) : `Source ${label}`;
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
  const colonSplit = content.match(/^\*\*([^*]+)\*\*:\s+(.+)$/);
  if (colonSplit) {
    return `<strong>${formatInline(colonSplit[1].trim(), sources, true)}:</strong> ${formatInline(colonSplit[2].trim(), sources, true)}`;
  }

  const titled = content.match(/^(.{1,90}?)\s*[-–—]\s+(.+)$/);
  if (titled) {
    return `<strong>${formatInline(titled[1].trim(), sources, true)}</strong> ${formatInline(titled[2].trim(), sources, true)}`;
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

  if (/^\*\*[^*]+\*\*:?\s*$/.test(content)) {
    return true;
  }
  if (/^[^:*]{2,90}:\s*$/.test(content.replace(/\*\*/g, ""))) {
    return true;
  }

  // Detail bullets keep citations or inline bold+explanation
  if (/\[[0-9]/.test(content)) {
    return false;
  }
  if (/\*\*[^*]+\*\*\s+\S/.test(content)) {
    return false;
  }

  const plain = plainListText(trimmed);
  const wordCount = plain.split(/\s+/).filter(Boolean).length;
  const isShortHeading =
    /^[-*]\s+/.test(trimmed) &&
    plain.length > 0 &&
    plain.length <= 70 &&
    wordCount <= 10 &&
    !/[.!?]$/.test(plain);

  if (!isShortHeading) {
    return false;
  }

  // Imperative detail lines without a trailing period
  if (
    /^(Apply|Assemble|Use|Show|Describe|Highlight|Diversify|Ensure|Include|Provide|Create|Develop|Deploy|Follow the|Plan for)\b/i.test(
      plain
    ) &&
    wordCount >= 5
  ) {
    return false;
  }

  const nextIsBullet = nextLine && /^[-*]\s+/.test(nextLine.trim());
  const nextPlain = nextLine ? plainListText(nextLine) : "";
  const nextIsLongDetail =
    nextIsBullet && (nextPlain.length > plain.length + 15 || /[.!?]/.test(nextPlain) || /\[[0-9]/.test(nextLine));
  const nextIsShortHeading =
    nextIsBullet &&
    nextPlain.length > 0 &&
    nextPlain.length <= 70 &&
    nextPlain.split(/\s+/).filter(Boolean).length <= 10 &&
    !/[.!?]$/.test(nextPlain) &&
    !/\[[0-9]/.test(nextLine || "");
  const prevPlain = prevLine ? plainListText(prevLine) : "";
  const prevIsLongDetail =
    prevLine &&
    /^[-*]\s+/.test(prevLine.trim()) &&
    (prevPlain.length > 80 || /[.!?]/.test(prevPlain) || /\[[0-9]/.test(prevLine));

  if (!prevLine && nextIsBullet) {
    return true;
  }
  if (nextIsLongDetail || nextIsShortHeading) {
    return true;
  }
  if (prevIsLongDetail && nextIsLongDetail) {
    return true;
  }

  return false;
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
    if (
      /^\*\*[^*]+\*\*:?\s*$/.test(lone) ||
      /^[^:*]{2,90}:\s*$/.test(lone.replace(/\*\*/g, ""))
    ) {
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

    if (
      lines.length === 1 &&
      ( /^\*\*[^*]+\*\*:?\s*$/.test(lines[0]) ||
        /^[^:*]{2,90}:\s*$/.test(lines[0].replace(/\*\*/g, "")) )
    ) {
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
  return source.title || source.filename || "Source";
}

function buildSourcesPanel(sources) {
  const panel = document.createElement("details");
  panel.className = "sources-panel";

  const summary = document.createElement("summary");
  summary.className = "sources-panel__title";
  summary.textContent = `Sources (${sources.length})`;
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
      meta.innerHTML = `${escapeHtml(formatLocation(source))} · <a class="sources-panel__link" href="${escapeHtml(source.public_url)}" target="_blank" rel="noopener noreferrer">View public document</a>`;
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
  heading.textContent = "Suggested questions";
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

function appendMessage(role, content, sources, suggestedQuestions) {
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

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "message__action";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(content || "");
        copyBtn.textContent = "Copied";
        window.setTimeout(() => {
          copyBtn.textContent = "Copy";
        }, 1500);
      } catch (_err) {
        copyBtn.textContent = "Copy failed";
      }
    });

    const readBtn = document.createElement("button");
    readBtn.type = "button";
    readBtn.className = "message__action";
    readBtn.textContent = "Read aloud";
    readBtn.setAttribute("aria-pressed", "false");
    readBtn.setAttribute("aria-label", "Read this answer aloud");
    readBtn.addEventListener("click", () => {
      toggleReadAloud(content, readBtn);
    });

    actions.append(copyBtn, readBtn);
    div.appendChild(actions);

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

function renderStarterPrompts() {
  startersEl.innerHTML = "";
  for (const prompt of STARTER_PROMPTS) {
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

async function sendMessage(message) {
  const payload = { message };
  const sessionId = getSessionId();
  if (sessionId) {
    payload.session_id = sessionId;
  }

  submitBtn.disabled = true;
  setStatus("Connecting to the LandIS knowledge base…");
  const pendingStatus = appendStatusMessage("Searching project documents…");

  try {
    const response = await fetch(`${apiBase}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`HTTP ${response.status}: ${detail}`);
    }

    const data = await response.json();
    pendingStatus.remove();
    setSessionId(data.session_id);

    if (data.error) {
      appendMessage("error", data.answer || data.error);
      setStatus("The assistant could not answer.");
    } else {
      appendMessage(
        "assistant",
        data.answer,
        data.sources,
        data.suggested_questions
      );
      const publicCount = (data.sources || []).filter((s) => s.public_url).length;
      let statusText = data.sources?.length
        ? `Answered using ${data.sources.length} cited source(s).`
        : "Answer ready.";
      if (publicCount > 0) {
        statusText += ` Expand Sources below for ${publicCount} public link(s).`;
      }
      setStatus(statusText);
    }
  } catch (err) {
    pendingStatus.remove();
    appendMessage("error", err.message || String(err));
    setStatus("Request failed.");
  } finally {
    submitBtn.disabled = false;
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
  setStatus("New conversation started.");
  inputEl.focus();
});

renderStarterPrompts();

const isLocalDev =
  window.location.hostname === "localhost" ||
  window.location.hostname === "127.0.0.1";

const existingSession = getSessionId();
if (existingSession) {
  setStatus(`Session restored (${existingSession.slice(0, 8)}…).`);
} else if (isLocalDev) {
  setStatus(`Local test client. API: ${apiBase}/api/chat`);
}

inputEl.focus();
