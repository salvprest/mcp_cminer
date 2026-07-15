/* ──────────────────────────────────────────────────────────────────────────
 * CMiner Viewer — client logic
 *
 * The viewer is a thin client. All heavy data lives on the server and is
 * fetched lazily from same-origin API endpoints:
 *
 *   GET /api/sessions/{sid}/meta      → {num_patterns, num_models, focus}
 *   GET /api/sessions/{sid}/patterns  → paginated pattern metadata
 *   GET /api/sessions/{sid}/models    → list of models
 *   GET /api/sessions/{sid}/patterns/{pid}/models  → matched models for a pattern
 *   GET /plantuml/svg/{encoded}       → SVG image
 *
 * Focus Protocol (boot-time only)
 * ───────────────────────────────
 * `meta.focus` tells the viewer where to position itself when it opens.
 * It is read once at boot and applied by `applyFocus()`. After that the
 * user is in control — there is NO polling, NO live sync. Tools mutate
 * the server-side focus; the user sees the change next time they open or
 * reload the visualization.
 *
 * See `app/core/visualization.py` for the full protocol specification.
 * ───────────────────────────────────────────────────────────────────── */

/* ── Runtime state ── */
let meta        = null;     // {num_patterns, num_models, focus}
let curPage     = 0;
let pageMetas   = [];       // pattern metadata for the current page
let models      = [];       // [{name, encoded}]
let curPat      = -1;       // selected pattern, local idx within page
let curPatGlobal = -1;      // selected pattern, global idx
let curModel    = -1;
let activeFilter = null;    // null | {patternId, matchedModels}
let activeTab   = null;     // 'patterns' | 'models'

const state = {
  patterns: { scale: 1, ox: 0, oy: 0, imgW: 0, imgH: 0 },
  models:   { scale: 1, ox: 0, oy: 0, imgW: 0, imgH: 0 },
};

/* ────────── DOM helpers ────────── */
function els(panel) {
  return {
    vp:           document.getElementById('vp-'     + panel),
    canvas:       document.getElementById('canvas-' + panel),
    svgContainer: document.getElementById(panel === 'patterns' ? 'pattern-svg' : 'model-svg'),
    status:       document.getElementById('status-' + panel),
  };
}

/* ────────── Viewport / zoom ────────── */
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

/* Click originated on a zoom button or the zoom-control toolbar —
   ignore it in the viewport drag/dblclick/pinch handlers so rapid clicks
   on +/- don’t trigger a reset or an unintended pan. */
function isZoomControl(el) {
  return el && (
    el.classList?.contains('zoom-btn') ||
    el.classList?.contains('zoom-controls') ||
    el.closest?.('.zoom-controls')
  );
}

/* ────────── SVG loading ────────── */
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

/* ────────── Pattern page loading ────────── */
async function loadPatternPage(page) {
  const totalPages = Math.ceil(meta.num_patterns / PAGE_SZ);
  if (page < 0 || page >= totalPages) return;
  curPage = page;

  const resp = await fetch(`${API}/api/sessions/${SID}/patterns?offset=${page * PAGE_SZ}&limit=${PAGE_SZ}`);
  if (!resp.ok) { console.error('Failed to load patterns'); return; }
  const data = await resp.json();
  pageMetas = data.patterns;

  const list = document.getElementById('pattern-list');
  list.innerHTML = '';
  pageMetas.forEach((p, localIdx) => {
    const globalIdx = page * PAGE_SZ + localIdx;
    const div = document.createElement('div');
    div.className = 'pat-item';
    div.id = 'pi-' + globalIdx;
    div.tabIndex = 0;
    div.onclick = () => onPatternClick(localIdx, globalIdx);
    div.onkeydown = (e) => { if (e.key === 'Enter') onPatternClick(localIdx, globalIdx); };
    div.innerHTML =
      `<div class="pat-title">Pattern #${p.id}</div>` +
      `<div class="pat-meta">Support\u202f${p.support}\u2003Freq\u202f${p.frequency}\u2003${p.nodes}N\u202f/\u202f${p.edges}E</div>`;
    if (globalIdx === curPatGlobal) div.classList.add('active');
    list.appendChild(div);
  });

  const pager = document.getElementById('pattern-pager');
  pager.style.display = totalPages > 1 ? 'flex' : 'none';
  document.getElementById('pg-prev').disabled = page === 0;
  document.getElementById('pg-next').disabled = page >= totalPages - 1;
  document.getElementById('pg-info').textContent = `Page ${page + 1} / ${totalPages}`;
}

