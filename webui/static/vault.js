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
let _allGraphNodes = [];
let _allGraphEdges = [];
let _currentGraphDepth = 'all'; // 'all', '1', '2'
let _activeTagFilter = null;
let _activeVaultNote = null;
let _vaultSimRunning = false;
let _vaultZoom = 1.0;
let _vaultPan = { x: 0, y: 0 };
let _isPanning = false;
let _dragNode = null;
let _hoverNode = null;
let _lastMousePos = { x: 0, y: 0 };
let _vaultResizeObserver = null;

let _vaultSearchMode = 'name'; // 'name' | 'content'
let _vaultSearchDebounceTimer = null;
let _vaultHealthFilter = 'all'; // 'all' | 'orphans' | 'unresolved'
let _disabledLegendFolders = new Set();
let _selectedVaultCategory = 'decisions';
let _nextAdrNumber = 1;

let _currentGraphSpacing = 'spacious';
try {
  const savedSpacing = localStorage.getItem('agy-vault-spacing');
  if (savedSpacing && ['compact', 'spacious', 'relaxed'].includes(savedSpacing)) {
    _currentGraphSpacing = savedSpacing;
  }
} catch (_) {}

const SPACING_CONFIGS = {
  compact: {
    repulsion: 3200,
    springLen: 170,
    springK: 0.045,
    damping: 0.83,
    centerPull: 0.006,
    minDistance: 50,
    spawnRadius: 180
  },
  spacious: {
    repulsion: 7500,
    springLen: 270,
    springK: 0.035,
    damping: 0.85,
    centerPull: 0.0025,
    minDistance: 80,
    spawnRadius: 280
  },
  relaxed: {
    repulsion: 15000,
    springLen: 400,
    springK: 0.025,
    damping: 0.88,
    centerPull: 0.0012,
    minDistance: 115,
    spawnRadius: 400
  }
};

const FOLDER_COLORS = {
  user: '#3b82f6',         // Blue
  architecture: '#10b981', // Emerald green
  decisions: '#f59e0b',    // Amber gold
  notes: '#8b5cf6',        // Purple
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
    _allGraphNodes = data.nodes || [];
    _allGraphEdges = data.edges || [];

    updateVaultMetrics(data.stats || {});
    const sBtns = document.querySelectorAll('#vaultSpacingFilter .vault-depth-btn');
    sBtns.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.spacing === _currentGraphSpacing);
    });
    renderVaultTagFilter((data.stats && data.stats.all_tags) || []);
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
  const mOrphans = document.getElementById('vaultMetricOrphans');
  const mUnresolved = document.getElementById('vaultMetricUnresolved');
  const hStats = document.getElementById('vaultHeaderStats');

  const notes = stats.total_notes || 0;
  const edges = stats.total_edges || 0;
  const orphans = (typeof stats.orphan_count === 'number')
    ? stats.orphan_count
    : (Array.isArray(stats.orphans) ? stats.orphans.length : 0);
  const unresolved = (typeof stats.unresolved_count === 'number')
    ? stats.unresolved_count
    : (Array.isArray(stats.unresolved_links) ? stats.unresolved_links.length : 0);

  if (mNotes) mNotes.textContent = String(notes);
  if (mEdges) mEdges.textContent = String(edges);
  if (mOrphans) {
    mOrphans.textContent = String(orphans);
    mOrphans.classList.toggle('warn', orphans > 0);
  }
  if (mUnresolved) {
    mUnresolved.textContent = String(unresolved);
    mUnresolved.classList.toggle('alert', unresolved > 0);
  }
  if (hStats && _currentGraphDepth === 'all') {
    hStats.textContent = `${notes} notes · ${edges} links`;
  }
}

function setVaultSearchMode(mode) {
  _vaultSearchMode = mode;
  const btnName = document.getElementById('vaultSearchModeName');
  const btnContent = document.getElementById('vaultSearchModeContent');
  if (btnName) btnName.classList.toggle('active', mode === 'name');
  if (btnContent) btnContent.classList.toggle('active', mode === 'content');

  const input = document.getElementById('vaultSearchInput');
  if (input) {
    input.placeholder = mode === 'content' ? 'Search full text body...' : 'Search notes or #tags...';
    onVaultSearchInput(input.value);
  }
}

function onVaultSearchInput(val) {
  if (_vaultSearchMode === 'name') {
    renderVaultSidebarList(_vaultData.nodes || [], val);
    return;
  }

  // Full-text body search with debounce
  clearTimeout(_vaultSearchDebounceTimer);
  const q = (val || '').trim();
  if (!q) {
    renderVaultSidebarList(_vaultData.nodes || [], '');
    return;
  }

  const listEl = document.getElementById('vaultNoteList');
  if (listEl) {
    listEl.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">Searching vault body...</div>';
  }

  _vaultSearchDebounceTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/vault/search?q=${encodeURIComponent(q)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderVaultSearchResults(data.results || [], q);
    } catch (err) {
      if (listEl) {
        listEl.innerHTML = `<div style="padding:12px;color:#ef4444;font-size:12px">Search failed: ${escapeHtml(err.message)}</div>`;
      }
    }
  }, 220);
}

