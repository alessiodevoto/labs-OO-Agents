"""FastAPI WebSocket server for the NeMo OO Agents web frontend.

Endpoints:
  GET  /          — serves the single-page UI (HTML embedded below)
  WS   /ws        — creates a Session + WebFrontend per connection

The HTML page is embedded as a string so the whole web module is a single
Python package with zero static-file deployment hassle.

Session resume: connect to /ws?session=<id> to resume, or use ``-c`` flag.
TODO: add authentication layer (token param or header) for multi-user deployments.
"""

from __future__ import annotations

import asyncio

# WebSocket type hint for the endpoint — resolved lazily via PEP 563.
try:
    from fastapi import WebSocket as WebSocket  # noqa: PLC0414
except ImportError:
    pass  # WebSocket type only needed at runtime when FastAPI is installed

# ---------------------------------------------------------------------------
# Embedded HTML UI
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>NeMo OO Agents</title>
<!-- highlight.js (matches viewer libs) -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/base16/monokai.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/python.min.js"></script>
<!-- Plotly.js for plotly RichOutput -->
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<!-- Vega + Vega-Lite for vega RichOutput -->
<script src="https://cdn.jsdelivr.net/npm/vega@5/build/vega.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-lite@5/build/vega-lite.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-embed@6/build/vega-embed.min.js"></script>
<!-- Monaco Editor (loaded lazily on first /edit) -->
<script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.48.0/min/vs/loader.js"></script>
<style>
  /* ---- Catppuccin Mocha palette ---- */
  :root {
    --base:     #1e1e2e;
    --mantle:   #181825;
    --crust:    #11111b;
    --surface0: #313244;
    --surface1: #45475a;
    --surface2: #585b70;
    --overlay0: #6c7086;
    --overlay1: #7f849c;
    --text:     #cdd6f4;
    --subtext1: #bac2de;
    --subtext0: #a6adc8;
    --lavender: #b4befe;
    --blue:     #89b4fa;
    --sapphire: #74c7ec;
    --sky:      #89dceb;
    --teal:     #94e2d5;
    --green:    #a6e3a1;
    --yellow:   #f9e2af;
    --peach:    #fab387;
    --maroon:   #eba0ac;
    --red:      #f38ba8;
    --mauve:    #cba6f7;
    --pink:     #f5c2e7;
    --flamingo: #f2cdcd;
    --rosewater:#f5e0dc;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; font-family: 'JetBrains Mono', 'Fira Mono', 'Cascadia Code', monospace; background: var(--base); color: var(--text); font-size: 14px; }

  /* ---- Layout ---- */
  #app { display: flex; flex-direction: column; height: 100vh; }
  #header {
    background: var(--mantle);
    border-bottom: 1px solid var(--surface0);
    padding: 10px 20px;
    display: flex; align-items: center; gap: 12px;
    flex-shrink: 0;
  }
  #header .logo { color: var(--mauve); font-weight: bold; font-size: 16px; letter-spacing: 0.5px; }
  #header .model-badge {
    background: var(--surface0); color: var(--sapphire);
    border-radius: 4px; padding: 2px 8px; font-size: 12px;
  }
  #header .working-dir { color: var(--overlay1); font-size: 12px; flex: 1; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #header .conn-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--surface2); flex-shrink: 0; transition: background 0.3s; }
  #header .conn-dot.connected { background: var(--green); }
  #header .conn-dot.error { background: var(--red); }

  #messages {
    flex: 1; overflow-y: auto; padding: 16px 20px;
    display: flex; flex-direction: column; gap: 10px;
    scroll-behavior: smooth;
  }
  #input-area {
    flex-shrink: 0;
    background: var(--mantle);
    border-top: 1px solid var(--surface0);
    padding: 12px 20px;
    position: relative;
  }
  #input-row { display: flex; gap: 8px; align-items: flex-end; }
  #input-box {
    flex: 1; background: var(--surface0); color: var(--text);
    border: 1px solid var(--surface1); border-radius: 6px;
    padding: 10px 12px; font-family: inherit; font-size: 14px;
    resize: none; outline: none; min-height: 44px; max-height: 200px;
    line-height: 1.5;
    transition: border-color 0.15s;
  }
  #input-box:focus { border-color: var(--mauve); }
  #send-btn {
    background: var(--mauve); color: var(--crust);
    border: none; border-radius: 6px; padding: 10px 16px;
    cursor: pointer; font-weight: bold; font-size: 14px;
    transition: background 0.15s; flex-shrink: 0; height: 44px;
  }
  #send-btn:hover { background: var(--lavender); }
  #send-btn:disabled { background: var(--surface2); cursor: not-allowed; }

  /* ---- Autocomplete dropdown ---- */
  #autocomplete {
    position: absolute; bottom: calc(100% + 4px); left: 20px; right: 20px;
    background: var(--surface0); border: 1px solid var(--surface1);
    border-radius: 6px; max-height: 200px; overflow-y: auto;
    display: none; z-index: 100;
    box-shadow: 0 -4px 12px rgba(0,0,0,0.4);
  }
  #autocomplete.visible { display: block; }
  .ac-item {
    padding: 7px 12px; cursor: pointer; display: flex;
    gap: 10px; align-items: center;
    border-bottom: 1px solid var(--surface1);
  }
  .ac-item:last-child { border-bottom: none; }
  .ac-item:hover, .ac-item.selected { background: var(--surface1); }
  .ac-cmd { color: var(--sapphire); font-weight: bold; min-width: 140px; }
  .ac-desc { color: var(--overlay1); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* ---- Hint bar ---- */
  #hint { color: var(--overlay0); font-size: 11px; margin-top: 6px; }

  /* ---- Message bubbles ---- */
  .msg { max-width: 100%; }

  .msg-user { display: flex; justify-content: flex-end; }
  .msg-user .bubble {
    background: var(--surface0); color: var(--text);
    border-radius: 8px 8px 2px 8px; padding: 10px 14px;
    max-width: 70%; white-space: pre-wrap; border: 1px solid var(--surface1);
  }

  .msg-agent .bubble {
    background: var(--mantle); border: 1px solid var(--mauve);
    border-radius: 2px 8px 8px 8px; padding: 12px 16px;
    max-width: 100%;
  }
  .msg-agent .bubble-header { color: var(--mauve); font-size: 11px; margin-bottom: 8px; font-weight: bold; letter-spacing: 0.3px; }

  .msg-status { color: var(--overlay1); font-size: 12px; text-align: center; font-style: italic; }
  .activity-line { color: var(--overlay1); font-size: 12px; font-style: italic; padding: 2px 0; opacity: 0.7; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .activity-code { font-style: normal; font-family: var(--font-mono, monospace); color: var(--blue); }
  .msg-error { color: var(--red); padding: 6px 0; }
  .msg-warning { color: var(--yellow); padding: 6px 0; }
  .msg-success { color: var(--green); padding: 6px 0; }
  .msg-info { color: var(--subtext1); padding: 4px 0; }

  /* ---- Thinking indicator ---- */
  .thinking {
    display: flex; align-items: center; gap: 8px;
    color: var(--subtext1); font-size: 13px; font-style: italic;
    padding: 4px 0;
  }
  .thinking-dots span {
    display: inline-block; width: 6px; height: 6px;
    border-radius: 50%; background: var(--mauve);
    animation: bounce 1.2s infinite;
    margin: 0 2px;
  }
  .thinking-dots span:nth-child(2) { animation-delay: 0.2s; }
  .thinking-dots span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce { 0%,80%,100%{transform:scale(0.8);opacity:0.5} 40%{transform:scale(1.2);opacity:1} }

  /* ---- Markdown rendering ---- */
  .md h1, .md h2, .md h3 { color: var(--lavender); margin: 12px 0 6px; }
  .md h1 { font-size: 1.3em; border-bottom: 1px solid var(--surface1); padding-bottom: 4px; }
  .md h2 { font-size: 1.15em; }
  .md h3 { font-size: 1em; color: var(--sapphire); }
  .md p { margin: 6px 0; line-height: 1.6; }
  .md ul, .md ol { margin: 6px 0 6px 20px; }
  .md li { margin: 3px 0; line-height: 1.5; }
  .md code { background: var(--surface0); color: var(--peach); padding: 1px 5px; border-radius: 3px; font-size: 0.9em; font-family: inherit; }
  .md pre { background: var(--surface0); border: 1px solid var(--surface1); border-radius: 6px; padding: 12px; margin: 8px 0; overflow-x: auto; }
  .md pre code { background: none; padding: 0; color: inherit; }
  .md blockquote { border-left: 3px solid var(--mauve); padding-left: 12px; color: var(--subtext1); margin: 8px 0; }
  .md table { border-collapse: collapse; width: 100%; margin: 8px 0; }
  .md th { background: var(--surface0); color: var(--lavender); padding: 6px 10px; border: 1px solid var(--surface1); }
  .md td { padding: 5px 10px; border: 1px solid var(--surface1); }
  .md tr:nth-child(even) td { background: var(--mantle); }
  .md a { color: var(--blue); text-decoration: none; }
  .md a:hover { text-decoration: underline; }
  .md hr { border: none; border-top: 1px solid var(--surface1); margin: 12px 0; }

  /* ---- Code execution panel ---- */
  .code-exec {
    border: 1px solid var(--blue); border-radius: 6px;
    overflow: hidden; margin: 2px 0;
  }
  .code-exec-header {
    background: var(--surface0); color: var(--blue);
    padding: 6px 12px; font-size: 12px; font-weight: bold;
    display: flex; align-items: center; gap: 8px; cursor: pointer;
    user-select: none;
  }
  .code-exec-header:hover { background: var(--surface1); }
  .code-exec-chevron { transition: transform 0.2s; }
  .code-exec-body { display: none; padding: 0; }
  .code-exec-body.open { display: block; }
  .code-exec-reasoning { color: var(--subtext1); font-style: italic; padding: 8px 12px; border-bottom: 1px solid var(--surface1); font-size: 13px; white-space: pre-wrap; }
  .code-exec-code { background: var(--crust); }
  .code-exec-code pre { margin: 0; border-radius: 0; border: none; border-bottom: 1px solid var(--surface1); }
  .code-exec-output { padding: 8px 12px; background: var(--mantle); font-size: 13px; }
  .exec-stdout { color: var(--text); white-space: pre-wrap; }
  .exec-stderr { color: var(--peach); white-space: pre-wrap; }
  .exec-error  { color: var(--red);   white-space: pre-wrap; font-weight: bold; }
  .exec-value  { color: var(--green); white-space: pre-wrap; }

  /* ---- Table output ---- */
  .output-table { width: 100%; border-collapse: collapse; margin: 4px 0; }
  .output-table caption { color: var(--lavender); font-weight: bold; text-align: left; margin-bottom: 6px; font-size: 13px; }
  .output-table th { background: var(--surface0); color: var(--lavender); padding: 6px 10px; border: 1px solid var(--surface1); font-size: 12px; text-align: left; }
  .output-table td { padding: 5px 10px; border: 1px solid var(--surface1); font-size: 12px; }
  .output-table tr:nth-child(even) td { background: var(--mantle); }
  .table-footer { color: var(--overlay1); font-size: 11px; margin-top: 4px; }

  /* ---- Help grid ---- */
  .help-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 6px; }
  .help-item { background: var(--surface0); border-radius: 4px; padding: 6px 10px; }
  .help-cmd { color: var(--sapphire); font-weight: bold; font-size: 13px; }
  .help-desc { color: var(--subtext1); font-size: 11px; margin-top: 2px; }

  /* ---- Bash output ---- */
  .bash-output { background: var(--crust); border: 1px solid var(--surface1); border-radius: 4px; padding: 8px 12px; font-size: 13px; }
  .bash-stdout { color: var(--text); white-space: pre-wrap; }
  .bash-stderr { color: var(--peach); white-space: pre-wrap; }

  /* ---- Rich output wrapper ---- */
  .rich-wrapper { border: 1px solid var(--surface1); border-radius: 6px; overflow: hidden; }
  .rich-header { background: var(--surface0); color: var(--subtext1); padding: 5px 10px; font-size: 11px; font-weight: bold; }
  .rich-body { padding: 0; }
  .rich-plotly { min-height: 400px; }
  .rich-html { padding: 10px; }
  .rich-image img { max-width: 100%; display: block; }
  .rich-json pre { background: var(--crust); padding: 12px; font-size: 12px; overflow-x: auto; }
  .rich-df {}

  /* ---- Monaco editor overlay ---- */
  #editor-overlay {
    display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: var(--base); z-index: 1000; flex-direction: column;
  }
  #editor-overlay.visible { display: flex; }
  #editor-toolbar {
    background: var(--mantle); border-bottom: 1px solid var(--surface0);
    padding: 8px 16px; display: flex; align-items: center; gap: 12px;
    flex-shrink: 0;
  }
  #editor-filename { color: var(--text); font-weight: bold; flex: 1; font-size: 13px; }
  #editor-hint { color: var(--overlay1); font-size: 11px; }
  .editor-btn {
    border: none; border-radius: 4px; padding: 6px 14px;
    cursor: pointer; font-size: 13px; font-family: inherit; font-weight: bold;
  }
  #editor-save-btn { background: var(--green); color: var(--crust); }
  #editor-save-btn:hover { background: var(--teal); }
  #editor-cancel-btn { background: var(--surface1); color: var(--text); }
  #editor-cancel-btn:hover { background: var(--surface2); }
  #monaco-container { flex: 1; }

  /* ---- Diff output ---- */
  .diff-block { border: 1px solid var(--surface1); border-radius: 6px; overflow: hidden; margin: 2px 0; }
  .diff-header { background: var(--surface0); color: var(--subtext1); padding: 5px 10px; font-size: 11px; font-weight: bold; }
  .diff-body { background: var(--crust); overflow-x: auto; }
  .diff-body pre { margin: 0; padding: 10px 12px; font-size: 12px; }
  .diff-add { color: var(--green); }
  .diff-del { color: var(--red); }
  .diff-hunk { color: var(--sapphire); }

  /* ---- History replay ---- */
  .history-replay { border: 1px solid var(--surface1); border-radius: 6px; overflow: hidden; margin: 4px 0; }
  .history-header { background: var(--surface0); color: var(--overlay1); padding: 5px 10px; font-size: 11px; font-style: italic; }
  .history-turn { padding: 8px 12px; border-top: 1px solid var(--surface1); }
  .history-turn-user { background: var(--mantle); }
  .history-turn-agent { background: var(--base); }
  .history-turn-label { font-size: 10px; color: var(--overlay0); margin-bottom: 4px; font-weight: bold; }
  .history-turn-content { color: var(--subtext1); font-size: 13px; }

  /* ---- Startup banner ---- */
  .startup-banner {
    background: var(--mantle); border: 1px solid var(--surface2);
    border-radius: 6px; padding: 12px 16px;
  }
  .startup-banner .banner-title { color: var(--mauve); font-weight: bold; margin-bottom: 8px; font-size: 13px; }
  .startup-row { display: flex; gap: 16px; margin: 3px 0; font-size: 12px; }
  .startup-key { color: var(--sapphire); font-weight: bold; min-width: 100px; }
  .startup-val { color: var(--subtext1); }

  /* ---- Scrollbar ---- */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: var(--mantle); }
  ::-webkit-scrollbar-thumb { background: var(--surface2); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--overlay0); }