/* ────────── Tab switching ────────── */
function switchTab(name) {
  activeTab = name;
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
    } else if (curPat >= 0 && pageMetas[curPat]) {
      // We had a quiet selection (e.g. from focus while the user was on the
      // models tab). Render its diagram now and scroll the list to it.
      const m = pageMetas[curPat];
      const e = els('patterns');
      const hasRenderedSvg = e.svgContainer.querySelector('svg') !== null;
      if (!hasRenderedSvg) {
        showPattern(curPat, curPatGlobal);
      } else {
        scrollItemIntoView('pi-' + curPatGlobal);
        document.getElementById('pane-title').textContent = 'Pattern #' + m.id;
        document.getElementById('pane-meta').innerHTML =
          'Support\u202f<b>' + m.support +
          '</b>\u2002&bull;\u2002Freq\u202f<b>' + m.frequency +
          '</b>\u2002&bull;\u2002Nodes\u202f<b>' + m.nodes +
          '</b>\u2002&bull;\u2002Edges\u202f<b>' + m.edges + '</b>';
      }
    }
  } else if (name === 'models' && hasMods) {
    patList.style.display = 'none';
    modList.style.display = 'block';
    pager.style.display   = 'none';
    title.textContent = models.length + ' Model' + (models.length !== 1 ? 's' : '');
    if (curModel < 0) {
      document.getElementById('pane-title').textContent = 'Select a model from the left panel.';
      document.getElementById('pane-meta').innerHTML = '';
    } else {
      // Same lazy-render handling for models
      const e = els('models');
      const hasRenderedSvg = e.svgContainer.querySelector('svg') !== null;
      if (!hasRenderedSvg) {
        showModel(curModel);
      } else {
        scrollItemIntoView('mi-' + curModel);
      }
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
  syncClearBtn();
}

/* ────────── Scroll helper ────────── */
// Bring the selected sidebar item into view inside its scrollable list.
// Uses 'nearest' so it never scrolls when the item is already visible.
function scrollItemIntoView(elementId) {
  const el = document.getElementById(elementId);
  if (el) el.scrollIntoView({ block: 'nearest', behavior: 'auto' });
}

/* ────────── Pattern selection ────────── */
function showPattern(localIdx, globalIdx) {
  if (curPatGlobal >= 0) {
    const prev = document.getElementById('pi-' + curPatGlobal);
    if (prev) prev.classList.remove('active');
  }
  curPat = localIdx;
  curPatGlobal = globalIdx;
  const cur = document.getElementById('pi-' + globalIdx);
  if (cur) cur.classList.add('active');
  scrollItemIntoView('pi-' + globalIdx);
  const m = pageMetas[localIdx];
  if (!m) return;
  document.getElementById('pane-title').textContent = 'Pattern #' + m.id;
  document.getElementById('pane-meta').innerHTML =
    'Support\u202f<b>' + m.support +
    '</b>\u2002&bull;\u2002Freq\u202f<b>' + m.frequency +
    '</b>\u2002&bull;\u2002Nodes\u202f<b>' + m.nodes +
    '</b>\u2002&bull;\u2002Edges\u202f<b>' + m.edges + '</b>';
  fetchAndLoadSvg('patterns', m.encoded);
}

/**
 * Select a pattern by its server-side id. Loads the right page if needed.
 * Quiet=true skips fetching/rendering the SVG (use when the patterns tab
 * is not the user-visible tab and we only want the selection state).
 * The selection is loaded lazily later by switchTab() when the user
 * actually opens the patterns tab.
 */
async function selectPatternById(patternId, { quiet = false } = {}) {
  // Patterns are 1-indexed and sequential — id == position + 1
  const globalIdx = patternId - 1;
  if (globalIdx < 0 || globalIdx >= meta.num_patterns) return false;
  const targetPage = Math.floor(globalIdx / PAGE_SZ);
  if (targetPage !== curPage || pageMetas.length === 0) {
    await loadPatternPage(targetPage);
  }
  const localIdx = globalIdx - targetPage * PAGE_SZ;
  if (quiet) {
    // Just update selection state, do not fetch the SVG
    if (curPatGlobal >= 0) {
      const prev = document.getElementById('pi-' + curPatGlobal);
      if (prev) prev.classList.remove('active');
    }
    curPat = localIdx;
    curPatGlobal = globalIdx;
    const cur = document.getElementById('pi-' + globalIdx);
    if (cur) cur.classList.add('active');
    scrollItemIntoView('pi-' + globalIdx);
  } else {
    showPattern(localIdx, globalIdx);
  }
  return true;
}

/* ────────── Model list rendering ────────── */
function renderModelList() {
  const list = document.getElementById('model-list');
  list.innerHTML = '';

  const matchedMap = new Map();
  if (activeFilter) {
    for (const mm of activeFilter.matchedModels) matchedMap.set(mm.name, mm);
  }

  const sorted = [...models].sort((a, b) => {
    const aM = matchedMap.has(a.name) ? 1 : 0;
    const bM = matchedMap.has(b.name) ? 1 : 0;
    return bM - aM;
  });

  sorted.forEach(m => {
    const origIdx   = models.indexOf(m);
    const matchInfo = matchedMap.get(m.name);
    const div = document.createElement('div');
    div.className = 'pat-item' + (matchInfo ? ' model-matched' : '');
    div.id        = 'mi-' + origIdx;
    div.tabIndex  = 0;
    div.onclick   = () => showModel(origIdx);
    div.onkeydown = (e) => { if (e.key === 'Enter') showModel(origIdx); };
    let html = `<div class="pat-title">${m.name}</div>`;
    if (matchInfo) {
      html += `<div class="pat-meta">${matchInfo.num_occurrences} occurrence(s) — pattern #${activeFilter.patternId}</div>`;
    }
    div.innerHTML = html;
    if (origIdx === curModel) div.classList.add('active');
    list.appendChild(div);
  });
}

/* ────────── Model selection ────────── */
function showModel(idx) {
  if (curModel >= 0) {
    const prev = document.getElementById('mi-' + curModel);
    if (prev) prev.classList.remove('active');
  }
  curModel = idx;
  const el = document.getElementById('mi-' + idx);
  if (el) el.classList.add('active');
  scrollItemIntoView('mi-' + idx);
  document.getElementById('pane-title').textContent = models[idx].name;
  document.getElementById('pane-meta').innerHTML = '';
  let encoded = models[idx].encoded;
  if (activeFilter) {
    const matchInfo = activeFilter.matchedModels.find(mm => mm.name === models[idx].name);
    if (matchInfo) encoded = matchInfo.encoded_highlighted;
  }
  fetchAndLoadSvg('models', encoded);
  syncClearBtn();
}

/* ────────── Pattern → Models highlight flow (two-step user gesture) ────────── */
async function onPatternClick(localIdx, globalIdx) {
  if (globalIdx !== curPatGlobal) {
    // First click — just show the pattern, stay on Patterns tab
    showPattern(localIdx, globalIdx);
    return;
  }
  // Second click on the already-selected pattern — trigger highlight flow
  const m = pageMetas[localIdx];
  await activatePatternHighlight(m.id, /*pickFirstModel*/ true);
  switchTab('models');
}

/**
 * Fetch matched models for a pattern, set activeFilter, re-render the model
 * list. If `targetModelName` is given, select that model; otherwise if
 * `pickFirstModel` is true, select the first matched model. Returns whether
 * anything was matched.
 *
 * occurrenceIndex: when non-null, only that occurrence is highlighted;
 * null means all occurrences (used for the interactive double-click gesture).
 */
async function activatePatternHighlight(patternId, pickFirstModel = false, targetModelName = null, occurrenceIndex = null) {
  let matchedModels = [];
  try {
    let url = `${API}/api/sessions/${SID}/patterns/${patternId}/models`;
    if (occurrenceIndex !== null) url += `?occurrence_index=${occurrenceIndex}`;
    const resp = await fetch(url);
    if (resp.ok) {
      const data = await resp.json();
      matchedModels = data.models || [];
    }
  } catch (e) {
    console.warn('Could not fetch matched models:', e);
  }

  if (matchedModels.length === 0) {
    activeFilter = null;
    return false;
  }

  activeFilter = { patternId, matchedModels };
  renderModelList();

  let pickIdx = -1;
  if (targetModelName) {
    pickIdx = models.findIndex(m => m.name === targetModelName);
    if (pickIdx < 0 || !matchedModels.find(mm => mm.name === targetModelName)) {
      // requested model isn't matched — fall back to first matched
      pickIdx = models.findIndex(m => m.name === matchedModels[0].name);
    }
  } else if (pickFirstModel) {
    pickIdx = models.findIndex(m => m.name === matchedModels[0].name);
  }
  if (pickIdx >= 0) showModel(pickIdx);
  return true;
}

/* ────────── Clear filter ────────── */
function clearFilter() {
  activeFilter = null;
  renderModelList();
  if (curModel >= 0) {
    fetchAndLoadSvg('models', models[curModel].encoded);
  }
  syncClearBtn();
}

function syncClearBtn() {
  const btn = document.getElementById('clear-filter-btn');
  const isModelsTab    = activeTab === 'models';
  const currentIsMatch = activeFilter && curModel >= 0 &&
    activeFilter.matchedModels.some(mm => mm.name === models[curModel]?.name);
  btn.style.display = (isModelsTab && currentIsMatch) ? 'inline-flex' : 'none';
}

/* ────────── Sidebar collapse / resize ────────── */
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
  if (isResizing) { isResizing = false; document.body.style.cursor = ''; }
});