function renderVaultSearchResults(results, query) {
  const listEl = document.getElementById('vaultNoteList');
  if (!listEl) return;

  if (!results.length) {
    listEl.innerHTML = `<div class="vault-empty-list">No notes containing &ldquo;${escapeHtml(query)}&rdquo;</div>`;
    return;
  }

  listEl.innerHTML = results.map(r => {
    const isSel = _activeVaultNote && (_activeVaultNote.path === r.path || _activeVaultNote.path === ('knowledge/' + r.path));
    const color = FOLDER_COLORS[r.folder] || FOLDER_COLORS.other;
    const snippetsHtml = (r.snippets && r.snippets.length)
      ? `<div class="vault-search-snippets">${r.snippets.slice(0, 2).map(s => `
          <div class="vault-search-snippet">
            <span class="vault-snippet-line">L${s.line}:</span> ${s.text}
          </div>
        `).join('')}</div>`
      : '';

    return `
      <div class="vault-sidebar-item ${isSel ? 'selected' : ''}" onclick="loadVaultNote('${escapeAttr(r.path)}', true, false)">
        <div class="vault-item-top">
          <span class="vault-folder-dot" style="background:${color}"></span>
          <span class="vault-item-title">${escapeHtml(r.title)}</span>
          <span class="vault-search-score" title="Relevance match score">${r.score}</span>
        </div>
        <div class="vault-item-meta">
          <span class="vault-folder-tag">${escapeHtml(r.folder)}</span>
          <span>${r.matches_count} hit${r.matches_count === 1 ? '' : 's'}</span>
        </div>
        ${snippetsHtml}
      </div>
    `;
  }).join('');
}

async function setVaultHealthFilter(filter) {
  if (_vaultHealthFilter === filter) {
    _vaultHealthFilter = 'all';
  } else {
    _vaultHealthFilter = filter;
  }

  const elOrphans = document.getElementById('vaultMetricOrphansWrap');
  const elUnresolved = document.getElementById('vaultMetricUnresolvedWrap');
  if (elOrphans) elOrphans.classList.toggle('active', _vaultHealthFilter === 'orphans');
  if (elUnresolved) elUnresolved.classList.toggle('active', _vaultHealthFilter === 'unresolved');

  const listEl = document.getElementById('vaultNoteList');
  if (!listEl) return;

  if (_vaultHealthFilter === 'orphans') {
    const orphans = (_vaultData.nodes || []).filter(n => (n.total_connections || 0) === 0 || ((n.in_degree || 0) === 0 && (n.out_degree || 0) === 0));
    if (!orphans.length) {
      listEl.innerHTML = '<div class="vault-empty-list" style="color:var(--accent)">✨ No orphan notes found! All notes are connected.</div>';
      return;
    }
    listEl.innerHTML = `
      <div class="vault-health-filter-banner">
        <span>Showing <b>${orphans.length}</b> orphan notes (0 connections)</span>
        <button type="button" class="vault-clear-filter-btn" onclick="setVaultHealthFilter('all')">&times; Clear</button>
      </div>
    ` + orphans.map(n => {
      const color = FOLDER_COLORS[n.folder] || FOLDER_COLORS.other;
      return `
        <div class="vault-sidebar-item" onclick="loadVaultNote('${escapeAttr(n.path)}', true, false)">
          <div class="vault-item-top">
            <span class="vault-folder-dot" style="background:${color}"></span>
            <span class="vault-item-title">${escapeHtml(n.title)}</span>
          </div>
          <div class="vault-item-meta">
            <span class="vault-folder-tag">${escapeHtml(n.folder)}</span>
            <span style="color:#f59e0b;font-weight:600">0 connections</span>
          </div>
        </div>
      `;
    }).join('');
  } else if (_vaultHealthFilter === 'unresolved') {
    listEl.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">Analyzing unresolved links...</div>';
    try {
      const res = await fetch('/api/vault/health');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const health = await res.json();
      const unresolved = health.unresolved_links || [];

      if (!unresolved.length) {
        listEl.innerHTML = '<div class="vault-empty-list" style="color:var(--accent)">✨ No unresolved links! All wikilinks resolve properly.</div>';
        return;
      }

      listEl.innerHTML = `
        <div class="vault-health-filter-banner">
          <span><b>${unresolved.length}</b> unresolved wikilinks</span>
          <button type="button" class="vault-clear-filter-btn" onclick="setVaultHealthFilter('all')">&times; Clear</button>
        </div>
      ` + unresolved.map(u => {
        const targetClean = escapeAttr(u.target);
        const targetDisplay = escapeHtml(u.target);
        const refCount = u.occurrences || (u.sources ? u.sources.length : 1);
        const srcText = (u.sources || []).map(s => escapeHtml(s)).join(', ');
        return `
          <div class="vault-sidebar-item vault-unresolved-item">
            <div class="vault-item-top">
              <span class="vault-folder-dot" style="background:#ef4444"></span>
              <span class="vault-item-title" style="color:#ef4444">[[${targetDisplay}]]</span>
              <button type="button" class="vault-create-missing-btn" onclick="promptCreateVaultNote('notes', '${targetClean}')">+ Create</button>
            </div>
            <div class="vault-item-meta" style="flex-direction:column;align-items:flex-start;gap:2px">
              <span>Referenced in: ${srcText || `${refCount} note(s)`}</span>
            </div>
          </div>
        `;
      }).join('');
    } catch (err) {
      listEl.innerHTML = `<div style="padding:12px;color:#ef4444;font-size:12px">Error: ${escapeHtml(err.message)}</div>`;
    }
  } else {
    // 'all'
    const searchVal = document.getElementById('vaultSearchInput') ? document.getElementById('vaultSearchInput').value : '';
    renderVaultSidebarList(_vaultData.nodes || [], searchVal);
  }
}