</style>
</head>
<body>
<div id="app">
  <div id="header">
    <span class="logo">NeMo OO Agents</span>
    <span class="model-badge" id="model-badge">connecting…</span>
    <span class="working-dir" id="working-dir"></span>
    <div class="conn-dot" id="conn-dot"></div>
  </div>

  <div id="messages"></div>

  <div id="input-area">
    <div id="autocomplete"></div>
    <div id="input-row">
      <textarea id="input-box" rows="1" placeholder="Message NeMo OO Agents… (Enter to send, Shift+Enter for newline)"></textarea>
      <button id="send-btn">Send</button>
    </div>
    <div id="hint">/ for commands &nbsp;·&nbsp; Shift+Enter for newline &nbsp;·&nbsp; Enter to send</div>
  </div>
</div>

<div id="editor-overlay">
  <div id="editor-toolbar">
    <span id="editor-filename">untitled</span>
    <span id="editor-hint">Ctrl+S to save &nbsp;·&nbsp; Esc to cancel</span>
    <button class="editor-btn" id="editor-cancel-btn" onclick="cancelEditor()">Cancel</button>
    <button class="editor-btn" id="editor-save-btn" onclick="saveEditor()">Save</button>
  </div>
  <div id="monaco-container"></div>
</div>

<script>
// ============================================================
// Minimal inline Markdown renderer (no external lib required)
// ============================================================
const md = (() => {
  function escape(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
  function inlineCode(s) {
    return s.replace(/`([^`]+)`/g, (_,c) => `<code>${escape(c)}</code>`);
  }
  function inlineFormat(s) {
    s = inlineCode(s);
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
    return s;
  }
  function render(text) {
    const lines = text.split('\n');
    let out = '';
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      // Fenced code block
      if (line.startsWith('```')) {
        const lang = line.slice(3).trim();
        let code = '';
        i++;
        while (i < lines.length && !lines[i].startsWith('```')) {
          code += lines[i] + '\n';
          i++;
        }
        i++;
        const highlighted = lang ? (hljs.getLanguage(lang) ? hljs.highlight(code.trimEnd(), {language:lang}).value : escape(code.trimEnd())) : escape(code.trimEnd());
        out += `<pre><code class="hljs language-${lang}">${highlighted}</code></pre>`;
        continue;
      }
      // Heading
      const hm = line.match(/^(#{1,3})\s+(.+)/);
      if (hm) { out += `<h${hm[1].length}>${inlineFormat(escape(hm[2]))}</h${hm[1].length}>`; i++; continue; }
      // Blockquote
      if (line.startsWith('> ')) { out += `<blockquote>${inlineFormat(escape(line.slice(2)))}</blockquote>`; i++; continue; }
      // HR
      if (/^[-*]{3,}$/.test(line.trim())) { out += '<hr>'; i++; continue; }
      // Table (simple GFM)
      if (line.includes('|') && i+1 < lines.length && lines[i+1].match(/^\|?[-| :]+\|?$/)) {
        const headers = line.split('|').map(s=>s.trim()).filter(Boolean);
        i += 2; // skip separator
        let rows = [];
        while (i < lines.length && lines[i].includes('|')) {
          rows.push(lines[i].split('|').map(s=>s.trim()).filter(Boolean));
          i++;
        }
        out += '<table class="md"><tr>' + headers.map(h=>`<th>${inlineFormat(escape(h))}</th>`).join('') + '</tr>';
        rows.forEach(r => { out += '<tr>' + r.map(c=>`<td>${inlineFormat(escape(c))}</td>`).join('') + '</tr>'; });
        out += '</table>';
        continue;
      }
      // Unordered list
      if (/^[-*+]\s/.test(line)) {
        out += '<ul>';
        while (i < lines.length && /^[-*+]\s/.test(lines[i])) {
          out += `<li>${inlineFormat(escape(lines[i].slice(2)))}</li>`;
          i++;
        }
        out += '</ul>';
        continue;
      }
      // Ordered list
      if (/^\d+\.\s/.test(line)) {
        out += '<ol>';
        while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
          out += `<li>${inlineFormat(escape(lines[i].replace(/^\d+\.\s/,'')))}</li>`;
          i++;
        }
        out += '</ol>';
        continue;
      }
      // Blank line
      if (!line.trim()) { out += ''; i++; continue; }
      // Paragraph
      out += `<p>${inlineFormat(escape(line))}</p>`;
      i++;
    }
    return out;
  }
  return { render };
})();

