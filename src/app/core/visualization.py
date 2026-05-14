import json
from ..utils.plantuml_helper import plantuml_encode, PLANTUML_SERVER
from ..utils.cminer_patterns_helper import PatternInfo


def generate_html_visualization(
    patterns: list[PatternInfo],
    patterns_plantuml: list[str],
    plantuml_server: str = PLANTUML_SERVER,
    models_plantuml: dict[str, str] | None = None,
) -> str:
    if models_plantuml is None:
        models_plantuml = {}

    # ── Pattern sidebar items ──
    sidebar_items: list[str] = []
    img_urls:      list[str] = []

    for idx, p in enumerate(patterns):
        encoded = plantuml_encode(patterns_plantuml[idx])
        img_urls.append(f"{plantuml_server}/svg/{encoded}")
        sidebar_items.append(
            f'<div class="pat-item" id="pi-{idx}" tabindex="0" onclick="showPattern({idx})" onkeydown="if(event.key===\'Enter\') showPattern({idx})">'
            f'<div class="pat-title">Pattern #{p.id}</div>'
            f'<div class="pat-meta">'
            f'Support\u202f{p.support}&ensp;Freq\u202f{p.frequency}'
            f'&ensp;{len(p.nodes)}N\u202f/\u202f{len(p.edges)}E'
            f'</div></div>'
        )

    sidebar_html = "\n".join(sidebar_items)
    urls_json    = json.dumps(img_urls, ensure_ascii=False)
    metas_json   = json.dumps(
        [{"id": p.id, "support": p.support, "frequency": p.frequency,
          "nodes": len(p.nodes), "edges": len(p.edges)} for p in patterns],
        ensure_ascii=False,
    )
    count = len(patterns)

    # ── Model list sidebar items + URLs ──
    model_names: list[str] = []
    model_urls:  list[str] = []
    model_sidebar_items: list[str] = []

    for idx, (name, puml) in enumerate(models_plantuml.items()):
        encoded = plantuml_encode(puml)
        model_urls.append(f"{plantuml_server}/svg/{encoded}")
        model_names.append(name)
        model_sidebar_items.append(
            f'<div class="pat-item" id="mi-{idx}" tabindex="0" onclick="showModel({idx})" onkeydown="if(event.key===\'Enter\') showModel({idx})">'
            f'<div class="pat-title">{name}</div>'
            f'</div>'
        )

    model_sidebar_html = "\n".join(model_sidebar_items)
    model_urls_json  = json.dumps(model_urls, ensure_ascii=False)
    model_names_json = json.dumps(model_names, ensure_ascii=False)
    has_models   = "true" if models_plantuml else "false"
    has_patterns = "true" if patterns       else "false"

    # Only show sidebar tabs if BOTH categories are populated
    sidebar_tabs_html = ""
    if patterns and models_plantuml:
        sidebar_tabs_html = (
            '<div id="sidebar-tabs">'
            '<button class="s-tab-btn" id="tab-models" onclick="switchTab(\'models\')">Models</button>'
            '<button class="s-tab-btn" id="tab-patterns" onclick="switchTab(\'patterns\')">Patterns</button>'
            '</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CMiner Visualization</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, sans-serif;
      display: flex; height: 100vh;
      background: #f0f2f5; overflow: hidden;
    }}

    /* ── Sidebar & Resizer ── */
    #sidebar {{
      width: 260px; min-width: 150px; max-width: 600px;
      background: #2c3e50; color: #ecf0f1;
      display: flex; flex-direction: column; flex-shrink: 0;
    }}
    #sidebar h2 {{
      padding: 12px 16px; background: #1f2c38;
      font-size: .85em; letter-spacing: .05em; color: #bdc3c7;
      text-transform: uppercase; border-bottom: 1px solid #1a252f;
    }}
    
    /* ── Sidebar Tabs ── */
    #sidebar-tabs {{
      display: flex; background: #1a252f;
      border-bottom: 1px solid #0f171e;
    }}
    .s-tab-btn {{
      flex: 1; padding: 12px 0;
      background: transparent; border: none;
      color: #95a5a6; cursor: pointer;
      font-weight: bold; font-size: 0.9em;
      border-bottom: 3px solid transparent;
      transition: all 0.2s;
    }}
    .s-tab-btn:hover  {{ color: #ecf0f1; background: #243447; }}
    .s-tab-btn.active {{ color: #3498db; border-bottom-color: #3498db; background: #2c3e50; }}
    .sidebar-list {{ flex: 1; overflow-y: auto; }}
    .sidebar-list::-webkit-scrollbar {{ width: 8px; }}
    .sidebar-list::-webkit-scrollbar-track {{ background: #1a252f; }}
    .sidebar-list::-webkit-scrollbar-thumb {{ background: #3d5166; border-radius: 4px; }}
    .sidebar-list::-webkit-scrollbar-thumb:hover {{ background: #5a738e; }}
    .pat-item {{
      padding: 11px 16px; cursor: pointer;
      border-bottom: 1px solid #3d5166;
    }}
    .pat-item:hover  {{ background: #34495e; }}
    .pat-item.active {{ background: #2980b9; border-left: 4px solid #6dd5ed; padding-left: 12px; }}
    .pat-title {{ font-weight: bold; font-size: .88em; }}
    .pat-meta  {{ font-size: .75em; color: #95a5a6; margin-top: 3px; }}

    #resizer {{
      width: 5px;
      background: #bdc3c7;
      cursor: col-resize;
      flex-shrink: 0;
      transition: background 0.2s;
    }}
    #resizer:hover, #resizer:active {{
      background: #7f8c8d;
    }}

    /* ── Collapse toggle ── */
    #collapse-btn {{
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
    }}
    #collapse-btn:hover {{ background: #34495e; }}

    /* ── Main ── */
    #main {{ flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; position: relative; }}

    /* ── Top Bar ── */
    #top-bar {{
      display: flex; align-items: center; background: #fff;
      border-bottom: 1px solid #ddd; padding: 0 20px;
      min-height: 48px; flex-shrink: 0; gap: 16px;
    }}
    #pane-title {{
      font-size: 1.15em; font-weight: bold; color: #2c3e50;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }}
    #pane-meta {{
      font-size: 0.85em; color: #7f8c8d;
      display: flex; gap: 12px; white-space: nowrap;
    }}
    #pane-meta b {{ color: #2c3e50; font-weight: 600; }}

    /* ── Viewport ── */
    .viewport {{
      flex: 1; position: relative; overflow: hidden;
      background: #fff; margin: 14px; border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0,0,0,.12);
      cursor: grab;
      user-select: none; -webkit-user-select: none;
    }}
    .viewport.is-dragging {{ cursor: grabbing; }}

    .canvas {{
      position: absolute;
      top: 0; left: 0;
      transform-origin: 0 0;
    }}

    .canvas img {{
      display: block;
      draggable: false;
      -webkit-user-drag: none;
      user-drag: none;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.2s ease-in-out;
    }}

    .zoom-controls {{
      position: absolute; top: 10px; right: 14px;
      display: flex; flex-direction: column; gap: 4px; z-index: 10;
    }}
    .zoom-btn {{
      width: 28px; height: 28px; border: 1px solid #ccc; border-radius: 5px;
      background: #fff; cursor: pointer; font-size: 1.1em;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 1px 4px rgba(0,0,0,.15);
    }}
    .zoom-btn:hover {{ background: #eaf3fb; border-color: #2980b9; color: #2980b9; }}

    .zoom-hint {{
      position: absolute; bottom: 10px; right: 14px;
      font-size: .72em; color: #aaa; pointer-events: none; z-index: 10;
      background: rgba(255,255,255,.8); padding: 3px 8px; border-radius: 4px;
    }}

    .status-msg {{
      position: absolute; inset: 0;
      display: flex; align-items: center; justify-content: center;
      color: #bbb; font-size: 1.1em; pointer-events: none; z-index: 5;
    }}

    .panel {{ display: none; flex-direction: column; flex: 1; overflow: hidden; min-height: 0; }}
    .panel.active {{ display: flex; }}
  </style>
</head>
<body>

  <!-- Sidebar -->
  <div id="sidebar">
    {sidebar_tabs_html}
    <h2 id="sidebar-title"></h2>
    <!-- Pattern list (shown when patterns tab active) -->
    <div class="sidebar-list" id="pattern-list" style="display:none">{sidebar_html}</div>
    <!-- Model list (shown when models tab active) -->
    <div class="sidebar-list" id="model-list" style="display:none">{model_sidebar_html}</div>
  </div>

  <!-- Resizer Handle -->
  <div id="resizer"></div>

  <!-- Main -->
  <div id="main">
    <!-- Collapse toggle (always visible) -->
    <button id="collapse-btn" title="Toggle sidebar" onclick="toggleSidebar()">&#9664;</button>

    <div id="top-bar">
      <div id="pane-title">Select an item from the left panel.</div>
      <div id="pane-meta"></div>
    </div>

    <!-- Patterns panel -->
    <div id="panel-patterns" class="panel">
      <div class="viewport" id="vp-patterns">
        <div class="canvas" id="canvas-patterns">
          <img id="pattern-img" src="" alt="Pattern diagram" draggable="false"/>
        </div>
        <div class="status-msg" id="status-patterns">No pattern selected</div>
        <div class="zoom-controls">
          <button class="zoom-btn" onclick="zoomStep('patterns',1.25)" title="Zoom in">+</button>
          <button class="zoom-btn" onclick="zoomStep('patterns',0.8)"  title="Zoom out">\u2212</button>
          <button class="zoom-btn" onclick="resetZoom('patterns')"     title="Fit to window">\u2922</button>
        </div>
        <div class="zoom-hint">scroll / pinch \u00b7 drag to pan</div>
      </div>
    </div>

    <!-- Models panel -->
    <div id="panel-models" class="panel">
      <div class="viewport" id="vp-models">
        <div class="canvas" id="canvas-models">
          <img id="model-img" src="" alt="Model graph" draggable="false"/>
        </div>
        <div class="status-msg" id="status-models">No model selected</div>
        <div class="zoom-controls">
          <button class="zoom-btn" onclick="zoomStep('models',1.25)" title="Zoom in">+</button>
          <button class="zoom-btn" onclick="zoomStep('models',0.8)"  title="Zoom out">\u2212</button>
          <button class="zoom-btn" onclick="resetZoom('models')"     title="Fit to window">\u2922</button>
        </div>
        <div class="zoom-hint">scroll / pinch \u00b7 drag to pan</div>
      </div>
    </div>
  </div>

<script>
/* ── Data ── */
const URLS        = {urls_json};
const METAS       = {metas_json};
const MODEL_URLS  = {model_urls_json};
const MODEL_NAMES = {model_names_json};
const HAS_MODELS  = {has_models};
const HAS_PATS    = {has_patterns};

/* ── Per-panel state ── */
const state = {{
  patterns: {{ scale: 1, ox: 0, oy: 0, imgW: 0, imgH: 0 }},
  models:   {{ scale: 1, ox: 0, oy: 0, imgW: 0, imgH: 0 }},
}};

function els(panel) {{
  return {{
    vp:     document.getElementById('vp-'     + panel),
    canvas: document.getElementById('canvas-' + panel),
    img:    document.getElementById(panel === 'patterns' ? 'pattern-img' : 'model-img'),
    status: document.getElementById('status-' + panel),
  }};
}}

function applyTransform(panel) {{
  const s = state[panel];
  els(panel).canvas.style.transform =
    `translate(${{s.ox}}px,${{s.oy}}px) scale(${{s.scale}})`;
}}

function fitToViewport(panel) {{
  const s  = state[panel];
  const e  = els(panel);
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
}}

function resetZoom(panel) {{ fitToViewport(panel); }}

function zoomAt(panel, factor, focalX, focalY) {{
  const s        = state[panel];
  const newScale = Math.min(Math.max(s.scale * factor, 0.05), 50);
  const ratio    = newScale / s.scale;
  s.ox    = focalX - ratio * (focalX - s.ox);
  s.oy    = focalY - ratio * (focalY - s.oy);
  s.scale = newScale;
  applyTransform(panel);
}}

function zoomStep(panel, factor) {{
  const e = els(panel);
  zoomAt(panel, factor, e.vp.clientWidth / 2, e.vp.clientHeight / 2);
}}

function loadImage(panel, url) {{
  const e = els(panel);
  e.img.style.display    = 'none';
  e.img.style.opacity    = '0';
  e.status.style.display = 'flex';
  e.status.textContent   = 'Loading\u2026';

  e.img.onload = () => {{
    const s = state[panel];
    let w = e.img.naturalWidth;
    let h = e.img.naturalHeight;
    if (!w || !h) {{
      e.img.style.display    = 'block';
      e.img.style.position   = 'absolute';
      e.img.style.visibility = 'hidden';
      w = e.img.offsetWidth  || 800;
      h = e.img.offsetHeight || 600;
      e.img.style.visibility = '';
      e.img.style.position   = '';
    }}
    s.imgW = w; s.imgH = h;
    e.canvas.style.width  = w + 'px';
    e.canvas.style.height = h + 'px';
    e.img.style.width     = w + 'px';
    e.img.style.height    = h + 'px';
    e.img.style.display   = 'block';
    e.status.style.display = 'none';
    fitToViewport(panel);
    void e.img.offsetWidth;
    e.img.style.opacity = '1';
  }};

  e.img.onerror = () => {{
    els(panel).status.textContent =
      'Failed to load diagram (check PlantUML server URL).';
  }};
  e.img.src = url;
}}

/* ── Selection state (declared before switchTab so it can reference them) ── */
let curPat   = -1;
let curModel = -1;

/* ── Tab switching ── */
function switchTab(name) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.s-tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  const tabBtn = document.getElementById('tab-' + name);
  if (tabBtn) tabBtn.classList.add('active');

  // Toggle sidebar lists
  const patList   = document.getElementById('pattern-list');
  const modList   = document.getElementById('model-list');
  const title     = document.getElementById('sidebar-title');

  const hasList = (name === 'patterns' && HAS_PATS) || (name === 'models' && HAS_MODELS);
  if (name === 'patterns' && HAS_PATS) {{
    patList.style.display = 'block';
    modList.style.display = 'none';
    title.innerHTML = '{count} Pattern' + ({count} !== 1 ? 's' : '') + ' found';
    if (curPat >= 0) {{
      const m = METAS[curPat];
      document.getElementById('pane-title').textContent = 'Pattern #' + m.id;
      document.getElementById('pane-meta').innerHTML =
        'Support\u202f<b>' + m.support + '</b>\u2002Freq\u202f<b>' + m.frequency +
        '</b>\u2002Nodes\u202f<b>' + m.nodes + '</b>\u2002Edges\u202f<b>' + m.edges + '</b>';
    }} else {{
      document.getElementById('pane-title').textContent = 'Select a pattern from the left panel.';
      document.getElementById('pane-meta').innerHTML = '';
    }}
  }} else if (name === 'models' && HAS_MODELS) {{
    patList.style.display = 'none';
    modList.style.display = 'block';
    title.innerHTML = MODEL_NAMES.length + ' Model' + (MODEL_NAMES.length !== 1 ? 's' : '');
    if (curModel >= 0) {{
      document.getElementById('pane-title').textContent = MODEL_NAMES[curModel];
      document.getElementById('pane-meta').innerHTML = '';
    }} else {{
      document.getElementById('pane-title').textContent = 'Select a model from the left panel.';
      document.getElementById('pane-meta').innerHTML = '';
    }}
  }} else {{
    patList.style.display = 'none';
    modList.style.display = 'none';
    document.getElementById('pane-title').textContent = 'Nothing to display.';
    document.getElementById('pane-meta').innerHTML = '';
  }}
  // Respect manual collapse; only auto-show sidebar when there's a list
  if (!sidebarCollapsed && hasList) {{
    sidebar.style.display = 'flex';
    document.getElementById('resizer').style.display = 'block';
  }} else if (!hasList) {{
    sidebar.style.display = sidebarCollapsed ? 'none' : 'flex';
    document.getElementById('resizer').style.display = 'none';
  }}
  requestAnimationFrame(() => fitToViewport(name));
}}

/* ── Pattern selection ── */
function showPattern(idx) {{
  if (curPat >= 0)
    document.getElementById('pi-' + curPat).classList.remove('active');
  curPat = idx;
  document.getElementById('pi-' + idx).classList.add('active');
  const m = METAS[idx];
  document.getElementById('pane-title').textContent = 'Pattern #' + m.id;
  document.getElementById('pane-meta').innerHTML =
    'Support\u202f<b>' + m.support +
    '</b>\u2002&bull;\u2002Freq\u202f<b>' + m.frequency +
    '</b>\u2002&bull;\u2002Nodes\u202f<b>' + m.nodes +
    '</b>\u2002&bull;\u2002Edges\u202f<b>' + m.edges + '</b>';
  loadImage('patterns', URLS[idx]);
}}

/* ── Model selection ── */
function showModel(idx) {{
  if (curModel >= 0)
    document.getElementById('mi-' + curModel).classList.remove('active');
  curModel = idx;
  document.getElementById('mi-' + idx).classList.add('active');
  document.getElementById('pane-title').textContent = MODEL_NAMES[idx];
  document.getElementById('pane-meta').innerHTML = '';
  loadImage('models', MODEL_URLS[idx]);
}}

/* ── Sidebar collapse ── */
const resizer = document.getElementById('resizer');
const sidebar = document.getElementById('sidebar');
let sidebarCollapsed = false;
let lastSidebarWidth  = null;   // remembers width before collapse

function setSidebarVisible(visible) {{
  const btn = document.getElementById('collapse-btn');
  if (visible) {{
    sidebar.style.display = 'flex';
    if (lastSidebarWidth) sidebar.style.width = lastSidebarWidth;
    // only show resizer when there is actually a list shown
    const anyListVisible =
      document.getElementById('pattern-list').style.display !== 'none' ||
      document.getElementById('model-list').style.display   !== 'none';
    resizer.style.display = anyListVisible ? 'block' : 'none';
    btn.innerHTML = '&#9664;'; // ◀
    sidebarCollapsed = false;
  }} else {{
    lastSidebarWidth = sidebar.style.width || sidebar.offsetWidth + 'px';
    sidebar.style.display = 'none';
    resizer.style.display = 'none';
    btn.innerHTML = '&#9654;'; // ▶
    sidebarCollapsed = true;
  }}
  // Refit active panel after layout change
  requestAnimationFrame(() => {{
    ['patterns', 'models'].forEach(p => {{
      const panel = document.getElementById('panel-' + p);
      if (panel && panel.classList.contains('active') && state[p].imgW) fitToViewport(p);
    }});
  }});
}}

function toggleSidebar() {{ setSidebarVisible(sidebarCollapsed); }}

/* ── Interaction: Sidebar Resizing ── */
let isResizing = false;

resizer.addEventListener('mousedown', (e) => {{
  isResizing = true;
  document.body.style.cursor = 'col-resize';
  e.preventDefault();
}});

document.addEventListener('mousemove', (e) => {{
  if (!isResizing) return;
  const newWidth = e.clientX;
  if (newWidth >= 150 && newWidth <= document.body.clientWidth * 0.5) {{
    sidebar.style.width = newWidth + 'px';
    ['patterns', 'models'].forEach(p => {{
      const panel = document.getElementById('panel-' + p);
      if (panel && panel.classList.contains('active') && state[p].imgW) {{
        fitToViewport(p);
      }}
    }});
  }}
}});

document.addEventListener('mouseup', () => {{
  if (isResizing) {{
    isResizing = false;
    document.body.style.cursor = '';
  }}
}});

/* ── Interaction: wheel, drag, pinch ── */
['patterns', 'models'].forEach(panel => {{
  const vp = document.getElementById('vp-' + panel);
  if (!vp) return;

  vp.addEventListener('dblclick', () => {{ resetZoom(panel); }});

  vp.addEventListener('wheel', e => {{
    e.preventDefault();
    const r = vp.getBoundingClientRect();
    zoomAt(panel, e.deltaY < 0 ? 1.12 : 1/1.12,
           e.clientX - r.left, e.clientY - r.top);
  }}, {{ passive: false }});

  let dragging = false, mx = 0, my = 0;
  vp.addEventListener('mousedown', e => {{
    if (e.button !== 0) return;
    e.preventDefault();
    dragging = true;
    mx = e.clientX; my = e.clientY;
    vp.classList.add('is-dragging');
  }});
  window.addEventListener('mousemove', e => {{
    if (!dragging) return;
    const s = state[panel];
    s.ox += e.clientX - mx;
    s.oy += e.clientY - my;
    mx = e.clientX; my = e.clientY;
    applyTransform(panel);
  }});
  window.addEventListener('mouseup', () => {{ dragging = false; vp.classList.remove('is-dragging'); }});

  let touches = [], lastDist = 0;
  vp.addEventListener('touchstart', e => {{
    e.preventDefault();
    touches = Array.from(e.touches);
    if (touches.length === 2)
      lastDist = Math.hypot(
        touches[0].clientX - touches[1].clientX,
        touches[0].clientY - touches[1].clientY);
  }}, {{ passive: false }});
  vp.addEventListener('touchmove', e => {{
    e.preventDefault();
    const t    = Array.from(e.touches);
    const rect = vp.getBoundingClientRect();
    if (t.length === 2 && lastDist) {{
      const dist = Math.hypot(
        t[0].clientX - t[1].clientX,
        t[0].clientY - t[1].clientY);
      const mid = {{
        x: (t[0].clientX + t[1].clientX) / 2 - rect.left,
        y: (t[0].clientY + t[1].clientY) / 2 - rect.top,
      }};
      zoomAt(panel, dist / lastDist, mid.x, mid.y);
      lastDist = dist;
    }} else if (t.length === 1 && touches.length === 1) {{
      const s = state[panel];
      s.ox += t[0].clientX - touches[0].clientX;
      s.oy += t[0].clientY - touches[0].clientY;
      applyTransform(panel);
    }}
    touches = t;
  }}, {{ passive: false }});
  vp.addEventListener('touchend', e => {{
    touches  = Array.from(e.touches);
    lastDist = 0;
  }}, {{ passive: false }});
}});

window.addEventListener('resize', () =>
  ['patterns', 'models'].forEach(p => {{ if (state[p] && state[p].imgW) fitToViewport(p); }}));

/* ── Keyboard shortcuts ── */
document.addEventListener('keydown', (e) => {{
  const active = document.querySelector('.panel.active');
  if (!active) return;
  const panel = active.id.replace('panel-', '');
  if (e.key === '=' || e.key === '+') zoomStep(panel, 1.25);
  else if (e.key === '-')             zoomStep(panel, 0.8);
  else if (e.key === '0')             resetZoom(panel);
}});

/* ── Boot ── */
if (HAS_MODELS)       {{ switchTab('models');   showModel(0); }}
else if (HAS_PATS)    {{ switchTab('patterns'); showPattern(0); }}
</script>
</body>
</html>"""