function renderVaultTagFilter(allTags) {
  const container = document.getElementById('vaultTagFilter');
  if (!container) return;
  if (!allTags || !allTags.length) {
    container.style.display = 'none';
    container.innerHTML = '';
    return;
  }
  container.style.display = 'flex';
  let html = `<button type="button" class="vault-tag-pill ${!_activeTagFilter ? 'active' : ''}" onclick="setVaultTagFilter(null)">All</button>`;
  allTags.forEach(t => {
    const isAct = _activeTagFilter === t;
    html += `<button type="button" class="vault-tag-pill ${isAct ? 'active' : ''}" onclick="setVaultTagFilter('${escapeAttr(t)}')">#${escapeHtml(t)}</button>`;
  });
  container.innerHTML = html;
}

function setVaultTagFilter(tag) {
  _activeTagFilter = (_activeTagFilter === tag) ? null : tag;
  renderVaultTagFilter(_vaultData.stats && _vaultData.stats.all_tags);
  const searchVal = document.getElementById('vaultSearchInput') ? document.getElementById('vaultSearchInput').value : '';
  renderVaultSidebarList(_vaultData.nodes || [], searchVal);
}

function renderVaultSidebarList(nodes, filterText = '') {
  const listEl = document.getElementById('vaultNoteList');
  if (!listEl) return;

  const rawQ = (filterText || '').trim();
  let tagSearch = _activeTagFilter;
  let textSearch = rawQ;

  if (rawQ.startsWith('#')) {
    tagSearch = rawQ.slice(1).toLowerCase();
    textSearch = '';
  }

  const q = textSearch.toLowerCase();
  const filtered = nodes.filter(n => {
    if (tagSearch) {
      const nTags = Array.isArray(n.tags) ? n.tags.map(t => t.toLowerCase()) : [];
      if (!nTags.some(t => t.includes(tagSearch.toLowerCase()))) return false;
    }
    if (q) {
      const matchText = n.title.toLowerCase().includes(q) ||
                        n.path.toLowerCase().includes(q) ||
                        n.folder.toLowerCase().includes(q) ||
                        (Array.isArray(n.tags) && n.tags.some(t => t.toLowerCase().includes(q)));
      if (!matchText) return false;
    }
    return true;
  });

  if (!filtered.length) {
    listEl.innerHTML = '<div class="vault-empty-list">No matching notes found.</div>';
    return;
  }

  listEl.innerHTML = filtered.map(n => {
    const isSel = _activeVaultNote && (_activeVaultNote.path === n.path || _activeVaultNote.id === n.id);
    const color = FOLDER_COLORS[n.folder] || FOLDER_COLORS.other;
    const tagsHtml = (Array.isArray(n.tags) && n.tags.length > 0)
      ? `<div class="vault-item-tags">${n.tags.map(t => `<span class="vault-item-tag" onclick="event.stopPropagation();setVaultTagFilter('${escapeAttr(t)}')">#${escapeHtml(t)}</span>`).join('')}</div>`
      : '';
    return `
      <div class="vault-sidebar-item ${isSel ? 'selected' : ''}" onclick="loadVaultNote('${escapeAttr(n.path)}', true, false)">
        <div class="vault-item-top">
          <span class="vault-folder-dot" style="background:${color}"></span>
          <span class="vault-item-title">${escapeHtml(n.title)}</span>
        </div>
        <div class="vault-item-meta">
          <span class="vault-folder-tag">${escapeHtml(n.folder)}</span>
          <span>${n.links_count} links · ${n.backlinks_count} backlinks</span>
        </div>
        ${tagsHtml}
      </div>
    `;
  }).join('');
}

function filterVaultNotes(val) {
  onVaultSearchInput(val);
}

/**
 * Open a note into the Right Sidebar Preview/Editor and highlight it in the 2D Graph.
 */
