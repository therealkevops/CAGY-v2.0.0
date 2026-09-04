/**
 * Antigravity (CAGY) Knowledge Vault & Graph Memory Engine
 * Provides an interactive 2D Force-Directed Knowledge Graph, bi-directional
 * wikilink navigation, note editing, backlinks inspection, and rules synchronization.
 */

let _vaultData = { nodes: [], edges: [], stats: {} };
let _activeVaultNote = null;
let _vaultViewMode = 'split'; // 'graph' | 'editor' | 'split'
let _vaultSimRunning = false;
let _vaultZoom = 1.0;
let _vaultPan = { x: 0, y: 0 };
let _isPanning = false;
let _dragNode = null;
let _hoverNode = null;
let _lastMousePos = { x: 0, y: 0 };

const FOLDER_COLORS = {
  user: '#3b82f6',         // Blue
  architecture: '#10b981', // Emerald green
  decisions: '#f59e0b',    // Amber gold
  journal: '#8b5cf6',      // Purple
  root: '#0288a8',         // Cyan / accent
  other: '#6b7280'         // Gray
};

/**
 * Load vault graph data and populate UI.
 */
async function loadVault(force = false) {
  const listEl = document.getElementById('vaultNoteList');
  if (listEl && (!listEl.children.length || force)) {
    listEl.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">Scanning vault...</div>';
  }

  try {
    const res = await fetch('/api/vault/graph');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _vaultData = data;

    updateVaultMetrics(data.stats || {});
    renderVaultSidebarList(data.nodes || []);
    initVaultGraph(data.nodes || [], data.edges || []);

    if (!_activeVaultNote && data.nodes && data.nodes.length > 0) {
      loadVaultNote(data.nodes[0].path);
    }
  } catch (err) {
    if (listEl) {
      listEl.innerHTML = `<div style="padding:12px;color:#ef4444;font-size:12px">Failed to load vault: ${escapeHtml(err.message)}</div>`;
    }
  }
}

function updateVaultMetrics(stats) {
  const mNotes = document.getElementById('vaultMetricNotes');
  const mEdges = document.getElementById('vaultMetricEdges');
  if (mNotes) mNotes.textContent = String(stats.total_notes || 0);
  if (mEdges) mEdges.textContent = String(stats.total_edges || 0);
}

function renderVaultSidebarList(nodes, filterText = '') {
  const listEl = document.getElementById('vaultNoteList');
  if (!listEl) return;

  const q = (filterText || '').toLowerCase().trim();
  const filtered = nodes.filter(n =>
    !q || n.title.toLowerCase().includes(q) || n.path.toLowerCase().includes(q) || n.folder.toLowerCase().includes(q)
  );

  if (!filtered.length) {
    listEl.innerHTML = '<div class="vault-empty-list">No matching notes found.</div>';
    return;
  }

  listEl.innerHTML = filtered.map(n => {
    const isSel = _activeVaultNote && _activeVaultNote.path === n.path;
    const color = FOLDER_COLORS[n.folder] || FOLDER_COLORS.other;
    return `
      <div class="vault-sidebar-item ${isSel ? 'selected' : ''}" onclick="loadVaultNote('${escapeAttr(n.path)}')">
        <div class="vault-item-top">
          <span class="vault-folder-dot" style="background:${color}"></span>
          <span class="vault-item-title">${escapeHtml(n.title)}</span>
        </div>
        <div class="vault-item-meta">
          <span class="vault-folder-tag">${escapeHtml(n.folder)}</span>
          <span>${n.links_count} links · ${n.backlinks_count} backlinks</span>
        </div>
      </div>
    `;
  }).join('');
}

function filterVaultNotes(val) {
  renderVaultSidebarList(_vaultData.nodes || [], val);
}

/**
 * Load a note into the editor and inspect its backlinks.
 */
