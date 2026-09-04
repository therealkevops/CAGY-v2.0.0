/**
 * Antigravity (CAGY) Knowledge Vault & 2D Graph Memory Engine
 * Features:
 * - Full-Canvas interactive 2D Force-Directed Knowledge Graph physics engine
 * - Node navigation, drag, zoom/pan, hover glowing, and folder coloring
 * - Direct integration with Right Panel file preview & in-place markdown editor
 * - Backlinks & Linked References inspector rendered right inside the preview panel
 * - Background auto-compilation to native Antigravity rules (.gemini/rules/knowledge_vault.md)
 */

let _vaultData = { nodes: [], edges: [], stats: {} };
let _activeVaultNote = null;
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
 * Load vault graph data and populate left sidebar and 2D canvas.
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

    if (_activeVaultNote) {
      highlightVaultGraphNode(_activeVaultNote.path || _activeVaultNote.id);
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
  const hStats = document.getElementById('vaultHeaderStats');
  const notes = stats.total_notes || 0;
  const edges = stats.total_edges || 0;
  if (mNotes) mNotes.textContent = String(notes);
  if (mEdges) mEdges.textContent = String(edges);
  if (hStats) hStats.textContent = `${notes} notes · ${edges} links`;
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
    const isSel = _activeVaultNote && (_activeVaultNote.path === n.path || _activeVaultNote.id === n.id);
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
 * Open a note into the Right Sidebar Preview/Editor and highlight it in the 2D Graph.
 */
async function loadVaultNote(relPath, openSidebar = true, openInEditor = true) {
  if (!relPath) return;

  let clean = relPath.trim().replace(/^knowledge\//, '');
  if (!clean.endsWith('.md')) clean += '.md';

  try {
    const res = await fetch(`/api/vault/note?path=${encodeURIComponent(clean)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const note = await res.json();
    _activeVaultNote = note;

    renderVaultSidebarList(_vaultData.nodes || []);
    highlightVaultGraphNode(clean);

    if (openSidebar) {
      openVaultNoteInRightSidebar(note, openInEditor);
    }
  } catch (err) {
    showToast(`Could not load note: ${err.message}`, 3000, 'error');
  }
}

/**
 * Delegate viewing & editing to the existing Right Sidebar.
 */
function openVaultNoteInRightSidebar(note, openInEditor = true) {
  if (!note) return;

  const fullPath = 'knowledge/' + (note.path.replace(/^knowledge\//, ''));

  // 1. Set preview state
  _previewCurrentPath = fullPath;
  _previewRawContent = note.content || '';
  _previewRawContentPath = fullPath;
  _previewSaveRoute = '/api/vault/note';
  _previewCurrentMode = 'md';
  _previewDirty = false;

  const previewPathText = document.getElementById('previewPathText');
  if (previewPathText) previewPathText.textContent = fullPath;

  const previewArea = document.getElementById('previewArea');
  if (previewArea) previewArea.classList.add('visible');

  const fileTree = document.getElementById('fileTree');
  if (fileTree) fileTree.style.display = 'none';

  // 2. Expand right sidebar in preview mode
  if (typeof _setWorkspacePanelMode === 'function') {
    _setWorkspacePanelMode('preview');
  } else if (typeof openWorkspacePanel === 'function') {
    openWorkspacePanel('preview');
  } else if (typeof toggleWorkspacePanel === 'function') {
    toggleWorkspacePanel(true);
  }

  // 3. Render rich Markdown preview in right panel (cached for when returning from edit)
  if (typeof renderMarkdownPreviewContent === 'function') {
    renderMarkdownPreviewContent({ content: note.content || '' });
  } else if (typeof renderMd === 'function') {
    const mdEl = document.getElementById('previewMd');
    if (mdEl) {
      mdEl.innerHTML = renderMd(note.content || '');
    }
  }

  // 4. Open in editor directly
  const editArea = document.getElementById('previewEditArea');
  const mdEl = document.getElementById('previewMd');
  const codeEl = document.getElementById('previewCode');

  if (openInEditor && editArea) {
    editArea.value = note.content || '';
    editArea.style.display = '';
    if (mdEl) mdEl.style.display = 'none';
    if (codeEl) codeEl.style.display = 'none';
    editArea.onkeydown = e => {
      if (e.key === 'Escape') {
        e.preventDefault();
        if (typeof cancelEditMode === 'function') cancelEditMode();
      }
    };
    editArea.focus();
  } else {
    if (typeof showPreview === 'function') {
      showPreview('md');
    }
  }

  // 5. Update edit/save toolbar buttons
  if (typeof updateEditBtn === 'function') {
    updateEditBtn();
  } else {
    const btnEdit = document.getElementById('btnEditFile');
    if (btnEdit) btnEdit.style.display = 'inline-flex';
  }

  // 6. Render Linked References (Backlinks) tray in the right panel
  renderVaultRelationsInPreview(note);
}

/**
 * Render backlinks and outgoing links at the bottom of the right panel preview.
 */
function renderVaultRelationsInPreview(note) {
  let container = document.getElementById('previewVaultRelations');
  if (!container) {
    const pArea = document.getElementById('previewArea');
    if (pArea) {
      container = document.createElement('div');
      container.id = 'previewVaultRelations';
      container.className = 'preview-vault-relations';
      pArea.appendChild(container);
    }
  }
  if (!container) return;

  const backlinks = Array.isArray(note.backlinks) ? note.backlinks : [];
  const backlinksHtml = backlinks.length > 0
    ? backlinks.map(b => `<button type="button" class="vault-chip" onclick="loadVaultNote('${escapeAttr(b)}')"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg> ${escapeHtml(b)}</button>`).join('')
    : '<span class="vault-empty-text">No incoming backlinks</span>';

  const outgoing = Array.isArray(note.outgoing_links) ? note.outgoing_links : [];
  const outgoingHtml = outgoing.length > 0
    ? outgoing.map(b => `<button type="button" class="vault-chip outgoing" onclick="loadVaultNote('${escapeAttr(b)}')"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg> ${escapeHtml(b)}</button>`).join('')
    : '<span class="vault-empty-text">No outgoing links</span>';

  container.innerHTML = `
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
  `;
  container.style.display = 'flex';
}

/**
 * Helper called by workspace.js when any file starting with knowledge/ is viewed.
 */
async function renderVaultRelationsForCurrentPreview(path) {
  if (!path || !path.startsWith('knowledge/')) return;
  const rel = path.replace(/^knowledge\//, '');
  try {
    const res = await fetch(`/api/vault/note?path=${encodeURIComponent(rel)}`);
    if (res.ok) {
      const note = await res.json();
      _activeVaultNote = note;
      renderVaultRelationsInPreview(note);
      highlightVaultGraphNode(note.path);
    }
  } catch (_) {}
}

function highlightVaultGraphNode(pathOrId) {
  if (!_vaultData.nodes) return;
  const clean = pathOrId.replace(/^knowledge\//, '').replace(/\.md$/, '');
  const match = _vaultData.nodes.find(n => n.id === clean || n.path === (clean + '.md') || n.path === pathOrId);
  if (match) {
    _hoverNode = match;
    triggerGraphRepaint();
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

  const title = clean.split('/').pop().replace('.md', '').replace(/_/g, ' ');
  const defaultContent = `# ${title.charAt(0).toUpperCase() + title.slice(1)}\n\nAdd your knowledge and link to other concepts using [[wikilinks]]!\n`;
  saveNewNote(clean, defaultContent);
}

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
    const dist = 140 + Math.random() * 90;
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
      radius: Math.max(8, Math.min(20, 7 + (n.total_connections || 1) * 2)),
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
  const maxIterations = 300;

  function step() {
    if (!_vaultSimRunning) return;

    // Physics constants
    const repulsion = 1100;
    const springLen = 130;
    const springK = 0.04;
    const damping = 0.83;
    const centerPull = 0.012;

    const canvas = document.getElementById('vaultGraphCanvas');
    const cx = canvas ? canvas.width / 2 : 400;
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

  // Subtle grid dots
  ctx.fillStyle = 'rgba(255, 255, 255, 0.04)';
  for (let x = -400; x < canvas.width + 600; x += 40) {
    for (let y = -400; y < canvas.height + 600; y += 40) {
      ctx.fillRect(x, y, 1.5, 1.5);
    }
  }

  // Draw links
  ctx.lineWidth = 1.3;
  _graphEdges.forEach(e => {
    const isHovered = _hoverNode && (e.source.id === _hoverNode.id || e.target.id === _hoverNode.id);
    const isSelected = _activeVaultNote && (_activeVaultNote.id === e.source.id || _activeVaultNote.id === e.target.id);
    ctx.strokeStyle = (isHovered || isSelected) ? 'rgba(2, 136, 168, 0.7)' : 'rgba(255, 255, 255, 0.12)';
    ctx.beginPath();
    ctx.moveTo(e.source.x, e.source.y);
    ctx.lineTo(e.target.x, e.target.y);
    ctx.stroke();
  });

  // Draw nodes
  _graphNodes.forEach(n => {
    const isSelected = _activeVaultNote && (_activeVaultNote.id === n.id || _activeVaultNote.path === n.path);
    const isHovered = _hoverNode && _hoverNode.id === n.id;

    // Glow ring
    if (isSelected || isHovered) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius + 7, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(2, 136, 168, 0.28)';
      ctx.fill();
    }

    // Node body
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
    ctx.fillStyle = isSelected ? '#ffffff' : n.color;
    ctx.fill();
    ctx.strokeStyle = isSelected ? 'var(--accent)' : 'rgba(0,0,0,0.45)';
    ctx.lineWidth = 1.6;
    ctx.stroke();

    // Node label
    ctx.fillStyle = isSelected ? '#ffffff' : 'rgba(255, 255, 255, 0.88)';
    ctx.font = isSelected ? 'bold 11px system-ui, sans-serif' : '10.5px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(n.title, n.x, n.y + n.radius + 14);
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
    _vaultZoom = Math.max(0.25, Math.min(3.5, _vaultZoom * zoomFactor));
    drawGraph();
  }, { passive: false });

  canvas.addEventListener('mousedown', (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left - _vaultPan.x) / _vaultZoom;
    const my = (e.clientY - rect.top - _vaultPan.y) / _vaultZoom;
    _lastMousePos = { x: e.clientX, y: e.clientY };

    const clicked = _graphNodes.find(n => {
      const d = Math.sqrt((n.x - mx) ** 2 + (n.y - my) ** 2);
      return d <= n.radius + 5;
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
      return d <= n.radius + 5;
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