// ============================================================
// App state
// ============================================================
const msgs = document.getElementById('messages');
const inputBox = document.getElementById('input-box');
const sendBtn = document.getElementById('send-btn');
const autocompleteEl = document.getElementById('autocomplete');
const connDot = document.getElementById('conn-dot');
const modelBadge = document.getElementById('model-badge');
const workingDirEl = document.getElementById('working-dir');

let ws = null;
let thinkingEl = null;
let reconnectDelay = 1000;
let pendingPromptResolve = null;
let completions = [];  // slash command completions (fallback for prompt_request overrides)
let _acDebounce = null;  // debounce timer for server-side completions
let acItems = [];
let acIndex = -1;

// ============================================================
// WebSocket connection
// ============================================================
function connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${location.host}/ws`);
  ws.onopen = () => {
    connDot.className = 'conn-dot connected';
    reconnectDelay = 1000;
  };
  ws.onclose = () => {
    connDot.className = 'conn-dot error';
    addStatus('Disconnected. Reconnecting…');
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 15000);
  };
  ws.onerror = () => { connDot.className = 'conn-dot error'; };
  ws.onmessage = (e) => {
    try { handle(JSON.parse(e.data)); }
    catch(err) { console.error('WS parse error', err, e.data); }
  };
}
connect();

// ============================================================
// Message handlers
// ============================================================
function handle(msg) {
  switch (msg.type) {
    case 'startup':       renderStartup(msg); break;
    case 'text':          renderText(msg); break;
    case 'agent_message': renderAgentMessage(msg); break;
    case 'table':         renderTable(msg); break;
    case 'help':          renderHelp(msg); break;
    case 'code_execution':renderCodeExecution(msg); break;
    case 'bash_output':   renderBashOutput(msg); break;
    case 'rich':          renderRich(msg); break;
    case 'clear':         msgs.innerHTML = ''; break;
    case 'thinking_start': showThinking(msg.message); break;
    case 'thinking_stop':  hideThinking(); break;
    case 'prompt_request': handlePromptRequest(msg); break;
    case 'editor_open':   openEditor(msg); break;
    case 'diff':          renderDiff(msg); break;
    case 'history_replay': renderHistoryReplay(msg); break;
    case 'activity':      renderActivity(msg); break;
    case 'completion_response': renderCompletionResponse(msg.items); break;
  }
}

function scrollToBottom() {
  msgs.scrollTop = msgs.scrollHeight;
}

function addEl(el) { msgs.appendChild(el); scrollToBottom(); return el; }
function addStatus(text) {
  const d = document.createElement('div');
  d.className = 'msg msg-status';
  d.textContent = text;
  addEl(d);
}

// ---- Startup banner ----
function renderStartup(msg) {
  modelBadge.textContent = msg.short_model || msg.model;
  workingDirEl.textContent = msg.working_dir || '';

  const d = document.createElement('div');
  d.className = 'msg startup-banner';
  let rows = [
    ['model', `<span style="color:var(--green);font-weight:bold">${escH(msg.short_model)}</span> <span style="color:var(--overlay1)">(${escH(msg.model)})</span>`],
    ['working dir', escH(msg.working_dir || '')],
  ];
  if (msg.history_policy) rows.push(['history', `${escH(msg.history_policy)}  limit ${(msg.history_limit||0).toLocaleString()} tokens`]);
  if (msg.sandbox_available !== null && msg.sandbox_available !== undefined) {
    rows.push(['sandbox', msg.sandbox_available ? `<span style="color:var(--green)">available</span>` : `<span style="color:var(--yellow)">not available</span>`]);
  }
  if (msg.tracing_enabled) rows.push(['tracing', `<span style="color:var(--peach)">${escH(msg.trace_dir || 'OTLP auto-probe')}</span>`]);
  if (msg.custom_agent) rows.push(['agent', `<span style="color:var(--green)">${escH(msg.custom_agent)}</span>`]);
  rows.push(['commands', '<span style="color:var(--sapphire)">/help</span> for all &nbsp;·&nbsp; <span style="color:var(--sapphire)">/exit</span> to quit']);

  d.innerHTML = `<div class="banner-title">NeMo OO Agents ready</div>` +
    rows.map(([k,v]) => `<div class="startup-row"><span class="startup-key">${k}</span><span class="startup-val">${v}</span></div>`).join('');
  addEl(d);
}

// ---- Text messages ----
function renderText(msg) {
  const d = document.createElement('div');
  const lvl = msg.level || 'info';
  d.className = `msg msg-${lvl}`;
  // allow Rich markup in content (strip it gracefully)
  d.innerHTML = simpleRichStrip(msg.content || '');
  addEl(d);
}

function simpleRichStrip(s) {
  // strip Rich [color]...[/color] markup to plain text, preserve newlines
  return escH(s.replace(/\[[^\]]*\]/g, '')).replace(/\n/g,'<br>');
}

// ---- Agent message (markdown) ----
function renderAgentMessage(msg) {
  hideThinking();
  const d = document.createElement('div');
  d.className = 'msg msg-agent';
  d.innerHTML = `<div class="bubble"><div class="bubble-header">NeMo OO Agents</div><div class="md">${md.render(msg.content || '')}</div></div>`;
  addEl(d);
  d.querySelectorAll('pre code').forEach(el => { if (!el.dataset.highlighted) hljs.highlightElement(el); });
}

// ---- Table ----
function renderTable(msg) {
  const d = document.createElement('div');
  d.className = 'msg';
  let html = `<table class="output-table">`;
  if (msg.title) html = `<div style="color:var(--lavender);font-weight:bold;font-size:13px;margin-bottom:4px">${escH(msg.title)}</div>` + html;
  html += `<thead><tr>${(msg.columns||[]).map(c=>`<th>${escH(c)}</th>`).join('')}</tr></thead><tbody>`;
  (msg.rows||[]).forEach(row => { html += `<tr>${row.map(c=>`<td>${escH(String(c))}</td>`).join('')}</tr>`; });
  html += `</tbody></table>`;
  if (msg.footer) html += `<div class="table-footer">${escH(msg.footer)}</div>`;
  d.innerHTML = html;
  addEl(d);
}

// ---- Help ----
function renderHelp(msg) {
  const d = document.createElement('div');
  d.className = 'msg';
  const items = Object.entries(msg.commands || {});
  d.innerHTML = `<div style="color:var(--mauve);font-weight:bold;margin-bottom:8px">Available Commands</div>` +
    `<div class="help-grid">` +
    items.map(([cmd,desc]) => `<div class="help-item"><div class="help-cmd">${escH(cmd)}</div><div class="help-desc">${escH(desc)}</div></div>`).join('') +
    `</div>`;
  addEl(d);
  // populate autocomplete list from help
  completions = items.map(([cmd]) => cmd.split('<')[0].trim()).filter(Boolean);
}

// ---- Code execution ----
function renderCodeExecution(msg) {
  hideThinking();
  const d = document.createElement('div');
  d.className = 'msg';

  const hasOutput = msg.stdout || msg.stderr || msg.error || msg.value;
  let bodyHtml = '';

  if (msg.reasoning && msg.reasoning.length) {
    bodyHtml += `<div class="code-exec-reasoning">${escH(msg.reasoning.join('\n'))}</div>`;
  }
  if (msg.code) {
    const highlighted = hljs.highlight(msg.code.trim(), {language:'python'}).value;
    bodyHtml += `<div class="code-exec-code"><pre><code class="hljs language-python">${highlighted}</code></pre></div>`;
  }
  if (hasOutput) {
    let outHtml = '<div class="code-exec-output">';
    if (msg.stdout) outHtml += `<div class="exec-stdout">${escH(msg.stdout)}</div>`;
    if (msg.stderr) outHtml += `<div class="exec-stderr">${escH(msg.stderr)}</div>`;
    if (msg.error) outHtml += `<div class="exec-error">${escH(msg.error)}</div>`;
    if (msg.value) outHtml += `<div class="exec-value">=> ${escH(msg.value)}</div>`;
    outHtml += '</div>';
    bodyHtml += outHtml;
  }

  d.innerHTML = `<div class="code-exec">
    <div class="code-exec-header" onclick="toggleExec(this)">
      <span class="code-exec-chevron">▼</span>
      <span style="color:var(--blue)">Python</span>
      ${msg.error ? '<span style="color:var(--red);margin-left:auto">error</span>' : ''}
    </div>
    <div class="code-exec-body open">${bodyHtml}</div>
  </div>`;
  addEl(d);
}

function toggleExec(header) {
  const body = header.nextElementSibling;
  const chevron = header.querySelector('.code-exec-chevron');
  body.classList.toggle('open');
  chevron.style.transform = body.classList.contains('open') ? '' : 'rotate(-90deg)';
}

// ---- Activity line (python=off preview) ----
function renderActivity(msg) {
  // Replace or create a single ephemeral activity line (like a status bar)
  let el = document.getElementById('activity-line');
  if (!el) {
    el = document.createElement('div');
    el.id = 'activity-line';
    el.className = 'msg activity-line';
    msgs.appendChild(el);
  }
  const kind = msg.kind || 'reasoning';
  el.className = `msg activity-line activity-${kind}`;
  el.textContent = msg.content || '';
  scrollToBottom();
}

// ---- Bash output ----
function renderBashOutput(msg) {
  if (!msg.stdout && !msg.stderr) return;
  const d = document.createElement('div');
  d.className = 'msg';
  let html = '<div class="bash-output">';
  if (msg.stdout) html += `<div class="bash-stdout">${escH(msg.stdout)}</div>`;
  if (msg.stderr) html += `<div class="bash-stderr">${escH(msg.stderr)}</div>`;
  html += '</div>';
  d.innerHTML = html;
  addEl(d);
}

// ---- Rich / generic visual output ----
function renderRich(msg) {
  hideThinking();
  const d = document.createElement('div');
  d.className = 'msg';
  const title = msg.title || msg.kind;

  const wrapper = document.createElement('div');
  wrapper.className = 'rich-wrapper';
  wrapper.innerHTML = `<div class="rich-header">${escH(title)}</div>`;

  const body = document.createElement('div');
  body.className = 'rich-body';

  switch (msg.kind) {
    case 'plotly': {
      const plotDiv = document.createElement('div');
      plotDiv.className = 'rich-plotly';
      body.appendChild(plotDiv);
      wrapper.appendChild(body);
      d.appendChild(wrapper);
      addEl(d);
      // render after DOM insertion
      try {
        const fig = JSON.parse(msg.data.figure_json);
        Plotly.react(plotDiv, fig.data, Object.assign({}, fig.layout, {
          paper_bgcolor: 'var(--mantle)',
          plot_bgcolor:  'var(--base)',
          font: { color: 'var(--text)', family: 'monospace' },
        }), { responsive: true });
      } catch(e) { plotDiv.textContent = `Plotly error: ${e.message}`; }
      return;
    }
    case 'vega': {
      const vegaDiv = document.createElement('div');
      body.appendChild(vegaDiv);
      wrapper.appendChild(body);
      d.appendChild(wrapper);
      addEl(d);
      try {
        vegaEmbed(vegaDiv, msg.data.spec, {
          theme: 'dark',
          actions: true,
        }).catch(e => { vegaDiv.textContent = `Vega error: ${e.message}`; });
      } catch(e) { vegaDiv.textContent = `Vega error: ${e.message}`; }
      return;
    }
    case 'html': {
      const wrap = document.createElement('div');
      wrap.className = 'rich-html';
      // NOTE: sanitize in production; here we trust agent-generated output
      wrap.innerHTML = msg.data.html || '';
      body.appendChild(wrap);
      break;
    }
    case 'image': {
      const wrap = document.createElement('div');
      wrap.className = 'rich-image';
      const img = document.createElement('img');
      img.src = msg.data.src || '';
      img.alt = msg.data.alt || title;
      wrap.appendChild(img);
      body.appendChild(wrap);
      break;
    }
    case 'dataframe': {
      const cols = msg.data.columns || [];
      const rows = msg.data.rows || [];
      let html = `<table class="output-table rich-df"><thead><tr>${cols.map(c=>`<th>${escH(c)}</th>`).join('')}</tr></thead><tbody>`;
      rows.forEach(r => { html += `<tr>${r.map(c=>`<td>${escH(String(c))}</td>`).join('')}</tr>`; });
      html += '</tbody></table>';
      const wrap = document.createElement('div');
      wrap.innerHTML = html;
      body.appendChild(wrap);
      break;
    }
    default: {
      // Unknown kind — show as JSON code block
      const pre = document.createElement('pre');
      pre.className = 'rich-json';
      const highlighted = hljs.highlight(JSON.stringify(msg.data, null, 2), {language:'json'}).value;
      pre.innerHTML = `<code class="hljs language-json">${highlighted}</code>`;
      body.appendChild(pre);
    }
  }
  wrapper.appendChild(body);
  d.appendChild(wrapper);
  addEl(d);
}

// ---- Thinking indicator ----
function showThinking(message) {
  if (thinkingEl) return;
  thinkingEl = document.createElement('div');
  thinkingEl.className = 'msg thinking';
  thinkingEl.innerHTML = `<div class="thinking-dots"><span></span><span></span><span></span></div><span>${escH(message || 'Thinking…')}</span>`;
  addEl(thinkingEl);
}
function hideThinking() {
  if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
  // Also remove ephemeral activity line
  const act = document.getElementById('activity-line');
  if (act) act.remove();
}

// ---- Prompt request (interactive get_input) ----
function handlePromptRequest(msg) {
  // Show as a status and re-enable input with temporary submit handler
  addStatus(msg.text || 'Input required:');
  if (msg.completions && msg.completions.length) {
    completions = msg.completions;
    _useClientCompletions = true;
  } else {
    _useClientCompletions = false;
  }
  pendingPromptResolve = (value) => {
    ws.send(JSON.stringify({ type: 'user_input', text: value }));
    pendingPromptResolve = null;
  };
  inputBox.focus();
}

// ============================================================
// User input
// ============================================================
function sendMessage() {
  const text = inputBox.value.trim();
  if (!text) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) { addStatus('Not connected — please wait…'); return; }

  hideAutocomplete();

  // Always echo user message in the chat
  const d = document.createElement('div');
  d.className = 'msg msg-user';
  d.innerHTML = `<div class="bubble">${escH(text).replace(/\n/g,'<br>')}</div>`;
  addEl(d);

  if (pendingPromptResolve) {
    pendingPromptResolve(text);
    inputBox.value = '';
    resizeInput();
    _useClientCompletions = false;
    return;
  }

  ws.send(JSON.stringify({ type: 'user_input', text }));
  inputBox.value = '';
  resizeInput();
  sendBtn.disabled = true;
}

sendBtn.addEventListener('click', sendMessage);

inputBox.addEventListener('keydown', (e) => {
  const acVisible = autocompleteEl.classList.contains('visible');

  // Tab: cycle through completions (same as TUI)
  if (e.key === 'Tab') {
    e.preventDefault();
    if (!acVisible) {
      // Open completions if we're in a / or ! command
      updateAutocomplete();
      return;
    }
    if (acItems.length === 0) return;
    // Cycle: advance to next item (wrap around)
    const next = (acIndex + 1) % acItems.length;
    acMove(next - acIndex);
    // Preview the selection in the input box (don't confirm yet)
    inputBox.value = acItems[acIndex];
    return;
  }

  if (acVisible) {
    if (e.key === 'ArrowDown') { e.preventDefault(); acMove(1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); acMove(-1); return; }
    if (e.key === 'Enter') {
      if (acIndex >= 0 && acIndex < acItems.length) {
        e.preventDefault();
        selectAc(acIndex);
        return;
      }
    }
    if (e.key === 'Escape') { hideAutocomplete(); return; }
  }
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

inputBox.addEventListener('input', () => {
  resizeInput();
  sendBtn.disabled = false;
  updateAutocomplete();
});

function resizeInput() {
  inputBox.style.height = 'auto';
  inputBox.style.height = Math.min(inputBox.scrollHeight, 200) + 'px';
}

// ============================================================
// Slash-command autocomplete (server-side completions)
// ============================================================
let _useClientCompletions = false;  // true when prompt_request provides temporary completions

// Patterns that need server-side data (filesystem, session DB)
const _needsServer = ['/edit ', '/session resume ', '/session delete ', '!'];

function updateAutocomplete() {
  const val = inputBox.value;

  // Client-side filtering for temporary completions (e.g. model selection from /switch)
  if (_useClientCompletions && completions.length) {
    const filtered = completions.filter(c => c.toLowerCase().startsWith(val.toLowerCase()));
    if (!filtered.length) { hideAutocomplete(); return; }
    renderCompletionResponse(filtered.map(c => ({ text: c, display: c, description: '' })));
    return;
  }

  if (!val.startsWith('/') && !val.startsWith('!')) { hideAutocomplete(); return; }

  // Round-trip to server only when we need data the client doesn't have
  const lower = val.toLowerCase();
  if (_needsServer.some(p => lower.startsWith(p.toLowerCase()))) {
    clearTimeout(_acDebounce);
    _acDebounce = setTimeout(() => {
      if (ws && ws.readyState === 1) {
        ws.send(JSON.stringify({ type: 'completion_request', text: val }));
      }
    }, 80);
    return;
  }

  // Client-side filtering for slash commands (fast, no round-trip)
  const filtered = completions.filter(c => c.toLowerCase().startsWith(lower));
  if (!filtered.length) { hideAutocomplete(); return; }
  renderCompletionResponse(filtered.map(c => ({ text: c, display: c, description: '' })));
}

function renderCompletionResponse(items) {
  if (!items || !items.length) { hideAutocomplete(); return; }
  // items: [{text, display, description}, ...]
  acItems = items.map(it => it.text);
  acIndex = -1;
  autocompleteEl.innerHTML = items.map((it,i) =>
    `<div class="ac-item" data-i="${i}"><span class="ac-cmd">${escH(it.display)}</span>${it.description ? `<span class="ac-desc">${escH(it.description)}</span>` : ''}</div>`
  ).join('');
  autocompleteEl.querySelectorAll('.ac-item').forEach((el,i) => {
    el.addEventListener('click', () => selectAc(i));
  });
  autocompleteEl.classList.add('visible');
}

function hideAutocomplete() {
  autocompleteEl.classList.remove('visible');
  acItems = [];
  acIndex = -1;
}

function acMove(dir) {
  acIndex = Math.max(-1, Math.min(acItems.length-1, acIndex + dir));
  autocompleteEl.querySelectorAll('.ac-item').forEach((el,i) => {
    el.classList.toggle('selected', i === acIndex);
  });
}

function selectAc(i) {
  const selected = acItems[i];
  // If the completion already ends with / (directory), don't add space
  inputBox.value = selected.endsWith('/') ? selected : selected + ' ';
  hideAutocomplete();
  inputBox.focus();
  // Auto-trigger sub-completions (e.g. /edit → file paths, /session resume → IDs)
  setTimeout(() => updateAutocomplete(), 50);
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('#input-area')) hideAutocomplete();
});

// ============================================================
// Utils
// ============================================================
function escH(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ============================================================
// Monaco editor overlay
// ============================================================
let monacoEditor = null;
let monacoReady = false;
const editorOverlay = document.getElementById('editor-overlay');

function loadMonaco(cb) {
  if (monacoReady) { cb(); return; }
  require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.48.0/min/vs' } });
  require(['vs/editor/editor.main'], () => { monacoReady = true; cb(); });
}

function openEditor(msg) {
  document.getElementById('editor-filename').textContent = msg.filename || 'untitled';
  loadMonaco(() => {
    editorOverlay.classList.add('visible');
    const lang = msg.language || 'plaintext';
    if (!monacoEditor) {
      monaco.editor.defineTheme('catppuccin', {
        base: 'vs-dark',
        inherit: true,
        rules: [
          { token: '', foreground: 'cdd6f4', background: '1e1e2e' },
          { token: 'comment', foreground: '6c7086', fontStyle: 'italic' },
          { token: 'keyword', foreground: 'cba6f7' },
          { token: 'string', foreground: 'a6e3a1' },
          { token: 'number', foreground: 'fab387' },
        ],
        colors: {
          'editor.background': '#1e1e2e',
          'editor.foreground': '#cdd6f4',
          'editorLineNumber.foreground': '#6c7086',
          'editorCursor.foreground': '#cba6f7',
          'editor.selectionBackground': '#45475a',
          'editor.lineHighlightBackground': '#181825',
          'editorWidget.background': '#181825',
          'input.background': '#313244',
          'input.foreground': '#cdd6f4',
        },
      });
      monacoEditor = monaco.editor.create(document.getElementById('monaco-container'), {
        theme: 'catppuccin',
        fontSize: 14,
        fontFamily: "'JetBrains Mono', 'Fira Mono', 'Cascadia Code', monospace",
        minimap: { enabled: true },
        scrollBeyondLastLine: false,
        renderLineHighlight: 'line',
        smoothScrolling: true,
        cursorBlinking: 'smooth',
        automaticLayout: true,
      });
      monacoEditor.addCommand(
        monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS,
        saveEditor
      );
      monacoEditor.addCommand(monaco.KeyCode.Escape, cancelEditor);
    }
    const model = monaco.editor.createModel(msg.content || '', lang);
    monacoEditor.setModel(model);
    monacoEditor.focus();
  });
}

function saveEditor() {
  if (!monacoEditor || !ws || ws.readyState !== WebSocket.OPEN) return;
  const content = monacoEditor.getValue();
  ws.send(JSON.stringify({ type: 'editor_save', content }));
  editorOverlay.classList.remove('visible');
}

function cancelEditor() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: 'editor_cancel' }));
  editorOverlay.classList.remove('visible');
}

// ============================================================
// Diff output
// ============================================================
function renderDiff(msg) {
  const d = document.createElement('div');
  d.className = 'msg';
  const diff = msg.diff || '';
  const filename = msg.filename || '';
  // Simple diff coloriser (no external lib needed)
  const lines = diff.split('\n').map(line => {
    if (line.startsWith('+') && !line.startsWith('+++')) return `<span class="diff-add">${escH(line)}</span>`;
    if (line.startsWith('-') && !line.startsWith('---')) return `<span class="diff-del">${escH(line)}</span>`;
    if (line.startsWith('@@')) return `<span class="diff-hunk">${escH(line)}</span>`;
    return escH(line);
  });
  d.innerHTML = `<div class="diff-block">
    <div class="diff-header">diff — ${escH(filename)}</div>
    <div class="diff-body"><pre>${lines.join('\n')}</pre></div>
  </div>`;
  addEl(d);
}

// ============================================================
// History replay
// ============================================================
function renderHistoryReplay(msg) {
  const d = document.createElement('div');
  d.className = 'msg';
  const turns = msg.turns || [];
  let turnsHtml = turns.map(t => {
    const isUser = t.role === 'user';
    const label = isUser ? 'You' : 'NeMo OO Agents';
    const cls = isUser ? 'history-turn history-turn-user' : 'history-turn history-turn-agent';
    const content = isUser ? escH(t.content).replace(/\n/g,'<br>') : md.render(t.content || '');
    return `<div class="${cls}"><div class="history-turn-label">${label}</div><div class="history-turn-content">${content}</div></div>`;
  }).join('');
  d.innerHTML = `<div class="history-replay">
    <div class="history-header">session ${escH(msg.session_id || '')} — history</div>
    ${turnsHtml}
  </div>`;
  addEl(d);
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------


def create_app(
    model: str | None = None,
    agent_spec: str | None = None,
    continue_last: bool = False,
):
    """Create and return the FastAPI application.

    Args:
        model: LLM model identifier (overrides config default).
        agent_spec: Optional ``module:Class`` for a custom agent.
        continue_last: Resume the most recent session on first connection.
    """
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise ImportError(
            "Web frontend requires FastAPI. Install with: uv add fastapi uvicorn[standard]"
        ) from exc

    app = FastAPI(title="NeMo OO Agents Web", docs_url=None, redoc_url=None)

    # ----------------------------------------------------------------
    # Routes
    # ----------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(_HTML)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        import logging
        import traceback

        _log = logging.getLogger("nemo_oo_agents.web")

        await websocket.accept()

        from .frontend import WebFrontend

        frontend = WebFrontend(websocket)

        try:
            from ..tui.bootstrap import bootstrap, build_registry, build_session, build_startup_info

            # Per-connection config (same shared bootstrap as terminal)
            from ..tui.config import Config as _Config
            from ..tui.output import HelpOutput, HistoryReplay, HistoryTurn, TextOutput
            from ..tui.session_manager import SessionManager

            _overrides: dict = {}
            if model:
                _overrides["model"] = model
            if agent_spec:
                _overrides["agent"] = agent_spec
            cfg = _Config.load(**_overrides)

            # Client can request session resume via ?session=<id> query param
            resume_id = websocket.query_params.get("session")

            # Shared bootstrap: tracing, LLM, storage, agent, session manager
            result = await bootstrap(
                cfg,
                continue_last=continue_last if not resume_id else False,
                resume_session_id=resume_id,
            )

            # Render bootstrap messages
            for msg in result.messages:
                await frontend.render(msg)

            # Startup info
            await frontend.render(build_startup_info(result))

            # Show resumed session history
            if result.resumed and result.session_id is not None:
                turns = SessionManager.load_turns(result.session_id)
                if turns:
                    await frontend.render(
                        HistoryReplay(
                            turns=[HistoryTurn(role=t.role, content=t.content) for t in turns],
                            session_id=result.session_id[:8],
                        )
                    )
                    await frontend.render(
                        TextOutput(f"Session {result.session_id[:8]} resumed.", "status")
                    )

            # Wire frontend → registry → session
            registry = build_registry(result, frontend)

            # Attach shared completer for server-side completions
            from ..tui.completer import Completer as _Completer

            frontend._completer = _Completer(registry=registry)

            # Send initial completions to browser for autocomplete
            await frontend.render(HelpOutput(registry.get_completions()))

            session = build_session(result, frontend, registry)

            session_task = asyncio.create_task(session.run())
            try:
                await session_task
            except Exception as exc:
                _log.error("Session crashed: %s\n%s", exc, traceback.format_exc())
                try:
                    await frontend.render(TextOutput(f"Session error: {exc}", "error"))
                except Exception:
                    pass
                session_task.cancel()

        except Exception as exc:
            # Catch-all: send the error to the browser so the user sees it
            # instead of a silent disconnect.
            _log.error("WebSocket handler crashed: %s\n%s", exc, traceback.format_exc())
            try:
                await frontend._send(
                    {
                        "type": "text",
                        "content": f"Server error: {exc}",
                        "level": "error",
                    }
                )
            except Exception:
                pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    return app
