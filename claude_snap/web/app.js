// claude-snap PWA — front-end app logic.
// Hosted mode: state stays in this tab; the only network endpoint is api.anthropic.com.
// Local mode (served by `claude-snap chat` on localhost): the only network endpoint
// is the local proxy; the API key never reaches the browser.

import { readJsonl, isPacked, unpack, eventsToMessages, stats } from './codec.js';

const STORAGE_KEY = 'claude-snap.api-key.v1';
const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages';
const ANTHROPIC_VERSION = '2023-06-01';
const MAX_TOKENS_DEFAULT = 4096;

const IS_LOCAL_MODE = (
  location.hostname === 'localhost'
  || location.hostname === '127.0.0.1'
  || location.hostname === '[::1]'
);
const CHAT_ENDPOINT = IS_LOCAL_MODE ? '/api/messages' : ANTHROPIC_API_URL;

const $ = (id) => document.getElementById(id);

const els = {
  forgetKeyBtn: $('forget-key-btn'),
  dropzone: $('dropzone'),
  fileInput: $('file-input'),
  pasteArea: $('paste-area'),
  pasteLoadBtn: $('paste-load-btn'),
  loadStatus: $('load-status'),
  transcriptSection: $('transcript-section'),
  transcriptMeta: $('transcript-meta'),
  transcript: $('transcript'),
  chatSection: $('chat-section'),
  apiKeyInput: $('api-key-input'),
  rememberKey: $('remember-key'),
  modelSelect: $('model-select'),
  userInput: $('user-input'),
  sendBtn: $('send-btn'),
  chatStatus: $('chat-status'),
  chatLog: $('chat-log'),
};

// State held in this tab only.
let unpackedEvents = [];
let priorMessages = [];   // chat-style messages derived from the snapshot
let liveMessages = [];    // turns added in this session (post-snapshot)

// ---------- helpers ----------

function setStatus(node, text, kind = 'info') {
  if (!node) return;
  node.textContent = text || '';
  node.dataset.kind = kind;
}

function escapeText(node, text) {
  // Always use textContent — never innerHTML — for any user-provided content.
  node.textContent = text;
}

function makeEl(tag, className) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  return el;
}

function loadStoredKey() {
  try {
    const k = localStorage.getItem(STORAGE_KEY);
    if (k && typeof k === 'string') {
      els.apiKeyInput.value = k;
      els.rememberKey.checked = true;
      els.forgetKeyBtn.classList.remove('hidden');
    }
  } catch (_) {
    // localStorage may be disabled; degrade silently.
  }
}

function persistKeyIfRequested() {
  const k = els.apiKeyInput.value.trim();
  if (!k) return;
  if (els.rememberKey.checked) {
    try {
      localStorage.setItem(STORAGE_KEY, k);
      els.forgetKeyBtn.classList.remove('hidden');
    } catch (_) {
      // Silent if localStorage is full / unavailable.
    }
  } else {
    forgetStoredKey();
  }
}

function forgetStoredKey() {
  try { localStorage.removeItem(STORAGE_KEY); } catch (_) {}
  els.forgetKeyBtn.classList.add('hidden');
}

// ---------- file loading ----------

async function loadText(text) {
  setStatus(els.loadStatus, 'Parsing…');
  const records = readJsonl(text);
  if (records.length === 0) {
    setStatus(els.loadStatus, 'No JSONL records found.', 'error');
    return;
  }

  let events;
  if (isPacked(records)) {
    events = unpack(records);
    const s = stats(records);
    setStatus(els.loadStatus,
      `Loaded packed snap: ${records.length} records → ${events.length} events restored ` +
      `(${s.refs} refs, ${s.compression_ratio}× compression).`, 'ok');
  } else {
    events = records;
    setStatus(els.loadStatus,
      `Loaded raw JSONL: ${events.length} events (no packing detected).`, 'ok');
  }

  unpackedEvents = events;
  priorMessages = eventsToMessages(events);
  liveMessages = [];

  renderTranscript();
  els.transcriptSection.classList.remove('hidden');
  els.chatSection.classList.remove('hidden');
}

async function loadFile(file) {
  if (!file) return;
  if (file.size > 64 * 1024 * 1024) {
    setStatus(els.loadStatus, 'File is larger than 64 MB; refuse to parse in-browser.', 'error');
    return;
  }
  try {
    const text = await file.text();
    await loadText(text);
  } catch (e) {
    setStatus(els.loadStatus, 'Failed to read file: ' + (e?.message || 'unknown'), 'error');
  }
}

// ---------- transcript rendering ----------

function renderTranscript() {
  const t = els.transcript;
  t.replaceChildren();

  const meta = els.transcriptMeta;
  meta.replaceChildren();
  const metaLine = makeEl('p');
  escapeText(metaLine,
    `${unpackedEvents.length} events · ${priorMessages.length} chat turns derived (assistant + user).`);
  meta.appendChild(metaLine);

  for (const m of priorMessages) {
    const bubble = makeEl('div', `bubble role-${m.role}`);
    const role = makeEl('div', 'role');
    escapeText(role, m.role === 'user' ? 'user' : 'claude');
    bubble.appendChild(role);

    const body = makeEl('pre', 'body');
    escapeText(body, m.content);
    bubble.appendChild(body);

    t.appendChild(bubble);
  }

  // Anchor to bottom on initial render.
  t.scrollTop = t.scrollHeight;
}

function appendLiveTurn(role, content) {
  const log = els.chatLog;
  const bubble = makeEl('div', `bubble role-${role}`);
  const r = makeEl('div', 'role');
  escapeText(r, role === 'user' ? 'you' : 'claude (live)');
  bubble.appendChild(r);
  const body = makeEl('pre', 'body');
  escapeText(body, content);
  bubble.appendChild(body);
  log.appendChild(bubble);
  log.scrollTop = log.scrollHeight;
}