async function loadVaultNote(relPath, openSidebar = true, openInEditor = false) {
  if (!relPath) return;

  let clean = relPath.trim().replace(/^knowledge\//, '');
  if (!clean.endsWith('.md')) clean += '.md';

  try {
    const res = await fetch(`/api/vault/note?path=${encodeURIComponent(clean)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const note = await res.json();
    _activeVaultNote = note;

    renderVaultSidebarList(_vaultData.nodes || []);
    if (_currentGraphDepth !== 'all') {
      applyGraphDepthFilter();
    } else {
      highlightVaultGraphNode(clean);
    }

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
function openVaultNoteInRightSidebar(note, openInEditor = false) {
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

  // Follow the panel expansion transition smoothly to avoid canvas warping
  requestAnimationFrame(() => {
    resizeGraphCanvas(false);
    setTimeout(() => resizeGraphCanvas(false), 120);
    setTimeout(() => resizeGraphCanvas(true), 260);
  });

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
    setupWikilinkAutocomplete();
    editArea.onkeydown = e => {
      if (e.key === 'Escape') {
        const dd = document.getElementById('wikilinkDropdown');
        if (dd && dd.style.display !== 'none') return; // handled by wikilink listener
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

  const tags = Array.isArray(note.tags) ? note.tags : [];
  const tagsHtml = tags.length > 0
    ? tags.map(t => `<span class="vault-chip tag" onclick="setVaultTagFilter('${escapeAttr(t)}')"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/><line x1="10" y1="3" x2="8" y2="21"/><line x1="16" y1="3" x2="14" y2="21"/></svg> #${escapeHtml(t)}</span>`).join('')
    : '<span class="vault-empty-text">No tags</span>';

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
        <span class="vault-relation-title">Tags:</span>
        <div class="vault-chips-row">${tagsHtml}</div>
      </div>
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
 * Open the structured modal to create a new vault note with category presets and auto ADR numbering.
 */
async function promptCreateVaultNote(initialCategory = 'decisions', initialTitle = '') {
  _selectedVaultCategory = initialCategory || 'decisions';
  const modal = document.getElementById('vaultNewNoteModal');
  if (!modal) return;

  const titleInput = document.getElementById('vaultNewNoteTitle');
  if (titleInput) titleInput.value = initialTitle || '';

  const tagsInput = document.getElementById('vaultNewNoteTags');
  if (tagsInput) tagsInput.value = '';

  await updateVaultCategorySelection();
  updateVaultNotePathPreview();

  modal.style.display = 'flex';
  if (titleInput) {
    titleInput.focus();
    if (initialTitle) titleInput.select();
  }
}

function closeVaultNewNoteModal() {
  const modal = document.getElementById('vaultNewNoteModal');
  if (modal) modal.style.display = 'none';
}

async function selectVaultCategory(category) {
  _selectedVaultCategory = category;
  await updateVaultCategorySelection();
  updateVaultNotePathPreview();
}

async function updateVaultCategorySelection() {
  const pills = {
    decisions: document.getElementById('vaultCatDecisions'),
    architecture: document.getElementById('vaultCatArch'),
    user: document.getElementById('vaultCatUser'),
    notes: document.getElementById('vaultCatNotes')
  };
  Object.keys(pills).forEach(k => {
    if (pills[k]) pills[k].classList.toggle('active', k === _selectedVaultCategory);
  });

  if (_selectedVaultCategory === 'decisions') {
    try {
      const res = await fetch('/api/vault/template?category=decisions&title=Sample');
      if (res.ok) {
        const data = await res.json();
        _nextAdrNumber = data.next_adr || 1;
      }
    } catch (_) {
      _nextAdrNumber = 1;
    }
  }
}

function updateVaultNotePathPreview() {
  const titleInput = document.getElementById('vaultNewNoteTitle');
  const pathPreview = document.getElementById('vaultNewNotePathPreview');
  if (!pathPreview) return;

  const title = (titleInput ? titleInput.value : '').trim() || 'untitled';
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'note';

  let relPath = '';
  if (_selectedVaultCategory === 'decisions') {
    const numStr = String(_nextAdrNumber || 1).padStart(3, '0');
    relPath = `knowledge/decisions/adr_${numStr}_${slug}.md`;
  } else if (_selectedVaultCategory === 'architecture') {
    relPath = `knowledge/architecture/${slug}.md`;
  } else if (_selectedVaultCategory === 'user') {
    relPath = `knowledge/user/${slug}.md`;
  } else {
    relPath = `knowledge/notes/${slug}.md`;
  }

  pathPreview.value = relPath;
}

async function submitVaultNewNote() {
  const titleInput = document.getElementById('vaultNewNoteTitle');
  const tagsInput = document.getElementById('vaultNewNoteTags');
  const pathPreview = document.getElementById('vaultNewNotePathPreview');

  const title = (titleInput ? titleInput.value : '').trim();
  if (!title) {
    if (typeof showToast === 'function') showToast('Please enter a note title', 2500, 'error');
    if (titleInput) titleInput.focus();
    return;
  }

  const targetPath = pathPreview ? pathPreview.value : '';
  if (!targetPath) return;

  const btn = document.getElementById('btnSubmitVaultNewNote');
  if (btn) btn.disabled = true;

  try {
    const tRes = await fetch(`/api/vault/template?category=${encodeURIComponent(_selectedVaultCategory)}&title=${encodeURIComponent(title)}`);
    let content = `# ${title}\n\n`;
    if (tRes.ok) {
      const tData = await tRes.json();
      if (tData.template) content = tData.template;
    }

    const rawTags = (tagsInput ? tagsInput.value : '').trim();
    if (rawTags) {
      const tagsList = rawTags.split(/[\s,]+/).map(t => t.startsWith('#') ? t : `#${t}`).join(' ');
      content = `${content}\n\nTags: ${tagsList}\n`;
    }

    const saveRel = targetPath.replace(/^knowledge\//, '');
    const sRes = await fetch('/api/vault/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: saveRel, content: content })
    });
    const sData = await sRes.json();

    if (sData.ok) {
      if (typeof showToast === 'function') showToast(`Created ${saveRel}`, 2500, 'success');
      closeVaultNewNoteModal();
      await loadVault(true);
      loadVaultNote(saveRel, true, false);
    } else {
      if (typeof showToast === 'function') showToast(sData.error || 'Failed to create note', 3000, 'error');
    }
  } catch (err) {
    if (typeof showToast === 'function') showToast(`Error creating note: ${err.message}`, 3000, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
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
      loadVaultNote(path, true, false);
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
   Wikilink Autocomplete Engine for Right-Sidebar Editor
   ───────────────────────────────────────────────────────────── */

let _wikilinkSelectedIdx = -1;
let _wikilinkMatches = [];

function setupWikilinkAutocomplete() {
  const editArea = document.getElementById('previewEditArea');
  const dropdown = document.getElementById('wikilinkDropdown');
  if (!editArea || !dropdown || editArea._hasWikilinkSetup) return;
  editArea._hasWikilinkSetup = true;

  function hideDropdown() {
    dropdown.style.display = 'none';
    dropdown.innerHTML = '';
    _wikilinkSelectedIdx = -1;
    _wikilinkMatches = [];
  }

  function checkTrigger() {
    const text = editArea.value;
    const pos = editArea.selectionStart;
    const before = text.slice(0, pos);
    const match = before.match(/\[\[([^\]\r\n]*)$/);
    if (!match) {
      hideDropdown();
      return;
    }

    const query = match[1].toLowerCase().trim();
    const allNotes = (_vaultData && _vaultData.nodes) ? _vaultData.nodes : [];

    _wikilinkMatches = allNotes.filter(n => {
      if (!query) return true;
      return n.title.toLowerCase().includes(query) ||
             n.id.toLowerCase().includes(query) ||
             (Array.isArray(n.tags) && n.tags.some(t => t.toLowerCase().includes(query))) ||
             n.folder.toLowerCase().includes(query);
    }).slice(0, 8);

    if (!_wikilinkMatches.length) {
      hideDropdown();
      return;
    }

    _wikilinkSelectedIdx = 0;
    renderDropdown();
  }

  function renderDropdown() {
    dropdown.innerHTML = _wikilinkMatches.map((n, idx) => {
      const isSel = idx === _wikilinkSelectedIdx;
      const color = FOLDER_COLORS[n.folder] || FOLDER_COLORS.other;
      const tags = (Array.isArray(n.tags) && n.tags.length)
        ? `<div class="wikilink-item-tags">${n.tags.map(t => `<span class="wikilink-tag">#${escapeHtml(t)}</span>`).join('')}</div>`
        : '';
      return `
        <div class="wikilink-item ${isSel ? 'selected' : ''}" data-idx="${idx}">
          <div class="wikilink-item-main">
            <span class="vault-folder-dot" style="background:${color}"></span>
            <span class="wikilink-item-title">${escapeHtml(n.title)}</span>
            <span class="vault-folder-tag">${escapeHtml(n.folder)}</span>
          </div>
          ${tags}
        </div>
      `;
    }).join('');

    dropdown.style.display = 'block';

    dropdown.querySelectorAll('.wikilink-item').forEach(el => {
      el.onmousedown = (e) => {
        e.preventDefault();
        const idx = parseInt(el.dataset.idx, 10);
        insertWikilink(_wikilinkMatches[idx]);
      };
    });
  }

  function insertWikilink(targetNote) {
    if (!targetNote) { hideDropdown(); return; }
    const text = editArea.value;
    const pos = editArea.selectionStart;
    const before = text.slice(0, pos);
    const after = text.slice(pos);

    const match = before.match(/\[\[([^\]\r\n]*)$/);
    if (!match) return;

    const prefix = before.slice(0, match.index);
    const linkText = (targetNote.title && targetNote.title !== targetNote.id)
      ? `[[${targetNote.id}|${targetNote.title}]]`
      : `[[${targetNote.id}]]`;

    editArea.value = prefix + linkText + after;
    const newCursor = prefix.length + linkText.length;
    editArea.selectionStart = newCursor;
    editArea.selectionEnd = newCursor;
    editArea.focus();

    if (typeof _previewDirty !== 'undefined') _previewDirty = true;
    if (typeof updateEditBtn === 'function') updateEditBtn();

    hideDropdown();
  }

  editArea.addEventListener('input', checkTrigger);
  editArea.addEventListener('click', checkTrigger);
  editArea.addEventListener('keyup', (e) => {
    if (['ArrowLeft', 'ArrowRight'].includes(e.key)) {
      checkTrigger();
    }
  });

  editArea.addEventListener('keydown', (e) => {
    if (dropdown.style.display === 'none') return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _wikilinkSelectedIdx = (_wikilinkSelectedIdx + 1) % _wikilinkMatches.length;
      renderDropdown();
      const sel = dropdown.querySelector(`.wikilink-item[data-idx="${_wikilinkSelectedIdx}"]`);
      if (sel) sel.scrollIntoView({ block: 'nearest' });
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      _wikilinkSelectedIdx = (_wikilinkSelectedIdx - 1 + _wikilinkMatches.length) % _wikilinkMatches.length;
      renderDropdown();
      const sel = dropdown.querySelector(`.wikilink-item[data-idx="${_wikilinkSelectedIdx}"]`);
      if (sel) sel.scrollIntoView({ block: 'nearest' });
      return;
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      if (_wikilinkMatches.length > 0 && _wikilinkSelectedIdx >= 0) {
        e.preventDefault();
        insertWikilink(_wikilinkMatches[_wikilinkSelectedIdx]);
        return;
      }
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      hideDropdown();
      return;
    }
  });

  document.addEventListener('click', (e) => {
    if (!editArea.contains(e.target) && !dropdown.contains(e.target)) {
      hideDropdown();
    }
  });
}

// Automatically setup wikilink dropdown when script is loaded
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', setupWikilinkAutocomplete);
} else {
  setupWikilinkAutocomplete();
}

/* ─────────────────────────────────────────────────────────────
   2D Force-Directed Graph Engine (Pure Zero-Dependency Canvas)
   ───────────────────────────────────────────────────────────── */

let _graphNodes = [];
let _graphEdges = [];
let _animFrameId = null;

function setGraphDepthFilter(depth) {
  _currentGraphDepth = String(depth || 'all');
  const btns = document.querySelectorAll('#vaultDepthFilter .vault-depth-btn');
  btns.forEach(btn => {
    btn.classList.toggle('active', btn.dataset.depth === _currentGraphDepth);
  });
  applyGraphDepthFilter();
}

function applyGraphDepthFilter() {
  const canvas = document.getElementById('vaultGraphCanvas');
  if (!canvas) return;

  let nodesToRender = _allGraphNodes;
  let edgesToRender = _allGraphEdges;

  if (_currentGraphDepth !== 'all' && _allGraphNodes.length > 0) {
    const maxHops = parseInt(_currentGraphDepth, 10) || 1;
    let rootId = null;
    if (_activeVaultNote) {
      rootId = _activeVaultNote.id || _activeVaultNote.path.replace(/^knowledge\//, '').replace(/\.md$/, '');
    } else if (_hoverNode) {
      rootId = _hoverNode.id;
    } else if (_allGraphNodes.length > 0) {
      rootId = _allGraphNodes[0].id;
    }

    // Build bidirectional adjacency list
    const adj = {};
    _allGraphNodes.forEach(n => { adj[n.id] = []; });
    _allGraphEdges.forEach(e => {
      if (adj[e.source] && adj[e.target]) {
        adj[e.source].push(e.target);
        adj[e.target].push(e.source);
      }
    });

    const visited = new Set();
    if (rootId && adj[rootId]) {
      visited.add(rootId);
      let frontier = [rootId];
      for (let hop = 0; hop < maxHops; hop++) {
        const nextFrontier = [];
        frontier.forEach(currId => {
          (adj[currId] || []).forEach(neighborId => {
            if (!visited.has(neighborId)) {
              visited.add(neighborId);
              nextFrontier.push(neighborId);
            }
          });
        });
        frontier = nextFrontier;
      }
    }

    nodesToRender = _allGraphNodes.filter(n => visited.has(n.id));
    edgesToRender = _allGraphEdges.filter(e => visited.has(e.source) && visited.has(e.target));

    const hStats = document.getElementById('vaultHeaderStats');
    if (hStats) {
      hStats.textContent = `${nodesToRender.length} of ${_allGraphNodes.length} notes (${_currentGraphDepth}-hop focus)`;
    }
  } else {
    const hStats = document.getElementById('vaultHeaderStats');
    if (hStats) {
      const totalNotes = (_vaultData.stats && _vaultData.stats.total_notes) || _allGraphNodes.length;
      const totalEdges = (_vaultData.stats && _vaultData.stats.total_edges) || _allGraphEdges.length;
      hStats.textContent = `${totalNotes} notes · ${totalEdges} links`;
    }
  }

  // Preserve node positions if already simulated to prevent jarring jumps
  const existingMap = {};
  _graphNodes.forEach(gn => { existingMap[gn.id] = gn; });

  const nodeMap = {};
  _graphNodes = nodesToRender.map((n, i) => {
    const existing = existingMap[n.id];
    if (existing) {
      nodeMap[n.id] = existing;
      return existing;
    }
    const config = SPACING_CONFIGS[_currentGraphSpacing] || SPACING_CONFIGS.spacious;
    const angle = (i / Math.max(nodesToRender.length, 1)) * 2 * Math.PI;
    const dist = (config.spawnRadius || 280) + Math.random() * 120;
    const totalConn = (typeof n.total_connections === 'number')
      ? n.total_connections
      : ((n.in_degree || 0) + (n.out_degree || 0) || (n.connections || 0));
    const gNode = {
      id: n.id,
      title: n.title,
      path: n.path,
      folder: n.folder,
      connections: totalConn,
      x: canvas.width / 2 + Math.cos(angle) * dist,
      y: canvas.height / 2 + Math.sin(angle) * dist,
      vx: (Math.random() - 0.5) * 2,
      vy: (Math.random() - 0.5) * 2,
      radius: Math.max(9, Math.min(26, 8 + Math.round(Math.sqrt(totalConn) * 5))),
      color: FOLDER_COLORS[n.folder] || FOLDER_COLORS.other
    };
    nodeMap[n.id] = gNode;
    return gNode;
  });

  _graphEdges = edgesToRender.map(e => {
    return {
      source: nodeMap[e.source] || null,
      target: nodeMap[e.target] || null
    };
  }).filter(e => e.source && e.target);

  setupCanvasListeners(canvas);
  resizeGraphCanvas();
  startGraphSimulation();
}

function initVaultGraph(nodes, edges) {
  _allGraphNodes = nodes || [];
  _allGraphEdges = edges || [];
  applyGraphDepthFilter();
}

function resizeGraphCanvas(restartPhysics = false) {
  const canvas = document.getElementById('vaultGraphCanvas');
  if (!canvas || !canvas.parentElement) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  const w = Math.round(rect.width);
  const h = Math.round(rect.height);
  if (w > 0 && h > 0) {
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      drawGraph();
      if (restartPhysics) {
        triggerGraphRepaint();
      }
    }
  }
}

function startGraphSimulation() {
  _vaultSimRunning = true;
  let iterations = 0;
  const maxIterations = 350;

  function step() {
    if (!_vaultSimRunning) return;

    // Physics constants dynamically derived from active spacing preset
    const config = SPACING_CONFIGS[_currentGraphSpacing] || SPACING_CONFIGS.spacious;
    const repulsion = config.repulsion;
    const springLen = config.springLen;
    const springK = config.springK;
    const damping = config.damping;
    const centerPull = config.centerPull;
    const minDistance = config.minDistance;

    const canvas = document.getElementById('vaultGraphCanvas');
    const cx = canvas ? canvas.width / 2 : 400;
    const cy = canvas ? canvas.height / 2 : 300;

    // 1. Repulsion between all node pairs + hard collision buffer
    for (let i = 0; i < _graphNodes.length; i++) {
      const n1 = _graphNodes[i];
      for (let j = i + 1; j < _graphNodes.length; j++) {
        const n2 = _graphNodes[j];
        const dx = n2.x - n1.x;
        const dy = n2.y - n1.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;

        // Hard collision resolution: immediately push overlapping nodes apart
        if (dist < minDistance) {
          const overlap = (minDistance - dist) * 0.5;
          const nx = (dx / dist) * overlap;
          const ny = (dy / dist) * overlap;
          if (n1 !== _dragNode) { n1.x -= nx; n1.y -= ny; }
          if (n2 !== _dragNode) { n2.x += nx; n2.y += ny; }
        }

        // Softened Coulomb repulsion for wide, spacious spread
        const f = repulsion / (dist * Math.pow(dist, 0.72) + 60);
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;

        n1.vx -= fx;
        n1.vy -= fy;
        n2.vx += fx;
        n2.vy += fy;
      }

      // Gentle center gravity
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

function setGraphSpacing(spacing) {
  if (!SPACING_CONFIGS[spacing]) spacing = 'spacious';
  _currentGraphSpacing = spacing;
  try {
    localStorage.setItem('agy-vault-spacing', spacing);
  } catch (_) {}

  const btns = document.querySelectorAll('#vaultSpacingFilter .vault-depth-btn');
  btns.forEach(btn => {
    btn.classList.toggle('active', btn.dataset.spacing === _currentGraphSpacing);
  });

  startGraphSimulation();
}

function zoomGraph(factor) {
  _vaultZoom = Math.max(0.2, Math.min(4.0, _vaultZoom * factor));
  drawGraph();
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
    const isSrcDisabled = _disabledLegendFolders.has(e.source.folder);
    const isTgtDisabled = _disabledLegendFolders.has(e.target.folder);
    if (isSrcDisabled || isTgtDisabled) {
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.02)';
    } else {
      const isHovered = _hoverNode && (e.source.id === _hoverNode.id || e.target.id === _hoverNode.id);
      const isSelected = _activeVaultNote && (_activeVaultNote.id === e.source.id || _activeVaultNote.id === e.target.id);
      ctx.strokeStyle = (isHovered || isSelected) ? 'rgba(2, 136, 168, 0.7)' : 'rgba(255, 255, 255, 0.12)';
    }
    ctx.beginPath();
    ctx.moveTo(e.source.x, e.source.y);
    ctx.lineTo(e.target.x, e.target.y);
    ctx.stroke();
  });

  // Draw nodes
  _graphNodes.forEach(n => {
    const isFolderDisabled = _disabledLegendFolders.has(n.folder);
    ctx.globalAlpha = isFolderDisabled ? 0.12 : 1.0;

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

    // Node label with pill background to prevent text clash & overlap
    const isMajor = (n.connections && n.connections >= 2) || isSelected || isHovered;
    const showLabel = _vaultZoom >= 0.65 || isMajor;

    if (showLabel) {
      const labelText = n.title && n.title.length > 28 ? (n.title.slice(0, 26) + '…') : (n.title || '');
      ctx.font = isSelected ? 'bold 11px system-ui, sans-serif' : (isHovered ? '600 11px system-ui, sans-serif' : '10px system-ui, sans-serif');
      const textWidth = ctx.measureText(labelText).width;
      const textHeight = 15;
      const labelX = n.x - textWidth / 2 - 5;
      const labelY = n.y + n.radius + 6;

      // Draw translucent pill backdrop
      ctx.fillStyle = isSelected
        ? 'rgba(2, 136, 168, 0.9)'
        : (isHovered ? 'rgba(30, 41, 59, 0.92)' : 'rgba(15, 23, 42, 0.75)');
      ctx.beginPath();
      if (typeof ctx.roundRect === 'function') {
        ctx.roundRect(labelX, labelY, textWidth + 10, textHeight, 4);
      } else {
        ctx.rect(labelX, labelY, textWidth + 10, textHeight);
      }
      ctx.fill();

      if (isSelected || isHovered) {
        ctx.strokeStyle = isSelected ? '#ffffff' : 'var(--accent, #0288a8)';
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      // Draw text
      ctx.fillStyle = isSelected ? '#ffffff' : (isHovered ? '#ffffff' : 'rgba(255, 255, 255, 0.92)');
      ctx.textAlign = 'center';
      ctx.fillText(labelText, n.x, labelY + 11);
    }
  });

  ctx.globalAlpha = 1.0;
  ctx.restore();
}

function toggleVaultLegendFolder(folder) {
  if (_disabledLegendFolders.has(folder)) {
    _disabledLegendFolders.delete(folder);
  } else {
    _disabledLegendFolders.add(folder);
  }

  const btns = document.querySelectorAll('.vault-legend-btn');
  btns.forEach(btn => {
    if (btn.dataset.folder === folder) {
      btn.classList.toggle('disabled', _disabledLegendFolders.has(folder));
    }
  });

  drawGraph();
}

function exportGraphImage() {
  const canvas = document.getElementById('vaultGraphCanvas');
  if (!canvas) return;

  drawGraph();

  try {
    const dataUrl = canvas.toDataURL('image/png');
    const a = document.createElement('a');
    a.download = `antigravity-knowledge-graph-${new Date().toISOString().slice(0, 10)}.png`;
    a.href = dataUrl;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    if (typeof showToast === 'function') {
      showToast('Exported Knowledge Graph as PNG', 2500, 'success');
    }
  } catch (err) {
    if (typeof showToast === 'function') {
      showToast(`Failed to export graph: ${err.message}`, 3000, 'error');
    }
  }
}

function setupCanvasListeners(canvas) {
  if (canvas._hasListeners) return;
  canvas._hasListeners = true;

  window.addEventListener('resize', () => resizeGraphCanvas(false));

  // 1. Observe parent container with ResizeObserver so any layout changes
  // (sidebar expands, collapses, or is dragged) immediately adapt canvas pixel dimensions
  // without stretching or warping the node shapes.
  if (window.ResizeObserver && canvas.parentElement) {
    if (_vaultResizeObserver) _vaultResizeObserver.disconnect();
    _vaultResizeObserver = new ResizeObserver(() => {
      resizeGraphCanvas(false);
    });
    _vaultResizeObserver.observe(canvas.parentElement);
  }

  // 2. Track smooth CSS width transitions on .rightpanel to guarantee real-time 1:1
  // aspect ratio updates during animation, preventing canvas warping/squashing.
  const rightpanel = document.querySelector('.rightpanel');
  if (rightpanel && !rightpanel._hasVaultTransition) {
    rightpanel._hasVaultTransition = true;
    let animId = null;
    const track = () => {
      resizeGraphCanvas(false);
      animId = requestAnimationFrame(track);
    };
    rightpanel.addEventListener('transitionrun', () => {
      cancelAnimationFrame(animId);
      animId = requestAnimationFrame(track);
    });
    rightpanel.addEventListener('transitionend', () => {
      cancelAnimationFrame(animId);
      resizeGraphCanvas(true);
    });
    rightpanel.addEventListener('transitioncancel', () => {
      cancelAnimationFrame(animId);
      resizeGraphCanvas(false);
    });
  }

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
