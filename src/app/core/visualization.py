import json
from ..utils.plantuml_helper import plantuml_encode, PLANTUML_SERVER
from ..utils.cminer_patterns_helper import PatternInfo


def generate_html_visualization(
    patterns: list[PatternInfo],
    patterns_plantuml: list[str],
    plantuml_server: str = PLANTUML_SERVER,
    model_plantuml: str | None = None,
) -> str:
    sidebar_items: list[str] = []
    img_urls:      list[str] = []

    for idx, p in enumerate(patterns):
        if idx == 300:
            print("print onl the 300 pattern")
            print(f"{patterns_plantuml[idx]}")
        encoded = plantuml_encode(patterns_plantuml[idx])
        img_urls.append(f"{plantuml_server}/svg/{encoded}")
        sidebar_items.append(
            f'<div class="pat-item" id="pi-{idx}" onclick="showPattern({idx})">'
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

    model_tab_btn = ""
    model_img_url = "null"
    if model_plantuml:
        encoded_model = plantuml_encode(model_plantuml)
        model_img_url = json.dumps(f"{plantuml_server}/svg/{encoded_model}")
        model_tab_btn = '<button class="tab-btn" id="tab-model" onclick="switchTab(\'model\')">Model Graph</button>'

    has_model    = "true" if model_plantuml else "false"
    has_patterns = "true" if patterns       else "false"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CMiner Visualization</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: Arial, sans-serif;
      display: flex; height: 100vh;
      background: #f0f2f5; overflow: hidden;
    }}

    /* ── Sidebar ── */
    #sidebar {{
      width: 260px; background: #2c3e50; color: #ecf0f1;
      display: flex; flex-direction: column; flex-shrink: 0;
    }}
    #sidebar h2 {{
      padding: 14px 16px; background: #1a252f;
      font-size: .95em; letter-spacing: .05em;
    }}
    #pattern-list {{ flex: 1; overflow-y: auto; }}
    .pat-item {{
      padding: 11px 16px; cursor: pointer;
      border-bottom: 1px solid #3d5166;
    }}
    .pat-item:hover  {{ background: #34495e; }}
    .pat-item.active {{ background: #2980b9; }}
    .pat-title {{ font-weight: bold; font-size: .88em; }}
    .pat-meta  {{ font-size: .75em; color: #95a5a6; margin-top: 3px; }}

    /* ── Main ── */
    #main {{ flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }}

    /* ── Tabs ── */
    #tab-bar {{
      display: flex; background: #fff;
      border-bottom: 2px solid #ddd; padding: 0 16px; flex-shrink: 0;
    }}
    .tab-btn {{
      padding: 10px 20px; border: none; background: none;
      cursor: pointer; font-size: .9em; color: #555;
      border-bottom: 3px solid transparent; margin-bottom: -2px;
    }}
    .tab-btn:hover  {{ color: #2980b9; }}
    .tab-btn.active {{ color: #2980b9; border-bottom-color: #2980b9; font-weight: bold; }}

    /* ── Info bar ── */
    #info-bar {{
      background: #fff; padding: 10px 20px;
      border-bottom: 1px solid #ddd; font-size: .85em; color: #555;
      min-height: 38px; flex-shrink: 0;
    }}

    /* ── Viewport: the clipping window ── */
    .viewport {{
      flex: 1; position: relative; overflow: hidden;
      background: #fff; margin: 14px; border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0,0,0,.12);
      cursor: grab;
      /* prevent browser text/image selection while dragging */
      user-select: none; -webkit-user-select: none;
    }}
    .viewport:active {{ cursor: grabbing; }}

    /* ── Canvas: the layer we pan & zoom ── */
    .canvas {{
      position: absolute;
      top: 0; left: 0;
      transform-origin: 0 0;
    }}

    /* ── Image inside canvas ── */
    .canvas img {{
      display: block;
      draggable: false;
      -webkit-user-drag: none;
      user-drag: none;
      pointer-events: none;
    }}

    /* ── Overlay controls / messages ── */
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

    /* ── Panels ── */
    .panel {{ display: none; flex-direction: column; flex: 1; overflow: hidden; min-height: 0; }}
    .panel.active {{ display: flex; }}
  </style>
</head>
<body>

  <!-- Sidebar -->
  <div id="sidebar">
    <h2>&#9741; {count} Pattern{'s' if count != 1 else ''} found</h2>
    <div id="pattern-list">{sidebar_html}</div>
  </div>

  <!-- Main -->
  <div id="main">
    <div id="tab-bar">
      <button class="tab-btn" id="tab-patterns" onclick="switchTab('patterns')">Patterns</button>
      {model_tab_btn}
    </div>

    <!-- Patterns panel -->
    <div id="panel-patterns" class="panel">
      <div id="info-bar">Select a pattern from the left panel.</div>
      <div class="viewport" id="vp-patterns">
        <div class="canvas" id="canvas-patterns">
          <img id="pattern-img" src="" alt="Pattern diagram" draggable="false"/>
        </div>
        <div class="status-msg" id="status-patterns">No pattern selected</div>
        <div class="zoom-controls">
          <button class="zoom-btn" onclick="zoomStep('patterns',1.25)" title="Zoom in">+</button>
          <button class="zoom-btn" onclick="zoomStep('patterns',0.8)"  title="Zoom out">−</button>
          <button class="zoom-btn" onclick="resetZoom('patterns')"     title="Fit to window">⤢</button>
        </div>
        <div class="zoom-hint">scroll / pinch · drag to pan</div>
      </div>
    </div>

    <!-- Model panel -->
    <div id="panel-model" class="panel">
      <div class="viewport" id="vp-model">
        <div class="canvas" id="canvas-model">
          <img id="model-img" src="" alt="Model graph" draggable="false"/>
        </div>
        <div class="status-msg" id="status-model">Loading model…</div>
        <div class="zoom-controls">
          <button class="zoom-btn" onclick="zoomStep('model',1.25)" title="Zoom in">+</button>
          <button class="zoom-btn" onclick="zoomStep('model',0.8)"  title="Zoom out">−</button>
          <button class="zoom-btn" onclick="resetZoom('model')"     title="Fit to window">⤢</button>
        </div>
        <div class="zoom-hint">scroll / pinch · drag to pan</div>
      </div>
    </div>
  </div>

<script>
/* ── Data ── */
const URLS      = {urls_json};
const METAS     = {metas_json};
const MODEL_URL = {model_img_url};
const HAS_MODEL = {has_model};
const HAS_PATS  = {has_patterns};

/* ── Per-panel state ── */
const state = {{
  patterns: {{ scale: 1, ox: 0, oy: 0, imgW: 0, imgH: 0 }},
  model:    {{ scale: 1, ox: 0, oy: 0, imgW: 0, imgH: 0 }},
}};

function els(panel) {{
  return {{
    vp:     document.getElementById('vp-'     + panel),
    canvas: document.getElementById('canvas-' + panel),
    img:    document.getElementById(panel === 'patterns' ? 'pattern-img' : 'model-img'),
    status: document.getElementById('status-' + panel),
  }};
}}

/* Push current state → CSS transform */
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

/* Zoom towards an arbitrary focal point (viewport-relative px) */
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

/* Load image into a panel, then auto-fit */
function loadImage(panel, url) {{
  const e = els(panel);
  e.img.style.display  = 'none';
  e.status.style.display = 'flex';
  e.status.textContent   = 'Loading\u2026';

  e.img.onload = () => {{
    const s = state[panel];

    let w = e.img.naturalWidth;
    let h = e.img.naturalHeight;
    if (!w || !h) {{
      e.img.style.display  = 'block';
      e.img.style.position = 'absolute';
      e.img.style.visibility = 'hidden';
      w = e.img.offsetWidth  || 800;
      h = e.img.offsetHeight || 600;
      e.img.style.visibility = '';
      e.img.style.position   = '';
    }}

    s.imgW = w;
    s.imgH = h;

    e.canvas.style.width  = w + 'px';
    e.canvas.style.height = h + 'px';
    e.img.style.width     = w + 'px';
    e.img.style.height    = h + 'px';
    e.img.style.display   = 'block';

    e.status.style.display = 'none';
    fitToViewport(panel);
  }};

  e.img.onerror = () => {{
    els(panel).status.textContent =
      'Failed to load diagram (check PlantUML server URL).';
  }};
  e.img.src = url;
}}

/* ── Tab switching ── */
function switchTab(name) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  document.getElementById('tab-'   + name).classList.add('active');
  document.getElementById('sidebar').style.display =
    name === 'patterns' ? 'flex' : 'none';
  requestAnimationFrame(() => fitToViewport(name));
}}

/* ── Pattern selection ── */
let curPat = -1;
function showPattern(idx) {{
  if (curPat >= 0)
    document.getElementById('pi-' + curPat).classList.remove('active');
  curPat = idx;
  document.getElementById('pi-' + idx).classList.add('active');
  const m = METAS[idx];
  document.getElementById('info-bar').innerHTML =
    '<b>Pattern #' + m.id + '</b>&ensp;|&ensp;Support\u202f<b>' + m.support +
    '</b>&ensp;Frequency\u202f<b>' + m.frequency +
    '</b>&ensp;Nodes\u202f<b>' + m.nodes +
    '</b>&ensp;Edges\u202f<b>' + m.edges + '</b>';
  loadImage('patterns', URLS[idx]);
}}

/* ── Interaction: wheel, drag, pinch — wired up per viewport ── */
['patterns', 'model'].forEach(panel => {{
  const vp = document.getElementById('vp-' + panel);

  /* Mouse wheel zoom */
  vp.addEventListener('wheel', e => {{
    e.preventDefault();
    const r = vp.getBoundingClientRect();
    zoomAt(panel, e.deltaY < 0 ? 1.12 : 1/1.12,
           e.clientX - r.left, e.clientY - r.top);
  }}, {{ passive: false }});

  /* Mouse drag */
  let dragging = false, mx = 0, my = 0;

  vp.addEventListener('mousedown', e => {{
    if (e.button !== 0) return;
    e.preventDefault();
    dragging = true;
    mx = e.clientX; my = e.clientY;
  }});

  window.addEventListener('mousemove', e => {{
    if (!dragging) return;
    const s = state[panel];
    s.ox += e.clientX - mx;
    s.oy += e.clientY - my;
    mx = e.clientX; my = e.clientY;
    applyTransform(panel);
  }});

  window.addEventListener('mouseup', () => {{ dragging = false; }});

  /* Touch: single-finger pan + two-finger pinch */
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
    const t   = Array.from(e.touches);
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

/* Re-fit both panels on window resize */
window.addEventListener('resize', () =>
  ['patterns', 'model'].forEach(p => {{ if (state[p].imgW) fitToViewport(p); }}));

/* ── Boot ── */
if (HAS_MODEL)  loadImage('model', MODEL_URL);
if (HAS_PATS)   {{ switchTab('patterns'); showPattern(0); }}
else if (HAS_MODEL) switchTab('model');
</script>
</body>
</html>"""
