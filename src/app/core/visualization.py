"""
Generates the static HTML shell for the CMiner visualization page.

The HTML contains NO embedded data — it fetches everything it needs from
same-origin API endpoints:
  GET /api/sessions/{sid}/meta      → {num_patterns, num_models, model_names}
  GET /api/sessions/{sid}/patterns  → paginated [{id, support, frequency, nodes, edges, encoded}]
  GET /api/sessions/{sid}/models    → [{name, encoded}]
  GET /plantuml/svg/{encoded}       → SVG image

This means the HTML file size is constant regardless of pattern count.
"""


import os

_DEFAULT_BASE_URL = os.environ.get("CMINER_BASE_URL", "http://localhost:8222").rstrip("/")


def generate_html_visualization(session_id: str, base_url: str = _DEFAULT_BASE_URL, version: int = 0) -> str:
    return (
        _HTML_TEMPLATE
        .replace("__SESSION_ID__", session_id)
        .replace("__BASE_URL__", base_url)
        .replace("__VERSION__", str(version))
    )


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CMiner Visualization</title>
  <!-- v:__VERSION__ -->
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, sans-serif;
      display: flex; height: 100vh;
      background: #f0f2f5; overflow: hidden;
    }

    /* ── Sidebar & Resizer ── */
    #sidebar {
      width: 260px; min-width: 150px; max-width: 600px;
      background: #2c3e50; color: #ecf0f1;
      display: flex; flex-direction: column; flex-shrink: 0;
    }
    #sidebar h2 {
      padding: 12px 16px; background: #1f2c38;
      font-size: .85em; letter-spacing: .05em; color: #bdc3c7;
      text-transform: uppercase; border-bottom: 1px solid #1a252f;
    }

    /* ── Sidebar Tabs ── */
    #sidebar-tabs {
      display: none; background: #1a252f;
      border-bottom: 1px solid #0f171e;
    }
    #sidebar-tabs.visible { display: flex; }
    .s-tab-btn {
      flex: 1; padding: 12px 0;
      background: transparent; border: none;
      color: #95a5a6; cursor: pointer;
      font-weight: bold; font-size: 0.9em;
      border-bottom: 3px solid transparent;
      transition: all 0.2s;
    }
    .s-tab-btn:hover  { color: #ecf0f1; background: #243447; }
    .s-tab-btn.active { color: #3498db; border-bottom-color: #3498db; background: #2c3e50; }
    .sidebar-list { flex: 1; overflow-y: auto; }
    .sidebar-list::-webkit-scrollbar { width: 8px; }
    .sidebar-list::-webkit-scrollbar-track { background: #1a252f; }
    .sidebar-list::-webkit-scrollbar-thumb { background: #3d5166; border-radius: 4px; }
    .sidebar-list::-webkit-scrollbar-thumb:hover { background: #5a738e; }
    .pat-item {
      padding: 11px 16px; cursor: pointer;
      border-bottom: 1px solid #3d5166;
    }
    .pat-item:hover  { background: #34495e; }
    .pat-item.active { background: #2980b9; border-left: 4px solid #6dd5ed; padding-left: 12px; }
    .pat-title { font-weight: bold; font-size: .88em; }
    .pat-meta  { font-size: .75em; color: #95a5a6; margin-top: 3px; }

    /* ── Pagination bar ── */
    .page-bar {
      display: flex; align-items: center; justify-content: center;
      gap: 8px; padding: 8px; background: #1a252f;
      border-top: 1px solid #0f171e; flex-shrink: 0;
    }
    .page-bar button {
      padding: 4px 10px; border: 1px solid #3d5166; border-radius: 4px;
      background: #2c3e50; color: #ecf0f1; cursor: pointer; font-size: .8em;
    }
    .page-bar button:hover { background: #34495e; }
    .page-bar button:disabled { opacity: .4; cursor: default; }
    .page-bar span { font-size: .8em; color: #95a5a6; }

    #resizer {
      width: 5px;
      background: #bdc3c7;
      cursor: col-resize;
      flex-shrink: 0;
      transition: background 0.2s;
    }
    #resizer:hover, #resizer:active {
      background: #7f8c8d;
    }

    /* ── Collapse toggle ── */
    #collapse-btn {
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      left: 0;
      z-index: 20;
      width: 18px; height: 48px;
      background: #2c3e50;
      border: none;
      border-radius: 0 6px 6px 0;
      color: #ecf0f1;
      cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      font-size: .85em;
      box-shadow: 2px 0 6px rgba(0,0,0,.25);
      transition: background 0.15s;
      padding: 0;
    }
    #collapse-btn:hover { background: #34495e; }

    /* ── Main ── */
    #main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; position: relative; }

    /* ── Top Bar ── */
    #top-bar {
      display: flex; align-items: center; background: #fff;
      border-bottom: 1px solid #ddd; padding: 0 20px;
      min-height: 48px; flex-shrink: 0; gap: 16px;
    }
    #pane-title {
      font-size: 1.15em; font-weight: bold; color: #2c3e50;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    #pane-meta {
      font-size: 0.85em; color: #7f8c8d;
      display: flex; gap: 12px; white-space: nowrap;
    }
    #pane-meta b { color: #2c3e50; font-weight: 600; }

    /* ── Viewport ── */
    .viewport {
      flex: 1; position: relative; overflow: hidden;
      background: #fff; margin: 14px; border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0,0,0,.12);
      cursor: grab;
      user-select: none; -webkit-user-select: none;
    }
    .viewport.is-dragging { cursor: grabbing; }

    .canvas {
      position: absolute;
      top: 0; left: 0;
      transform-origin: 0 0;
    }

    .canvas .svg-container {
      display: block;
      opacity: 0;
      transition: opacity 0.2s ease-in-out;
    }
    .canvas .svg-container svg {
      display: block;
      pointer-events: none;
    }

    .zoom-controls {
      position: absolute; top: 10px; right: 14px;
      display: flex; flex-direction: column; gap: 4px; z-index: 10;
    }
    .zoom-btn {
      width: 28px; height: 28px; border: 1px solid #ccc; border-radius: 5px;
      background: #fff; cursor: pointer; font-size: 1.1em;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 1px 4px rgba(0,0,0,.15);
    }
    .zoom-btn:hover { background: #eaf3fb; border-color: #2980b9; color: #2980b9; }

    .zoom-hint {
      position: absolute; bottom: 10px; right: 14px;
      font-size: .72em; color: #aaa; pointer-events: none; z-index: 10;
      background: rgba(255,255,255,.8); padding: 3px 8px; border-radius: 4px;
    }

    .status-msg {
      position: absolute; inset: 0;
      display: flex; align-items: center; justify-content: center;
      color: #bbb; font-size: 1.1em; pointer-events: none; z-index: 5;
    }

    .panel { display: none; flex-direction: column; flex: 1; overflow: hidden; min-height: 0; }
    .panel.active { display: flex; }
  </style>
</head>
<body>

  <!-- Sidebar -->
  <div id="sidebar">
    <div id="sidebar-tabs">
      <button class="s-tab-btn" id="tab-models" onclick="switchTab('models')">Models</button>
      <button class="s-tab-btn" id="tab-patterns" onclick="switchTab('patterns')">Patterns</button>
    </div>
    <h2 id="sidebar-title">Loading…</h2>
    <div class="sidebar-list" id="pattern-list" style="display:none"></div>
    <div class="sidebar-list" id="model-list" style="display:none"></div>
    <div class="page-bar" id="pattern-pager" style="display:none">
      <button id="pg-prev" onclick="loadPatternPage(curPage-1)">&laquo; Prev</button>
      <span id="pg-info"></span>
      <button id="pg-next" onclick="loadPatternPage(curPage+1)">Next &raquo;</button>
    </div>
  </div>

  <!-- Resizer Handle -->
  <div id="resizer"></div>

  <!-- Main -->
  <div id="main">
    <button id="collapse-btn" title="Toggle sidebar" onclick="toggleSidebar()">&#9664;</button>

    <div id="top-bar">
      <div id="pane-title">Loading…</div>
      <div id="pane-meta"></div>
    </div>

    <!-- Patterns panel -->
    <div id="panel-patterns" class="panel">
      <div class="viewport" id="vp-patterns">
        <div class="canvas" id="canvas-patterns">
          <div id="pattern-svg" class="svg-container"></div>
        </div>
        <div class="status-msg" id="status-patterns">No pattern selected</div>
        <div class="zoom-controls">
          <button class="zoom-btn" onclick="zoomStep('patterns',1.25)" title="Zoom in">+</button>
          <button class="zoom-btn" onclick="zoomStep('patterns',0.8)"  title="Zoom out">&minus;</button>
          <button class="zoom-btn" onclick="resetZoom('patterns')"     title="Fit to window">&#x2922;</button>
        </div>
        <div class="zoom-hint">scroll / pinch &middot; drag to pan</div>
      </div>
    </div>

    <!-- Models panel -->
    <div id="panel-models" class="panel">
      <div class="viewport" id="vp-models">
        <div class="canvas" id="canvas-models">
          <div id="model-svg" class="svg-container"></div>
        </div>
        <div class="status-msg" id="status-models">No model selected</div>
        <div class="zoom-controls">
          <button class="zoom-btn" onclick="zoomStep('models',1.25)" title="Zoom in">+</button>
          <button class="zoom-btn" onclick="zoomStep('models',0.8)"  title="Zoom out">&minus;</button>
          <button class="zoom-btn" onclick="resetZoom('models')"     title="Fit to window">&#x2922;</button>
        </div>
        <div class="zoom-hint">scroll / pinch &middot; drag to pan</div>
      </div>
    </div>
  </div>

<script>
/* ── Config ── */
const SID      = "__SESSION_ID__";
const API      = "__BASE_URL__";  // absolute URL to CMiner server for iframe context
const PAGE_SZ  = 50;

/* ── Runtime state ── */
let meta       = null;        // {num_patterns, num_models, model_names}
let curPage    = 0;           // current pattern page (0-based)
let pageMetas  = [];          // current page pattern metadata
let models     = [];          // [{name, encoded}]
let curPat     = -1;          // selected pattern index within current page
let curPatGlobal = -1;        // global pattern index (for highlighting)
let curModel   = -1;

const state = {
  patterns: { scale: 1, ox: 0, oy: 0, imgW: 0, imgH: 0 },
  models:   { scale: 1, ox: 0, oy: 0, imgW: 0, imgH: 0 },
};

/* ── DOM helpers ── */
function els(panel) {
  return {
    vp:           document.getElementById('vp-'     + panel),
    canvas:       document.getElementById('canvas-' + panel),
    svgContainer: document.getElementById(panel === 'patterns' ? 'pattern-svg' : 'model-svg'),
    status:       document.getElementById('status-' + panel),
  };
}

/* ── Viewport / zoom ── */
function applyTransform(panel) {
  const s = state[panel];
  els(panel).canvas.style.transform =
    `translate(${s.ox}px,${s.oy}px) scale(${s.scale})`;
}

function fitToViewport(panel) {
  const s = state[panel];
  const e = els(panel);
  if (!s.imgW || !s.imgH) return;
  const vpW = e.vp.clientWidth;
  const vpH = e.vp.clientHeight;
  const PADDING = 32;
  const fit = Math.min(
    (vpW - PADDING) / s.imgW,
    (vpH - PADDING) / s.imgH,
    1
  );
  s.scale = fit;
  s.ox    = (vpW - s.imgW * fit) / 2;
  s.oy    = (vpH - s.imgH * fit) / 2;
  applyTransform(panel);
}

function resetZoom(panel) { fitToViewport(panel); }

function zoomAt(panel, factor, focalX, focalY) {
  const s        = state[panel];
  const newScale = Math.min(Math.max(s.scale * factor, 0.05), 50);
  const ratio    = newScale / s.scale;
  s.ox    = focalX - ratio * (focalX - s.ox);
  s.oy    = focalY - ratio * (focalY - s.oy);
  s.scale = newScale;
  applyTransform(panel);
}

function zoomStep(panel, factor) {
  const e = els(panel);
  zoomAt(panel, factor, e.vp.clientWidth / 2, e.vp.clientHeight / 2);
}

/* ── SVG loading (fetch from proxy on demand) ── */
function loadSvg(panel, svgContent) {
  const e = els(panel);
  e.svgContainer.style.opacity = '0';
  e.svgContainer.innerHTML     = svgContent;
  e.status.style.display       = 'none';
  const svgEl = e.svgContainer.querySelector('svg');
  const s     = state[panel];
  if (svgEl) {
    s.imgW = parseFloat(svgEl.getAttribute('width'))  || svgEl.viewBox?.baseVal?.width  || 800;
    s.imgH = parseFloat(svgEl.getAttribute('height')) || svgEl.viewBox?.baseVal?.height || 600;
    svgEl.style.width         = s.imgW + 'px';
    svgEl.style.height        = s.imgH + 'px';
    svgEl.style.pointerEvents = 'none';
  } else {
    s.imgW = 800; s.imgH = 600;
  }
  e.canvas.style.width  = s.imgW + 'px';
  e.canvas.style.height = s.imgH + 'px';
  requestAnimationFrame(() => {
    fitToViewport(panel);
    e.svgContainer.style.opacity = '1';
  });
}

async function fetchAndLoadSvg(panel, encoded) {
  const e = els(panel);
  e.status.style.display = 'flex';
  e.status.textContent   = 'Loading\u2026';
  e.svgContainer.style.opacity = '0';
  try {
    const resp = await fetch(`${API}/plantuml/svg/${encoded}`);
    const svg  = await resp.text();
    loadSvg(panel, svg);
  } catch (err) {
    e.status.textContent = `Failed to load diagram: ${err}`;
  }
}

/* ── Pattern page loading ── */
async function loadPatternPage(page) {
  const totalPages = Math.ceil(meta.num_patterns / PAGE_SZ);
  if (page < 0 || page >= totalPages) return;
  curPage = page;

  const resp = await fetch(`${API}/api/sessions/${SID}/patterns?offset=${page * PAGE_SZ}&limit=${PAGE_SZ}`);
  if (!resp.ok) { console.error('Failed to load patterns'); return; }
  const data = await resp.json();
  pageMetas = data.patterns;

  // Render sidebar items
  const list = document.getElementById('pattern-list');
  list.innerHTML = '';
  pageMetas.forEach((p, localIdx) => {
    const globalIdx = page * PAGE_SZ + localIdx;
    const div = document.createElement('div');
    div.className = 'pat-item';
    div.id = 'pi-' + globalIdx;
    div.tabIndex = 0;
    div.onclick = () => showPattern(localIdx, globalIdx);
    div.onkeydown = (e) => { if (e.key === 'Enter') showPattern(localIdx, globalIdx); };
    div.innerHTML =
      `<div class="pat-title">Pattern #${p.id}</div>` +
      `<div class="pat-meta">Support\u202f${p.support}\u2003Freq\u202f${p.frequency}\u2003${p.nodes}N\u202f/\u202f${p.edges}E</div>`;
    if (globalIdx === curPatGlobal) div.classList.add('active');
    list.appendChild(div);
  });

  // Update pager
  const pager = document.getElementById('pattern-pager');
  pager.style.display = totalPages > 1 ? 'flex' : 'none';
  document.getElementById('pg-prev').disabled = page === 0;
  document.getElementById('pg-next').disabled = page >= totalPages - 1;
  document.getElementById('pg-info').textContent =
    `Page ${page + 1} / ${totalPages}`;
}

/* ── Tab switching ── */
function switchTab(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.s-tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  const tabBtn = document.getElementById('tab-' + name);
  if (tabBtn) tabBtn.classList.add('active');

  const patList = document.getElementById('pattern-list');
  const modList = document.getElementById('model-list');
  const pager   = document.getElementById('pattern-pager');
  const title   = document.getElementById('sidebar-title');
  const hasPats = meta && meta.num_patterns > 0;
  const hasMods = meta && meta.num_models > 0;

  const hasList = (name === 'patterns' && hasPats) || (name === 'models' && hasMods);

  if (name === 'patterns' && hasPats) {
    patList.style.display = 'block';
    modList.style.display = 'none';
    const totalPages = Math.ceil(meta.num_patterns / PAGE_SZ);
    pager.style.display = totalPages > 1 ? 'flex' : 'none';
    title.textContent = meta.num_patterns + ' Pattern' + (meta.num_patterns !== 1 ? 's' : '') + ' found';
    if (curPatGlobal < 0) {
      document.getElementById('pane-title').textContent = 'Select a pattern from the left panel.';
      document.getElementById('pane-meta').innerHTML = '';
    }
  } else if (name === 'models' && hasMods) {
    patList.style.display = 'none';
    modList.style.display = 'block';
    pager.style.display   = 'none';
    title.textContent = models.length + ' Model' + (models.length !== 1 ? 's' : '');
    if (curModel < 0) {
      document.getElementById('pane-title').textContent = 'Select a model from the left panel.';
      document.getElementById('pane-meta').innerHTML = '';
    }
  } else {
    patList.style.display = 'none';
    modList.style.display = 'none';
    pager.style.display   = 'none';
    document.getElementById('pane-title').textContent = 'Nothing to display.';
    document.getElementById('pane-meta').innerHTML = '';
  }

  if (!sidebarCollapsed && hasList) {
    sidebar.style.display = 'flex';
    document.getElementById('resizer').style.display = 'block';
  } else if (!hasList) {
    sidebar.style.display = sidebarCollapsed ? 'none' : 'flex';
    document.getElementById('resizer').style.display = 'none';
  }
  requestAnimationFrame(() => fitToViewport(name));
}

/* ── Pattern selection ── */
function showPattern(localIdx, globalIdx) {
  if (curPatGlobal >= 0) {
    const prev = document.getElementById('pi-' + curPatGlobal);
    if (prev) prev.classList.remove('active');
  }
  curPat = localIdx;
  curPatGlobal = globalIdx;
  document.getElementById('pi-' + globalIdx).classList.add('active');
  const m = pageMetas[localIdx];
  document.getElementById('pane-title').textContent = 'Pattern #' + m.id;
  document.getElementById('pane-meta').innerHTML =
    'Support\u202f<b>' + m.support +
    '</b>\u2002&bull;\u2002Freq\u202f<b>' + m.frequency +
    '</b>\u2002&bull;\u2002Nodes\u202f<b>' + m.nodes +
    '</b>\u2002&bull;\u2002Edges\u202f<b>' + m.edges + '</b>';
  fetchAndLoadSvg('patterns', m.encoded);
}

/* ── Model selection ── */
function showModel(idx) {
  if (curModel >= 0) {
    const prev = document.getElementById('mi-' + curModel);
    if (prev) prev.classList.remove('active');
  }
  curModel = idx;
  document.getElementById('mi-' + idx).classList.add('active');
  document.getElementById('pane-title').textContent = models[idx].name;
  document.getElementById('pane-meta').innerHTML = '';
  fetchAndLoadSvg('models', models[idx].encoded);
}

/* ── Sidebar collapse ── */
const resizer = document.getElementById('resizer');
const sidebar = document.getElementById('sidebar');
let sidebarCollapsed = false;
let lastSidebarWidth = null;

function setSidebarVisible(visible) {
  const btn = document.getElementById('collapse-btn');
  if (visible) {
    sidebar.style.display = 'flex';
    if (lastSidebarWidth) sidebar.style.width = lastSidebarWidth;
    const anyListVisible =
      document.getElementById('pattern-list').style.display !== 'none' ||
      document.getElementById('model-list').style.display   !== 'none';
    resizer.style.display = anyListVisible ? 'block' : 'none';
    btn.innerHTML = '&#9664;';
    sidebarCollapsed = false;
  } else {
    lastSidebarWidth = sidebar.style.width || sidebar.offsetWidth + 'px';
    sidebar.style.display = 'none';
    resizer.style.display = 'none';
    btn.innerHTML = '&#9654;';
    sidebarCollapsed = true;
  }
  requestAnimationFrame(() => {
    ['patterns', 'models'].forEach(p => {
      const panel = document.getElementById('panel-' + p);
      if (panel && panel.classList.contains('active') && state[p].imgW) fitToViewport(p);
    });
  });
}

function toggleSidebar() { setSidebarVisible(sidebarCollapsed); }

/* ── Interaction: Sidebar Resizing ── */
let isResizing = false;

resizer.addEventListener('mousedown', (e) => {
  isResizing = true;
  document.body.style.cursor = 'col-resize';
  e.preventDefault();
});

document.addEventListener('mousemove', (e) => {
  if (!isResizing) return;
  const newWidth = e.clientX;
  if (newWidth >= 150 && newWidth <= document.body.clientWidth * 0.5) {
    sidebar.style.width = newWidth + 'px';
    ['patterns', 'models'].forEach(p => {
      const panel = document.getElementById('panel-' + p);
      if (panel && panel.classList.contains('active') && state[p].imgW) {
        fitToViewport(p);
      }
    });
  }
});

document.addEventListener('mouseup', () => {
  if (isResizing) {
    isResizing = false;
    document.body.style.cursor = '';
  }
});

/* ── Interaction: wheel, drag, pinch ── */
['patterns', 'models'].forEach(panel => {
  const vp = document.getElementById('vp-' + panel);
  if (!vp) return;

  vp.addEventListener('dblclick', () => { resetZoom(panel); });

  vp.addEventListener('wheel', e => {
    e.preventDefault();
    const r = vp.getBoundingClientRect();
    zoomAt(panel, e.deltaY < 0 ? 1.12 : 1/1.12,
           e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });

  let dragging = false, mx = 0, my = 0;
  vp.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    e.preventDefault();
    dragging = true;
    mx = e.clientX; my = e.clientY;
    vp.classList.add('is-dragging');
  });
  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    const s = state[panel];
    s.ox += e.clientX - mx;
    s.oy += e.clientY - my;
    mx = e.clientX; my = e.clientY;
    applyTransform(panel);
  });
  window.addEventListener('mouseup', () => { dragging = false; vp.classList.remove('is-dragging'); });

  let touches = [], lastDist = 0;
  vp.addEventListener('touchstart', e => {
    e.preventDefault();
    touches = Array.from(e.touches);
    if (touches.length === 2)
      lastDist = Math.hypot(
        touches[0].clientX - touches[1].clientX,
        touches[0].clientY - touches[1].clientY);
  }, { passive: false });
  vp.addEventListener('touchmove', e => {
    e.preventDefault();
    const t    = Array.from(e.touches);
    const rect = vp.getBoundingClientRect();
    if (t.length === 2 && lastDist) {
      const dist = Math.hypot(
        t[0].clientX - t[1].clientX,
        t[0].clientY - t[1].clientY);
      const mid = {
        x: (t[0].clientX + t[1].clientX) / 2 - rect.left,
        y: (t[0].clientY + t[1].clientY) / 2 - rect.top,
      };
      zoomAt(panel, dist / lastDist, mid.x, mid.y);
      lastDist = dist;
    } else if (t.length === 1 && touches.length === 1) {
      const s = state[panel];
      s.ox += t[0].clientX - touches[0].clientX;
      s.oy += t[0].clientY - touches[0].clientY;
      applyTransform(panel);
    }
    touches = t;
  }, { passive: false });
  vp.addEventListener('touchend', e => {
    touches  = Array.from(e.touches);
    lastDist = 0;
  }, { passive: false });
});

window.addEventListener('resize', () =>
  ['patterns', 'models'].forEach(p => { if (state[p] && state[p].imgW) fitToViewport(p); }));

/* ── Keyboard shortcuts ── */
document.addEventListener('keydown', (e) => {
  const active = document.querySelector('.panel.active');
  if (!active) return;
  const panel = active.id.replace('panel-', '');
  if (e.key === '=' || e.key === '+') zoomStep(panel, 1.25);
  else if (e.key === '-')             zoomStep(panel, 0.8);
  else if (e.key === '0')             resetZoom(panel);
});

/* ── Boot: fetch metadata, then render ── */
async function boot() {
  try {
    const metaResp = await fetch(`${API}/api/sessions/${SID}/meta`);
    if (!metaResp.ok) {
      document.getElementById('pane-title').textContent = 'Session not found or expired.';
      return;
    }
    meta = await metaResp.json();
  } catch (err) {
    document.getElementById('pane-title').textContent = `Error: ${err}`;
    return;
  }

  const hasPats = meta.num_patterns > 0;
  const hasMods = meta.num_models > 0;

  // Show tabs only if both categories exist
  if (hasPats && hasMods) {
    document.getElementById('sidebar-tabs').classList.add('visible');
  }

  // Load models (always few — no pagination needed)
  if (hasMods) {
    const modResp = await fetch(`${API}/api/sessions/${SID}/models`);
    if (modResp.ok) {
      const data = await modResp.json();
      models = data.models;
      const list = document.getElementById('model-list');
      models.forEach((m, idx) => {
        const div = document.createElement('div');
        div.className = 'pat-item';
        div.id = 'mi-' + idx;
        div.tabIndex = 0;
        div.onclick = () => showModel(idx);
        div.onkeydown = (e) => { if (e.key === 'Enter') showModel(idx); };
        div.innerHTML = `<div class="pat-title">${m.name}</div>`;
        list.appendChild(div);
      });
    }
  }

  // Load first page of patterns
  if (hasPats) {
    await loadPatternPage(0);
  }

  // Boot into the right tab
  if (hasMods)      { switchTab('models');   showModel(0); }
  else if (hasPats) { switchTab('patterns'); showPattern(0, 0); }
}

boot();
</script>
</body>
</html>"""