// ---------- Anthropic call ----------

function buildRequestMessages(newUserText) {
  const out = [];
  for (const m of priorMessages) {
    out.push({ role: m.role, content: m.content });
  }
  for (const m of liveMessages) {
    out.push({ role: m.role, content: m.content });
  }
  out.push({ role: 'user', content: newUserText });
  return collapseAdjacent(out);
}

function collapseAdjacent(messages) {
  // Anthropic API requires alternating roles. Collapse any same-role runs.
  const out = [];
  for (const m of messages) {
    if (out.length > 0 && out[out.length - 1].role === m.role) {
      out[out.length - 1].content += '\n\n' + m.content;
    } else {
      out.push({ ...m });
    }
  }
  // Ensure first message is user; otherwise prepend a benign user nudge.
  if (out.length === 0 || out[0].role !== 'user') {
    out.unshift({ role: 'user', content: '(continuing session)' });
  }
  return out;
}

async function sendMessage() {
  const apiKey = IS_LOCAL_MODE ? '' : els.apiKeyInput.value.trim();
  if (!IS_LOCAL_MODE && !apiKey) {
    setStatus(els.chatStatus, 'API key required.', 'error');
    return;
  }
  const userText = els.userInput.value.trim();
  if (!userText) {
    setStatus(els.chatStatus, 'Type a message first.', 'error');
    return;
  }

  persistKeyIfRequested();

  const model = els.modelSelect.value;
  const messages = buildRequestMessages(userText);

  els.sendBtn.disabled = true;
  setStatus(els.chatStatus,
    IS_LOCAL_MODE ? 'Sending via localhost proxy…' : 'Sending to api.anthropic.com…');
  appendLiveTurn('user', userText);

  try {
    const headers = { 'content-type': 'application/json' };
    if (!IS_LOCAL_MODE) {
      // Hosted mode: browser holds and sends the key.
      headers['x-api-key'] = apiKey;
      headers['anthropic-version'] = ANTHROPIC_VERSION;
      headers['anthropic-dangerous-direct-browser-access'] = 'true';
    }
    // Local mode: the localhost proxy attaches x-api-key from $ANTHROPIC_API_KEY.

    const resp = await fetch(CHAT_ENDPOINT, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        model,
        max_tokens: MAX_TOKENS_DEFAULT,
        messages,
      }),
    });

    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${errText.slice(0, 400)}`);
    }

    const data = await resp.json();
    const assistantText = (data?.content || [])
      .filter(b => b && b.type === 'text' && typeof b.text === 'string')
      .map(b => b.text)
      .join('\n\n')
      .trim() || '(empty response)';

    liveMessages.push({ role: 'user', content: userText });
    liveMessages.push({ role: 'assistant', content: assistantText });
    appendLiveTurn('assistant', assistantText);
    setStatus(els.chatStatus,
      `done · ${data?.usage?.input_tokens || '?'} in / ${data?.usage?.output_tokens || '?'} out`, 'ok');
    els.userInput.value = '';
  } catch (e) {
    setStatus(els.chatStatus, 'Failed: ' + (e?.message || 'unknown'), 'error');
    appendLiveTurn('assistant', '(request failed — see status above)');
  } finally {
    els.sendBtn.disabled = false;
  }
}

// ---------- wire up ----------

function applyModeVisibility() {
  document.querySelectorAll('.hosted-mode-only').forEach(el => {
    el.classList.toggle('hidden', IS_LOCAL_MODE);
  });
  document.querySelectorAll('.local-mode-only').forEach(el => {
    el.classList.toggle('hidden', !IS_LOCAL_MODE);
  });
}

async function tryAutoloadFromProxy() {
  if (!IS_LOCAL_MODE) return;
  try {
    const r = await fetch('/api/session');
    if (!r.ok) return;
    const text = await r.text();
    if (text && text.trim()) {
      await loadText(text);
    }
  } catch (_) {
    // Best-effort autoload; ignore failures.
  }
}

function init() {
  applyModeVisibility();
  if (!IS_LOCAL_MODE) {
    loadStoredKey();
  }

  els.forgetKeyBtn.addEventListener('click', () => {
    els.apiKeyInput.value = '';
    els.rememberKey.checked = false;
    forgetStoredKey();
  });

  // File picker.
  els.dropzone.addEventListener('click', () => els.fileInput.click());
  els.dropzone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      els.fileInput.click();
    }
  });
  els.fileInput.addEventListener('change', () => {
    const f = els.fileInput.files && els.fileInput.files[0];
    if (f) loadFile(f);
  });

  // Drag and drop.
  els.dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    els.dropzone.classList.add('dragover');
  });
  els.dropzone.addEventListener('dragleave', () => {
    els.dropzone.classList.remove('dragover');
  });
  els.dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    els.dropzone.classList.remove('dragover');
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) loadFile(f);
  });

  // Paste area.
  els.pasteLoadBtn.addEventListener('click', () => {
    const t = els.pasteArea.value;
    if (!t.trim()) {
      setStatus(els.loadStatus, 'Paste area is empty.', 'error');
      return;
    }
    loadText(t);
  });

  // Send.
  els.sendBtn.addEventListener('click', sendMessage);
  els.userInput.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      sendMessage();
    }
  });

  // Service worker — hosted mode only. In local mode there's no caching
  // benefit and we don't want a SW lingering after the local server stops.
  if (!IS_LOCAL_MODE && 'serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('./sw.js').catch(() => { /* ignore */ });
    });
  }

  // If we're in local mode and the server pre-loaded a session, fetch it.
  tryAutoloadFromProxy();
}

init();