async function loadVaultNote(relPath) {
  if (!relPath) return;

  try {
    const res = await fetch(`/api/vault/note?path=${encodeURIComponent(relPath)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const note = await res.json();
    _activeVaultNote = note;

    renderVaultSidebarList(_vaultData.nodes || []);
    renderVaultEditor(note);

    // Highlight node in graph
    if (_vaultData.nodes) {
      const match = _vaultData.nodes.find(n => n.id === note.id || n.path === note.path);
      if (match) {
        _hoverNode = match;
        triggerGraphRepaint();
      }
    }
  } catch (err) {
    showToast(`Could not load note: ${err.message}`, 3000, 'error');
  }
}

function renderVaultEditor(note) {
  const container = document.getElementById('vaultEditorContainer');
  if (!container) return;

  const folder = note.path.includes('/') ? note.path.split('/')[0] : 'root';
  const color = FOLDER_COLORS[folder] || FOLDER_COLORS.other;

  // Render clickable incoming backlinks
  const backlinks = Array.isArray(note.backlinks) ? note.backlinks : [];
  const backlinksHtml = backlinks.length > 0
    ? backlinks.map(b => `<button type="button" class="vault-chip" onclick="loadVaultNote('${escapeAttr(b)}')"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg> ${escapeHtml(b)}</button>`).join('')
    : '<span class="vault-empty-text">No incoming backlinks yet.</span>';

  // Render clickable outgoing links
  const outgoing = Array.isArray(note.outgoing_links) ? note.outgoing_links : [];
  const outgoingHtml = outgoing.length > 0
    ? outgoing.map(b => `<button type="button" class="vault-chip outgoing" onclick="loadVaultNote('${escapeAttr(b)}')"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg> ${escapeHtml(b)}</button>`).join('')
    : '<span class="vault-empty-text">No outgoing links.</span>';

  container.innerHTML = `
    <div class="vault-editor-card">
      <div class="vault-editor-header">
        <div class="vault-title-wrap">
          <span class="vault-folder-badge" style="border-color:${color};color:${color}">${escapeHtml(folder)}</span>
          <input type="text" class="vault-note-title-input" id="vaultNoteTitle" value="${escapeAttr(note.title)}" readonly>
          <span class="vault-path-sub">${escapeHtml(note.path)}</span>
        </div>
        <div class="vault-editor-actions">
          <button type="button" class="btn-vault-action primary" onclick="saveActiveVaultNote()">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg> Save
          </button>
          <button type="button" class="btn-vault-action danger" onclick="deleteActiveVaultNote()">Delete</button>
        </div>
      </div>
      <div class="vault-editor-body">
        <textarea class="vault-textarea" id="vaultNoteTextarea" placeholder="Write Markdown with [[wikilinks]]...">${escapeHtml(note.content || '')}</textarea>
      </div>
      <div class="vault-relations-footer">
        <div class="vault-relation-block">
          <span class="vault-relation-title">Linked References (Backlinks):</span>
          <div class="vault-chips-row">${backlinksHtml}</div>
        </div>
        <div class="vault-relation-block">
          <span class="vault-relation-title">Outgoing Links:</span>
          <div class="vault-chips-row">${outgoingHtml}</div>
        </div>
      </div>
    </div>
  `;
}

/**
 * Save the currently open note.
 */
async function saveActiveVaultNote() {
  if (!_activeVaultNote) return;
  const textarea = document.getElementById('vaultNoteTextarea');
  if (!textarea) return;

  const content = textarea.value;
  try {
    const res = await fetch('/api/vault/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: _activeVaultNote.path, content: content })
    });
    const data = await res.json();
    if (data.ok) {
      showToast('Note saved & knowledge rules synchronized!', 2000, 'success');
      loadVault(true);
    } else {
      showToast(data.error || 'Failed to save note', 3000, 'error');
    }
  } catch (err) {
    showToast(err.message, 3000, 'error');
  }
}

/**
 * Delete active note with confirmation.
 */
async function deleteActiveVaultNote() {
  if (!_activeVaultNote) return;
  if (!confirm(`Are you sure you want to delete "${_activeVaultNote.path}"?`)) return;

  try {
    const res = await fetch('/api/vault/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: _activeVaultNote.path })
    });
    const data = await res.json();
    if (data.ok) {
      showToast('Note deleted', 2000);
      _activeVaultNote = null;
      loadVault(true);
    } else {
      showToast(data.error || 'Failed to delete note', 3000, 'error');
    }
  } catch (err) {
    showToast(err.message, 3000, 'error');
  }
}

/**
 * Prompt user to create a new note in the vault.
 */
function promptCreateVaultNote() {
  const noteName = prompt('Enter note path (e.g. architecture/memory_graph or user/preferences):');
  if (!noteName || !noteName.trim()) return;

  let clean = noteName.trim().replace(/\\/g, '/').replace(/^\/+/, '');
  if (!clean.endsWith('.md')) clean += '.md';

  const defaultContent = `# ${clean.split('/').pop().replace('.md', '').replace(/_/g, ' ').titleCase()}\n\nAdd your knowledge and link to other concepts using [[wikilinks]]!\n`;
  saveNewNote(clean, defaultContent);
}

String.prototype.titleCase = function() {
  return this.replace(/\w\S*/g, function(txt) { return txt.charAt(0).toUpperCase() + txt.substr(1).toLowerCase(); });
};

async function saveNewNote(path, content) {
  try {
    const res = await fetch('/api/vault/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path, content: content })
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`Created ${path}`, 2000, 'success');
      await loadVault(true);
      loadVaultNote(path);
    } else {
      showToast(data.error || 'Error creating note', 3000, 'error');
    }
  } catch (err) {
    showToast(err.message, 3000, 'error');
  }
}

/**
 * Compile vault knowledge directly into native Antigravity rules.
 */
async function syncVaultRules() {
  const btn = document.getElementById('btnSyncVaultRules');
  if (btn) btn.disabled = true;

  try {
    const res = await fetch('/api/vault/sync', { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      showToast(`Synced ${data.total_compiled_notes} notes to native Antigravity rules!`, 3000, 'success');
    } else {
      showToast(data.error || 'Sync failed', 3000, 'error');
    }
  } catch (err) {
    showToast(err.message, 3000, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

/**
 * Switch view mode: 'graph' (graph only), 'editor' (editor only), 'split' (both side by side).
 */
function switchVaultView(mode) {
  _vaultViewMode = mode;
  document.querySelectorAll('.vault-view-toggle-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === mode);
  });

  const graphBox = document.getElementById('vaultGraphBox');
  const editorBox = document.getElementById('vaultEditorBox');

  if (graphBox && editorBox) {
    if (mode === 'graph') {
      graphBox.style.display = 'flex';
      graphBox.style.flex = '1';
      editorBox.style.display = 'none';
    } else if (mode === 'editor') {
      graphBox.style.display = 'none';
      editorBox.style.display = 'flex';
      editorBox.style.flex = '1';
    } else {
      graphBox.style.display = 'flex';
      graphBox.style.flex = '1';
      editorBox.style.display = 'flex';
      editorBox.style.flex = '1';
    }
  }
  resizeGraphCanvas();
}

/* ─────────────────────────────────────────────────────────────
   2D Force-Directed Graph Engine (Pure Zero-Dependency Canvas)
   ───────────────────────────────────────────────────────────── */

let _graphNodes = [];
let _graphEdges = [];
let _animFrameId = null;

function initVaultGraph(nodes, edges) {
  const canvas = document.getElementById('vaultGraphCanvas');
  if (!canvas) return;

  const nodeMap = {};
  _graphNodes = nodes.map((n, i) => {
    const angle = (i / Math.max(nodes.length, 1)) * 2 * Math.PI;
    const dist = 120 + Math.random() * 80;
    const gNode = {
      id: n.id,
      title: n.title,
      path: n.path,
      folder: n.folder,
      connections: n.total_connections || 1,
      x: canvas.width / 2 + Math.cos(angle) * dist,
      y: canvas.height / 2 + Math.sin(angle) * dist,
      vx: (Math.random() - 0.5) * 2,
      vy: (Math.random() - 0.5) * 2,
      radius: Math.max(7, Math.min(18, 6 + (n.total_connections || 1) * 2)),
      color: FOLDER_COLORS[n.folder] || FOLDER_COLORS.other
    };
    nodeMap[n.id] = gNode;
    return gNode;
  });

  _graphEdges = edges.map(e => {
    return {
      source: nodeMap[e.source] || null,
      target: nodeMap[e.target] || null
    };
  }).filter(e => e.source && e.target);

  setupCanvasListeners(canvas);
  resizeGraphCanvas();
  startGraphSimulation();
}

function resizeGraphCanvas() {
  const canvas = document.getElementById('vaultGraphCanvas');
  if (!canvas) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  if (rect.width > 0 && rect.height > 0) {
    canvas.width = rect.width;
    canvas.height = rect.height;
    triggerGraphRepaint();
  }
}

function startGraphSimulation() {
  _vaultSimRunning = true;
  let iterations = 0;
  const maxIterations = 280;

  function step() {
    if (!_vaultSimRunning) return;

    // Physics constants
    const repulsion = 900;
    const springLen = 110;
    const springK = 0.04;
    const damping = 0.82;
    const centerPull = 0.015;

    const canvas = document.getElementById('vaultGraphCanvas');
    const cx = canvas ? canvas.width / 2 : 300;
    const cy = canvas ? canvas.height / 2 : 300;

    // 1. Repulsion between all node pairs
    for (let i = 0; i < _graphNodes.length; i++) {
      const n1 = _graphNodes[i];
      for (let j = i + 1; j < _graphNodes.length; j++) {
        const n2 = _graphNodes[j];
        const dx = n2.x - n1.x;
        const dy = n2.y - n1.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = repulsion / (dist * dist);
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;

        n1.vx -= fx;
        n1.vy -= fy;
        n2.vx += fx;
        n2.vy += fy;
      }

      // Center gravity
      n1.vx += (cx - n1.x) * centerPull;
      n1.vy += (cy - n1.y) * centerPull;
    }

    // 2. Spring attraction along links
    _graphEdges.forEach(e => {
      const dx = e.target.x - e.source.x;
      const dy = e.target.y - e.source.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = (dist - springLen) * springK;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;

      e.source.vx += fx;
      e.source.vy += fy;
      e.target.vx -= fx;
      e.target.vy -= fy;
    });

    // 3. Update positions
    _graphNodes.forEach(n => {
      if (n !== _dragNode) {
        n.vx *= damping;
        n.vy *= damping;
        n.x += n.vx;
        n.y += n.vy;
      }
    });

    drawGraph();

    iterations++;
    if (iterations < maxIterations || _dragNode) {
      _animFrameId = requestAnimationFrame(step);
    } else {
      _vaultSimRunning = false;
    }
  }

  cancelAnimationFrame(_animFrameId);
  _animFrameId = requestAnimationFrame(step);
}

function triggerGraphRepaint() {
  if (!_vaultSimRunning) {
    startGraphSimulation();
  }
}

function drawGraph() {
  const canvas = document.getElementById('vaultGraphCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  ctx.save();
  ctx.translate(_vaultPan.x, _vaultPan.y);
  ctx.scale(_vaultZoom, _vaultZoom);

  // Draw subtle grid dots
  ctx.fillStyle = 'rgba(255, 255, 255, 0.04)';
  for (let x = -200; x < canvas.width + 400; x += 40) {
    for (let y = -200; y < canvas.height + 400; y += 40) {
      ctx.fillRect(x, y, 1.5, 1.5);
    }
  }

  // Draw links
  ctx.lineWidth = 1.2;
  _graphEdges.forEach(e => {
    const isHovered = _hoverNode && (e.source.id === _hoverNode.id || e.target.id === _hoverNode.id);
    ctx.strokeStyle = isHovered ? 'rgba(2, 136, 168, 0.6)' : 'rgba(255, 255, 255, 0.12)';
    ctx.beginPath();
    ctx.moveTo(e.source.x, e.source.y);
    ctx.lineTo(e.target.x, e.target.y);
    ctx.stroke();
  });

  // Draw nodes
  _graphNodes.forEach(n => {
    const isSelected = _activeVaultNote && (_activeVaultNote.id === n.id || _activeVaultNote.path === n.path);
    const isHovered = _hoverNode && _hoverNode.id === n.id;

    // Node glow
    if (isSelected || isHovered) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius + 6, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(2, 136, 168, 0.25)';
      ctx.fill();
    }

    // Node body
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
    ctx.fillStyle = isSelected ? '#fff' : n.color;
    ctx.fill();
    ctx.strokeStyle = isSelected ? 'var(--accent)' : 'rgba(0,0,0,0.4)';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Node label
    ctx.fillStyle = isSelected ? '#fff' : 'rgba(255, 255, 255, 0.85)';
    ctx.font = isSelected ? 'bold 11px system-ui, sans-serif' : '10px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(n.title, n.x, n.y + n.radius + 13);
  });

  ctx.restore();
}

function setupCanvasListeners(canvas) {
  if (canvas._hasListeners) return;
  canvas._hasListeners = true;

  window.addEventListener('resize', resizeGraphCanvas);

  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.08 : 0.92;
    _vaultZoom = Math.max(0.3, Math.min(3.0, _vaultZoom * zoomFactor));
    drawGraph();
  }, { passive: false });

  canvas.addEventListener('mousedown', (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left - _vaultPan.x) / _vaultZoom;
    const my = (e.clientY - rect.top - _vaultPan.y) / _vaultZoom;
    _lastMousePos = { x: e.clientX, y: e.clientY };

    const clicked = _graphNodes.find(n => {
      const d = Math.sqrt((n.x - mx) ** 2 + (n.y - my) ** 2);
      return d <= n.radius + 4;
    });

    if (clicked) {
      _dragNode = clicked;
      loadVaultNote(clicked.path);
      triggerGraphRepaint();
    } else {
      _isPanning = true;
    }
  });

  window.addEventListener('mousemove', (e) => {
    if (_isPanning) {
      _vaultPan.x += e.clientX - _lastMousePos.x;
      _vaultPan.y += e.clientY - _lastMousePos.y;
      _lastMousePos = { x: e.clientX, y: e.clientY };
      drawGraph();
      return;
    }

    if (_dragNode) {
      const canvas = document.getElementById('vaultGraphCanvas');
      const rect = canvas.getBoundingClientRect();
      _dragNode.x = (e.clientX - rect.left - _vaultPan.x) / _vaultZoom;
      _dragNode.y = (e.clientY - rect.top - _vaultPan.y) / _vaultZoom;
      _lastMousePos = { x: e.clientX, y: e.clientY };
      triggerGraphRepaint();
      return;
    }

    // Hover detection
    const canvas = document.getElementById('vaultGraphCanvas');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left - _vaultPan.x) / _vaultZoom;
    const my = (e.clientY - rect.top - _vaultPan.y) / _vaultZoom;

    const hover = _graphNodes.find(n => {
      const d = Math.sqrt((n.x - mx) ** 2 + (n.y - my) ** 2);
      return d <= n.radius + 4;
    });

    if (hover !== _hoverNode) {
      _hoverNode = hover || null;
      canvas.style.cursor = hover ? 'pointer' : 'default';
      drawGraph();
    }
  });

  window.addEventListener('mouseup', () => {
    _isPanning = false;
    _dragNode = null;
  });
}

function resetGraphView() {
  _vaultZoom = 1.0;
  _vaultPan = { x: 0, y: 0 };
  triggerGraphRepaint();
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function escapeAttr(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