/* ────────── Wheel / drag / pinch interaction ────────── */
['patterns', 'models'].forEach(panel => {
  const vp = document.getElementById('vp-' + panel);
  if (!vp) return;

  vp.addEventListener('dblclick', e => {
    if (isZoomControl(e.target)) return;
    resetZoom(panel);
  });

  vp.addEventListener('wheel', e => {
    e.preventDefault();
    const r = vp.getBoundingClientRect();

    // Normalize wheel delta units: Firefox reports line-mode by default,
    // and some devices report page-mode. Convert both to pixel-like units.
    let dy = e.deltaY;
    if (e.deltaMode === 1)      dy *= 33;   // line units
    else if (e.deltaMode === 2) dy *= 800;  // page units (fallback)

    // Trackpad pinch gestures (Chrome/Edge) set ctrlKey and need higher
    // sensitivity because their deltas are much smaller.
    const intensity = e.ctrlKey ? 0.01 : 0.0015;
    let factor = Math.exp(-dy * intensity);
    // Cap the factor so a single wheel tick doesn't jump too far.
    factor = Math.min(Math.max(factor, 0.8), 1.25);

    zoomAt(panel, factor, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });

  let dragging = false, mx = 0, my = 0;
  vp.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    if (isZoomControl(e.target)) return;
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
    if (isZoomControl(e.target)) return;
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

document.addEventListener('keydown', (e) => {
  const active = document.querySelector('.panel.active');
  if (!active) return;
  const panel = active.id.replace('panel-', '');
  if (e.key === '=' || e.key === '+') zoomStep(panel, 1.25);
  else if (e.key === '-')             zoomStep(panel, 0.8);
  else if (e.key === '0')             resetZoom(panel);
});

/* ──────────────────────────────────────────────────────────────────────────
 * Focus application (boot-time only — see Focus Protocol at top of file)
 * ──────────────────────────────────────────────────────────────────── */
async function applyFocus(focus) {
  const hasPats = meta.num_patterns > 0;
  const hasMods = meta.num_models > 0;

  const f = focus || {};
  // Resolve effective tab
  let tab = f.tab;
  if (tab !== 'patterns' && tab !== 'models') {
    tab = hasMods ? 'models' : (hasPats ? 'patterns' : 'models');
  }
  if (tab === 'models' && !hasMods) tab = 'patterns';
  if (tab === 'patterns' && !hasPats) tab = 'models';

  // Pre-select a pattern (quietly if patterns won't be the visible tab
  // user requested that the selection persist across tabs even if hidden)
  if (hasPats) {
    const targetPid = f.pattern_id ?? (f.highlight && f.highlight.pattern_id);
    if (targetPid != null) {
      await selectPatternById(targetPid, { quiet: tab !== 'patterns' });
    } else {
      // Default: first pattern. Load page 1 so the list is populated.
      await loadPatternPage(0);
      if (tab === 'patterns' && pageMetas.length > 0) {
        showPattern(0, 0);
      } else if (pageMetas.length > 0) {
        // mark first as selected silently
        await selectPatternById(pageMetas[0].id, { quiet: true });
      }
    }
  }

  // Handle highlight (overrides plain model selection)
  let modelHandled = false;
  if (f.highlight && hasMods) {
    const h = f.highlight;
    const ok = await activatePatternHighlight(
      h.pattern_id,
      /*pickFirstModel*/ false,
      /*targetModelName*/ h.model_name || null,
      /*occurrenceIndex*/ h.occurrence_index ?? null,
    );
    modelHandled = ok;
  }

  // Plain model selection (when no highlight handled it)
  if (!modelHandled && hasMods) {
    let idx = -1;
    if (f.model_name) {
      idx = models.findIndex(m => m.name === f.model_name);
    }
    if (idx < 0) idx = 0;
    showModel(idx);
  }

  // Switch to the chosen tab last so its render uses the final state
  switchTab(tab);
}

/* ──────────────────────────────────────────────────────────────────────────
 * Boot
 * ──────────────────────────────────────────────────────────────────── */
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

  if (hasPats && hasMods) {
    document.getElementById('sidebar-tabs').classList.add('visible');
  }

  if (hasMods) {
    const modResp = await fetch(`${API}/api/sessions/${SID}/models`);
    if (modResp.ok) {
      const data = await modResp.json();
      models = data.models;
      renderModelList();
    }
  }

  await applyFocus(meta.focus);
}

boot();
