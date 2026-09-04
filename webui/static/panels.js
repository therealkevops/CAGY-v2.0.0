let _currentPanel = 'chat';
let _renamingAppTitlebar = false;  // guard against re-entrant rename
let _skillsData = null; // cached skills list
let _currentWorkspaceDetail = null; // { path, name, is_default }
let _workspaceMode = 'empty'; // 'empty' | 'read' | 'create' | 'edit'
let _workspacePreFormDetail = null;
let _currentProfileDetail = null; // full profile object
let _profileMode = 'empty'; // 'empty' | 'read' | 'create'
let _profilePreFormDetail = null;
let _pendingSettingsTargetPanel = null; // destination selected while settings had unsaved changes

// Map of panel names → i18n keys for the app titlebar label.
const APP_TITLEBAR_KEYS = {
  chat: 'tab_chat', skills: 'tab_skills',
  mcp: 'tab_mcp', subagents: 'tab_subagents', workspaces: 'tab_workspaces',
  settings: 'tab_settings', vault: 'tab_vault',
};
const MAIN_VIEW_PANELS = ['settings','skills','mcp','subagents','workspaces','plugin','vault'];
const MAIN_VIEW_SIDEBAR_PANEL_FALLBACKS = { plugin: 'settings' };

/**
 * Update the top app titlebar to reflect the current page or selected conversation.
 * On the chat panel, a selected session's title takes precedence over the page name.
 */
function syncAppTitlebar() {
  const titleEl = document.getElementById('appTitlebarTitle');
  const subEl = document.getElementById('appTitlebarSub');
  if (!titleEl) return;
  const panel = (typeof _currentPanel === 'string' && _currentPanel) ? _currentPanel : 'chat';
  let mainText = '';
  let subText = '';
  let sourceLabel = '';
  if (panel === 'chat' && typeof S !== 'undefined' && S && S.session) {
    mainText = S.session.title || (typeof t === 'function' ? t('untitled') : 'Untitled');
    const vis = Array.isArray(S.messages) ? S.messages.filter(m => m && m.role && m.role !== 'tool') : [];
    subText = String(vis.length);
    sourceLabel = S.session.source_label || S.session.source_tag || S.session.raw_source || '';
    // Recovered sidecars stamp source_label 'WebUI' (api/session_recovery.py); don't badge a native session as its own source (#3338).
    if (/^webui$/i.test(sourceLabel)) sourceLabel = '';
  } else {
    const key = APP_TITLEBAR_KEYS[panel];
    mainText = key && typeof t === 'function' ? t(key) : (panel.charAt(0).toUpperCase() + panel.slice(1));
  }

  // Don't touch the element while an inline rename is in progress — replacing
  // the span with an input would fire a MutationObserver that calls
  // syncAppTitlebar again, destroying the input before the user finishes.
  if (_renamingAppTitlebar) return;

  titleEl.textContent = mainText;
  if (panel !== 'chat') {
    const bot = typeof assistantDisplayName === 'function' ? assistantDisplayName() : '';
    document.title = bot ? mainText + ' \u2014 ' + bot : mainText;
  }
  if (subEl) {
    if (subText) {
      subEl.textContent = subText;
      if (sourceLabel) {
        const badge = document.createElement('span');
        badge.className = 'topbar-source-badge';
        badge.textContent = sourceLabel + (S.session && S.session.read_only ? ' · read-only' : '');
        subEl.appendChild(document.createTextNode(' '));
        subEl.appendChild(badge);
      }
      subEl.hidden = false;
    }
    else { subEl.textContent = ''; subEl.hidden = true; }
  }

  // Double-click on the titlebar title → rename the active session (same behaviour
  // as double-clicking a session title in the sidebar).  Only active on the chat
  // panel when a session is open.
  titleEl.ondblclick = null;  // remove any previous handler before adding a fresh one
  if (panel === 'chat' && typeof S !== 'undefined' && S && S.session && !(S.session.read_only || S.session.is_read_only)) {
    titleEl.ondblclick = (e) => {
      e.stopPropagation();
      e.preventDefault();
      if (_renamingAppTitlebar) return;
      _renamingAppTitlebar = true;

      const inp = document.createElement('input');
      inp.type = 'text';
      inp.className = 'app-titlebar-rename-input';
      inp.value = S.session.title || (typeof t === 'function' ? t('untitled') : 'Untitled');

      // Prevent click/dblclick on the input from bubbling — we don't want
      // panel switches, session switches, or any other handler firing.
      ['click', 'mousedown', 'dblclick', 'pointerdown'].forEach(ev =>
        inp.addEventListener(ev, e2 => e2.stopPropagation())
      );

      const finish = async (save) => {
        _renamingAppTitlebar = false;
        if (save) {
          const newTitle = inp.value.trim() || (typeof t === 'function' ? t('untitled') : 'Untitled');
          S.session.title = newTitle;
          syncTopbar();   // update #topbarTitle in the chat header
          syncAppTitlebar();
          // Update the sidebar list so the renamed title appears immediately.
          // _renderOneSession reads from _allSessions cache, so patch it there too.
          try {
            const _cached = typeof _allSessions !== 'undefined' && _allSessions.find(s => s && s.session_id === S.session.session_id);
            if (_cached) _cached.title = newTitle;
          } catch (_) {}
          if (typeof renderSessionListFromCache === 'function') renderSessionListFromCache();
          try {
            await api('/api/session/rename', {
              method: 'POST',
              body: JSON.stringify({ session_id: S.session.session_id, title: newTitle })
            });
          } catch (err) {
            if (typeof setStatus === 'function') setStatus('Rename failed: ' + err.message);
          }
        }
        inp.replaceWith(titleEl);
        syncAppTitlebar();
      };

      inp.onkeydown = e2 => {
        if (e2.key === 'Enter') { e2.preventDefault(); e2.stopPropagation(); finish(true); }
        if (e2.key === 'Escape') { e2.preventDefault(); e2.stopPropagation(); finish(false); }
      };
      inp.onblur = () => finish(false);

      titleEl.replaceWith(inp);
      inp.focus();
      inp.select();
    };
  }

  // Dismiss stale popover on session/panel switch
  const _existingPop = document.querySelector('.app-titlebar-title-popover');
  if (_existingPop) {
    _existingPop.remove(); titleEl._titlePopover = null;
    if (titleEl._popoverOutsideHandler) {
      document.removeEventListener('click', titleEl._popoverOutsideHandler, true);
      titleEl._popoverOutsideHandler = null;
    }
  }

  // Mobile touch interactions
  if ('ontouchstart' in window) {
    // Tap-to-reveal full title popover — wired once per element lifetime
    if (!titleEl._mobileTouchWired) {
      titleEl._mobileTouchWired = true;
      titleEl._titlePopover = null;
      const _dismissTitlePopover = () => {
        if (titleEl._titlePopover) { titleEl._titlePopover.remove(); titleEl._titlePopover = null; }
        if (titleEl._popoverOutsideHandler) {
          document.removeEventListener('click', titleEl._popoverOutsideHandler, true);
          titleEl._popoverOutsideHandler = null;
        }
      };
      titleEl.addEventListener('click', function _onTitleClick(e) {
        if (_renamingAppTitlebar) return;
        if (titleEl._titlePopover) {
          _dismissTitlePopover();
          return;
        }
        e.stopPropagation();
        const pop = document.createElement('div');
        pop.className = 'app-titlebar-title-popover';
        pop.textContent = (S && S.session && S.session.title) ||
          (typeof t === 'function' ? t('untitled') : 'Untitled');
        document.body.appendChild(pop);
        const rect = titleEl.getBoundingClientRect();
        pop.style.top = (rect.bottom + 6) + 'px';
        pop.style.left = Math.max(8, rect.left) + 'px';
        pop.style.maxWidth = (window.innerWidth - 16) + 'px';
        titleEl._titlePopover = pop;
        const _outside = titleEl._popoverOutsideHandler = (ev) => {
          if (!pop.contains(ev.target) && ev.target !== titleEl) {
            _dismissTitlePopover();
            document.removeEventListener('click', _outside, true);
            titleEl._popoverOutsideHandler = null;
          }
        };
        setTimeout(() => document.addEventListener('click', _outside, true), 0);
      }, { passive: true });
    }

    // Long-press → session action menu (re-evaluated each sync so late-arriving sessions attach)
    if (!titleEl._mobileLpWired && panel === 'chat' && S && S.session &&
        !S.session.read_only && !S.session.is_read_only &&
        typeof _openSessionActionMenu === 'function') {
      titleEl._mobileLpWired = true;
      let _lpTimer = null;
      let _lpHandled = false;
      let _lpStartX = 0, _lpStartY = 0;
      const _lpDelay = typeof SESSION_LONG_PRESS_DELAY_MS !== 'undefined' ?
        SESSION_LONG_PRESS_DELAY_MS : 400;
      titleEl.addEventListener('touchstart', (e) => {
        const touch = e.changedTouches && e.changedTouches[0];
        if (!touch) return;
        if (_lpTimer) { clearTimeout(_lpTimer); _lpTimer = null; }
        _lpHandled = false; _lpStartX = touch.clientX; _lpStartY = touch.clientY;
        titleEl.classList.add('long-pressing');
        _lpTimer = setTimeout(() => {
          _lpTimer = null;
          if (_lpHandled) return;
          _lpHandled = true;
          titleEl.classList.remove('long-pressing');
          _openSessionActionMenu(S.session, titleEl);
        }, _lpDelay);
      }, { passive: true });
      titleEl.addEventListener('touchmove', (e) => {
        if (!_lpTimer) return;
        const touch = e.changedTouches && e.changedTouches[0];
        if (!touch) return;
        if (Math.abs(touch.clientX - _lpStartX) > 10 || Math.abs(touch.clientY - _lpStartY) > 10) {
          clearTimeout(_lpTimer); _lpTimer = null;
          titleEl.classList.remove('long-pressing');
        }
      }, { passive: true });
      titleEl.addEventListener('touchend', (e) => {
        clearTimeout(_lpTimer); _lpTimer = null;
        titleEl.classList.remove('long-pressing');
        if (_lpHandled) { e.preventDefault(); e.stopPropagation(); }
      }, { passive: false });
      titleEl.addEventListener('touchcancel', () => {
        clearTimeout(_lpTimer); _lpTimer = null; _lpHandled = false;
        titleEl.classList.remove('long-pressing');
      }, { passive: true });
    }
  }
}

function _beginSettingsPanelSession() {
  _settingsIndex = null;
  _settingsIndexPromise = null;
  // Invalidate any in-flight search render from a PRIOR Settings session and
  // reset the search UI, so a slow index build that resolves after the panel
  // was closed/reopened can't paint stale results into the dropdown. #4340
  // review fix (filterSettings() bails when its captured seq != current).
  ++_settingsSearchSeq;
  const _searchInput = $('settingsSearch');
  if (_searchInput) _searchInput.value = '';
  const _searchResults = $('settingsSearchResults');
  if (_searchResults) {
    _searchResults.style.display = 'none';
    _searchResults.innerHTML = '';
  }
  _settingsDirty = false;
  _settingsThemeOnOpen = localStorage.getItem('agy-theme') || 'dark';
  _settingsSkinOnOpen = localStorage.getItem('agy-skin') || 'default';
  _settingsFontSizeOnOpen = localStorage.getItem('agy-font-size') || 'default';
  _pendingSettingsTargetPanel = null;
  if (_settingsAppearanceAutosaveTimer) {
    clearTimeout(_settingsAppearanceAutosaveTimer);
    _settingsAppearanceAutosaveTimer = null;
  }
  _settingsAppearanceAutosaveRetryPayload = null;
  if (!_settingsSearchDismissListenerRegistered) {
    _settingsSearchDismissListenerRegistered = true;
    document.addEventListener('click', e => {
      if (!e.target.closest('#settingsMenu')) {
        // Invalidate an in-flight first-build too, so it can't resurrect the
        // dropdown after an outside-click dismiss. #4340 review fix.
        ++_settingsSearchSeq;
        const r = $('settingsSearchResults');
        if (r) {
          r.style.display = 'none';
          r.innerHTML = '';
        }
      }
    });
  }
  _resetSettingsPanelState();
}

function _beforePanelSwitch(nextPanel) {
  if (_currentPanel !== 'settings' || nextPanel === 'settings') return true;
  if (_settingsDirty) {
    _pendingSettingsTargetPanel = nextPanel || 'chat';
    _showSettingsUnsavedBar();
    return false;
  }
  _revertSettingsPreview();
  _pendingSettingsTargetPanel = null;
  _resetSettingsPanelState();
  return true;
}

function _consumeSettingsTargetPanel(fallback = 'chat') {
  const target = (_pendingSettingsTargetPanel && _pendingSettingsTargetPanel !== 'settings')
    ? _pendingSettingsTargetPanel
    : fallback;
  _pendingSettingsTargetPanel = null;
  return target;
}

function _resyncChatSidebarAfterPanelSwitch() {
  if (_currentPanel !== 'chat') return;
  if (typeof renderSessionListFromCache !== 'function') return;
  const run = () => {
    if (_currentPanel !== 'chat') return;
    if (typeof _renamingSid !== 'undefined' && _renamingSid) return;
    // If the user opens the per-conversation action menu immediately after
    // returning to Chat, do not let the deferred sidebar resync tear it down.
    // renderSessionListFromCache() intentionally closes that menu before it
    // rebuilds rows, which is correct for normal list refreshes but hostile to
    // this one-shot panel-transition repair.
    if (typeof _sessionActionMenu !== 'undefined' && _sessionActionMenu) return;
    renderSessionListFromCache();
  };
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
  else run();
}

function _closeMobileSidebarAfterPanelSelection(){
  if(typeof closeMobileSidebar!=='function')return;
  if(typeof _isDesktopWidth==='function'&&_isDesktopWidth())return;
  closeMobileSidebar();
}

function _panelFromCurrentMainView(){
  const mainEl=document.querySelector('main.main');
  if(!mainEl)return _currentPanel||'chat';
  for(const panel of MAIN_VIEW_PANELS){
    if(mainEl.classList.contains('showing-'+panel))return MAIN_VIEW_SIDEBAR_PANEL_FALLBACKS[panel]||panel;
  }
  if(_currentPanel&&$('panel'+_currentPanel.charAt(0).toUpperCase()+_currentPanel.slice(1)))return _currentPanel;
  return 'chat';
}

function _syncMobileSidebarPanelFromMainView(){
  const mainEl=document.querySelector('main.main');
  // Extension panels are intentionally outside MAIN_VIEW_PANELS: the host must
  // not try to lazy-load or own their main view. They do, however, publish the
  // visible view as `showing-x-<token>` and install a matching sidebar
  // `.panel-view[data-panel-token]`. The mobile drawer is reopened through this
  // sync helper, so treating that state as Chat deactivated the extension's
  // sidebar view every time the operator opened the hamburger or edge drawer.
  // The frame remained behind the drawer, but its content had no reachable
  // navigation state on the phone.
  const extensionClass=mainEl&&Array.from(mainEl.classList)
    .find(name=>name.startsWith('showing-x-'));
  const extensionToken=extensionClass&&extensionClass.slice('showing-x-'.length);
  if(extensionToken){
    const extensionView=Array.from(document.querySelectorAll('.sidebar .panel-view'))
      .find(view=>view.dataset.panelToken===extensionToken);
    if(extensionView){
      const extensionPanel=`x-${extensionToken}`;
      document.querySelectorAll('[data-panel]').forEach(t=>t.classList.toggle('active',t.dataset.panel===extensionPanel));
      document.querySelectorAll('.panel-view').forEach(p=>p.classList.remove('active'));
      extensionView.classList.add('active');
      // Do not put an extension token in _currentPanel: switchPanel owns that
      // state and only accepts its native panel names. Returning the token keeps
      // this helper truthful without corrupting the host state machine.
      return extensionPanel;
    }
  }
  const panel=_panelFromCurrentMainView();
  if(!panel)return _currentPanel||'chat';
  const panelEl=$('panel'+panel.charAt(0).toUpperCase()+panel.slice(1));
  if(!panelEl)return _currentPanel||'chat';
  _currentPanel=panel;
  document.querySelectorAll('[data-panel]').forEach(t=>t.classList.toggle('active',t.dataset.panel===panel));
  document.querySelectorAll('.panel-view').forEach(p=>p.classList.remove('active'));
  panelEl.classList.add('active');
  return panel;
}

async function switchPanel(name, opts = {}) {
  const nextPanel = name || 'chat';
  const prevPanel = _currentPanel;
  // ── Desktop sidebar collapse toggle (rail-click only) ──
  // If the click came from a rail icon AND we're on desktop, the rail icon
  // does double duty: clicking the already-active panel collapses the sidebar;
  // clicking any panel while collapsed expands first. Programmatic switches
  // (no opts.fromRailClick) are unaffected so legacy callers preserve
  // behaviour exactly.
  if (opts.fromRailClick && typeof _isSidebarCollapsed === 'function'
      && typeof _isDesktopWidth === 'function' && _isDesktopWidth()) {
    if (_isSidebarCollapsed()) {
      // Expand first, then continue to the normal panel switch below so
      // the clicked panel becomes (or stays) active in the same gesture.
      expandSidebar();
    } else if (prevPanel === nextPanel) {
      // Same panel clicked while sidebar is open → collapse and short-circuit.
      // Skip the guard/cleanup work below; nothing about the active panel
      // is changing, only the visibility of the panel container.
      toggleSidebar(true);
      return false;
    }
  }
  if (!opts.bypassSettingsGuard && !_beforePanelSwitch(nextPanel)) return false;
  if (prevPanel !== 'settings' && nextPanel === 'settings') _beginSettingsPanelSession();
  _currentPanel = nextPanel;
  // Mobile drawer visibility: a rail/tab click on a phone should surface the
  // panel synchronously, NOT after the panel's async data load. If the re-open
  // stayed at the bottom of this function, a form opened from inside the drawer
  // (e.g. openWorkspaceCreate closing the drawer) would race the deferred
  // re-open and the drawer would win, covering the main-view form.
  if (opts.fromRailClick && typeof _isDesktopWidth === 'function' && !_isDesktopWidth()) {
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
      sidebar.classList.remove('mobile-session-page');
      sidebar.classList.add('mobile-panel-drawer', 'mobile-open');
    }
  }
  // Update nav tabs (rail + mobile sidebar-nav share data-panel)
  document.querySelectorAll('[data-panel]').forEach(t => t.classList.toggle('active', t.dataset.panel === nextPanel));
  // Refresh aria-expanded on the newly-active rail button to mirror sidebar state.
  if (typeof _syncSidebarAria === 'function') _syncSidebarAria();
  // Update panel views
  document.querySelectorAll('.panel-view').forEach(p => p.classList.remove('active'));
  const panelEl = $('panel' + nextPanel.charAt(0).toUpperCase() + nextPanel.slice(1));
  if (panelEl) panelEl.classList.add('active');
  // Update main content view. Each entry in MAIN_VIEW_PANELS gets a matching
  // showing-<name> class on <main>; no class means chat (the default).
  const mainEl = document.querySelector('main.main');
  if (mainEl) {
    MAIN_VIEW_PANELS.forEach(p => {
      mainEl.classList.toggle('showing-' + p, nextPanel === p);
    });
  }
  // Lazy-load panel data
  if (nextPanel === 'skills') await loadSkills();
  if (nextPanel === 'mcp') await loadMcpHub();
  if (nextPanel === 'subagents') await loadSubagents();
  if (nextPanel === 'workspaces') await loadWorkspacesPanel();
  if (nextPanel === 'vault') {
    if (typeof loadVault === 'function') await loadVault();
    if (typeof resizeGraphCanvas === 'function') resizeGraphCanvas(true);
  }
  if (typeof _syncSystemHealthMonitorVisibility === 'function') _syncSystemHealthMonitorVisibility();
  if (nextPanel === 'settings') {
    switchSettingsSection(_currentSettingsSection);
    loadSettingsPanel();
  }
  _resyncChatSidebarAfterPanelSwitch();
  if (nextPanel === 'chat' && typeof syncTopbar === 'function') syncTopbar();
  else syncAppTitlebar();
  return true;
}

async function clearConversation() {
  if(!S.session) return;
  const _clrMsg=await showConfirmDialog({title:t('clear_conversation_title'),message:t('clear_conversation_message'),confirmLabel:t('clear'),danger:true,focusCancel:true});
  if(!_clrMsg) return;
  try {
    const data = await api('/api/session/clear', {method:'POST',
      body: JSON.stringify({session_id: S.session.session_id})});
    S.session = data.session;
    S.messages = [];
    S.toolCalls = [];
    syncTopbar();
    renderMessages();
    showToast(t('conversation_cleared'));
  } catch(e) { setStatus(t('clear_failed') + e.message); }
}

// ── Skills panel ──
async function loadSkills() {
  if (_skillsData) { renderSkills(_skillsData); return; }
  const box = $('skillsList');
  try {
    const data = await api('/api/skills');
    _skillsData = data.skills || [];
    // Prune collapsed state to only keep categories present in fresh data,
    // avoiding stale keys when categories are renamed or removed server-side.
    const liveCats = new Set(_skillsData.map(s => s.category || '(general)'));
    for (const c of _collapsedCats) { if (!liveCats.has(c)) _collapsedCats.delete(c); }
    renderSkills(_skillsData);
  } catch(e) { box.innerHTML = `<div style="padding:12px;color:var(--accent);font-size:12px">Error: ${esc(e.message)}</div>`; }
}

let _collapsedCats = new Set(); // persisted collapsed state across re-renders

function _toggleCatCollapse(cat) {
  if (_collapsedCats.has(cat)) _collapsedCats.delete(cat);
  else _collapsedCats.add(cat);
  // Toggle DOM without full re-render
  document.querySelectorAll('.skills-category').forEach(sec => {
    const header = sec.querySelector('.skills-cat-header');
    if (header && header.dataset.cat === cat) {
      const collapsed = _collapsedCats.has(cat);
      sec.classList.toggle('collapsed', collapsed);
      header.querySelector('.cat-chevron').style.transform = collapsed ? '' : 'rotate(90deg)';
      sec.querySelectorAll('.skill-item').forEach(el => el.style.display = collapsed ? 'none' : '');
    }
  });
}

function renderSkills(skills) {
  const query = ($('skillsSearch').value || '').toLowerCase();
  const filtered = query ? skills.filter(s =>
    (s.name||'').toLowerCase().includes(query) ||
    (s.description||'').toLowerCase().includes(query) ||
    (s.category||'').toLowerCase().includes(query)
  ) : skills;
  // Group by category
  const cats = {};
  for (const s of filtered) {
    const cat = s.category || '(general)';
    if (!cats[cat]) cats[cat] = [];
    cats[cat].push(s);
  }
  const box = $('skillsList');
  box.innerHTML = '';
  if (!filtered.length) { box.innerHTML = `<div style="padding:12px;color:var(--muted);font-size:12px">${esc(t('skills_no_match'))}</div>`; return; }
  for (const [cat, items] of Object.entries(cats).sort()) {
    const collapsed = _collapsedCats.has(cat);
    const sec = document.createElement('div');
    sec.className = 'skills-category' + (collapsed ? ' collapsed' : '');
    const hdr = document.createElement('div');
    hdr.className = 'skills-cat-header';
    hdr.dataset.cat = cat;
    hdr.innerHTML = `<span class="cat-chevron" style="display:inline-flex;transition:transform .15s;${collapsed ? '' : 'transform:rotate(90deg)'}">${li('chevron-right',12)}</span> ${esc(cat)} <span style="opacity:.5">(${items.length})</span>`;
    hdr.onclick = () => _toggleCatCollapse(cat);
    sec.appendChild(hdr);
    for (const skill of items.sort((a,b) => a.name.localeCompare(b.name))) {
      const el = document.createElement('div');
      el.className = 'skill-item' + (skill.disabled ? ' disabled' : '');
      el.style.display = collapsed ? 'none' : '';
      const isDisabled = skill.disabled || false;
      const toggle = document.createElement('span');
      toggle.className = 'skill-toggle' + (isDisabled ? '' : ' enabled');
      toggle.title = isDisabled ? t('skill_disabled') : t('skill_enabled');
      toggle.addEventListener('click', (ev) => {
        ev.stopPropagation();
        toggleSkill(skill.name, !isDisabled);
      });
      const nameEl = document.createElement('span');
      nameEl.className = 'skill-name';
      nameEl.textContent = skill.name;
      const descEl = document.createElement('span');
      descEl.className = 'skill-desc';
      descEl.textContent = skill.description || '';
      el.append(toggle, nameEl, descEl);
      el.onclick = () => openSkill(skill.name, el);
      sec.appendChild(el);
    }
    box.appendChild(sec);
  }
}

function filterSkills() {
  if (_skillsData) renderSkills(_skillsData);
}


async function toggleSkill(name, currentlyEnabled) {
  const newEnabled = !currentlyEnabled;
  try {
    const result = await api('/api/skills/toggle', {
      method: 'POST',
      body: JSON.stringify({ name, enabled: newEnabled })
    });
    if (result && result.ok) {
      if (_skillsData) {
        const skill = _skillsData.find(s => s.name === name);
        if (skill) skill.disabled = !newEnabled;
      }
      if(typeof window!=='undefined'&&typeof window.invalidateSlashSkillCaches==='function') window.invalidateSlashSkillCaches();
      renderSkills(_skillsData || []);
    } else {
      setStatus((result && result.error) || t('skill_toggle_failed'));
    }
  } catch(e) {
    setStatus(t('skill_toggle_failed') + e.message);
  }
}

// Currently selected skill detail — kept across panel switches so re-entering
// the Skills view shows the last-viewed skill.
let _currentSkillDetail = null; // { name, category, content }
let _skillMode = 'empty'; // 'empty' | 'read' | 'create' | 'edit'
let _skillPreFormDetail = null; // snapshot of previously-viewed skill when entering a form
let _editingSkillName = null;

function _stripYamlFrontmatter(content) {
  if (!content) return { frontmatter: null, body: '' };
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(content);
  if (!m) return { frontmatter: null, body: content };
  return { frontmatter: m[1], body: content.slice(m[0].length) };
}

function _skillMarkdownHtml(markdown) {
  return `<div class="preview-md">${renderMd(markdown || '')}</div>`;
}

function _enhanceSkillMarkdown(root) {
  if (!root) return;
  requestAnimationFrame(() => {
    const mdRoot = root.querySelector('.preview-md') || root;
    if (typeof highlightCode === 'function') highlightCode(mdRoot);
    if (typeof renderKatexBlocks === 'function') renderKatexBlocks(mdRoot);
  });
}

function _renderSkillDetail(name, content, linkedFiles) {
  const title = $('skillDetailTitle');
  const body = $('skillDetailBody');
  const empty = $('skillDetailEmpty');
  const editBtn = $('btnEditSkillDetail');
  const delBtn = $('btnDeleteSkillDetail');
  if (title) title.textContent = name;
  const { frontmatter, body: markdownBody } = _stripYamlFrontmatter(content);
  let html = '';
  if (frontmatter) {
    html += `<details class="skill-frontmatter"><summary>${esc(t('skill_metadata'))}</summary><pre><code>${esc(frontmatter)}</code></pre></details>`;
  }
  html += _skillMarkdownHtml(markdownBody || '(no content)');
  const lf = linkedFiles || {};
  const categories = Object.entries(lf).filter(([,files]) => files && files.length > 0);
  if (categories.length) {
    html += `<div class="skill-linked-files"><div style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">${esc(t('linked_files'))}</div>`;
    for (const [cat, files] of categories) {
      html += `<div class="skill-linked-section"><h4>${esc(cat)}</h4>`;
      for (const f of files) {
        html += `<a class="skill-linked-file" href="#" data-skill-name="${esc(name)}" data-skill-file="${esc(f)}">${esc(f)}</a>`;
      }
      html += '</div>';
    }
    html += '</div>';
  }
  body.innerHTML = `<div class="main-view-content skill-detail-content">${html}</div>`;
  _enhanceSkillMarkdown(body);
  body.querySelectorAll('.skill-linked-file').forEach(a => {
    a.addEventListener('click', e => { e.preventDefault(); openSkillFile(a.dataset.skillName, a.dataset.skillFile); });
  });
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _skillMode = 'read';
  _setSkillHeaderButtons('read');
}

function _renderSkillError(name, message) {
  const title = $('skillDetailTitle');
  const body = $('skillDetailBody');
  const empty = $('skillDetailEmpty');
  if (title) title.textContent = name;
  if (body) {
    body.innerHTML = `<div class="main-view-content"><div class="detail-form-error" style="display:block">${esc(message || t('skill_load_failed'))}</div></div>`;
    body.style.display = '';
  }
  if (empty) empty.style.display = 'none';
  _currentSkillDetail = null;
  _skillMode = 'empty';
  _setSkillHeaderButtons('empty');
}

function _setSkillHeaderButtons(mode) {

  const header = $('mainSkills') && $('mainSkills').querySelector('.main-view-header');  const editBtn = $('btnEditSkillDetail');
  const delBtn = $('btnDeleteSkillDetail');
  const cancelBtn = $('btnCancelSkillDetail');
  const saveBtn = $('btnSaveSkillDetail');
  const show = b => b && (b.style.display = '');
  const hide = b => b && (b.style.display = 'none');
  if (mode === 'read') { if (header) header.style.display = 'flex';  show(editBtn); show(delBtn); hide(cancelBtn); hide(saveBtn); }
  else if (mode === 'create' || mode === 'edit') { if (header) header.style.display = 'flex'; hide(editBtn); hide(delBtn); show(cancelBtn); show(saveBtn); }
  else { if (header) header.style.display = 'none';  hide(editBtn); hide(delBtn); hide(cancelBtn); hide(saveBtn); }
}

async function openSkill(name, el) {
  // Highlight active skill in the sidebar list
  document.querySelectorAll('.skill-item').forEach(e => e.classList.remove('active'));
  if (el) el.classList.add('active');
  _skillPreFormDetail = null;
  _editingSkillName = null;
  try {
    const data = await api(`/api/skills/content?name=${encodeURIComponent(name)}`);
    if (data && (data.success === false || data.error)) {
      const message = data.error || t('skill_load_failed');
      _renderSkillError(name, message);
      setStatus(t('skill_load_failed') + message);
      return;
    }
    _currentSkillDetail = { name, content: data.content || '', linked_files: data.linked_files || {} };
    _renderSkillDetail(name, data.content || '', data.linked_files || {});
    _closeMobileSidebarAfterPanelSelection();
  } catch(e) { setStatus(t('skill_load_failed') + e.message); }
}

async function openSkillFile(skillName, filePath) {
  try {
    const data = await api(`/api/skills/content?name=${encodeURIComponent(skillName)}&file=${encodeURIComponent(filePath)}`);
    if (data && data.error) {
      _renderSkillError(skillName, data.error);
      setStatus(t('skill_file_load_failed') + data.error);
      return;
    }
    const body = $('skillDetailBody');
    if (!body) return;
    const ext = (filePath.split('.').pop() || '').toLowerCase();
    const isMd = ['md','markdown'].includes(ext);
    const backLabel = t('skills_back_to').replace('{0}', skillName);
    const header = `<div class="skill-file-breadcrumb"><a href="#" class="skill-file-back" data-skill-name="${esc(skillName)}">&larr; ${esc(backLabel)}</a><span class="skill-file-path">${esc(filePath)}</span></div>`;
    let content;
    if (isMd) {
      content = `<div class="main-view-content">${_skillMarkdownHtml(data.content || '')}</div>`;
    } else {
      const escaped = esc(data.content || '');
      content = `<pre class="skill-file-code"><code>${escaped}</code></pre>`;
    }
    body.innerHTML = header + content;
    body.style.display = '';
    const empty = $('skillDetailEmpty');
    if (empty) empty.style.display = 'none';
    body.querySelectorAll('.skill-file-back').forEach(a => {
      a.addEventListener('click', e => {
        e.preventDefault();
        if (_currentSkillDetail && _currentSkillDetail.name === a.dataset.skillName) {
          _renderSkillDetail(_currentSkillDetail.name, _currentSkillDetail.content, _currentSkillDetail.linked_files);
        } else {
          openSkill(a.dataset.skillName, null);
        }
      });
    });
    if (isMd) _enhanceSkillMarkdown(body);
    else requestAnimationFrame(() => { if (typeof highlightCode === 'function') highlightCode(); });
  } catch(e) { setStatus(t('skill_file_load_failed') + e.message); }
}

function editCurrentSkill() {
  if (!_currentSkillDetail) return;
  const s = _currentSkillDetail;
  let category = '';
  if (_skillsData) {
    const match = _skillsData.find(x => x.name === s.name);
    if (match) category = match.category || '';
  }
  _skillPreFormDetail = { name: s.name, content: s.content, linked_files: s.linked_files };
  _editingSkillName = s.name;
  _skillMode = 'edit';
  _renderSkillForm({ name: s.name, category, content: s.content || '', isEdit: true });
}

function openSkillCreate() {
  if (typeof openSkillOrRuleWizard === 'function') {
    openSkillOrRuleWizard();
    return;
  }
  if (typeof switchPanel === 'function' && _currentPanel !== 'skills') switchPanel('skills');
  _skillPreFormDetail = _currentSkillDetail ? { ..._currentSkillDetail } : null;
  _editingSkillName = null;
  _skillMode = 'create';
  _renderSkillForm({ name: '', category: '', content: '', isEdit: false });
  _closeMobileSidebarAfterPanelSelection();
}

function _renderSkillForm({ name, category, content, isEdit }) {
  const title = $('skillDetailTitle');
  const body = $('skillDetailBody');
  const empty = $('skillDetailEmpty');
  if (!body || !title) return;
  title.textContent = isEdit ? t('skills_edit') + ' · ' + name : t('new_skill');
  const nameDisabled = isEdit ? 'disabled' : '';
  const nameHint = isEdit ? `<div class="detail-form-hint">${esc(t('skill_rename_not_supported') || 'Renaming a skill is not supported. Create a new skill and delete the old one to rename.')}</div>` : '';
  body.innerHTML = `
    <div class="main-view-content">
      <form class="detail-form" onsubmit="event.preventDefault(); saveSkillForm();">
        <div class="detail-form-row">
          <label for="skillFormName">${esc(t('skill_name') || 'Name')}</label>
          <input type="text" id="skillFormName" value="${esc(name || '')}" placeholder="my-skill" autocomplete="off" ${nameDisabled} required>
          ${nameHint}
        </div>
        <div class="detail-form-row">
          <label for="skillFormCategory">${esc(t('skill_category') || 'Category')}</label>
          <input type="text" id="skillFormCategory" value="${esc(category || '')}" placeholder="${esc(t('skill_category_placeholder') || 'Optional, e.g. devops')}" autocomplete="off">
        </div>
        <div class="detail-form-row">
          <label for="skillFormContent">${esc(t('skill_content') || 'SKILL.md content')}</label>
          <textarea id="skillFormContent" rows="18" placeholder="${esc(t('skill_content_placeholder') || 'YAML frontmatter + markdown body')}">${esc(content || '')}</textarea>
        </div>
        <div id="skillFormError" class="detail-form-error" style="display:none"></div>
      </form>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _setSkillHeaderButtons(isEdit ? 'edit' : 'create');
  const focusEl = isEdit ? $('skillFormCategory') : $('skillFormName');
  if (focusEl) focusEl.focus();
}

function cancelSkillForm() {
  _editingSkillName = null;
  if (_skillPreFormDetail) {
    const snap = _skillPreFormDetail;
    _skillPreFormDetail = null;
    _currentSkillDetail = snap;
    _renderSkillDetail(snap.name, snap.content || '', snap.linked_files || {});
    return;
  }
  // Revert to empty state
  _skillPreFormDetail = null;
  _currentSkillDetail = null;
  _skillMode = 'empty';
  const body = $('skillDetailBody');
  const empty = $('skillDetailEmpty');
  const title = $('skillDetailTitle');
  if (body) { body.innerHTML = ''; body.style.display = 'none'; }
  if (empty) empty.style.display = '';
  if (title) title.textContent = '';
  _setSkillHeaderButtons('empty');
}

async function saveSkillForm() {
  const nameInput = $('skillFormName');
  const catInput = $('skillFormCategory');
  const contentInput = $('skillFormContent');
  const errEl = $('skillFormError');
  if (!nameInput || !contentInput || !errEl) return;
  const name = (nameInput.value || '').trim().toLowerCase().replace(/\s+/g, '-');
  const category = (catInput ? (catInput.value || '').trim() : '');
  const content = contentInput.value;
  errEl.style.display = 'none';
  if (!name) { errEl.textContent = t('skill_name_required'); errEl.style.display = ''; return; }
  if (!content.trim()) { errEl.textContent = t('content_required'); errEl.style.display = ''; return; }
  try {
    await api('/api/skills/save', {method:'POST', body: JSON.stringify({name, category: category||undefined, content})});
    showToast(_editingSkillName ? t('skill_updated') : t('skill_created'));
    _skillsData = null;
    _cronSkillsCache = null;
    if(typeof window!=='undefined'&&typeof window.invalidateSlashSkillCaches==='function') window.invalidateSlashSkillCaches();
    _editingSkillName = null;
    _skillPreFormDetail = null;
    await loadSkills();
    // Reload the saved skill in read mode with fresh content
    const row = document.querySelector(`.skill-item .skill-name`);
    const match = document.querySelectorAll('.skill-item');
    let targetEl = null;
    match.forEach(el => {
      const nm = el.querySelector('.skill-name');
      if (nm && nm.textContent === name) targetEl = el;
    });
    await openSkill(name, targetEl);
  } catch(e) { errEl.textContent = t('error_prefix') + e.message; errEl.style.display = ''; }
}

// Back-compat aliases (delete flow + any old callers)
const submitSkillSave = saveSkillForm;
function toggleSkillForm(){ openSkillCreate(); }

async function deleteCurrentSkill() {
  if (!_currentSkillDetail) return;
  const name = _currentSkillDetail.name;
  const message = t('skill_delete_confirm')
    ? t('skill_delete_confirm').replace('{0}', name)
    : `Delete skill "${name}"?`;
  const ok = await showConfirmDialog({
    title: t('delete_title') || 'Delete',
    message,
    confirmLabel: t('delete_title') || 'Delete',
    danger: true,
    focusCancel: true,
  });
  if (!ok) return;
  try {
    await api('/api/skills/delete', { method:'POST', body: JSON.stringify({ name }) });
    _currentSkillDetail = null;
    _skillPreFormDetail = null;
    _skillsData = null;
    _cronSkillsCache = null;
    if(typeof window!=='undefined'&&typeof window.invalidateSlashSkillCaches==='function') window.invalidateSlashSkillCaches();
    _skillMode = 'empty';
    const body = $('skillDetailBody');
    const empty = $('skillDetailEmpty');
    const title = $('skillDetailTitle');
    if (body) { body.innerHTML = ''; body.style.display = 'none'; }
    if (empty) empty.style.display = '';
    if (title) title.textContent = '';
    _setSkillHeaderButtons('empty');
    await loadSkills();
    showToast(t('skill_deleted') || 'Skill deleted');
  } catch(e) { setStatus(t('error_prefix') + e.message); }
}

// ── Workspace management ──
let _workspaceList = [];  // cached from /api/workspaces
let _wsSuggestTimer = null;
let _wsSuggestReq = 0;
let _wsSuggestIndex = -1;

function closeWorkspacePathSuggestions(){
  const box=$('workspaceFormPathSuggestions');
  if(box){
    box.innerHTML='';
    box.style.display='none';
  }
  _wsSuggestIndex=-1;
}

function _applyWorkspaceSuggestion(path){
  const input=$('workspaceFormPath');
  const next=(path||'').endsWith('/')?(path||''):`${path||''}/`;
  if(input){
    input.value=next;
    input.focus();
    input.setSelectionRange(next.length, next.length);
  }
  scheduleWorkspacePathSuggestions();
}

function _highlightWorkspaceSuggestion(idx){
  const box=$('workspaceFormPathSuggestions');
  if(!box)return;
  const items=[...box.querySelectorAll('.ws-suggest-item')];
  items.forEach((el,i)=>{
    const active=i===idx;
    el.classList.toggle('active', active);
    if(active) el.scrollIntoView({block:'nearest'});
  });
}

function _renderWorkspacePathSuggestions(paths){
  const box=$('workspaceFormPathSuggestions');
  if(!box)return;
  box.innerHTML='';
  if(!paths || !paths.length){
    box.style.display='none';
    _wsSuggestIndex=-1;
    return;
  }
  paths.forEach((path, idx)=>{
    const pathParts=(path||'').split('/').filter(Boolean);
    const leaf=pathParts[pathParts.length-1]||path;
    const parent=pathParts.length>1?`/${pathParts.slice(0,-1).join('/')}`:'/';
    const item=document.createElement('button');
    item.type='button';
    item.className='ws-suggest-item';
    item.innerHTML=`<span class="ws-suggest-leaf">${esc(leaf)}</span><span class="ws-suggest-parent">${esc(parent)}</span>`;
    item.dataset.path=path;
    item.onmouseenter=()=>{_wsSuggestIndex=idx;_highlightWorkspaceSuggestion(idx);};
    item.onmousedown=(e)=>{e.preventDefault();_applyWorkspaceSuggestion(path);};
    box.appendChild(item);
  });
  box.style.display='block';
  _wsSuggestIndex=0;
  _highlightWorkspaceSuggestion(_wsSuggestIndex);
}

async function _loadWorkspacePathSuggestions(prefix){
  const reqId=++_wsSuggestReq;
  try{
    const qs=new URLSearchParams({prefix:prefix||''}).toString();
    const data=await api(`/api/workspaces/suggest?${qs}`);
    if(reqId!==_wsSuggestReq)return;
    _renderWorkspacePathSuggestions(data.suggestions||[]);
  }catch(_){
    if(reqId!==_wsSuggestReq)return;
    closeWorkspacePathSuggestions();
  }
}

function scheduleWorkspacePathSuggestions(){
  const input=$('workspaceFormPath');
  if(!input)return;
  const prefix=input.value.trim();
  if(!prefix){
    closeWorkspacePathSuggestions();
    return;
  }
  if(_wsSuggestTimer) clearTimeout(_wsSuggestTimer);
  _wsSuggestTimer=setTimeout(()=>{
    _loadWorkspacePathSuggestions(prefix);
  }, 120);
}

function getWorkspaceFriendlyName(path){
  // Look up the friendly name from the workspace list cache, fallback to last path segment
  if(_workspaceList && _workspaceList.length){
    const match=_workspaceList.find(w=>w.path===path);
    if(match && match.name) return match.name;
  }
  return path.split('/').filter(Boolean).pop()||path;
}

function syncWorkspaceDisplays(){
  const hasSession=!!(S.session&&S.session.workspace);
  // Fall back to the profile default workspace when no session is active yet.
  // S._profileDefaultWorkspace is set during boot and profile switches from /api/settings.
  const defaultWs=(typeof S._profileDefaultWorkspace==='string'&&S._profileDefaultWorkspace)||'';
  const ws=hasSession?S.session.workspace:(defaultWs||'');
  const hasWorkspace=!!(ws);
  const label=hasWorkspace?getWorkspaceFriendlyName(ws):t('no_workspace');

  const sidebarName=$('sidebarWsName');
  const sidebarPath=$('sidebarWsPath');
  if(sidebarName) sidebarName.textContent=label;
  if(sidebarPath) sidebarPath.textContent=ws;

  const composerChip=$('composerWorkspaceChip');
  const composerLabel=$('composerWorkspaceLabel');
  const mobileAction=$('composerMobileWorkspaceAction');
  const mobileLabel=$('composerMobileWorkspaceLabel');
  const composerDropdown=$('composerWsDropdown');
  if(!hasWorkspace && composerDropdown) _setWorkspaceDropdownOpenState(composerDropdown,false);
  // Only show workspace label once boot has finished to prevent
  // flash of "No workspace" before the saved session finishes loading.
  if(composerLabel) composerLabel.textContent=S._bootReady?label:'';
  if(mobileLabel) mobileLabel.textContent=S._bootReady?label:'';
  const composerExpanded=!!(composerDropdown&&composerDropdown.classList.contains('open'));
  if(composerChip){
    composerChip.disabled=!hasWorkspace;
    composerChip.title=hasWorkspace?ws:t('no_workspace');
    composerChip.setAttribute('aria-label',hasWorkspace?t('workspace_switcher_aria',label):t('no_workspace'));
    composerChip.setAttribute('aria-expanded',composerExpanded?'true':'false');
    composerChip.classList.toggle('active',composerExpanded);
  }
  if(mobileAction){
    mobileAction.title=hasWorkspace?ws:t('no_workspace');
    mobileAction.setAttribute('aria-label',hasWorkspace?t('workspace_switcher_aria',label):t('no_workspace'));
    mobileAction.setAttribute('aria-expanded',composerExpanded?'true':'false');
    mobileAction.classList.toggle('active',composerExpanded);
  }
}

async function loadWorkspaceList(){
  try{
    const data = await api('/api/workspaces');
    if(typeof syncTerminalBackendState==='function') syncTerminalBackendState(data);
    _workspaceList = data.workspaces || [];
    syncWorkspaceDisplays();
    if(typeof syncTerminalButton==='function') syncTerminalButton();
    return data;
  }catch(e){ return {workspaces:[], last:''}; }
}

function _setWorkspaceDropdownOpenState(dd,open){
  if(!dd)return;
  dd.classList.toggle('open',!!open);
  dd.hidden=!open;
  dd.setAttribute('aria-hidden',open?'false':'true');
  if(open){
    try{dd.inert=false;}catch(_){}
    dd.removeAttribute('inert');
  }else{
    try{dd.inert=true;}catch(_){}
    dd.setAttribute('inert','');
  }
}

function _getComposerWorkspaceFocusTarget(){
  const panel=(typeof $==='function')?$('composerMobileConfigPanel'):null;
  const mobileAction=(typeof $==='function')?$('composerMobileWorkspaceAction'):null;
  if(panel&&panel.classList.contains('open')&&mobileAction&&!mobileAction.disabled) return mobileAction;
  return (typeof $==='function')?$('composerWorkspaceChip'):null;
}

function _focusComposerWorkspaceTarget(target){
  if(target&&!target.disabled&&typeof target.focus==='function'){
    try{target.focus({preventScroll:true});}
    catch(_){target.focus();}
  }
}

function _shouldRestoreComposerWorkspaceFocus(dd){
  if(typeof document==='undefined') return true;
  const active=document.activeElement;
  if(!active||active===document.body) return true;
  return !!(dd&&dd.contains(active));
}

function _renderWorkspaceAction(label, meta, iconSvg, onClick){
  const opt=document.createElement('div');
  opt.className='ws-opt ws-opt-action';
  opt.innerHTML=`<span class="ws-opt-icon">${iconSvg}</span><span><span class="ws-opt-name">${esc(label)}</span>${meta?`<span class="ws-opt-meta">${esc(meta)}</span>`:''}</span>`;
  opt.onclick=onClick;
  return opt;
}

function _positionComposerWsDropdown(){
  const dd=$('composerWsDropdown');
  const chip=$('composerWorkspaceGroup')||$('composerWorkspaceChip');
  const mobileAction=$('composerMobileWorkspaceAction');
  const panel=$('composerMobileConfigPanel');
  const footer=document.querySelector('.composer-footer');
  // While the mobile config panel is open, anchor to #composerMobileWorkspaceAction instead of only the desktop workspace chip.
  const anchor=(panel&&panel.classList.contains('open')&&mobileAction)?mobileAction:chip;
  if(!dd||!anchor||!footer)return;
  const chipRect=anchor.getBoundingClientRect();
  const footerRect=footer.getBoundingClientRect();
  let left=chipRect.left-footerRect.left;
  const maxLeft=Math.max(0, footer.clientWidth-dd.offsetWidth);
  left=Math.max(0, Math.min(left, maxLeft));
  dd.style.left=`${left}px`;
}

function _positionProfileDropdown(){
  const dd=$('profileDropdown');
  const trigger=_profileDropdownTrigger||$('profileChip');
  if(!dd||!trigger)return;
  const rect=trigger.getBoundingClientRect();
  const gap=4;
  const ddW=dd.offsetWidth||260;
  // Decide direction: below for titlebar, above for composer
  const openBelow=trigger===document.getElementById('titlebarProfileBtn');
  // Horizontal: center on trigger, clamp to viewport
  let left=rect.left+(rect.width/2)-(ddW/2);
  left=Math.max(8, Math.min(left, window.innerWidth-ddW-8));
  dd.style.left=left+'px';
  // Vertical
  if(openBelow){
    dd.style.bottom=''; // clear any stale bottom from a prior composer-chip open
    dd.style.top=(rect.bottom+gap)+'px';
    dd.classList.add('open-below');
  }else{
    dd.style.top=''; dd.style.bottom=''; // clear fixed top/bottom
    dd.style.bottom=(window.innerHeight-rect.top+gap)+'px';
    dd.classList.remove('open-below');
  }
}

function renderWorkspaceDropdownInto(dd, workspaces, currentWs){
  if(!dd)return;
  dd.innerHTML='';

  // ── Search row ──────────────────────────────────────────────────────────
  const searchRow=document.createElement('div');
  searchRow.className='ws-search-row';
  searchRow.innerHTML=`<input class="ws-search-input" type="text" placeholder="${esc(t('ws_search_placeholder')||'Search workspaces…')}" spellcheck="false" autocomplete="off"><button class="ws-search-clear" title="Clear search">${li('x',10)}</button>`;
  const si=searchRow.querySelector('.ws-search-input');
  const sc=searchRow.querySelector('.ws-search-clear');
  dd.appendChild(searchRow);

  // ── Workspace list ──────────────────────────────────────────────────────
  // Sort alphabetically by name (case-insensitive) before rendering.
  const sorted=[...workspaces].sort((a,b)=>(a.name||'').localeCompare(b.name||''));
  const listContainer=document.createElement('div');
  listContainer.className='ws-list-container';
  dd.appendChild(listContainer);

  // Pre-create noResults element so filterWs can reference it safely from the start.
  const noResults=document.createElement('div');
  noResults.className='ws-no-results';
  noResults.textContent=t('ws_no_results')||'No workspaces found';
  noResults.style.display='none';

  function filterWs(term){
    term=(term||'').trim().toLowerCase();
    let visible=0;
    const opts=listContainer.querySelectorAll('.ws-opt');
    for(const opt of opts){
      const name=(opt.dataset.name||'').toLowerCase();
      const path=(opt.dataset.path||'').toLowerCase();
      const show=!term||name.includes(term)||path.includes(term);
      opt.style.display=show?'':'none';
      if(show) visible++;
    }
    noResults.style.display=visible?'none':'';
  }

  function renderList(){
    listContainer.innerHTML='';
    for(const w of sorted){
      const opt=document.createElement('div');
      opt.className='ws-opt'+(w.path===currentWs?' active':'');
      opt.dataset.name=w.name||'';
      opt.dataset.path=w.path||'';
      opt.innerHTML=`<span class="ws-opt-name">${esc(w.name)}</span><span class="ws-opt-path">${esc(w.path)}</span>`;
      opt.onclick=()=>switchToWorkspace(w.path,w.name);
      listContainer.appendChild(opt);
    }
    listContainer.appendChild(noResults);
  }

  renderList();
  filterWs('');

  si.addEventListener('input',()=>{ filterWs(si.value); });
  sc.addEventListener('click',()=>{ si.value=''; filterWs(''); si.focus(); });

  // ── Footer actions ────────────────────────────────────────────────────────
  dd.appendChild(document.createElement('div')).className='ws-divider';
  dd.appendChild(_renderWorkspaceAction(
    t('workspace_new_worktree_conversation'),
    t('workspace_new_worktree_conversation_meta'),
    li('git-branch',12),
    async()=>{
      closeWsDropdown();
      try{
        await newSession(false,{worktree:true});
        await renderSessionList();
        const msg=$('msg');
        if(msg)msg.focus();
        showToast(t('workspace_worktree_created'));
      }catch(e){
        showToast(t('workspace_worktree_failed')+(e&&e.message?e.message:e),'error');
      }
    }
  ));
  dd.appendChild(document.createElement('div')).className='ws-divider';
  dd.appendChild(_renderWorkspaceAction(
    t('workspace_choose_path'),
    t('workspace_choose_path_meta'),
    li('folder',12),
    ()=>promptWorkspacePath()
  ));
  const div=document.createElement('div');div.className='ws-divider';dd.appendChild(div);
  dd.appendChild(_renderWorkspaceAction(
    t('workspace_manage'),
    t('workspace_manage_meta'),
    li('settings',12),
    ()=>{closeWsDropdown();mobileSwitchPanel('workspaces');}
  ));
}

function toggleWsDropdown(){
  const dd=$('wsDropdown');
  if(!dd)return;
  const open=dd.classList.contains('open');
  if(open){closeWsDropdown();}
  else{
    closeProfileDropdown(); // close profile dropdown if open
    loadWorkspaceList().then(data=>{
      renderWorkspaceDropdownInto(dd, data.workspaces, S.session?.workspace||S._profileDefaultWorkspace||data.last||'');
      _setWorkspaceDropdownOpenState(dd,true);
    });
  }
}

function toggleComposerWsDropdown(){
  const dd=$('composerWsDropdown');
  const chip=$('composerWorkspaceChip');
  const mobileAction=$('composerMobileWorkspaceAction');
  const panel=$('composerMobileConfigPanel');
  const usingMobileAction=!!(panel&&panel.classList.contains('open')&&mobileAction);
  if(!dd||(!usingMobileAction&&(!chip||chip.disabled)))return;
  const open=dd.classList.contains('open');
  if(open){closeWsDropdown();}
  else{
    closeProfileDropdown();
    if(typeof closeModelDropdown==='function') closeModelDropdown();
    if(typeof closeReasoningDropdown==='function') closeReasoningDropdown();
    loadWorkspaceList().then(data=>{
      renderWorkspaceDropdownInto(dd, data.workspaces, S.session?.workspace||S._profileDefaultWorkspace||data.last||'');
      _setWorkspaceDropdownOpenState(dd,true);
      _positionComposerWsDropdown();
      if(chip){
        chip.classList.add('active');
        chip.setAttribute('aria-expanded','true');
      }
      if(mobileAction){
        mobileAction.classList.add('active');
        mobileAction.setAttribute('aria-expanded','true');
      }
    });
  }
}

function closeWsDropdown(){
  const dd=$('wsDropdown');
  const composerDd=$('composerWsDropdown');
  const composerChip=$('composerWorkspaceChip');
  const mobileAction=$('composerMobileWorkspaceAction');
  if(dd)_setWorkspaceDropdownOpenState(dd,false);
  if(composerDd)_setWorkspaceDropdownOpenState(composerDd,false);
  if(composerChip){
    composerChip.classList.remove('active');
    composerChip.setAttribute('aria-expanded','false');
  }
  if(mobileAction){
    mobileAction.classList.remove('active');
    mobileAction.setAttribute('aria-expanded','false');
  }
}
document.addEventListener('click',e=>{
  if(
    !e.target.closest('#composerWorkspaceChip') &&
    !e.target.closest('#composerMobileWorkspaceAction') &&
    !e.target.closest('#composerWsDropdown')
  ) closeWsDropdown();
});
window.addEventListener('resize',()=>{
  const dd=$('composerWsDropdown');
  if(dd&&dd.classList.contains('open')) _positionComposerWsDropdown();
});

async function loadWorkspacesPanel(){
  const panel=$('workspacesPanel');
  if(!panel)return;
  const data=await loadWorkspaceList();
  renderWorkspacesPanel(data.workspaces);
}

function renderWorkspacesPanel(workspaces){
  const panel=$('workspacesPanel');
  panel.innerHTML='';
  const activePath = S.session ? S.session.workspace : '';
  for(let i=0;i<workspaces.length;i++){
    const w=workspaces[i];
    const row=document.createElement('div');
    row.className='ws-row';
    row.dataset.path = w.path;
    row.draggable=true;
    const isActive = w.path === activePath;
    const activeBadge = isActive ? `<span class="detail-badge active" style="margin-left:6px;font-size:9px;padding:1px 6px">${esc(t('profile_active'))}</span>` : '';
    row.innerHTML=`
      <span class="ws-drag-handle" title="${esc(t('workspace_drag_hint'))}">${li('grip-vertical',12)}</span>
      <div class="ws-row-info">
        <div class="ws-row-name">${esc(w.name)}${activeBadge}</div>
        <div class="ws-row-path">${esc(w.path)}</div>
      </div>`;
    // Click on info area only — not on drag handle
    const info=row.querySelector('.ws-row-info');
    if(info) info.onclick = (e) => { e.stopPropagation(); openWorkspaceDetail(w.path, row); };
    if (_currentWorkspaceDetail && _currentWorkspaceDetail.path === w.path) row.classList.add('active');

    // ── Drag-and-drop reorder ──
    row.addEventListener('dragstart', (e) => {
      // Only allow drag from the grip handle or the row itself
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed='move';
      e.dataTransfer.setData('text/plain', w.path);
      // Required for Firefox drag ghost
      if(e.dataTransfer.setDragImage) e.dataTransfer.setDragImage(row, 0, 0);
    });
    row.addEventListener('dragend', () => {
      row.classList.remove('dragging');
      panel.querySelectorAll('.ws-row.drag-over').forEach(r => r.classList.remove('drag-over'));
    });
    row.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect='move';
      // Highlight drop target
      panel.querySelectorAll('.ws-row.drag-over').forEach(r => r.classList.remove('drag-over'));
      if(!row.classList.contains('dragging')) row.classList.add('drag-over');
    });
    row.addEventListener('dragleave', () => {
      row.classList.remove('drag-over');
    });
    row.addEventListener('drop', async (e) => {
      e.preventDefault();
      row.classList.remove('drag-over');
      const fromPath = e.dataTransfer.getData('text/plain');
      const toPath = w.path;
      if(fromPath === toPath) return; // Same item, no-op
      // Compute new order
      const currentPaths = workspaces.map(ws => ws.path);
      const fromIdx = currentPaths.indexOf(fromPath);
      const toIdx = currentPaths.indexOf(toPath);
      if(fromIdx < 0 || toIdx < 0) return;
      currentPaths.splice(fromIdx, 1);
      currentPaths.splice(toIdx, 0, fromPath);
      try {
        const res = await api('/api/workspaces/reorder', {
          method: 'POST',
          body: JSON.stringify({ paths: currentPaths })
        });
        if(res && res.ok){
          renderWorkspacesPanel(res.workspaces);
          // Also refresh sidebar dropdown
          loadWorkspaceList().then(() => {});
        }
      } catch(err){
        showToast(t('workspace_reorder_failed'), 'error');
      }
    });

    panel.appendChild(row);
  }
  const hint=document.createElement('div');
  hint.style.cssText='font-size:11px;color:var(--muted);padding:8px 0';
  hint.textContent=t('workspace_paths_validated_hint');
  panel.appendChild(hint);
  // Re-render detail if we have one cached and we're not in a form
  if (_currentWorkspaceDetail && _workspaceMode !== 'create' && _workspaceMode !== 'edit') {
    const refreshed = workspaces.find(w => w.path === _currentWorkspaceDetail.path);
    if (refreshed) _renderWorkspaceDetail(refreshed);
    else _clearWorkspaceDetail();
  }
}

function _renderWorkspaceDetail(ws){
  _currentWorkspaceDetail = ws;
  const title = $('workspaceDetailTitle');
  const body = $('workspaceDetailBody');
  const empty = $('workspaceDetailEmpty');
  if (!title || !body) return;
  title.textContent = ws.name || ws.path;
  const activePath = S.session ? S.session.workspace : '';
  const isActive = ws.path === activePath;
  const isDefault = !!ws.is_default;
  const statusBadge = isActive
    ? `<span class="detail-badge active">${esc(t('profile_active'))}</span>`
    : `<span class="detail-badge">Inactive</span>`;
  const defaultBadge = isDefault ? ` <span class="detail-badge">${esc(t('profile_default_label'))}</span>` : '';
  body.innerHTML = `
    <div class="main-view-content">
      <div class="detail-card">
        <div class="detail-card-title">Space</div>
        <div class="detail-row"><div class="detail-row-label">Name</div><div class="detail-row-value">${esc(ws.name || '')}</div></div>
        <div class="detail-row"><div class="detail-row-label">Path</div><div class="detail-row-value"><code>${esc(ws.path)}</code></div></div>
        <div class="detail-row"><div class="detail-row-label">Status</div><div class="detail-row-value">${statusBadge}${defaultBadge}</div></div>
      </div>
      <div class="detail-card" style="margin-top:12px">
        <div class="detail-card-title">${esc(t('checkpoint_title'))}</div>
        <div id="checkpointListContainer">
          <div style="color:var(--muted);font-size:12px;padding:8px 0">${esc(t('checkpoint_loading'))}</div>
        </div>
      </div>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _workspaceMode = 'read';
  _setWorkspaceHeaderButtons('read', ws);
  _loadCheckpoints(ws.path);
}

function _setWorkspaceHeaderButtons(mode, ws){
  const header = $('mainWorkspaces') && $('mainWorkspaces').querySelector('.main-view-header');
  const actBtn = $('btnActivateWorkspaceDetail');
  const editBtn = $('btnEditWorkspaceDetail');
  const delBtn = $('btnDeleteWorkspaceDetail');
  const cancelBtn = $('btnCancelWorkspaceDetail');
  const saveBtn = $('btnSaveWorkspaceDetail');
  const show = b => b && (b.style.display = '');
  const hide = b => b && (b.style.display = 'none');
  if (mode === 'read') { if (header) header.style.display = 'flex';
    const activePath = S.session ? S.session.workspace : '';
    const isActive = ws && ws.path === activePath;
    const isDefault = !!(ws && ws.is_default);
    if (isActive) hide(actBtn); else show(actBtn);
    show(editBtn);
    if (isDefault) hide(delBtn); else show(delBtn);
    hide(cancelBtn); hide(saveBtn);
  } else if (mode === 'create' || mode === 'edit') {
    if (header) header.style.display = 'flex';
    hide(actBtn); hide(editBtn); hide(delBtn); show(cancelBtn); show(saveBtn);
  } else {
    if (header) header.style.display = 'none';
    [actBtn, editBtn, delBtn, cancelBtn, saveBtn].forEach(hide);
  }
}

function openWorkspaceDetail(path, el){
  if (!_workspaceList) return;
  const ws = _workspaceList.find(w => w.path === path);
  if (!ws) return;
  document.querySelectorAll('.ws-row').forEach(e => e.classList.remove('active'));
  const target = el || document.querySelector(`.ws-row[data-path="${CSS.escape(path)}"]`);
  if (target) target.classList.add('active');
  _workspacePreFormDetail = null;
  _renderWorkspaceDetail(ws);
  _closeMobileSidebarAfterPanelSelection();
}

function _clearWorkspaceDetail(){
  _currentWorkspaceDetail = null;
  _workspaceMode = 'empty';
  const title = $('workspaceDetailTitle');
  const body = $('workspaceDetailBody');
  const empty = $('workspaceDetailEmpty');
  if (title) title.textContent = '';
  if (body) { body.innerHTML = ''; body.style.display = 'none'; }
  if (empty) empty.style.display = '';
  _setWorkspaceHeaderButtons('empty');
}

async function activateCurrentWorkspace(){
  if (!_currentWorkspaceDetail) return;
  await switchToWorkspace(_currentWorkspaceDetail.path, _currentWorkspaceDetail.name);
  // Re-render detail after activation so the active badge updates
  _renderWorkspaceDetail(_currentWorkspaceDetail);
}

async function deleteCurrentWorkspace(){
  if (!_currentWorkspaceDetail) return;
  const path = _currentWorkspaceDetail.path;
  const _ok = await showConfirmDialog({title:t('workspace_remove_confirm_title'),message:t('workspace_remove_confirm_message',path),confirmLabel:t('remove'),danger:true,focusCancel:true});
  if(!_ok) return;
  try{
    const data=await api('/api/workspaces/remove',{method:'POST',body:JSON.stringify({path})});
    _workspaceList=data.workspaces;
    _clearWorkspaceDetail();
    renderWorkspacesPanel(data.workspaces);
    showToast(t('workspace_removed'));
  }catch(e){setStatus(t('remove_failed')+e.message);}
}

function openWorkspaceCreate(){
  if (typeof switchPanel === 'function' && _currentPanel !== 'workspaces') switchPanel('workspaces');
  _workspacePreFormDetail = _currentWorkspaceDetail ? { ..._currentWorkspaceDetail } : null;
  _workspaceMode = 'create';
  _renderWorkspaceForm({ name:'', path:'', isEdit:false });
  // Mobile: the add-space form lives in the main view, which is covered by the
  // full-screen sidebar drawer. Close the drawer so the form is visible (mirror
  // openWorkspaceDetail's behaviour); no-op on desktop.
  _closeMobileSidebarAfterPanelSelection();
}

function editCurrentWorkspace(){
  if (!_currentWorkspaceDetail) return;
  _workspacePreFormDetail = { ..._currentWorkspaceDetail };
  _workspaceMode = 'edit';
  _renderWorkspaceForm({ name: _currentWorkspaceDetail.name || '', path: _currentWorkspaceDetail.path || '', isEdit: true });
}

function _renderWorkspaceForm({ name, path, isEdit }){
  const title = $('workspaceDetailTitle');
  const body = $('workspaceDetailBody');
  const empty = $('workspaceDetailEmpty');
  if (!title || !body) return;
  title.textContent = isEdit ? (t('edit') + ' · ' + (name || path)) : (t('workspace_new_title') || 'New space');
  const pathDisabled = isEdit ? 'disabled' : '';
  const pathHint = isEdit
    ? `<div class="detail-form-hint">${esc(t('workspace_path_readonly') || 'Path cannot be changed. Rename only.')}</div>`
    : `<div class="detail-form-hint">${esc(t('workspace_paths_validated_hint'))}</div>`;
  body.innerHTML = `
    <div class="main-view-content">
      <form class="detail-form" onsubmit="event.preventDefault(); saveWorkspaceForm();">
        <div class="detail-form-row">
          <label for="workspaceFormName">${esc(t('workspace_name_label') || 'Name')}</label>
          <input type="text" id="workspaceFormName" value="${esc(name || '')}" placeholder="${esc(t('workspace_name_placeholder') || 'Optional friendly name')}" autocomplete="off">
        </div>
        <div class="detail-form-row">
          <label for="workspaceFormPath">${esc(t('workspace_path_label') || 'Path')}</label>
          <div class="workspace-form-path-wrap" style="position:relative">
            <input type="text" id="workspaceFormPath" value="${esc(path || '')}" placeholder="${esc(t('workspace_add_path_placeholder') || '/absolute/path/to/folder')}" autocomplete="off" ${pathDisabled} required>
            <div id="workspaceFormPathSuggestions" class="ws-suggestions" style="display:none"></div>
          </div>
          ${pathHint}
        </div>
        <div id="workspaceFormError" class="detail-form-error" style="display:none"></div>
      </form>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _setWorkspaceHeaderButtons(isEdit ? 'edit' : 'create');
  if (!isEdit) _wireWorkspaceFormPathSuggestions();
  const focus = isEdit ? $('workspaceFormName') : $('workspaceFormPath');
  if (focus) focus.focus();
}

function cancelWorkspaceForm(){
  closeWorkspacePathSuggestions();
  if (_workspacePreFormDetail) {
    const snap = _workspacePreFormDetail;
    _workspacePreFormDetail = null;
    _renderWorkspaceDetail(snap);
    return;
  }
  _clearWorkspaceDetail();
}

async function saveWorkspaceForm(){
  const nameEl = $('workspaceFormName');
  const pathEl = $('workspaceFormPath');
  const errEl = $('workspaceFormError');
  if (!pathEl || !errEl) return;
  const name = (nameEl ? nameEl.value : '').trim();
  const path = (pathEl.value || '').trim();
  errEl.style.display = 'none';
  if (!path) { errEl.textContent = t('workspace_path_required') || 'Path is required'; errEl.style.display = ''; return; }
  try {
    if (_workspaceMode === 'edit' && _currentWorkspaceDetail) {
      const targetPath = _currentWorkspaceDetail.path;
      const newName = name || _currentWorkspaceDetail.name || '';
      await api('/api/workspaces/rename', { method:'POST', body: JSON.stringify({ path: targetPath, name: newName }) });
      // Refresh list and re-render detail
      const data = await api('/api/workspaces');
      _workspaceList = data.workspaces || [];
      _workspacePreFormDetail = null;
      showToast(t('workspace_renamed') || t('workspace_added'));
      renderWorkspacesPanel(_workspaceList);
      openWorkspaceDetail(targetPath);
      return;
    }
    const data = await api('/api/workspaces/add', { method:'POST', body: JSON.stringify({ path }) });
    _workspaceList = data.workspaces || [];
    _workspacePreFormDetail = null;
    // Apply rename if a friendly name was supplied
    if (name) {
      try { await api('/api/workspaces/rename', { method:'POST', body: JSON.stringify({ path, name }) }); } catch(_) {}
      const refreshed = await api('/api/workspaces');
      _workspaceList = refreshed.workspaces || _workspaceList;
    }
    renderWorkspacesPanel(_workspaceList);
    showToast(t('workspace_added'));
    const added = _workspaceList.find(w => w.path === path) || _workspaceList[_workspaceList.length - 1];
    if (added) openWorkspaceDetail(added.path);
  } catch (e) {
    errEl.textContent = t('error_prefix') + e.message;
    errEl.style.display = '';
  }
}

// Back-compat: any legacy caller of addWorkspace() opens the new form instead.
function addWorkspace(){ openWorkspaceCreate(); }

function _wireWorkspaceFormPathSuggestions(){
  const input=$('workspaceFormPath');
  if(!input) return;
  input.oninput=()=>scheduleWorkspacePathSuggestions();
  input.onfocus=()=>{
    if(input.value.trim()) scheduleWorkspacePathSuggestions();
    else closeWorkspacePathSuggestions();
  };
  input.onkeydown=(e)=>{
    const box=$('workspaceFormPathSuggestions');
    const items=box?[...box.querySelectorAll('.ws-suggest-item')]:[];
    if(!items.length){
      return;
    }
    if(e.key==='ArrowDown'){
      e.preventDefault();
      _wsSuggestIndex=Math.min(items.length-1,Math.max(-1,_wsSuggestIndex)+1);
      _highlightWorkspaceSuggestion(_wsSuggestIndex);
      return;
    }
    if(e.key==='ArrowUp'){
      e.preventDefault();
      _wsSuggestIndex=_wsSuggestIndex<=0?0:_wsSuggestIndex-1;
      _highlightWorkspaceSuggestion(_wsSuggestIndex);
      return;
    }
    if(e.key==='Escape'){
      e.preventDefault();
      closeWorkspacePathSuggestions();
      return;
    }
    if(e.key==='Enter' && _wsSuggestIndex>=0 && items[_wsSuggestIndex]){
      e.preventDefault();
      _applyWorkspaceSuggestion(items[_wsSuggestIndex].dataset.path||'');
      return;
    }
    if(e.key==='Tab' && _wsSuggestIndex>=0 && items[_wsSuggestIndex]){
      e.preventDefault();
      _applyWorkspaceSuggestion(items[_wsSuggestIndex].dataset.path||'');
      return;
    }
  };
}

document.addEventListener('click',e=>{
  if(!e.target.closest('.workspace-form-path-wrap')) closeWorkspacePathSuggestions();
});

async function removeWorkspace(path){
  const _rmWs=await showConfirmDialog({title:t('workspace_remove_confirm_title'),message:t('workspace_remove_confirm_message',path),confirmLabel:t('remove'),danger:true,focusCancel:true});
  if(!_rmWs) return;
  try{
    const data=await api('/api/workspaces/remove',{method:'POST',body:JSON.stringify({path})});
    _workspaceList=data.workspaces;
    renderWorkspacesPanel(data.workspaces);
    showToast(t('workspace_removed'));
  }catch(e){setStatus(t('remove_failed')+e.message);}
}

async function promptWorkspacePath(){
  // Opus review Q6: if called from blank page (no session), auto-create one first.
  if(!S.session){
    const ws=(typeof S._profileDefaultWorkspace==='string'&&S._profileDefaultWorkspace)||'';
    if(!ws)return;
    try{
      // System-minted session (#6022): worktree:false is explicit so a config
      // worktree default can't leak a worktree from a workspace prompt.
      const r=await api('/api/session/new',{method:'POST',body:JSON.stringify({workspace:ws,worktree:false})});
      if(r&&r.session){S._pendingSessionToolsets=null;S.session=r.session;S.messages=[];if(typeof syncTopbar==='function')syncTopbar();if(typeof renderMessages==='function')renderMessages();if(typeof renderSessionList==='function')await renderSessionList();}
    }catch(e){showToast(t('workspace_switch_failed')+e.message);return;}
    if(!S.session)return;
  }
  const value=await showPromptDialog({
    title:t('workspace_switch_prompt_title'),
    message:t('workspace_switch_prompt_message'),
    confirmLabel:t('workspace_switch_prompt_confirm'),
    placeholder:t('workspace_switch_prompt_placeholder'),
    value:S.session.workspace||''
  });
  const path=(value||'').trim();
  if(!path)return;
  try{
    const data=await api('/api/workspaces/add',{method:'POST',body:JSON.stringify({path})});
    _workspaceList=data.workspaces||[];
    const target=_workspaceList[_workspaceList.length-1];
    if(!target) throw new Error(t('workspace_not_added'));
    await switchToWorkspace(target.path,target.name);
  }catch(e){
    if(String(e.message||'').includes('Workspace already in list')){
      showToast(t('workspace_already_saved'));
      return;
    }
    showToast(t('workspace_switch_failed')+e.message);
  }
}

async function switchToWorkspace(path,name){
  // Opus review Q6: if called from blank page, auto-create a session bound to
  // the requested workspace so the switch doesn't silently no-op.
  if(!S.session){
    const ws=path||(typeof S._profileDefaultWorkspace==='string'&&S._profileDefaultWorkspace)||'';
    if(!ws){showToast(t('no_workspace'));return;}
    try{
      // System-minted session (#6022): explicit worktree:false — a workspace
      // switch from a blank page is not deliberate New Chat intent.
      const r=await api('/api/session/new',{method:'POST',body:JSON.stringify({workspace:ws,worktree:false})});
      if(r&&r.session){S._pendingSessionToolsets=null;S.session=r.session;S.messages=[];if(typeof syncTopbar==='function')syncTopbar();if(typeof renderMessages==='function')renderMessages();if(typeof renderSessionList==='function')await renderSessionList();}
    }catch(e){if(typeof setStatus==='function')setStatus(t('switch_failed')+e.message);return;}
    if(!S.session)return;
  }
  // Workspace mutation during a live turn would desync the active stream context.
  if(S.busy){
    showToast(t('workspace_busy_switch'));
    return;
  }
  // #5473 (opt-in, default off): treat switching to a DIFFERENT workspace as a
  // new-chat boundary instead of mutating the current session in place. A
  // workspace switch changes the project-context files the agent loaded, so
  // reusing the session would carry stale cross-workspace context. Only fires
  // when: the setting is on, the target workspace actually differs from the
  // current one, and the current conversation has real messages worth keeping on
  // its original workspace. Same-workspace selection stays an in-place refresh.
  if(
    window._newChatOnWorkspaceSwitch===true &&
    S.session && S.session.workspace && path && path!==S.session.workspace &&
    Array.isArray(S.messages) && S.messages.length>0
  ){
    if(typeof _previewDirty!=='undefined'&&_previewDirty){
      const discard=await showConfirmDialog({
        title:t('discard_file_edits_title'),
        message:t('discard_file_edits_message'),
        confirmLabel:t('discard'),
        danger:true
      });
      if(!discard)return;
      if(typeof cancelEditMode==='function')cancelEditMode();
      if(typeof clearPreview==='function')clearPreview();
    }
    closeWsDropdown();
    // Bind the new chat to the selected workspace via the one-shot flag newSession() reads.
    S._profileSwitchWorkspace=path;
    if(typeof newSession==='function') await newSession(false);
    showToast(t('workspace_switched_new_chat',name||getWorkspaceFriendlyName(path)));
    return;
  }
  if(typeof _previewDirty!=='undefined'&&_previewDirty){
    const discard=await showConfirmDialog({
      title:t('discard_file_edits_title'),
      message:t('discard_file_edits_message'),
      confirmLabel:t('discard'),
      danger:true
    });
    if(!discard)return;
    if(typeof cancelEditMode==='function')cancelEditMode();
    if(typeof clearPreview==='function')clearPreview();
  }
  const composerDd=(typeof $==='function')?$('composerWsDropdown'):null;
  const restoreComposerFocusTarget=(composerDd&&composerDd.classList.contains('open')&&typeof _getComposerWorkspaceFocusTarget==='function')
    ? _getComposerWorkspaceFocusTarget()
    : null;
  try{
    closeWsDropdown();
    // Invalidate any older /api/list response before the explicit workspace
    // mutation. Otherwise a delayed recovery response for this same session can
    // overwrite the user's newer selection and reject this switch's fresh tree.
    if(typeof bumpWorkspaceTreeGen==='function')bumpWorkspaceTreeGen();
    await api('/api/session/update',{method:'POST',body:JSON.stringify({
      session_id:S.session.session_id, workspace:path, model:S.session.model, model_provider:S.session.model_provider||null
    })});
    S.session.workspace=path;
    // Explicit workspace switch = user overriding any pending profile-switch default.
    // Clear the one-shot flag so a subsequent newSession() inherits this choice instead.
    S._profileSwitchWorkspace=null;
    S._pendingSessionToolsets=null;
    syncTopbar();
    if(
      restoreComposerFocusTarget&&
      typeof _shouldRestoreComposerWorkspaceFocus==='function'&&
      _shouldRestoreComposerWorkspaceFocus(composerDd)&&
      typeof _focusComposerWorkspaceTarget==='function'
    ) _focusComposerWorkspaceTarget(restoreComposerFocusTarget);
    await loadDir('.');
    showToast(t('workspace_switched_to',name||getWorkspaceFriendlyName(path)));
  }catch(e){setStatus(t('switch_failed')+e.message);}
}

// ── Profile panel + dropdown ──
let _profilesCache = null;
let _profileDropdownFetchPromise = null;
let _profileDropdownCacheLoadedFromStorage = false;
const PROFILE_DROPDOWN_CACHE_KEY = 'agy-webui-profile-dropdown-cache-v1';
const PROFILE_DROPDOWN_CACHE_TTL_MS = 5 * 60 * 1000;
let _profileSwitchGeneration = 0;
let _profileDropdownTrigger = null;  // tracks which element triggered the dropdown
let _profileDropdownOpenGeneration = 0;

function _profileDropdownClearStoredCache(){
  try{localStorage.removeItem(PROFILE_DROPDOWN_CACHE_KEY);}catch(_){}
}

function _profileDropdownDataCacheUsable(data){
  return !!(
    data &&
    Array.isArray(data.profiles) &&
    data.profiles.length &&
    data.profiles.every(p=>
      p &&
      typeof p.name==='string' &&
      // Renderer-read fields must be safe types: renderProfileDropdown /
      // renderProfilesPanel call p.model.split('/') guarded only by truthiness,
      // so a poisoned cached row like {name:"x", model:{}} would pass a
      // name-only check yet throw synchronously on dropdown open (bricking
      // profile switching). Reject rows whose model is a non-string truthy value.
      (p.model==null || typeof p.model==='string')
    )
  );
}

function _profileDropdownCacheUsable(data){
  return !!(_profileDropdownDataCacheUsable(data) && data.single_profile_mode !== true);
}

function _profileDropdownReadStoredCache(){
  if(_profileDropdownCacheLoadedFromStorage) return _profileDropdownCacheUsable(_profilesCache) ? _profilesCache : null;
  _profileDropdownCacheLoadedFromStorage = true;
  try{
    const raw=localStorage.getItem(PROFILE_DROPDOWN_CACHE_KEY);
    if(!raw) return null;
    const parsed=JSON.parse(raw);
    if(!parsed || typeof parsed.ts!=='number' || !parsed.data) { _profileDropdownClearStoredCache(); return null; }
    if(Date.now()-parsed.ts>PROFILE_DROPDOWN_CACHE_TTL_MS) { _profileDropdownClearStoredCache(); return null; }
    if(!_profileDropdownCacheUsable(parsed.data)) { _profileDropdownClearStoredCache(); return null; }
    _profilesCache = parsed.data;
    return _profilesCache;
  }catch(_){_profileDropdownClearStoredCache();return null;}
}

function _profileDropdownWriteStoredCache(data){
  if(!_profileDropdownCacheUsable(data)) { _profileDropdownClearStoredCache(); return; }
  try{localStorage.setItem(PROFILE_DROPDOWN_CACHE_KEY, JSON.stringify({ts:Date.now(), data}));}catch(_){}
}

function _profileDropdownBestCachedData(){
  if(_profileDropdownCacheUsable(_profilesCache)) return _profilesCache;
  if(_profileDropdownDataCacheUsable(_profilesCache)) return null;
  _profilesCache = null;
  return _profileDropdownReadStoredCache();
}

function _profileDropdownFetchFresh(){
  if(_profileDropdownFetchPromise) return _profileDropdownFetchPromise;
  _profileDropdownFetchPromise = api('/api/profiles', {timeoutToast:false}).then(data=>{
    if(_profileDropdownDataCacheUsable(data)) _profilesCache = data;
    _profileDropdownWriteStoredCache(data);
    return data;
  }).finally(()=>{ _profileDropdownFetchPromise = null; });
  return _profileDropdownFetchPromise;
}

function _warmProfileDropdownCache(){
  _profileDropdownBestCachedData();
  _profileDropdownFetchFresh().catch(()=>{});
}

if(typeof window!=='undefined'){
  window.addEventListener('load',()=>{
    setTimeout(()=>{
      if(typeof document==='undefined'||!document.hidden) _warmProfileDropdownCache();
    },1200);
  },{once:true});
}

function _renderProfileDropdownLoading(){
  const dd=$('profileDropdown');
  if(!dd)return;
  dd.innerHTML=`<div class="profile-opt profile-opt-loading"><div class="profile-opt-name">${esc(t('loading')||'Loading...')}</div></div>`;
}

function _openProfileDropdownShell(){
  const dd=$('profileDropdown');
  if(!dd)return;
  dd.classList.add('open');
  _positionProfileDropdown();
  const chip=$('profileChip');
  if(chip && _profileDropdownTrigger===chip) chip.classList.add('active');
  const tbtn=$('titlebarProfileBtn');
  if(tbtn && _profileDropdownTrigger===tbtn) tbtn.classList.add('active');
}

async function _profileSwitchPanelLoad(){
  // Cross-profile cron visibility is an active-profile opt-in; never carry it
  // into the next profile when the Tasks panel wasn't the visible panel.
  _showAllCronProfiles = false;
  _cronOtherProfileCount = 0;
  _cronPreFormDetail = null;
  _editingCronId = null;
  _cronIsDuplicate = false;
  _clearCronDetail();
  if (_currentPanel === 'skills') await loadSkills();
  if (_currentPanel === 'tasks') await loadCrons();
  if (_currentPanel === 'profiles') await loadProfilesPanel();
  if (_currentPanel === 'workspaces') await loadWorkspacesPanel();
}

function _refreshProfileSwitchBackground(gen){
  window._modelDropdownReady=null;
  if (typeof window._ensureModelDropdownReady === 'function') {
    Promise.resolve(window._ensureModelDropdownReady()).catch(()=>{});
  }
  Promise.resolve(loadWorkspaceList()).then(()=>{
    if (gen !== _profileSwitchGeneration) return;
    if (S.session && typeof syncTopbar === 'function') syncTopbar();
  }).catch(()=>{});
  // Reconcile per-profile sidebar tab visibility. hidden_tabs is a per-profile
  // appearance setting; without this fetch, Profile A's hidden-tabs choice
  // would remain in effect under Profile B until the user opens Settings.
  // Stage-394 follow-up to #2636 deep review.
  Promise.resolve(api('/api/settings')).then(function(s){
    if (gen !== _profileSwitchGeneration) return;
    var hidden = (s && Array.isArray(s.hidden_tabs)) ? s.hidden_tabs : [];
    hidden = hidden.filter(function(x){ return typeof x === 'string' && x.trim(); });
    var order = (s && Array.isArray(s.tab_order)) ? s.tab_order : [];
    order = order.filter(function(x){ return typeof x === 'string' && x.trim(); });
    if (typeof _setHiddenTabs === 'function') _setHiddenTabs(hidden);
    if (typeof _setTabOrder === 'function') _setTabOrder(order);
    if (typeof _applyTabOrder === 'function') _applyTabOrder(order);
    if (typeof _applyTabVisibility === 'function') _applyTabVisibility(hidden);
    _ensureComposerControlVisibilityState(s||{});
    if(Array.isArray(s&&s.composer_control_order)){
      const nextOrder=_setComposerControlOrder(s.composer_control_order);
      if(typeof window._applyComposerControlOrder==='function') window._applyComposerControlOrder(nextOrder);
    }
    _renderComposerControlChips();
    _renderComposerSituationalControlChips();
    if(typeof _applyComposerFooterVisibilitySettings==='function') _applyComposerFooterVisibilitySettings();
    window._showTitlebarProfile=!!(s&&s.show_titlebar_profile);
    if(typeof _applyTitlebarProfileVisibility==='function') _applyTitlebarProfileVisibility();
  }).catch(function(){});
}

async function loadProfilesPanel() {
  const panel = $('profilesPanel');
  if (!panel) return;
  try {
    const data = await api('/api/profiles');
    _profilesCache = data;
    _profileDropdownWriteStoredCache(data);
    panel.innerHTML = '';

    // Hide "New profile" button in single profile mode
    const newProfileBtn = document.querySelector('[onclick="openProfileCreate()"]');
    if (newProfileBtn) {
      newProfileBtn.style.display = data.single_profile_mode ? 'none' : '';
    }

    // In single profile mode, don't show the explanatory card
    if (!data.single_profile_mode) {
      const explainer = document.createElement('div');
      explainer.className = 'profile-card profile-help-card';
      explainer.innerHTML = `
        <div class="profile-card-header">
          <div style="min-width:0;flex:1">
            <div class="profile-card-name">${esc(t('profile_concept_title'))}</div>
            <div class="profile-card-meta">${esc(t('profile_concept_subtitle'))}</div>
          </div>
        </div>`;
      explainer.onclick = () => _renderProfileConceptHelp(data.active || 'default');
      panel.appendChild(explainer);
    }

    if (!data.profiles || !data.profiles.length) {
      const emptyMsg = document.createElement('div');
      emptyMsg.style.cssText = 'padding:16px;color:var(--muted);font-size:12px';
      emptyMsg.textContent = t('profiles_no_profiles');
      panel.appendChild(emptyMsg);
      if (_profileMode !== 'create') _clearProfileDetail();
      return;
    }
    const activeName = (S.activeProfile && data.profiles.some(p => p.name === S.activeProfile))
      ? S.activeProfile
      : (data.active || 'default');
    for (const p of data.profiles) {
      const card = document.createElement('div');
      card.className = 'profile-card';
      card.dataset.name = p.name;
      const meta = [];
      if (typeof p.model === 'string' && p.model) meta.push(p.model.split('/').pop());
      if (p.provider) meta.push(p.provider);
      if (p.total_skills && p.total_skills > 0) meta.push(t('profile_skill_count', p.total_skills).replace(String(p.total_skills), `${p.enabled_skills} / ${p.total_skills}`));
      const gwDot = p.gateway_running
        ? `<span class="profile-opt-badge running" title="${esc(t('profile_gateway_running'))}"></span>`
        : `<span class="profile-opt-badge stopped" title="${esc(t('profile_gateway_stopped'))}"></span>`;
      const isActive = p.name === activeName;
      const activeBadge = isActive ? `<span style="color:var(--link);font-size:10px;font-weight:600;margin-left:6px">${esc(t('profile_active'))}</span>` : '';
      const defaultBadge = p.is_default ? ` <span style="opacity:.5">${esc(t('profile_default_label'))}</span>` : '';
      const hiddenBadge = p.visible === false ? ' <span class="detail-badge" title="Hidden from chat">Hidden from chat</span>' : '';
      card.innerHTML = `
        <div class="profile-card-header">
          <div style="min-width:0;flex:1">
            <div class="profile-card-name${isActive ? ' is-active' : ''}">${gwDot}${esc(p.name)}${defaultBadge}${activeBadge}${hiddenBadge}</div>
            ${meta.length ? `<div class="profile-card-meta">${esc(meta.join(' \u00b7 '))}</div>` : `<div class="profile-card-meta">${esc(t('profile_no_configuration'))}</div>`}
          </div>
        </div>`;
      card.onclick = () => openProfileDetail(p.name, card);
      if (_currentProfileDetail && _currentProfileDetail.name === p.name) card.classList.add('active');
      panel.appendChild(card);
    }
    // Re-render detail with fresh data if we have one and we're not in a form
    if (_currentProfileDetail && _profileMode !== 'create') {
      const refreshed = data.profiles.find(p => p.name === _currentProfileDetail.name);
      if (refreshed) _renderProfileDetail(refreshed, data.active);
      else _clearProfileDetail();
    }
  } catch (e) {
    panel.innerHTML = `<div style="color:var(--accent);font-size:12px;padding:12px">${esc(t('error_prefix'))}${esc(e.message)}</div>`;
  }
}

function _renderProfileConceptHelp(activeName){
  const title = $('profileDetailTitle');
  const body = $('profileDetailBody');
  const empty = $('profileDetailEmpty');
  if (!title || !body) return;
  title.textContent = t('profile_concept_title');
  body.innerHTML = `
    <div class="main-view-content">
      <div class="detail-card">
        <div class="detail-card-title">${esc(t('profile_concept_title'))}</div>
        <div class="detail-row"><div class="detail-row-label">${esc(t('tab_profiles'))}</div><div class="detail-row-value">${esc(t('profile_concept_desc_profiles'))}</div></div>
        <div class="detail-row"><div class="detail-row-label">${esc(t('tab_workspaces'))}</div><div class="detail-row-value">${esc(t('profile_concept_desc_workspaces'))}</div></div>
        <div class="detail-row"><div class="detail-row-label">${esc(t('profile_concept_label_together'))}</div><div class="detail-row-value">${esc(t('profile_concept_desc_together'))}</div></div>
        <div class="detail-row" style="border-top:1px solid var(--border);padding-top:8px;margin-top:4px"><div class="detail-row-label">${esc(t('profile_concept_label_example'))}</div><div class="detail-row-value">${esc(t('profile_concept_example'))}</div></div>
      </div>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _profileMode = 'read';
  _currentProfileDetail = null;
  _setProfileHeaderButtons('help');
}

function _renderProfileDetail(p, activeName){
  _currentProfileDetail = p;
  const title = $('profileDetailTitle');
  const body = $('profileDetailBody');
  const empty = $('profileDetailEmpty');
  if (!title || !body) return;
  title.textContent = p.name;
  const isActive = p.name === activeName;
  const isDefault = !!p.is_default;
  const statusBadge = isActive
    ? `<span class="detail-badge active">${esc(t('profile_active'))}</span>`
    : `<span class="detail-badge">Inactive</span>`;
  const defaultBadge = isDefault ? ` <span class="detail-badge">${esc(t('profile_default_label'))}</span>` : '';
  const gwBadge = p.gateway_running
    ? `<span class="detail-badge ok">${esc(t('profile_gateway_running'))}</span>`
    : `<span class="detail-badge">${esc(t('profile_gateway_stopped'))}</span>`;
  const rows = [];
  rows.push(`<div class="detail-row"><div class="detail-row-label">Status</div><div class="detail-row-value">${statusBadge}${defaultBadge}</div></div>`);
  rows.push(`<div class="detail-row"><div class="detail-row-label">Gateway</div><div class="detail-row-value">${gwBadge}</div></div>`);
  if (p.model) rows.push(`<div class="detail-row"><div class="detail-row-label">Model</div><div class="detail-row-value"><code>${esc(p.model)}</code></div></div>`);
  if (p.provider) rows.push(`<div class="detail-row"><div class="detail-row-label">Provider</div><div class="detail-row-value">${esc(p.provider)}</div></div>`);
  if (p.base_url) rows.push(`<div class="detail-row"><div class="detail-row-label">Base URL</div><div class="detail-row-value"><code>${esc(p.base_url)}</code></div></div>`);
  rows.push(`<div class="detail-row"><div class="detail-row-label">API key</div><div class="detail-row-value">${p.has_env ? esc(t('profile_api_keys_configured')) : '<span style="color:var(--muted)">Not configured</span>'}</div></div>`);
  if (p.total_skills && p.total_skills > 0) rows.push(`<div class="detail-row"><div class="detail-row-label">Skills</div><div class="detail-row-value">${esc(t('profile_skill_count', p.total_skills).replace(String(p.total_skills), `${p.enabled_skills} / ${p.total_skills}`))}</div></div>`);
  if (p.default_workspace) rows.push(`<div class="detail-row"><div class="detail-row-label">Default space</div><div class="detail-row-value"><code>${esc(p.default_workspace)}</code></div></div>`);
  body.innerHTML = `
    <div class="main-view-content">
      <div class="detail-card">
        <div class="detail-card-title">Profile</div>
        ${rows.join('')}
      </div>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _profileMode = 'read';
  _setProfileHeaderButtons('read', p, activeName);
}

function _setProfileHeaderButtons(mode, p, activeName){
  const header = $('mainProfiles') && $('mainProfiles').querySelector('.main-view-header');
  const actBtn = $('btnActivateProfileDetail');
  const delBtn = $('btnDeleteProfileDetail');
  const cancelBtn = $('btnCancelProfileDetail');
  const saveBtn = $('btnSaveProfileDetail');
  const show = b => b && (b.style.display = '');
  const hide = b => b && (b.style.display = 'none');
  if (mode === 'read') {
    if (header) header.style.display = 'flex';
    const isActive = p && p.name === activeName;
    const isDefault = !!(p && p.is_default);
    const singleProfileMode = !!(_profilesCache && _profilesCache.single_profile_mode);
    if (isActive || singleProfileMode) hide(actBtn); else show(actBtn);
    if (isDefault || singleProfileMode) hide(delBtn); else show(delBtn);
    hide(cancelBtn); hide(saveBtn);
  } else if (mode === 'create') {
    if (header) header.style.display = 'flex';
    hide(actBtn); hide(delBtn); show(cancelBtn); show(saveBtn);
  } else if (mode === 'help') {
    // Read-only help/concept view: title is populated, so show the header but
    // hide every action button (no profile to act on).
    if (header) header.style.display = 'flex';
    [actBtn, delBtn, cancelBtn, saveBtn].forEach(hide);
  } else {
    if (header) header.style.display = 'none';
    [actBtn, delBtn, cancelBtn, saveBtn].forEach(hide);
  }
}

function openProfileDetail(name, el){
  if (!_profilesCache || !_profilesCache.profiles) return;
  const p = _profilesCache.profiles.find(x => x.name === name);
  if (!p) return;
  document.querySelectorAll('.profile-card').forEach(e => e.classList.remove('active'));
  const target = el || document.querySelector(`.profile-card[data-name="${CSS.escape(name)}"]`);
  if (target) target.classList.add('active');
  _profilePreFormDetail = null;
  _renderProfileDetail(p, _profilesCache.active);
  _closeMobileSidebarAfterPanelSelection();
}

function _clearProfileDetail(){
  _currentProfileDetail = null;
  _profileMode = 'empty';
  const title = $('profileDetailTitle');
  const body = $('profileDetailBody');
  const empty = $('profileDetailEmpty');
  if (title) title.textContent = '';
  if (body) { body.innerHTML = ''; body.style.display = 'none'; }
  if (empty) empty.style.display = '';
  _setProfileHeaderButtons('empty');
}

async function activateCurrentProfile(){
  if (!_currentProfileDetail) return;
  await switchToProfile(_currentProfileDetail.name);
}

async function deleteCurrentProfile(){
  if (!_currentProfileDetail) return;
  const name = _currentProfileDetail.name;
  const _ok = await showConfirmDialog({title:t('profile_delete_confirm_title',name),message:t('profile_delete_confirm_message'),confirmLabel:t('delete_title'),danger:true,focusCancel:true});
  if(!_ok) return;
  try {
    await api('/api/profile/delete', { method: 'POST', body: JSON.stringify({ name }) });
    _invalidateKanbanProfileCache();
    _clearProfileDetail();
    await loadProfilesPanel();
    showToast(t('profile_deleted', name));
  } catch (e) { showToast(t('delete_failed') + e.message); }
}

function renderProfileDropdown(data) {
  data = data || {};
  const dd = $('profileDropdown');
  if (!dd) return;
  dd.innerHTML = '';
  const allProfiles = (Array.isArray(data.profiles) ? data.profiles : []).filter(p => p && typeof p.name === 'string');
  const active = (S.activeProfile && allProfiles.some(p => p.name === S.activeProfile))
    ? S.activeProfile
    : (data.active || 'default');
  const profiles = allProfiles.filter(p => p && (p.visible !== false || p.name === active));
  for (const p of profiles) {
    const opt = document.createElement('div');
    opt.className = 'profile-opt' + (p.name === active ? ' active' : '');
    const meta = [];
    if (typeof p.model === 'string' && p.model) meta.push(p.model.split('/').pop());
    if (p.total_skills && p.total_skills > 0) meta.push(t('profile_skill_count', p.total_skills).replace(String(p.total_skills), `${p.enabled_skills} / ${p.total_skills}`));
    const gwDot = `<span class="profile-opt-badge ${p.gateway_running ? 'running' : 'stopped'}"></span>`;
    const checkmark = p.name === active ? ' <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--link)" stroke-width="3" style="vertical-align:-1px"><polyline points="20 6 9 17 4 12"/></svg>' : '';
    const defaultBadge = p.is_default ? ` <span style="opacity:.5;font-weight:400">${esc(t('profile_default_label'))}</span>` : '';
    opt.innerHTML = `<div class="profile-opt-name">${gwDot}${esc(p.name)}${defaultBadge}${checkmark}</div>` +
      (meta.length ? `<div class="profile-opt-meta">${esc(meta.join(' \u00b7 '))}</div>` : '');
    opt.onclick = async () => {
      closeProfileDropdown();
      if (p.name === active) return;
      await switchToProfile(p.name);
    };
    dd.appendChild(opt);
  }
  // Divider + Manage link (hidden in single profile mode)
  if (!data.single_profile_mode) {
    const div = document.createElement('div'); div.className = 'ws-divider'; dd.appendChild(div);
    const mgmt = document.createElement('div'); mgmt.className = 'profile-opt ws-manage';
    mgmt.innerHTML = `${li('settings',12)} ${esc(t('manage_profiles'))}`;
    mgmt.onclick = () => { closeProfileDropdown(); mobileSwitchPanel('profiles'); };
    dd.appendChild(mgmt);
  }
  // Sync titlebar label to the resolved active profile
  const tbl = $('titlebarProfileLabel');
  if (tbl) tbl.textContent = active;
}

function toggleProfileDropdown(e) {
  const dd = $('profileDropdown');
  if (!dd) return;
  if (dd.classList.contains('open')) { closeProfileDropdown(); return; }
  closeWsDropdown(); // close workspace dropdown if open
  if(typeof closeModelDropdown==='function') closeModelDropdown();
  // Track which element triggered the dropdown for positioning
  _profileDropdownTrigger = (e && e.currentTarget) || $('profileChip');
  const openGen = ++_profileDropdownOpenGeneration;
  const cached = _profileDropdownBestCachedData();

  if(cached && !cached.single_profile_mode){
    renderProfileDropdown(cached);
    _openProfileDropdownShell();
  }else{
    _renderProfileDropdownLoading();
    _openProfileDropdownShell();
  }

  _profileDropdownFetchFresh().then(data => {
    if(openGen !== _profileDropdownOpenGeneration) return;
    // In single profile mode, don't show profile dropdown at all
    if (data.single_profile_mode) {
      closeProfileDropdown();
      return;
    }
    renderProfileDropdown(data);
    _openProfileDropdownShell();
  }).catch(e => {
    if(openGen !== _profileDropdownOpenGeneration) return;
    if(cached && !cached.single_profile_mode){
      // Keep the cached menu open; the next click/background refresh will retry.
      return;
    }
    closeProfileDropdown();
    showToast(t('profiles_load_failed'));
  });
}

function closeProfileDropdown() {
  _profileDropdownOpenGeneration++;
  const dd = $('profileDropdown');
  if (dd) dd.classList.remove('open');
  const chip=$('profileChip');
  if(chip) chip.classList.remove('active');
  const tbtn=$('titlebarProfileBtn');
  if(tbtn) tbtn.classList.remove('active');
}
document.addEventListener('click', e => {
  if (!e.target.closest('#profileChipWrap') && !e.target.closest('#titlebarProfileBtn') && !e.target.closest('#profileDropdown')) closeProfileDropdown();
});
window.addEventListener('resize',()=>{
  const dd=$('profileDropdown');
  if(dd&&dd.classList.contains('open')) _positionProfileDropdown();
});

function _openProfileSwitchSessionBrowser(){
  try{
    const isDesktop = (typeof _isDesktopWidth === 'function') ? _isDesktopWidth() : true;
    if(isDesktop){
      if(typeof expandSidebar === 'function') expandSidebar();
      return;
    }
    const sidebar=document.querySelector('.sidebar');
    if(!sidebar)return;
    try{if(typeof _syncMobileSidebarPanelFromMainView==='function')_syncMobileSidebarPanelFromMainView();}catch(_){}
    sidebar.classList.remove('mobile-session-page');
    sidebar.classList.add('mobile-panel-drawer','mobile-open');
  }catch(_){}
}

async function switchToProfile(name) {
  // ── #4671 profile-switch loading-skeleton — FOUR-GUARD CONTRACT ───────────────
  // The skeleton must never be clobbered by the OLD profile's content and must never
  // strand. Four interacting pieces of state cooperate; an edit touching one without
  // the others can silently reopen a clobber/strand window, so keep them in sync:
  //   1. _profileSwitchListEmbargo (sessions.js) — set BEFORE the skeleton, drops EVERY
  //      session-list payload (success + fetch-failure) during the switch window; lifted
  //      immediately before the switch-owned renderSessionList(), on failure-restore, and
  //      in the _switchGen-guarded finally. Closes the "render that STARTS mid-switch,
  //      before the new-profile cookie is set, fetched the old profile" window.
  //   2. _invalidateSessionListRenders() (sessions.js) — bumps _renderSessionListGen +
  //      clears pending/queued at switch start; discards renders already in flight/queued.
  //   3. _sessionListSkeletonActive (sessions.js) — renderSessionListFromCache() bails
  //      while true; cleared ONLY on fresh data (_applySessionListPayload), fetch-error,
  //      and failure-restore — so a bail can't strand the skeleton.
  //   4. _wsTreeGen (workspace.js) — bumped UNCONDITIONALLY here (incl. panel-closed, since
  //      loadDir('.') still runs); loadDir rejects stale /api/list whose gen is superseded.
  //   Plus _profileSwitchGeneration / _switchGen — guards superseded switches so a slower
  //   earlier switch can't clobber a newer one's skeleton/embargo.
  // ──────────────────────────────────────────────────────────────────────────────
  // No-op self-switch guard: bail before showing any loading skeleton if we're
  // already on this profile, so paths like activateCurrentProfile() (which
  // doesn't pre-check) can't flash a skeleton→restore for a click that changes
  // nothing. (#4662 Opus gate)
  if (name && name === S.activeProfile) return true;
  S._pendingSessionToolsets=null;
  // Profile switches are per-client cookie/TLS scoped, so a running stream in
  // the current session can safely continue while this tab moves to another
  // profile. The in-flight session stays attached to its original profile.

  // ── Loading indicator ───────────────────────────────────────────────────
  // Show spinner on the profile chip immediately so the user gets visual
  // feedback while the async switch is in progress.
  const _chip = $('profileChip');
  const _chipLabel = $('profileChipLabel');
  const _titlebarBtn = $('titlebarProfileBtn');
  const _titlebarLabel = $('titlebarProfileLabel');
  const _prevProfileName = S.activeProfile || 'default';
  const _switchGen = ++_profileSwitchGeneration;
  const _openingExistingSidebarSession = !!(typeof _profileSwitchOpeningExistingSession !== 'undefined' && _profileSwitchOpeningExistingSession);
  if (_chip) { _chip.classList.add('switching'); _chip.disabled = true; }
  if (_titlebarBtn) { _titlebarBtn.classList.add('switching'); _titlebarBtn.disabled = true; }
  // Optimistic name update — shows the target name right away
  if (_chipLabel) _chipLabel.textContent = name;
  if (_titlebarLabel) _titlebarLabel.textContent = name;

  // ── Clear stale content + show loading skeletons immediately (#4662) ───────
  // The conversation list and workspace tree still show the PREVIOUS profile's
  // content until their fetches resolve (~1s). Replace them with skeletons the
  // instant the switch begins so the user never stares at the wrong profile's
  // data, and gets consistent loading feedback across the whole surface — not
  // just the spinning chip. The real renders below overwrite these.
  //
  // First dismiss any open inline-rename or row action menu: renderSessionList
  // FromCache() early-returns (no DOM swap) while _renamingSid or
  // _sessionActionMenu is set, which would otherwise strand the skeleton AND
  // defeat the failure-path restore (#4662 Opus gate). A profile switch is a
  // context change where dismissing those transient affordances is correct.
  if (typeof _renamingSid !== 'undefined' && _renamingSid) _renamingSid = null;
  if (typeof closeSessionActionMenu === 'function') closeSessionActionMenu();
  // Determine whether the current session must be replaced instead of being
  // retagged in place. A session with messages/active runtime belongs to the
  // current profile. After the profile-switch POST returns, we also treat an
  // otherwise-empty session whose recorded profile does not match the target
  // profile as replace-only: uploads send S.session.session_id and the backend
  // correctly rejects old-profile sessions under the new profile cookie.
  let sessionInProgress = !!(S.session && (
    (S.messages && S.messages.length > 0) ||
    S.session.active_stream_id ||
    S.session.pending_user_message
  ));
  if (_openingExistingSidebarSession && S.session) {
    // A cross-profile sidebar click is about to load a concrete existing session.
    // Do not create or retag a blank intermediary session in the destination profile.
    sessionInProgress = true;
  }
  const _workspaceVisibleAtStart = typeof _workspacePanelMode !== 'undefined' && _workspacePanelMode !== 'closed';

  // #4671 CORE: the skeleton/embargo/generation setup is INSIDE the try so the
  // _switchGen-guarded finally always lifts the embargo — a throw in this synchronous
  // setup can't leak the embargo and freeze the sidebar (Codex re-gate 4).
  try {
    // Invalidate any in-flight/queued session-list render BEFORE showing the skeleton,
    // so a pre-switch /api/sessions response (old profile's rows, issued before the
    // switch) can't resolve, pass the generation guard, clear the skeleton flag, and
    // paint stale rows. Must precede showSessionListSkeleton().
    if (typeof _invalidateSessionListRenders === 'function') _invalidateSessionListRenders();
    // ...and set the embargo so a render that STARTS during the switch window (after the
    // skeleton, before the new-profile cookie is set) also can't paint the old profile's
    // rows. Cleared right before the switch-owned renderSessionList() and on failure.
    if (typeof _setProfileSwitchListEmbargo === 'function') _setProfileSwitchListEmbargo(true);
    if (typeof showSessionListSkeleton === 'function') showSessionListSkeleton(name);
    // invalidate any in-flight workspace-tree load UNCONDITIONALLY at switch start — even
    // when the panel is closed, loadDir('.') still runs later, and an empty-session switch
    // reuses the same session_id so loadDir's id guard alone can't reject a stale
    // previous-workspace /api/list. Bump here (not only inside the panel-gated
    // showWorkspaceTreeSkeleton) to close the closed-panel race.
    if (typeof bumpWorkspaceTreeGen === 'function') bumpWorkspaceTreeGen();
    if (_workspaceVisibleAtStart && typeof showWorkspaceTreeSkeleton === 'function') showWorkspaceTreeSkeleton();
    // timeoutToast:false — suppress api()'s generic "Request timed out" toast so a
    // superseded or transient-but-eventually-successful switch can't pop a spurious
    // red error while the real switch completes and renders. The catch block below is
    // the single source of truth for switch failure and is gated on _switchGen, so the
    // error surfaces ONLY when the CURRENT switch genuinely fails (@rodboev review, #4662).
    const data = await api('/api/profile/switch', { method: 'POST', body: JSON.stringify({ name }), timeoutToast: false });
    if (_switchGen !== _profileSwitchGeneration) return false;
    S.activeProfile = data.active || name;
    S.activeProfileIsDefault = !!data.is_default;
    if (typeof _resetCronUnreadForProfileSwitch === 'function') {
      _resetCronUnreadForProfileSwitch();
    }
    const targetActiveProfile = S.activeProfile || 'default';
    let sessionProfileMatchesTarget = true;
    if (!sessionInProgress && S.session) {
      const currentSessionProfile = (typeof S.session.profile === 'string' && S.session.profile.trim())
        ? S.session.profile.trim()
        : 'default';
      sessionProfileMatchesTarget = (typeof _profileMatchesActiveProfile === 'function')
        ? _profileMatchesActiveProfile(currentSessionProfile, targetActiveProfile)
        : (currentSessionProfile === targetActiveProfile || (currentSessionProfile === 'default' && !!S.activeProfileIsDefault));
      if (!sessionProfileMatchesTarget) {
        sessionInProgress = true;
      }
    }
    // Reconnect the gateway SSE to the NEW profile's watcher. The backend watcher
    // registry is now profile-keyed (#3629), but this tab's existing EventSource is
    // still subscribed to the PREVIOUS profile's watcher — and the probe-based
    // reattach is gated on `!_gatewaySSE`, which can't fire while the old stream is
    // open. startGatewaySSE() closes the old ES (stopGatewaySSE) and reconnects with
    // the new profile cookie; it self-gates on window._showCliSessions internally.
    if (typeof startGatewaySSE === 'function') startGatewaySSE();

    // Update composer placeholder and title bar while the core profile-switch
    // state is still close to the profile API response.
    if (typeof applyBotName === 'function') applyBotName();

    // ── Model + Workspace ──────────────────────────────────────────────────
    // Apply the profile defaults returned by /api/profile/switch immediately.
    // Refreshing the full model/workspace catalogs is useful, but it should not
    // hold the visible switch animation open.
    if(typeof _clearPersistedModelState==='function') _clearPersistedModelState();
    else localStorage.removeItem('agy-webui-model');
    _skillsData = null;
    _workspaceList = null;
    if (data.default_model) window._defaultModel = data.default_model;
    if (data.default_model_provider) window._activeProvider = data.default_model_provider;

    // ── Apply model ────────────────────────────────────────────────────────
    if (data.default_model) {
      const sel = $('modelSelect');
      const providerId = data.default_model_provider || window._activeProvider || null;
      const existingDefaultOpt = sel ? Array.from(sel.options).find(o => o.value === data.default_model) : null;
      if (existingDefaultOpt && providerId && !existingDefaultOpt.dataset.provider) {
        existingDefaultOpt.dataset.provider = providerId;
      }
      if (sel && !existingDefaultOpt) {
        const opt = document.createElement('option');
        opt.value = data.default_model;
        opt.textContent = typeof getModelLabel === 'function' ? getModelLabel(data.default_model) : data.default_model;
        opt.dataset.custom = '1';
        if (providerId) opt.dataset.provider = providerId;
        sel.querySelectorAll('option[data-custom]').forEach(o => o.remove());
        sel.appendChild(opt);
      }
      const resolved = _applyModelToDropdown(data.default_model, sel, providerId);
      const modelToUse = resolved || data.default_model;
      const modelState = (typeof _modelStateForSelect==='function')
        ? _modelStateForSelect(sel, modelToUse)
        : {model:modelToUse,model_provider:providerId};
      S._pendingProfileModel = modelToUse;
      S._pendingProfileModelProvider = modelState.model_provider||providerId||null;
      // Only patch the in-memory session model if we're NOT about to replace the session
      if (S.session && !sessionInProgress) {
        S.session.model = modelToUse;
        S.session.model_provider = modelState.model_provider||providerId||null;
        S.session.profile = data.active || name;
      }
    }
    // #3331 follow-up (Codex gate): retag the in-memory session's profile on
    // ANY profile switch, even when the switched-to profile returns no
    // default_model (empty session / model-less profile). Without this the
    // profile chip + project-picker filter keep the stale profile after a
    // switch to a model-less profile. Guarded by !sessionInProgress like the
    // model patch above (don't touch a session about to be replaced).
    if (S.session && !sessionInProgress) {
      S.session.profile = data.active || name;
    }
    if (typeof refreshProfileTransitionReasoningChip === 'function') {
      refreshProfileTransitionReasoningChip(data.default_model, data.default_model_provider);
    }

    // ── Apply workspace ────────────────────────────────────────────────────
    if (data.default_workspace) {
      // Always store the persistent profile default — used for blank-page display
      // and workspace auto-bind throughout the session lifecycle (#804, #823).
      S._profileDefaultWorkspace = data.default_workspace;
      // Also set the one-shot flag consumed by newSession() so the first new
      // session after a profile switch inherits this workspace (#424).
      S._profileSwitchWorkspace = data.default_workspace;

      if (S.session && !sessionInProgress) {
        // Empty session (no messages yet) — safe to update it in place
        try {
          await api('/api/session/update', { method: 'POST', body: JSON.stringify({
            session_id: S.session.session_id,
            workspace: data.default_workspace,
            model: S.session.model,
            model_provider: S.session.model_provider||null,
          })});
          S.session.workspace = data.default_workspace;
        } catch (_) {}
      }
    }

    // ── Session ────────────────────────────────────────────────────────────
    // Keep the all-profiles sidebar scope sticky across profile switches. It is
    // a navigation preference shared by the browser session, not a per-profile flag.
    if (typeof animateNextSessionListRefresh === 'function') animateNextSessionListRefresh();

    if (sessionInProgress && _openingExistingSidebarSession) {
      // The caller will immediately load the clicked session after this profile
      // cookie switch. Avoid creating/retagging an intermediate blank chat.
      const workspaceVisible = typeof _workspacePanelMode !== 'undefined' && _workspacePanelMode !== 'closed';
      if (typeof _setProfileSwitchListEmbargo === 'function') _setProfileSwitchListEmbargo(false);
      await renderSessionList();
      if (_switchGen !== _profileSwitchGeneration) return false;
      if (workspaceVisible && typeof clearWorkspaceTreeSkeleton === 'function') clearWorkspaceTreeSkeleton();
      showToast(t('profile_switched', name));
    } else if (sessionInProgress) {
      // The current session has messages and belongs to the previous profile.
      // Start a new session for the new profile so nothing gets cross-tagged.
      const workspaceVisible = typeof _workspacePanelMode !== 'undefined' && _workspacePanelMode !== 'closed';
      await newSession(false, {awaitWorkspaceLoad: workspaceVisible, worktree: false});
      if (_switchGen !== _profileSwitchGeneration) return false;
      // Keep topbar chips (workspace/profile) in sync after creating the
      // new profile-scoped session.
      syncTopbar();
      // #4671: lift the embargo immediately before the switch-owned render — JS is
      // single-threaded so nothing interleaves between this clear and the call, making
      // this render the first allowed to paint the new profile's rows.
      if (typeof _setProfileSwitchListEmbargo === 'function') _setProfileSwitchListEmbargo(false);
      await renderSessionList();
      // Re-check generation after the awaited list render: a newer switch can be
      // started while renderSessionList() is in flight, and without this guard
      // the superseded switch would clear the newer switch's workspace skeleton
      // and pop a stale toast. Mirrors the no-messages branch guard below.
      // (@rodboev/greptile review, #4662)
      if (_switchGen !== _profileSwitchGeneration) return false;
      if (typeof _openProfileSwitchSessionBrowser === 'function') _openProfileSwitchSessionBrowser();
      // Safety net: if the new session has no workspace, newSession() won't have
      // painted the file tree — clear the up-front skeleton so it can't strand
      // (#4662 Opus gate). No-op when a real tree already rendered.
      if ((!S.session || !S.session.workspace) && typeof clearWorkspaceTreeSkeleton === 'function') {
        clearWorkspaceTreeSkeleton();
      }
      showToast(t('profile_switched_new_conversation', name));
    } else {
      // No messages yet — refresh the list and topbar in place, then the
      // workspace tree. The loading skeletons shown up front (top of this
      // function) already give immediate cross-surface feedback, so we keep the
      // workspace refresh AFTER the stale-switch guard: loadDir() paints the
      // file tree as soon as its fetch resolves with only a session-id check,
      // and empty-session switches reuse the same session id — so starting it
      // before the guard could let an older switch's /api/list paint over a
      // newer one (Codex gate #4662). renderSessionList() is the slow fetch and
      // has its own internal generation guard, so awaiting it first is fine.
      const workspaceVisible = typeof _workspacePanelMode !== 'undefined' && _workspacePanelMode !== 'closed';
      // #4671: lift the embargo immediately before the switch-owned render (see above).
      if (typeof _setProfileSwitchListEmbargo === 'function') _setProfileSwitchListEmbargo(false);
      await renderSessionList();
      if (_switchGen !== _profileSwitchGeneration) return;
      if (typeof _openProfileSwitchSessionBrowser === 'function') _openProfileSwitchSessionBrowser();
      syncTopbar();
      // Refresh workspace file tree so the right panel shows the new
      // profile's workspace, not the previous one (#1214).
      if (S.session && S.session.workspace) {
        const dirLoad = loadDir('.');
        if (workspaceVisible) await dirLoad;
      } else if (typeof clearWorkspaceTreeSkeleton === 'function') {
        // New profile has no bound workspace — clear the up-front skeleton so it
        // doesn't strand (#4662 Opus gate).
        clearWorkspaceTreeSkeleton();
      }
      showToast(t('profile_switched', name));
    }

    await _profileSwitchPanelLoad();
    _refreshProfileSwitchBackground(_switchGen);
    return true;

  } catch (e) {
    // Revert the optimistic name update on error
    if (_switchGen === _profileSwitchGeneration && _chipLabel) _chipLabel.textContent = _prevProfileName;
    if (_switchGen === _profileSwitchGeneration && _titlebarLabel) _titlebarLabel.textContent = _prevProfileName;
    if (_switchGen === _profileSwitchGeneration) showToast(t('switch_failed') + e.message);
    // The switch failed, so we're still on the previous profile and its caches
    // are intact — restore the real list/tree so the loading skeletons we showed
    // up front don't strand. (#4662)
    if (_switchGen === _profileSwitchGeneration) {
      // The switch failed; _allSessions still holds the (still-current) previous
      // profile, so clear the skeleton flag and re-render to restore the real list
      // rather than strand the up-front skeleton (#4671). Lift the embargo too so the
      // restore render (and subsequent normal renders) can paint.
      if (typeof _setProfileSwitchListEmbargo === 'function') _setProfileSwitchListEmbargo(false);
      _sessionListSkeletonActive = false;
      if (typeof renderSessionListFromCache === 'function') renderSessionListFromCache();
      if (_workspaceVisibleAtStart && S.session && S.session.workspace && typeof loadDir === 'function') {
        loadDir('.');
      } else if (_workspaceVisibleAtStart && typeof clearWorkspaceTreeSkeleton === 'function') {
        // No workspace to restore on the (still-current) previous profile —
        // clear the up-front workspace skeleton so it doesn't strand on a switch
        // failure, mirroring the success-path no-workspace handling (#4662).
        clearWorkspaceTreeSkeleton();
      }
    }
    return false;
  } finally {
    // Always remove loading indicator regardless of success or failure
    if (_switchGen === _profileSwitchGeneration && _chip) { _chip.classList.remove('switching'); _chip.disabled = false; }
    if (_switchGen === _profileSwitchGeneration && _titlebarBtn) { _titlebarBtn.classList.remove('switching'); _titlebarBtn.disabled = false; }
    // #4671 safety net: guarantee the session-list embargo is lifted on EVERY exit of the
    // current switch (success paths clear it before their authoritative render; this covers
    // early-returns/throws between skeleton-show and those clears so it can't freeze the
    // sidebar). Guarded by _switchGen so a superseded switch can't lift a newer switch's embargo.
    if (_switchGen === _profileSwitchGeneration && typeof _setProfileSwitchListEmbargo === 'function') {
      _setProfileSwitchListEmbargo(false);
    }
  }
}

function openProfileCreate(){
  if (typeof switchPanel === 'function' && _currentPanel !== 'profiles') switchPanel('profiles');
  _profilePreFormDetail = _currentProfileDetail ? { ..._currentProfileDetail } : null;
  _profileMode = 'create';
  _renderProfileForm();
  // Mobile: the new-profile form lives in the main view, which is covered by the
  // full-screen sidebar drawer. Close the drawer so the form is visible (mirror
  // openWorkspaceDetail's behaviour); no-op on desktop.
  _closeMobileSidebarAfterPanelSelection();
}

function _renderProfileForm(){
  const title = $('profileDetailTitle');
  const body = $('profileDetailBody');
  const empty = $('profileDetailEmpty');
  if (!title || !body) return;
  title.textContent = t('new_profile');
  body.innerHTML = `
    <div class="main-view-content">
      <form class="detail-form" onsubmit="event.preventDefault(); saveProfileForm();">
        <div class="detail-form-row">
          <label for="profileFormName">${esc(t('profile_name_label') || 'Name')}</label>
          <input type="text" id="profileFormName" placeholder="${esc(t('profile_name_placeholder') || 'lowercase, a-z 0-9 hyphens')}" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false" required>
          <div class="detail-form-hint">${esc(t('profile_name_rule') || 'Lowercase letters, numbers, hyphens, underscores only.')}</div>
        </div>
        <div class="detail-form-row">
          <label class="detail-form-check" for="profileFormClone">
            <input type="checkbox" id="profileFormClone"> <span>${esc(t('profile_clone_label') || 'Clone config from active profile')}</span>
          </label>
        </div>
        <div class="detail-form-row">
          <label for="profileFormModel">${esc(t('profile_model_label') || 'Model / provider')}</label>
          <select id="profileFormModel"></select>
          <div class="detail-form-hint">${esc(t('profile_model_hint') || 'Choose from configured providers and models for this new profile.')}</div>
        </div>
        <div class="detail-form-row">
          <label for="profileFormBaseUrl">${esc(t('profile_base_url_label') || 'Base URL')}</label>
          <input type="text" id="profileFormBaseUrl" placeholder="${esc(t('profile_base_url_placeholder') || 'Optional, e.g. http://localhost:11434')}" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false">
        </div>
        <div class="detail-form-row">
          <label for="profileFormApiKey">${esc(t('profile_api_key_label') || 'API key')}</label>
          <input type="password" id="profileFormApiKey" placeholder="${esc(t('profile_api_key_placeholder') || 'Optional')}" autocomplete="off">
        </div>
        <div id="profileFormError" class="detail-form-error" style="display:none"></div>
      </form>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _setProfileHeaderButtons('create');
  const n = $('profileFormName');
  if (n) n.focus();
  _populateProfileFormModelSelect();
}

async function _populateProfileFormModelSelect(){
  const sel = $('profileFormModel');
  if (!sel) return;
  sel.innerHTML = `<option value="">${esc(t('profile_model_use_default') || 'Use active profile default')}</option>`;
  try {
    const data = await api('/api/models');
    const groups = (Array.isArray(data && data.groups) && data.groups.length) ? data.groups : [];
    for (const g of groups) {
      const og = document.createElement('optgroup');
      og.label = g.provider || g.provider_id || 'Configured';
      if (g.provider_id) og.dataset.provider = g.provider_id;
      for (const m of [...(Array.isArray(g.models) ? g.models : []), ...(Array.isArray(g.extra_models) ? g.extra_models : [])]) {
        if (!m || !m.id) continue;
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.label || m.id;
        og.appendChild(opt);
      }
      if (og.children.length) sel.appendChild(og);
    }
    if (data && data.default_model && typeof _applyModelToDropdown === 'function') {
      _applyModelToDropdown(data.default_model, sel, data.active_provider || window._activeProvider || null);
    }
  } catch (e) {
    console.warn('Failed to load profile model picker:', e.message);
  }
}

function cancelProfileForm(){
  if (_profilePreFormDetail) {
    const snap = _profilePreFormDetail;
    _profilePreFormDetail = null;
    const activeName = _profilesCache ? _profilesCache.active : null;
    _renderProfileDetail(snap, activeName);
    return;
  }
  _clearProfileDetail();
}

async function saveProfileForm(){
  const nameEl = $('profileFormName');
  const cloneEl = $('profileFormClone');
  const modelEl = $('profileFormModel');
  const baseEl = $('profileFormBaseUrl');
  const apiKeyEl = $('profileFormApiKey');
  const errEl = $('profileFormError');
  if (!nameEl || !errEl) return;
  const name = (nameEl.value || '').trim().toLowerCase();
  const cloneConfig = !!(cloneEl && cloneEl.checked);
  errEl.style.display = 'none';
  if (!name) { errEl.textContent = t('name_required'); errEl.style.display = ''; return; }
  if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(name)) { errEl.textContent = t('profile_name_rule'); errEl.style.display = ''; return; }
  const baseUrl = (baseEl ? (baseEl.value || '') : '').trim();
  const apiKey = (apiKeyEl ? (apiKeyEl.value || '') : '').trim();
  if (baseUrl && !/^https?:\/\//.test(baseUrl)) { errEl.textContent = t('profile_base_url_rule'); errEl.style.display = ''; return; }
  try {
    const payload = { name, clone_config: cloneConfig };
    const selectedModel = modelEl ? (modelEl.value || '').trim() : '';
    if (selectedModel) {
      const modelState = (typeof _modelStateForSelect === 'function')
        ? _modelStateForSelect(modelEl, selectedModel)
        : { model: selectedModel, model_provider: null };
      if (modelState.model) payload.default_model = modelState.model;
      if (modelState.model_provider) payload.model_provider = modelState.model_provider;
    }
    if (baseUrl) payload.base_url = baseUrl;
    if (apiKey) payload.api_key = apiKey;
    await api('/api/profile/create', { method: 'POST', body: JSON.stringify(payload) });
    _invalidateKanbanProfileCache();
    _profilePreFormDetail = null;
    await loadProfilesPanel();
    showToast(t('profile_created', name));
    openProfileDetail(name);
  } catch (e) {
    errEl.textContent = e.message || t('create_failed');
    errEl.style.display = '';
  }
}

// Back-compat
const submitProfileCreate = saveProfileForm;
function toggleProfileForm(){ openProfileCreate();
}

async function deleteProfile(name) {
  const _delProf=await showConfirmDialog({title:t('profile_delete_confirm_title',name),message:t('profile_delete_confirm_message'),confirmLabel:t('delete_title'),danger:true,focusCancel:true});
  if(!_delProf) return;
  try {
    await api('/api/profile/delete', { method: 'POST', body: JSON.stringify({ name }) });
    _invalidateKanbanProfileCache();
    await loadProfilesPanel();
    showToast(t('profile_deleted', name));
  } catch (e) { showToast(t('delete_failed') + e.message); }
}


// Drag and drop
const wrap=$('composerWrap');let dragCounter=0;
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('dragenter',e=>{e.preventDefault();
  const isWsPath=e.dataTransfer.types.includes('application/ws-path');
  const isFiles=e.dataTransfer.types.includes('Files');
  if(isFiles||isWsPath){
    dragCounter++;
    // Context-aware hint: a workspace-file drag inserts an @path reference;
    // an OS-file drag attaches the file to the message.
    const hint=$('dropHintText');
    if(hint) hint.textContent=isWsPath?'Drop to insert workspace reference':'Drop files to attach';
    wrap.classList.add('drag-over');
  }
});
document.addEventListener('dragleave',e=>{dragCounter--;if(dragCounter<=0){dragCounter=0;wrap.classList.remove('drag-over');}});
document.addEventListener('drop',e=>{
  e.preventDefault();dragCounter=0;wrap.classList.remove('drag-over');
  // Workspace file/folder drag → insert @path reference into composer
  const wsPath=e.dataTransfer.getData('application/ws-path');
  if(wsPath){
    const msgEl=$('msg');
    if(msgEl){
      const start=msgEl.selectionStart;const end=msgEl.selectionEnd;
      const val=msgEl.value;
      const prefix=start>0&&!val[start-1].match(/\s/)?' ':'';
      const insert=prefix+'@'+wsPath+' ';
      msgEl.value=val.slice(0,start)+insert+val.slice(end);
      msgEl.selectionStart=msgEl.selectionEnd=start+insert.length;
      msgEl.focus();
    }
    return;
  }
  // OS file drag → attach files
  const files=Array.from(e.dataTransfer.files);
  if(files.length){addFiles(files);$('msg').focus();}
});

// ── Settings panel ───────────────────────────────────────────────────────────

let _settingsDirty = false;
let _settingsThemeOnOpen = null; // track theme at open time for discard revert
let _settingsSkinOnOpen = null; // track skin at open time for discard revert
let _settingsFontSizeOnOpen = null; // track font size at open time for discard revert
let _settingsAgyDefaultModelOnOpen = '';
let _settingsAgyDefaultModelProviderOnOpen = null;
let _settingsSection = 'conversation';
let _currentSettingsSection = 'conversation';
let _settingsIndex = null;
let _settingsIndexPromise = null;
let _settingsSearchSeq = 0;
let _extensionsStatusData = null;
let _extensionsSidecarMonitorSeq = 0;
let _extensionsGalleryData = null;
let _extensionsGalleryLoaded = false;
let _extensionsActiveTab = 'gallery';
let _settingsSearchDismissListenerRegistered = false;
let _settingsAppearanceAutosaveTimer = null;
let _settingsAppearanceAutosaveRetryPayload = null;
let _settingsPreferencesAutosaveTimer = null;
let _settingsPreferencesAutosaveRetryPayload = null;

// ── Sidebar tab visibility/order ────────────────────────────────────────────
const _ALWAYS_VISIBLE_TABS = new Set(['chat','settings']);
const _HIDDEN_TABS_LS_KEY = 'agy-webui-hidden-tabs';
const _TAB_ORDER_LS_KEY = 'agy-webui-tab-order';
const _COMPOSER_CONTROL_ORDER_LS_KEY = 'agy-webui-composer-control-order';
let _tabVisibilityDragSuppressUntil = 0;
let _composerControlDragSuppressUntil = 0;
let _composerControlDraggingKey = '';

function _sanitizeTabPanelList(panels){
  if(!Array.isArray(panels)) return [];
  var out=[];
  panels.forEach(function(panel){
    if(typeof panel!=='string') return;
    panel=panel.trim();
    if(!panel||_ALWAYS_VISIBLE_TABS.has(panel)) return;
    if(out.indexOf(panel)===-1) out.push(panel);
  });
  return out;
}

function _getHiddenTabs(){
  try{var h=localStorage.getItem(_HIDDEN_TABS_LS_KEY);if(h)return _sanitizeTabPanelList(JSON.parse(h));}catch(e){}
  return[];
}

function _setHiddenTabs(panels){
  try{localStorage.setItem(_HIDDEN_TABS_LS_KEY,JSON.stringify(_sanitizeTabPanelList(panels)));}catch(e){}
}

function _getTabOrder(){
  try{var h=localStorage.getItem(_TAB_ORDER_LS_KEY);if(h)return _sanitizeTabPanelList(JSON.parse(h));}catch(e){}
  return[];
}

function _setTabOrder(panels){
  try{localStorage.setItem(_TAB_ORDER_LS_KEY,JSON.stringify(_sanitizeTabPanelList(panels)));}catch(e){}
}

function _availableSidebarPanels(){
  var out=[];
  var tabs=document.querySelectorAll('.rail .rail-btn.nav-tab[data-panel], .sidebar-nav .nav-tab[data-panel]');
  tabs.forEach(function(tab){
    var panel=tab.dataset.panel;
    if(!panel||_ALWAYS_VISIBLE_TABS.has(panel)) return;
    if(tab.classList.contains('dashboard-link')||tab.hasAttribute('data-dashboard-link')) return;
    if(out.indexOf(panel)===-1) out.push(panel);
  });
  return out;
}

function _orderedSidebarPanels(order){
  var available=_availableSidebarPanels();
  var requested=_sanitizeTabPanelList(Array.isArray(order)?order:_getTabOrder());
  var out=[];
  requested.forEach(function(panel){ if(available.indexOf(panel)!==-1&&out.indexOf(panel)===-1) out.push(panel); });
  available.forEach(function(panel){ if(out.indexOf(panel)===-1) out.push(panel); });
  return out;
}

function _dashboardPanelMode(){
  var modeEl=$('settingsDashboardMode');
  var mode=modeEl&&modeEl.value;
  return mode==='never'||mode==='always'||mode==='auto'?mode:'auto';
}

function _isDashboardChipOn(){
  return _dashboardPanelMode()!=='never';
}

function _renderDashboardVisibilityChip(container){
  if(!container)return null;
  var chip=document.createElement('button');
  chip.type='button';
  chip.className='tab-visibility-chip';
  chip.setAttribute('data-tab-panel','__agy_dashboard__');
  chip.setAttribute('role','switch');
  var isOn=_isDashboardChipOn();
  chip.setAttribute('aria-checked',isOn?'true':'false');
  if(!isOn) chip.classList.add('chip-off');
  chip.textContent=typeof t==='function'?t('tab_dashboard'):'Dashboard';
  chip.onclick=function(){
    if(Date.now()<_tabVisibilityDragSuppressUntil)return;
    _toggleDashboardVisibilityChip();
  };
  return chip;
}

function _applyTabOrder(order){
  var ordered=_orderedSidebarPanels(order);
  ['.rail','.sidebar-nav'].forEach(function(selector){
    var container=document.querySelector(selector);
    if(!container) return;
    var anchor=Array.prototype.find.call(container.children,function(child){
      if(child.classList&&child.classList.contains('rail-spacer')) return true;
      if(child.classList&&child.classList.contains('dashboard-link')) return true;
      if(child.hasAttribute&&child.hasAttribute('data-dashboard-link')) return true;
      return child.dataset&&child.dataset.panel==='settings';
    });
    ordered.forEach(function(panel){
      var node=container.querySelector('.nav-tab[data-panel="'+panel+'"]');
      if(node) container.insertBefore(node,anchor||null);
    });
  });
}

function _applyTabVisibility(hidden){
  hidden=_sanitizeTabPanelList(hidden);
  _applyTabOrder(_getTabOrder());
  // Hide/unhide all [data-panel] elements (sidebar-nav buttons + rail buttons)
  document.querySelectorAll('[data-panel]').forEach(function(el){
    var panel=el.dataset.panel;
    if(!panel)return;
    var shouldHide=hidden.indexOf(panel)!==-1;
    // Never hide always-visible panels (chat, settings) even if present in hidden_tabs
    if(_ALWAYS_VISIBLE_TABS.has(panel)) shouldHide=false;
    el.classList.toggle('nav-tab-hidden',shouldHide);
  });
  // If the currently active tab is hidden, switch to chat
  var activeRail=document.querySelector('.rail .rail-btn.nav-tab.active[data-panel]');
  var activeSidebar=document.querySelector('.sidebar-nav .nav-tab.active[data-panel]');
  var activeEl=activeRail||activeSidebar;
  if(activeEl&&activeEl.classList.contains('nav-tab-hidden')){
    if(typeof switchPanel==='function') switchPanel('chat');
  }
}

function _renderTabVisibilityChips(){
  var container=$('tabVisibilityChips');
  if(!container)return;
  var hidden=_getHiddenTabs();
  var panels=_orderedSidebarPanels();
  container.innerHTML='';
  panels.forEach(function(panel){
    var tab=document.querySelector('.rail .rail-btn.nav-tab[data-panel="'+panel+'"]')
      ||document.querySelector('.sidebar-nav .nav-tab[data-panel="'+panel+'"]');
    var label=(tab&&(tab.dataset.tooltip||tab.dataset.label))||panel;
    // Capitalize first letter
    label=label.charAt(0).toUpperCase()+label.slice(1);
    var chip=document.createElement('button');
    chip.type='button';
    chip.className='tab-visibility-chip';
    var isOff=hidden.indexOf(panel)!==-1;
    if(isOff)chip.classList.add('chip-off');
    chip.textContent=label;
    chip.setAttribute('data-tab-panel',panel);
    chip.setAttribute('draggable','true');
    // Use role="switch" + aria-checked instead of aria-pressed so screen
    // readers narrate "Tasks switch on/off" (matches user mental model) rather
    // than "Tasks toggle button pressed/not-pressed" (where the polarity is
    // confusing because chip-off looks like the "off" state).
    chip.setAttribute('role','switch');
    chip.setAttribute('aria-checked',isOff?'false':'true');
    chip.onclick=function(){
      if(Date.now()<_tabVisibilityDragSuppressUntil)return;
      _toggleTabVisibilityChip(panel);
    };
    _wireTabChipDrag(chip,panel);
    container.appendChild(chip);
  });
}

function _wireTabChipDrag(chip,panel){
  if(!chip)return;
  chip.addEventListener('dragstart',function(e){
    chip.classList.add('dragging');
    if(e.dataTransfer){
      e.dataTransfer.effectAllowed='move';
      e.dataTransfer.setData('text/plain',panel);
    }
  });
  chip.addEventListener('dragend',function(){chip.classList.remove('dragging');});
  chip.addEventListener('dragover',function(e){e.preventDefault();chip.classList.add('drag-over');if(e.dataTransfer)e.dataTransfer.dropEffect='move';});
  chip.addEventListener('dragleave',function(){chip.classList.remove('drag-over');});
  chip.addEventListener('drop',function(e){_handleTabVisibilityChipDrop(e,panel);});
}

function _moveTabOrderPanel(sourcePanel,targetPanel){
  if(!sourcePanel||!targetPanel||sourcePanel===targetPanel) return false;
  var order=_orderedSidebarPanels();
  var from=order.indexOf(sourcePanel);
  var to=order.indexOf(targetPanel);
  if(from===-1||to===-1) return false;
  order.splice(from,1);
  order.splice(to,0,sourcePanel);
  _setTabOrder(order);
  _applyTabOrder(order);
  _renderTabVisibilityChips();
  _scheduleAppearanceAutosave();
  return true;
}

function _handleTabVisibilityChipDrop(e,targetPanel){
  if(e){e.preventDefault();e.stopPropagation();}
  document.querySelectorAll('.tab-visibility-chip.drag-over').forEach(function(el){el.classList.remove('drag-over');});
  var sourcePanel=e&&e.dataTransfer?e.dataTransfer.getData('text/plain'):'';
  if(_moveTabOrderPanel(sourcePanel,targetPanel)) _tabVisibilityDragSuppressUntil=Date.now()+250;
}

function _toggleTabVisibilityChip(panel){
  if(_ALWAYS_VISIBLE_TABS.has(panel))return;
  var hidden=_getHiddenTabs();
  var idx=hidden.indexOf(panel);
  if(idx!==-1){
    hidden.splice(idx,1);
  }else{
    hidden.push(panel);
  }
  _setHiddenTabs(hidden);
  _applyTabVisibility(hidden);
  _renderTabVisibilityChips();
  _scheduleAppearanceAutosave();
}

function _toggleDashboardVisibilityChip(){
  var modeEl=$('settingsDashboardMode');
  if(!modeEl||typeof saveDashboardSettings!=='function') return;
  var currentMode=_dashboardPanelMode();
  var nextMode=currentMode==='never'
    ? (typeof _getDashboardChipRestoreMode==='function' ? _getDashboardChipRestoreMode() : 'auto')
    : 'never';
  var previousMode=currentMode;
  modeEl.value=nextMode;
  Promise.resolve(saveDashboardSettings({raiseOnError:true})).catch(function(){
    modeEl.value=previousMode;
    if(typeof _renderTabVisibilityChips==='function') _renderTabVisibilityChips();
  });
}

function _ensureComposerControlVisibilityState(settings){
  const fromSettings=(typeof _composerControlVisibilityFromSettings==='function')
    ? _composerControlVisibilityFromSettings(settings||{})
    : {};
  if(!window._composerControlVisibility) window._composerControlVisibility={};
  Object.assign(window._composerControlVisibility, fromSettings);
}

function _composerControlDefsForSettings(){
  const baseDefs=Array.isArray(window._COMPOSER_CONTROL_TOGGLE_DEFS)?window._COMPOSER_CONTROL_TOGGLE_DEFS:[];
  const situationalDefs=Array.isArray(window._COMPOSER_SITUATIONAL_CONTROL_TOGGLE_DEFS)?window._COMPOSER_SITUATIONAL_CONTROL_TOGGLE_DEFS:[];
  return baseDefs.concat(situationalDefs);
}

function _getComposerControlOrder(){
  if(Array.isArray(window._composerControlOrder)){
    return typeof window._sanitizeComposerControlOrder==='function'
      ? window._sanitizeComposerControlOrder(window._composerControlOrder)
      : window._composerControlOrder.slice();
  }
  try{
    const raw=localStorage.getItem(_COMPOSER_CONTROL_ORDER_LS_KEY);
    if(raw){
      const parsed=JSON.parse(raw);
      if(typeof window._sanitizeComposerControlOrder==='function') return window._sanitizeComposerControlOrder(parsed);
      if(Array.isArray(parsed)) return parsed.filter(key=>typeof key==='string');
    }
  }catch(e){}
  return [];
}

function _setComposerControlOrder(order){
  const sanitized=typeof window._sanitizeComposerControlOrder==='function'
    ? window._sanitizeComposerControlOrder(order)
    : (Array.isArray(order)?order.filter(key=>typeof key==='string') : []);
  window._composerControlOrder=sanitized;
  try{localStorage.setItem(_COMPOSER_CONTROL_ORDER_LS_KEY,JSON.stringify(sanitized));}catch(e){}
  return sanitized;
}

function _orderedComposerControlDefsForSettings(defs){
  defs=Array.isArray(defs)?defs:[];
  const byKey=new Map(defs.map(def=>[def.key,def]));
  const out=[];
  _getComposerControlOrder().forEach(function(key){
    if(byKey.has(key)) out.push(byKey.get(key));
  });
  defs.forEach(function(def){if(out.indexOf(def)===-1) out.push(def);});
  return out;
}

function _composerControlOrderGroupKey(key){
  const def=_composerControlDefsForSettings().find(item=>item&&item.key===key);
  return def&&def.orderGroup?def.orderGroup:'';
}

function _composerControlDropAllowed(sourceKey,targetKey){
  if(!sourceKey||!targetKey||sourceKey===targetKey) return false;
  const sourceGroup=_composerControlOrderGroupKey(sourceKey);
  const targetGroup=_composerControlOrderGroupKey(targetKey);
  return !!sourceGroup&&sourceGroup===targetGroup;
}

function _clearComposerControlDragOver(){
  document.querySelectorAll('[data-composer-control-key].drag-over').forEach(function(el){el.classList.remove('drag-over');});
}

function _moveComposerControlOrderKey(sourceKey,targetKey){
  if(!_composerControlDropAllowed(sourceKey,targetKey)) return false;
  const order=_orderedComposerControlDefsForSettings(_composerControlDefsForSettings()).map(def=>def.key);
  const from=order.indexOf(sourceKey);
  const to=order.indexOf(targetKey);
  if(from===-1||to===-1) return false;
  order.splice(from,1);
  order.splice(to,0,sourceKey);
  const next=_setComposerControlOrder(order);
  if(typeof window._applyComposerControlOrder==='function') window._applyComposerControlOrder(next);
  _renderComposerControlChips();
  _renderComposerSituationalControlChips();
  _scheduleAppearanceAutosave();
  return true;
}

function _handleComposerControlChipDrop(e,targetKey){
  if(e){e.preventDefault();e.stopPropagation();}
  _clearComposerControlDragOver();
  const sourceKey=e&&e.dataTransfer?e.dataTransfer.getData('text/plain'):_composerControlDraggingKey;
  if(_moveComposerControlOrderKey(sourceKey,targetKey)) _composerControlDragSuppressUntil=Date.now()+250;
  _composerControlDraggingKey='';
}

function _wireComposerControlChipDrag(chip,key){
  if(!chip)return;
  chip.setAttribute('data-composer-control-key',key);
  chip.setAttribute('draggable','true');
  chip.addEventListener('dragstart',function(e){
    _composerControlDraggingKey=key;
    chip.classList.add('dragging');
    if(e.dataTransfer){
      e.dataTransfer.effectAllowed='move';
      e.dataTransfer.setData('text/plain',key);
    }
  });
  chip.addEventListener('dragend',function(){
    chip.classList.remove('dragging');
    _clearComposerControlDragOver();
    _composerControlDraggingKey='';
  });
  chip.addEventListener('dragover',function(e){
    const sourceKey=_composerControlDraggingKey;
    if(!_composerControlDropAllowed(sourceKey,key)){
      if(e.dataTransfer)e.dataTransfer.dropEffect='none';
      return;
    }
    e.preventDefault();
    chip.classList.add('drag-over');
    if(e.dataTransfer)e.dataTransfer.dropEffect='move';
  });
  chip.addEventListener('dragleave',function(){chip.classList.remove('drag-over');});
  chip.addEventListener('drop',function(e){_handleComposerControlChipDrop(e,key);});
}

function _composerControlVisibilityPayload(){
  const payload={};
  const defs=_composerControlDefsForSettings();
  const state=window._composerControlVisibility||{};
  defs.forEach(function(def){payload[def.key]=!!state[def.key];});
  return payload;
}

function _toggleComposerControlChip(key){
  if(!window._composerControlVisibility) window._composerControlVisibility={};
  window._composerControlVisibility[key]=!window._composerControlVisibility[key];
  if(typeof _renderComposerControlChips==='function') _renderComposerControlChips();
  if(typeof _renderComposerSituationalControlChips==='function') _renderComposerSituationalControlChips();
  if(typeof _applyComposerFooterVisibilitySettings==='function') _applyComposerFooterVisibilitySettings();
  _scheduleAppearanceAutosave();
}

function _composerControlChipLabel(def){
  if(!def) return '';
  if(def.labelKey&&typeof t==='function'){
    const localized=t(def.labelKey);
    if(typeof localized==='string'&&localized&&localized!==def.labelKey) return localized;
  }
  return def.label||'';
}

function _renderComposerControlChips(){
  const container=$('composerControlsChips');
  if(!container) return;
  const defs=Array.isArray(window._COMPOSER_CONTROL_TOGGLE_DEFS)?window._COMPOSER_CONTROL_TOGGLE_DEFS:[];
  const state=window._composerControlVisibility||{};
  container.innerHTML='';
  _orderedComposerControlDefsForSettings(defs).forEach(function(def){
    const chip=document.createElement('button');
    chip.type='button';
    chip.className='tab-visibility-chip';
    const hidden=!!state[def.key];
    if(hidden) chip.classList.add('chip-off');
    chip.textContent=_composerControlChipLabel(def);
    chip.setAttribute('role','switch');
    chip.setAttribute('aria-checked',hidden?'false':'true');
    chip.onclick=function(){if(Date.now()<_composerControlDragSuppressUntil)return;_toggleComposerControlChip(def.key);};
    _wireComposerControlChipDrag(chip,def.key);
    container.appendChild(chip);
  });
}

function _renderComposerSituationalControlChips(){
  const container=$('composerSituationalControlsChips');
  if(!container) return;
  const defs=Array.isArray(window._COMPOSER_SITUATIONAL_CONTROL_TOGGLE_DEFS)?window._COMPOSER_SITUATIONAL_CONTROL_TOGGLE_DEFS:[];
  const state=window._composerControlVisibility||{};
  container.innerHTML='';
  _orderedComposerControlDefsForSettings(defs).forEach(function(def){
    const chip=document.createElement('button');
    chip.type='button';
    chip.className='tab-visibility-chip';
    const hidden=!!state[def.key];
    if(hidden) chip.classList.add('chip-off');
    chip.textContent=_composerControlChipLabel(def);
    chip.setAttribute('role','switch');
    chip.setAttribute('aria-checked',hidden?'false':'true');
    chip.onclick=function(){if(Date.now()<_composerControlDragSuppressUntil)return;_toggleComposerControlChip(def.key);};
    _wireComposerControlChipDrag(chip,def.key);
    container.appendChild(chip);
  });
}

function switchSettingsSection(name,opts){
  // If the main content is not showing settings, just remember the section
  // without force-switching the panel. The section will be applied when the
  // user next opens settings via switchPanel(). (#appearance-auto-reopen)
  if (_currentPanel !== 'settings') {
    _currentSettingsSection = name;
    _settingsSection = name;
    return;
  }
  let section=(name==='appearance'||name==='preferences'||name==='providers'||name==='plugins'||name==='extensions'||name==='system'||name==='help')?name:'conversation';
  // Deep-linking to the Plugins pane when the tab is hidden (no plugins
  // installed, #3457) falls back to Conversation. Resolve this BEFORE toggling
  // panes/sidebar/dropdown below so every downstream selection uses the
  // corrected section — otherwise the plugins pane would still render active
  // but empty. (#3457)
  if(section==='plugins'){
    const pluginsTabBtn=document.querySelector('[data-settings-section="plugins"]');
    if(pluginsTabBtn && pluginsTabBtn.style.display==='none') section='conversation';
  }
  _settingsSection=section;
  _currentSettingsSection=section;
  const map={conversation:'Conversation',appearance:'Appearance',preferences:'Preferences',providers:'Providers',plugins:'Plugins',extensions:'Extensions',system:'System',help:'Help'};
  // Sidebar menu items
  document.querySelectorAll('#settingsMenu .side-menu-item').forEach(it=>{
    it.classList.toggle('active', it.dataset.settingsSection===section);
  });
  // Panes in main
  ['conversation','appearance','preferences','providers','plugins','extensions','system','help'].forEach(key=>{
    const pane=$('settingsPane'+map[key]);
    if(pane) pane.classList.toggle('active', key===section);
  });
  // Sync mobile dropdown
  const dd=$('settingsSectionDropdown');
  if(dd && dd.value!==section) dd.value=section;
  // Lazy-load integration panels when their tabs are opened. Search
  // navigation passes skipLazyLoad: the loaders rebuild the pane DOM from a
  // fresh fetch, which would detach the field it is about to scroll to.
  if(!(opts&&opts.skipLazyLoad)){
    if(section==='providers') loadProvidersPanel();
    if(section==='plugins') loadPluginsPanel();
    if(section==='extensions') loadExtensionsPanel();
  }
  if(opts&&opts.fromSidebarItem)_closeMobileSidebarAfterPanelSelection();
}

function _normalizeSettingsSearchText(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

function _extractSettingsDescriptionText(field, labelEl) {
  const chunks = [];
  const settingsSearch = (field.dataset && field.dataset.settingsSearch) || '';
  if (settingsSearch) chunks.push(settingsSearch);
  field.querySelectorAll('[data-i18n]').forEach(node => {
    if (labelEl && (node === labelEl || labelEl.contains(node))) return;
    const key = node.dataset ? node.dataset.i18n : null;
    if (key) chunks.push(t(key));
  });
  return chunks.join(' ');
}

function _extractSettingsValueText(field) {
  const chunks = [];
  const controls = [...field.querySelectorAll('select, input, textarea')];
  controls.forEach(control => {
    const tagName = (control.tagName || '').toLowerCase();
    if (tagName === 'select') {
      control.querySelectorAll('option').forEach(option => {
        if (option.dataset && option.dataset.i18n) {
          chunks.push(t(option.dataset.i18n));
        } else {
          chunks.push(option.textContent);
        }
      });
      return;
    }
    const type = (control.getAttribute && control.getAttribute('type')) || control.type || '';
    if (tagName === 'input' && ['checkbox', 'radio', 'file', 'submit', 'reset', 'button'].includes(type)) return;
    if (control.value) chunks.push(control.value);
  });
  return chunks.join(' ');
}

async function _buildSettingsIndex() {
  if (_settingsIndex) return;
  // Memoize the in-flight build so concurrent searches share one pass; the
  // lazy pane loaders are not guaranteed re-entrant.
  if (_settingsIndexPromise) return _settingsIndexPromise;
  const promise = (async () => {
    // Ensure lazy-loaded panes are populated before reading the DOM
    await Promise.all([loadProvidersPanel(), loadPluginsPanel(), loadExtensionsPanel()]);
    const index = [];
    const add = (entry) => {
      index.push({ ...entry, _settingsSearchIndex: index.length });
    };
    const sectionMap = {
      settingsPaneConversation: 'conversation',
      settingsPaneAppearance: 'appearance',
      settingsPanePreferences: 'preferences',
      settingsPaneProviders: 'providers',
      settingsPanePlugins: 'plugins',
      settingsPaneExtensions: 'extensions',
      settingsPaneSystem: 'system',
      settingsPaneHelp: 'help',
    };
    for (const [paneId, sectionKey] of Object.entries(sectionMap)) {
      const pane = $(paneId);
      if (!pane) continue;
      pane.querySelectorAll('.settings-field').forEach(field => {
        // The i18n key may live on the <label> itself (label[data-i18n]) OR on
        // a child of the label — the common toggle shape is
        // <label><input><span data-i18n="..."></span></label>. Match both, plus
        // a plain <label> with no i18n key, so every field is searchable
        // (previously only label[data-i18n] indexed, silently dropping most
        // checkbox settings). #4340 review fix.
        const labelEl = field.querySelector('label[data-i18n], label [data-i18n], label');
        if (!labelEl) return;
        const i18nKey = labelEl.dataset ? labelEl.dataset.i18n : undefined;
        const titleText = (i18nKey && t(i18nKey)) || labelEl.textContent.trim();
        if (!titleText) return;
        const valueText = _normalizeSettingsSearchText(_extractSettingsValueText(field));
        const descriptionText = _normalizeSettingsSearchText(_extractSettingsDescriptionText(field, labelEl));
        const searchBlob = [titleText, valueText, descriptionText, field.textContent]
          .filter(Boolean)
          .join(' ')
          .replace(/\s+/g, ' ')
          .trim();
        add({
          label: titleText,
          titleText,
          valueText,
          descriptionText,
          searchBlob,
          sectionKey,
          i18nKey,
          el: field,
        });
      });
      if (sectionKey === 'providers') {
        pane.querySelectorAll('.provider-card').forEach(card => {
          const cardName = ((card.querySelector('.provider-card-name') || {}).textContent || '').trim();
          if (cardName) {
            const titleText = cardName;
            const valueText = _normalizeSettingsSearchText(_extractSettingsValueText(card));
            const descriptionText = _normalizeSettingsSearchText(_extractSettingsDescriptionText(card));
            const searchBlob = [cardName, valueText, descriptionText, card.textContent]
              .filter(Boolean)
              .join(' ')
              .replace(/\s+/g, ' ')
              .trim();
            add({
              label: cardName,
              titleText,
              valueText,
              descriptionText,
              searchBlob,
              sectionKey,
              el: card,
              cardName,
            });
          }
          card.querySelectorAll('.provider-card-field').forEach(field => {
            const fieldLabel = ((field.querySelector('.provider-card-label') || {}).textContent || '').trim();
            const label = [cardName, fieldLabel].filter(Boolean).join(' ');
            if (!label) return;
            const valueText = _normalizeSettingsSearchText(_extractSettingsValueText(field));
            const descriptionText = _normalizeSettingsSearchText(_extractSettingsDescriptionText(field));
            const searchBlob = [label, valueText, descriptionText, field.textContent]
              .filter(Boolean)
              .join(' ')
              .replace(/\s+/g, ' ')
              .trim();
            add({
              label,
              titleText: label,
              valueText,
              descriptionText,
              searchBlob,
              sectionKey,
              el: field,
              cardName,
              fieldLabel,
            });
          });
        });
      }
      if (sectionKey === 'plugins') {
        pane.querySelectorAll('.plugin-card').forEach(card => {
          const cardName = ((card.querySelector('.provider-card-name') || {}).textContent || '').trim();
          if (!cardName) return;
          const titleText = cardName;
          const valueText = _normalizeSettingsSearchText(_extractSettingsValueText(card));
          const descriptionText = _normalizeSettingsSearchText(_extractSettingsDescriptionText(card));
          const searchBlob = [cardName, valueText, descriptionText, card.textContent]
            .filter(Boolean)
            .join(' ')
            .replace(/\s+/g, ' ')
            .trim();
          add({
            label: cardName,
            titleText,
            valueText,
            descriptionText,
            searchBlob,
            sectionKey,
            el: card,
            cardName,
          });
        });
      }
    }
    // A panel-session reset while building clears the memo; drop this result
    // instead of resurrecting a stale index for the new session.
    if (_settingsIndexPromise === promise) _settingsIndex = index;
  })().catch(e => { if (_settingsIndexPromise === promise) _settingsIndexPromise = null; throw e; });
  _settingsIndexPromise = promise;
  return promise;
}

async function filterSettings(query) {
  const resultsEl = $('settingsSearchResults');
  if (!resultsEl) return;
  const q = (query || '').trim().toLowerCase();
  if (!q) { ++_settingsSearchSeq; resultsEl.style.display = 'none'; resultsEl.innerHTML = ''; return; }
  const seq = ++_settingsSearchSeq;
  await _buildSettingsIndex();
  // A newer keystroke superseded this query while the index was building.
  if (seq !== _settingsSearchSeq) return;
  const sectionLabels = {
    conversation: t('settings_tab_conversation') || 'Conversation',
    appearance: t('settings_tab_appearance') || 'Appearance',
    preferences: t('settings_tab_preferences') || 'Preferences',
    providers: t('providers_tab_title') || 'Providers',
    plugins: t('settings_tab_plugins') || 'Plugins',
    extensions: t('settings_tab_extensions') || 'Extensions',
    system: t('settings_tab_system') || 'System',
    help: t('settings_tab_help') || 'Help',
  };
  const matches = (_settingsIndex || []).map((entry) => {
    const score = _scoreSettingsSearchMatch(entry, q);
    return score ? { entry, score, index: entry._settingsSearchIndex } : null;
  }).filter(Boolean);
  if (!matches.length) {
    resultsEl.innerHTML = `<div class="settings-search-empty">${esc(t('settings_search_no_results') || 'No settings found.')}</div>`;
    resultsEl.style.display = '';
    return;
  }
  resultsEl.innerHTML = '';
  matches.sort((left, right) => {
    if (left.score.bucketIndex !== right.score.bucketIndex) {
      return left.score.bucketIndex - right.score.bucketIndex;
    }
    if (left.score.matchIndex !== right.score.matchIndex) {
      return left.score.matchIndex - right.score.matchIndex;
    }
    return left.index - right.index;
  });
  for (const { entry: m } of matches.slice(0, 12)) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'settings-search-result';
    item.innerHTML = `<span class="settings-search-section">${esc(sectionLabels[m.sectionKey] || m.sectionKey)}</span>` +
      `<span class="settings-search-arrow">›</span>` +
      `<span class="settings-search-label">${esc(m.label)}</span>`;
    item.addEventListener('click', () => {
      _navigateToSettingsField(m);
      resultsEl.style.display = 'none';
      resultsEl.innerHTML = '';
      const input = $('settingsSearch');
      if (input) input.value = '';
    });
    resultsEl.appendChild(item);
  }
  resultsEl.style.display = '';
}

function _scoreSettingsSearchMatch(entry, q) {
  const query = (q || '').toLowerCase().trim();
  if (!query) return null;
  const buckets = [
    ['titleText', 0],
    ['valueText', 1],
    ['descriptionText', 2],
    ['searchBlob', 3],
  ];
  for (const [bucketName, bucketIndex] of buckets) {
    const hay = _normalizeSettingsSearchText(entry[bucketName]);
    if (!hay) continue;
    const matchIndex = hay.indexOf(query);
    if (matchIndex < 0) continue;
    return {
      bucketIndex,
      matchType: matchIndex === 0 ? 'prefix' : 'contains',
      matchIndex,
    };
  }
  return null;
}

function _navigateToSettingsField(entry) {
  // The panes were populated when the index was built, so skip the tab-switch
  // lazy reload: loadProvidersPanel()/loadPluginsPanel() rebuild the pane DOM
  // from a fresh fetch and would detach the node mid-scroll.
  switchSettingsSection(entry.sectionKey, { skipLazyLoad: true });
  requestAnimationFrame(() => {
    const el = _resolveSettingsField(entry);
    if (!el) return;
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    _highlightSettingsField(el);
  });
}

function _resolveSettingsField(entry) {
  // Re-resolve in the live DOM: any pane re-render since indexing (e.g. the
  // user visited the tab) replaces the node the index captured.
  const paneIds = {
    conversation: 'settingsPaneConversation',
    appearance: 'settingsPaneAppearance',
    preferences: 'settingsPanePreferences',
    providers: 'settingsPaneProviders',
    plugins: 'settingsPanePlugins',
    extensions: 'settingsPaneExtensions',
    system: 'settingsPaneSystem',
    help: 'settingsPaneHelp',
  };
  const pane = $(paneIds[entry.sectionKey]);
  if (pane && entry.cardName && (entry.sectionKey === 'providers' || entry.sectionKey === 'plugins')) {
    const cards = entry.sectionKey === 'providers'
      ? pane.querySelectorAll('.provider-card')
      : pane.querySelectorAll('.plugin-card');
    for (const card of cards) {
      const name = ((card.querySelector('.provider-card-name') || {}).textContent || '').trim();
      if (name !== entry.cardName) continue;
      if (entry.fieldLabel && entry.sectionKey === 'providers') {
        for (const field of card.querySelectorAll('.provider-card-field')) {
          const label = ((field.querySelector('.provider-card-label') || {}).textContent || '').trim();
          if (label === entry.fieldLabel) return field;
        }
      }
      return card;
    }
  }
  // The i18n key may sit on the label or on a child of it (span inside a
  // toggle label), so resolve via any [data-i18n] node, then climb to the
  // enclosing .settings-field. #4340 review fix.
  const labelEl = pane && entry.i18nKey
    ? pane.querySelector(`[data-i18n="${CSS.escape(entry.i18nKey)}"]`)
    : null;
  const live = labelEl && labelEl.closest('.settings-field');
  if (live) return live;
  return entry.el && entry.el.isConnected ? entry.el : null;
}

function _highlightSettingsField(el) {
  if (!el) return;
  el.classList.remove('settings-field-highlight');
  void el.offsetWidth;
  el.classList.add('settings-field-highlight');
  setTimeout(() => el.classList.remove('settings-field-highlight'), 1800);
}

function _syncAgyPanelSessionActions(){
  const hasSession=!!S.session;
  const visibleMessages=hasSession?(S.messages||[]).filter(m=>m&&m.role&&m.role!=='tool').length:0;
  const title=hasSession?(S.session.title||t('untitled')):t('active_conversation_none');
  const meta=$('agySessionMeta');
  const hasShare=!!(hasSession&&S.session&&S.session.share_token);
  if(meta){
    if(!hasSession){
      meta.textContent=t('active_conversation_none');
    }else{
      const base=t('active_conversation_meta', title, visibleMessages);
      meta.textContent=hasShare
        ? `${base} · ${t('share_session_status_active')}`
        : base;
    }
  }
  const setDisabled=(id,disabled)=>{
    const el=$(id);
    if(!el)return;
    el.disabled=!!disabled;
    el.classList.toggle('disabled',!!disabled);
  };
  setDisabled('btnDownload',!hasSession||visibleMessages===0);
  setDisabled('btnExportJSON',!hasSession);
  setDisabled('btnClearConvModal',!hasSession||visibleMessages===0);
}

// Thin wrapper: settings now live in the main content area. External callers
// (keyboard shortcuts, commands) keep working through this name.
function toggleSettings(){
  if(_currentPanel==='settings'){
    _closeSettingsPanel();
  } else {
    switchPanel('settings');
  }
}

function _resetSettingsPanelState(){
  const bar=$('settingsUnsavedBar');
  if(bar) bar.style.display='none';
  _setAppearanceAutosaveStatus('');
}

function _hideSettingsPanel(){
  _resetSettingsPanelState();
  const target = _consumeSettingsTargetPanel('chat');
  if(_currentPanel==='settings') switchPanel(target, {bypassSettingsGuard:true});
}

// Close with unsaved-changes check. If dirty, show a confirm dialog.
function _closeSettingsPanel(){
  if(!_settingsDirty){
    _revertSettingsPreview();
    _hideSettingsPanel();
    return;
  }
  _pendingSettingsTargetPanel = _pendingSettingsTargetPanel || 'chat';
  _showSettingsUnsavedBar();
}

// Revert live DOM/localStorage to what they were when the panel opened
function _revertSettingsPreview(){
  // Appearance controls autosave immediately. Closing/discarding the settings
  // panel must not roll back theme, skin, or font-size after the user sees the
  // inline saved state.
}

// Show the "Unsaved changes" bar inside the settings panel
function _showSettingsUnsavedBar(){
  let bar = $('settingsUnsavedBar');
  if(bar){ bar.style.display=''; return; }
  // Create it
  bar = document.createElement('div');
  bar.id = 'settingsUnsavedBar';
  bar.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;background:rgba(233,69,96,.12);border:1px solid rgba(233,69,96,.3);border-radius:8px;padding:10px 14px;margin:0 0 12px;font-size:13px;';
  bar.innerHTML = `<span style="color:var(--text)">${esc(t('settings_unsaved_changes'))}</span>`
    + '<span style="display:flex;gap:8px">'
    + `<button onclick="_discardSettings()" style="padding:5px 12px;border-radius:6px;border:1px solid var(--border2);background:rgba(255,255,255,.06);color:var(--muted);cursor:pointer;font-size:12px;font-weight:600">${esc(t('discard'))}</button>`
    + `<button onclick="saveSettings(true)" style="padding:5px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px;font-weight:600">${esc(t('save'))}</button>`
    + '</span>';
  const body = document.querySelector('#mainSettings .settings-main') || document.querySelector('.settings-main');
  if(body) body.prepend(bar);
}

function _discardSettings(){
  _revertSettingsPreview();
  _settingsDirty = false;
  _hideSettingsPanel();
}

// Mark settings as dirty whenever anything changes
function _markSettingsDirty(){
  _settingsDirty = true;
}

// Apply TTS enabled state: toggles a body class so the CSS rule
// `body.tts-enabled .msg-tts-btn` shows/hides the speaker icon. We toggle the
// body class instead of writing inline `style.display` because the parent
// `.msg-action-btn` has no display rule, so clearing the inline style let the
// `.msg-tts-btn{display:none;}` cascade re-hide the button (#1409).
function _applyTtsEnabled(enabled){
  document.body.classList.toggle('tts-enabled', !!enabled);
}

// Read + sanitize the JSON/YAML structured code-block default-view controls
// (#484). mode is one of auto|on|off; lines is clamped to an int 1..1000 with a
// fallback of 10 (the original hardcoded threshold).
function _structuredCodeViewFromUi(){
  const modeSel=$('settingsStructuredCodeMode');
  const mode=modeSel&&['auto','on','off'].includes(modeSel.value)?modeSel.value:'auto';
  const linesField=$('settingsStructuredCodeAutoLines');
  const n=parseInt((linesField||{}).value,10);
  const lines=(Number.isFinite(n)&&n>=1&&n<=1000)?n:10;
  return {structured_code_default_view:mode,structured_code_auto_tree_lines:lines};
}

// Apply the structured code-block settings to runtime globals and re-render the
// transcript so already-rendered JSON/YAML blocks pick up the new default. The
// per-block Raw/Tree toggle is unaffected.
function _applyStructuredCodeViewSettings(mode,lines,rerender){
  window._structuredCodeDefaultView=['auto','on','off'].includes(mode)?mode:'auto';
  const n=parseInt(lines,10);
  window._structuredCodeAutoTreeLines=(Number.isFinite(n)&&n>=1&&n<=1000)?n:10;
  if(rerender){
    if(typeof clearMessageRenderCache==='function') clearMessageRenderCache();
    if(typeof renderMessages==='function') renderMessages({preserveScroll:true});
  }
}

// The Auto-threshold input is only meaningful in 'auto' mode; disable it
// otherwise so the control reads as inactive without hiding it.
function _syncStructuredCodeLinesEnabled(){
  const modeSel=$('settingsStructuredCodeMode');
  const linesField=$('settingsStructuredCodeAutoLines');
  // Both controls live in the same settings-field and are present together;
  // if either is missing there's nothing to sync.
  if(!modeSel||!linesField) return;
  const isAuto=modeSel.value==='auto';
  linesField.disabled=!isAuto;
  linesField.style.opacity=isAuto?'':'0.5';
}

function _appearancePayloadFromUi(){
  const worklogDetailsExpanded=!!($('settingsWorklogDetailsExpandedDefault')||{}).checked;
  const chatActivityModeSel=$('settingsChatActivityDisplayMode');
  const transparentEventTimestamps=$('settingsTransparentEventTimestamps');
  return {
    theme: ($('settingsTheme')||{}).value || localStorage.getItem('agy-theme') || 'dark',
    skin: ($('settingsSkin')||{}).value || localStorage.getItem('agy-skin') || 'default',
    font_size: ($('settingsFontSize')||{}).value || localStorage.getItem('agy-font-size') || 'default',
    chat_activity_display_mode: chatActivityModeSel&&(chatActivityModeSel.value==='transparent_stream'||chatActivityModeSel.value==='hide_all_activity')
      ? chatActivityModeSel.value
      : 'compact_worklog',
    transparent_stream_event_timestamps: transparentEventTimestamps ? transparentEventTimestamps.checked : true,
    session_jump_buttons: !!($('settingsSessionJumpButtons')||{}).checked,
    session_endless_scroll: !!($('settingsSessionEndlessScroll')||{}).checked,
    auto_scroll_follow: !!($('settingsAutoScrollFollow')||{}).checked,
    render_user_markdown: !!($('settingsRenderUserMarkdown')||{}).checked,
    large_text_paste_as_attachment: !!($('settingsLargeTextPasteAsAttachment')||{}).checked,
    project_quick_create_buttons: !!($('settingsProjectQuickCreate')||{}).checked,
    ..._structuredCodeViewFromUi(),
    show_titlebar_profile: !!($('settingsShowTitlebarProfile')||{}).checked,
    worklog_details_expanded_default: worklogDetailsExpanded,
    activity_feed_expanded_default: worklogDetailsExpanded,
    ..._composerControlVisibilityPayload(),
    composer_control_order: _getComposerControlOrder(),
    hidden_tabs: _getHiddenTabs(),
    tab_order: _getTabOrder(),
  };
}

function _syncChatActivityDisplayModeControl(mode){
  const next=mode==='transparent_stream'||mode==='hide_all_activity' ? mode : 'compact_worklog';
  const select=$('settingsChatActivityDisplayMode');
  if(select) select.value=next;
  document.querySelectorAll('[data-chat-activity-mode]').forEach(btn=>{
    const active=btn.getAttribute('data-chat-activity-mode')===next;
    btn.classList.toggle('active',active);
    btn.setAttribute('aria-pressed',active?'true':'false');
  });
  window._chatActivityDisplayMode=next;
  window._transparentStream=next==='transparent_stream';
  if(typeof _syncTransparentEventTimestampsControl==='function') _syncTransparentEventTimestampsControl(window._transparentEventTimestamps,next);
  if(next==='hide_all_activity'&&typeof window._hideLiveActivityForFinalAnswerOnly==='function') window._hideLiveActivityForFinalAnswerOnly();
}

function _syncTransparentEventTimestampsControl(enabled, mode){
  const next=enabled!==false;
  const activeMode=mode==='transparent_stream'||mode==='hide_all_activity' ? mode : (window._chatActivityDisplayMode||'compact_worklog');
  const checkbox=$('settingsTransparentEventTimestamps');
  if(checkbox){
    checkbox.checked=next;
    checkbox.disabled=activeMode!=='transparent_stream';
    checkbox.style.opacity=activeMode==='transparent_stream'?'':'0.5';
  }
  window._transparentEventTimestamps=next;
}

function _pickChatActivityDisplayMode(mode){
  _syncChatActivityDisplayModeControl(mode);
  if(typeof clearMessageRenderCache==='function') clearMessageRenderCache();
  if(typeof renderMessages==='function') renderMessages({preserveScroll:true});
  _scheduleAppearanceAutosave();
}
if(typeof window!=='undefined') window._pickChatActivityDisplayMode=_pickChatActivityDisplayMode;

function _pickTransparentEventTimestamps(enabled){
  _syncTransparentEventTimestampsControl(enabled,window._chatActivityDisplayMode);
  if(typeof clearMessageRenderCache==='function') clearMessageRenderCache();
  if(typeof renderMessages==='function') renderMessages({preserveScroll:true});
  _scheduleAppearanceAutosave();
}
if(typeof window!=='undefined') window._pickTransparentEventTimestamps=_pickTransparentEventTimestamps;

function _setAppearanceAutosaveStatus(state){
  const el=$('settingsAppearanceAutosaveStatus');
  if(!el) return;
  el.className='settings-autosave-status';
  if(!state){
    el.textContent='';
    return;
  }
  el.classList.add('is-'+state);
  if(state==='saving'){
    el.textContent=t('settings_autosave_saving');
  }else if(state==='saved'){
    el.textContent=t('settings_autosave_saved');
  }else if(state==='failed'){
    el.innerHTML=`<span>${esc(t('settings_autosave_failed'))}</span> <button type="button" onclick="_retryAppearanceAutosave()">${esc(t('settings_autosave_retry'))}</button>`;
  }
}

function _rememberAppearanceSaved(payload){
  if(!payload) return;
  _settingsThemeOnOpen=payload.theme||localStorage.getItem('agy-theme')||'dark';
  _settingsSkinOnOpen=payload.skin||localStorage.getItem('agy-skin')||'default';
  _settingsFontSizeOnOpen=payload.font_size||localStorage.getItem('agy-font-size')||'default';
}

function _scheduleAppearanceAutosave(){
  const payload=_appearancePayloadFromUi();
  // Keep discard/close behavior aligned with the new mental model: appearance
  // changes are committed immediately instead of treated as preview-only edits.
  _rememberAppearanceSaved(payload);
  _settingsAppearanceAutosaveRetryPayload=payload;
  _setAppearanceAutosaveStatus('saving');
  if(_settingsAppearanceAutosaveTimer) clearTimeout(_settingsAppearanceAutosaveTimer);
  _settingsAppearanceAutosaveTimer=setTimeout(()=>_autosaveAppearanceSettings(payload),350);
}

async function _autosaveAppearanceSettings(payload){
  try{
    const saved=await _enqueueSettingsPost({method:'POST',body:JSON.stringify(payload)});
    _settingsAppearanceAutosaveRetryPayload=null;
    _rememberAppearanceSaved(payload);
    if(saved&&saved.font_size){
      localStorage.setItem('agy-font-size',saved.font_size);
    }
    if(saved){
      window._sessionJumpButtonsEnabled=!!saved.session_jump_buttons;
      if(Object.prototype.hasOwnProperty.call(saved,'chat_activity_display_mode')){
        const beforeMode=window._chatActivityDisplayMode;
        const beforeTimestamps=window._transparentEventTimestamps!==false;
        _syncChatActivityDisplayModeControl(saved.chat_activity_display_mode);
        _syncTransparentEventTimestampsControl(saved.transparent_stream_event_timestamps, saved.chat_activity_display_mode);
        if(window._chatActivityDisplayMode!==beforeMode||((window._transparentEventTimestamps!==false)!==beforeTimestamps)){
          if(typeof clearMessageRenderCache==='function') clearMessageRenderCache();
          if(typeof renderMessages==='function') renderMessages({preserveScroll:true});
        }
      }
      if(typeof _applySessionNavigationPrefs==='function') _applySessionNavigationPrefs();
    }
    window._sessionEndlessScrollEnabled=!!(saved&&saved.session_endless_scroll);
    window._autoScrollFollow=!saved||saved.auto_scroll_follow!==false;
    // #6819: persist ONLY from an explicit boolean in the server response.
    // A failed autosave (`saved` falsy) or a partial response without the key
    // would otherwise write the synthesized default (ON) into the mirror,
    // corrupting the value a later boot-failure fallback would restore
    // (Greptile P1 review on #6856).
    if(saved&&typeof saved.auto_scroll_follow==='boolean'&&typeof _persistAutoScrollFollow==='function'){
      _persistAutoScrollFollow(saved.auto_scroll_follow);
    }
    window._largeTextPasteAsAttachment=!saved||saved.large_text_paste_as_attachment!==false;
    window._projectQuickCreate=!!(saved&&saved.project_quick_create_buttons);
    if(saved&&Object.prototype.hasOwnProperty.call(saved,'structured_code_default_view')){
      // Re-sync from the server-validated/clamped values so the UI and runtime
      // globals match exactly what was persisted.
      _applyStructuredCodeViewSettings(saved.structured_code_default_view,saved.structured_code_auto_tree_lines,false);
      const modeSel=$('settingsStructuredCodeMode');
      if(modeSel) modeSel.value=window._structuredCodeDefaultView;
      const linesField=$('settingsStructuredCodeAutoLines');
      if(linesField) linesField.value=window._structuredCodeAutoTreeLines;
      _syncStructuredCodeLinesEnabled();
    }
    if(saved&&payload&&Object.prototype.hasOwnProperty.call(payload,'worklog_details_expanded_default')&&(
      Object.prototype.hasOwnProperty.call(saved,'worklog_details_expanded_default') ||
      Object.prototype.hasOwnProperty.call(saved,'activity_feed_expanded_default')
    )){
      window._worklogDetailsExpandedByDefault=!!(
        Object.prototype.hasOwnProperty.call(saved,'worklog_details_expanded_default')
          ? saved.worklog_details_expanded_default
          : saved.activity_feed_expanded_default
      );
    }
    if(saved){
      _ensureComposerControlVisibilityState(saved);
      if(Array.isArray(saved.composer_control_order)){
        const nextOrder=_setComposerControlOrder(saved.composer_control_order);
        if(typeof window._applyComposerControlOrder==='function') window._applyComposerControlOrder(nextOrder);
      }
      _renderComposerControlChips();
      _renderComposerSituationalControlChips();
      if(typeof _applyComposerFooterVisibilitySettings==='function') _applyComposerFooterVisibilitySettings();
    }
    _setAppearanceAutosaveStatus('saved');
  }catch(e){
    console.warn('[settings] appearance autosave failed', e);
    _setAppearanceAutosaveStatus('failed');
  }
}

function _retryAppearanceAutosave(){
  const payload=_settingsAppearanceAutosaveRetryPayload||_appearancePayloadFromUi();
  _setAppearanceAutosaveStatus('saving');
  _autosaveAppearanceSettings(payload);
}

// ── Phase 2: Preferences autosave (Issue #1003) ───────────────────────

const _SETTINGS_SPEECH_STORAGE_KEYS={
  tts_enabled:'agy-tts-enabled',
  tts_auto_read:'agy-tts-auto-read',
  tts_engine:'agy-tts-engine',
  tts_voice:'agy-tts-voice',
  tts_rate:'agy-tts-rate',
  tts_pitch:'agy-tts-pitch',
  voice_mode_button:'agy-voice-mode-button',
  voice_continuous:'agy-voice-continuous',
  voice_silence_ms:'agy-voice-silence-ms',
  raw_audio_mode:'agy-raw-audio-mode',
};
let _settingsSpeechPersistedKeys=new Set();
let _settingsSpeechLocalStorageKeys=new Set();
let _settingsSpeechChangedKeys=new Set();

function _captureSpeechPreferenceOwnership(settings){
  _settingsSpeechPersistedKeys=new Set(Array.isArray(settings&&settings.persisted_speech_keys)?settings.persisted_speech_keys:[]);
  _settingsSpeechLocalStorageKeys=new Set();
  _settingsSpeechChangedKeys=new Set();
  Object.entries(_SETTINGS_SPEECH_STORAGE_KEYS).forEach(([settingKey,storageKey])=>{
    try{if(localStorage.getItem(storageKey)!==null) _settingsSpeechLocalStorageKeys.add(settingKey);}catch(_){}
  });
}

function _speechPreferenceIsOwned(settingKey){
  return _settingsSpeechPersistedKeys.has(settingKey)||_settingsSpeechLocalStorageKeys.has(settingKey)||_settingsSpeechChangedKeys.has(settingKey);
}

function _markSpeechPreferenceChanged(settingKey){
  _settingsSpeechChangedKeys.add(settingKey);
}

function _syncSpeechPreferenceCache(settingKey,value){
  if(!_speechPreferenceIsOwned(settingKey)) return;
  const storageKey=_SETTINGS_SPEECH_STORAGE_KEYS[settingKey];
  if(storageKey) localStorage.setItem(storageKey,String(value));
}

function _setOwnedSpeechPayload(payload,settingKey,value){
  if(_speechPreferenceIsOwned(settingKey)) payload[settingKey]=value;
}

function _preferencesPayloadFromUi(){
  const payload={};
  const sendKeySel=$('settingsSendKey');
  if(sendKeySel) payload.send_key=sendKeySel.value;
  const langSel=$('settingsLanguage');
  if(langSel) payload.language=langSel.value;
  const showUsageCb=$('settingsShowTokenUsage');
  if(showUsageCb) payload.show_token_usage=showUsageCb.checked;
  const showQuotaChipCb=$('settingsShowQuotaChip');
  if(showQuotaChipCb) payload.show_quota_chip=showQuotaChipCb.checked;
  const showConversationOutlineCb=$('settingsShowConversationOutline');
  if(showConversationOutlineCb) payload.show_conversation_outline=showConversationOutlineCb.checked;
  const hideSuggestionsCb=$('settingsHideSuggestions');
  if(hideSuggestionsCb) payload.hide_empty_state_suggestions=hideSuggestionsCb.checked;
  const hideEmptyStatePanelCb=$('settingsHideEmptyStatePanel');
  if(hideEmptyStatePanelCb) payload.hide_empty_state_panel=hideEmptyStatePanelCb.checked;
  const virtualizeTranscriptCb=$('settingsVirtualizeTranscript');
  if(virtualizeTranscriptCb){
    payload.virtualize_transcript=virtualizeTranscriptCb.checked;
    // #4343: persist the opt-in marker alongside. Enabling the experimental
    // feature records an explicit post-flip opt-in so load_settings honors it
    // (a stored true WITHOUT this marker is treated as a stale pre-flip value
    // and reset to off). Unchecking clears the marker.
    payload.virtualize_transcript_optin=virtualizeTranscriptCb.checked;
  }
  const showTpsCb=$('settingsShowTps');
  if(showTpsCb) payload.show_tps=showTpsCb.checked;
  const fadeTextCb=$('settingsFadeTextEffect');
  if(fadeTextCb) payload.fade_text_effect=fadeTextCb.checked;
  const terminalAutoExpandCb=$('settingsTerminalAutoExpand');
  if(terminalAutoExpandCb) payload.terminal_auto_expand_on_output=terminalAutoExpandCb.checked;
  const workspaceTodosTabCb=$('settingsWorkspaceTodosTab');
  if(workspaceTodosTabCb) payload.workspace_todos_tab=workspaceTodosTabCb.checked;
  const apiRedactCb=$('settingsApiRedact');
  if(apiRedactCb) payload.api_redact_enabled=apiRedactCb.checked;
  const showCliCb=$('settingsShowCliSessions');
  if(showCliCb) payload.show_cli_sessions=showCliCb.checked;
  const showClaudeCodeCb=$('settingsShowClaudeCodeSessions');
  if(showClaudeCodeCb) payload.show_claude_code_sessions=showClaudeCodeCb.checked;
  const showCronCb=$('settingsShowCronSessions');
  // Gate cron sessions on CLI sessions (the server short-circuits otherwise),
  // identically to the explicit saveSettings() path, so neither save route can
  // persist show_cron_sessions=true while show_cli_sessions=false. (#3514)
  if(showCronCb) payload.show_cron_sessions=!!(showCliCb&&showCliCb.checked&&showCronCb.checked);
  const showWebhookCb=$('settingsShowWebhookSessions');
  if(showWebhookCb) payload.show_webhook_sessions=!!(showCliCb&&showCliCb.checked&&showWebhookCb.checked);
  const showKanbanCb=$('settingsShowKanbanSessions');
  if(showKanbanCb) payload.show_kanban_sessions=!!(showCliCb&&showCliCb.checked&&showKanbanCb.checked);
  const showPreviousMessagingCb=$('settingsShowPreviousMessagingSessions');
  if(showPreviousMessagingCb) payload.show_previous_messaging_sessions=showPreviousMessagingCb.checked;
  const syncCb=$('settingsSyncInsights');
  if(syncCb) payload.sync_to_insights=syncCb.checked;
  const updateCb=$('settingsCheckUpdates');
  if(updateCb) payload.check_for_updates=updateCb.checked;
  // update_channel is NOT included here — it has its own dedicated write path
  // (_saveUpdateChannelFromSelector) so a stale tab's generic autosave cannot
  // overwrite a newer channel selection made in another tab. (#6612)
  const ignoreAgentUpdatesCb=$('settingsIgnoreAgentUpdates');
  if(ignoreAgentUpdatesCb) payload.ignore_agent_updates=ignoreAgentUpdatesCb.checked;
  const whatsNewSummaryCb=$('settingsWhatsNewSummary');
  if(whatsNewSummaryCb) payload.whats_new_summary_enabled=whatsNewSummaryCb.checked;
  const soundCb=$('settingsSoundEnabled');
  if(soundCb) payload.sound_enabled=soundCb.checked;
  const rtlCb=$('settingsRtl');
  if(rtlCb) payload.rtl=rtlCb.checked;
  const notifCb=$('settingsNotificationsEnabled');
  if(notifCb) payload.notifications_enabled=notifCb.checked;
  const sidebarDensitySel=$('settingsSidebarDensity');
  if(sidebarDensitySel) payload.sidebar_density=sidebarDensitySel.value;
  const pinnedLimitField=$('settingsPinnedSessionsLimit');
  if(pinnedLimitField) payload.pinned_sessions_limit=parseInt(pinnedLimitField.value,10);
  const autoTitleRefreshSel=$('settingsAutoTitleRefresh');
  if(autoTitleRefreshSel) payload.auto_title_refresh_every=parseInt(autoTitleRefreshSel.value,10);
  const defaultMessageModeSel=$('settingsDefaultMessageMode');
  if(defaultMessageModeSel) payload.default_message_mode=defaultMessageModeSel.value;
  const showBusyPlaceholderHintCb=$('settingsShowBusyPlaceholderHint');
  if(showBusyPlaceholderHintCb) payload.show_busy_placeholder_hint=showBusyPlaceholderHintCb.checked;
  const newChatOnWorkspaceSwitchCb=$('settingsNewChatOnWorkspaceSwitch');
  if(newChatOnWorkspaceSwitchCb) payload.new_chat_on_workspace_switch=newChatOnWorkspaceSwitchCb.checked;
  const botNameField=$('settingsBotName');
  if(botNameField) payload.bot_name=botNameField.value;
  Object.assign(payload,_speechPreferencesPayloadFromUi());
  return payload;
}

function _speechPreferencesPayloadFromUi(){
  const payload={};
  const ttsEnabledCb=$('settingsTtsEnabled');
  if(ttsEnabledCb) _setOwnedSpeechPayload(payload,'tts_enabled',ttsEnabledCb.checked);
  const ttsAutoReadCb=$('settingsTtsAutoRead');
  if(ttsAutoReadCb) _setOwnedSpeechPayload(payload,'tts_auto_read',ttsAutoReadCb.checked);
  const ttsEngineSel=$('settingsTtsEngine');
  if(ttsEngineSel) _setOwnedSpeechPayload(payload,'tts_engine',ttsEngineSel.value||'browser');
  const ttsVoiceSel=$('settingsTtsVoice');
  if(ttsVoiceSel) _setOwnedSpeechPayload(payload,'tts_voice',ttsVoiceSel.value||'');
  const ttsRateSlider=$('settingsTtsRate');
  if(ttsRateSlider) _setOwnedSpeechPayload(payload,'tts_rate',parseFloat(ttsRateSlider.value));
  const ttsPitchSlider=$('settingsTtsPitch');
  if(ttsPitchSlider) _setOwnedSpeechPayload(payload,'tts_pitch',parseFloat(ttsPitchSlider.value));
  const voiceModeCb=$('settingsVoiceModeEnabled');
  if(voiceModeCb) _setOwnedSpeechPayload(payload,'voice_mode_button',voiceModeCb.checked);
  const rawAudioCb=$('settingsRawAudio');
  _setOwnedSpeechPayload(payload,'raw_audio_mode',rawAudioCb?rawAudioCb.checked:localStorage.getItem('agy-raw-audio-mode')==='true');
  _setOwnedSpeechPayload(payload,'voice_continuous',localStorage.getItem('agy-voice-continuous')==='true');
  const voiceSilence=parseInt(localStorage.getItem('agy-voice-silence-ms'),10);
  _setOwnedSpeechPayload(payload,'voice_silence_ms',(Number.isFinite(voiceSilence)&&voiceSilence>=200)?voiceSilence:1800);
  return payload;
}

// Keep same-page settings merges FIFO so one response cannot race the next read-modify-write.
let _settingsPanelPostQueue=Promise.resolve();

function _enqueueSettingsPost(options){
  const requestOptions={...(options||{})};
  if(typeof requestOptions.body==='string') requestOptions.body=String(requestOptions.body);
  const run=_settingsPanelPostQueue.then(async()=>{
    const saved=await api('/api/settings',requestOptions);
    if(!saved||typeof saved!=='object'||Array.isArray(saved)){
      throw new Error('Invalid settings response');
    }
    return saved;
  });
  _settingsPanelPostQueue=run.catch(()=>{});
  return run;
}

// Ownership token for the shared preferences autosave status slot. Prevents
// the channel writer from clearing or overwriting a 'failed'+Retry state the
// generic preferences autosave set; that Retry button has exactly one call
// site and becomes unreachable if another writer replaces the node.
let _preferencesAutosaveStatusOwner=null;

function _setPreferencesAutosaveStatus(state,owner){
  owner=owner||'preferences';
  const el=$('settingsPreferencesAutosaveStatus');
  if(!el) return;
  // Guard: if the slot shows 'failed' from a different writer, do not overwrite.
  // The DOM class is the source of truth; an external clear would remove
  // 'is-failed' and unblock writes without needing an extra variable.
  if(_preferencesAutosaveStatusOwner&&
     _preferencesAutosaveStatusOwner!==owner&&
     el.classList.contains('is-failed')){
    return;
  }
  _preferencesAutosaveStatusOwner=state?owner:null;
  el.className='settings-autosave-status';
  if(!state){
    el.textContent='';
    return;
  }
  el.classList.add('is-'+state);
  if(state==='saving'){
    el.textContent=t('settings_autosave_saving');
  }else if(state==='saved'){
    el.textContent=t('settings_autosave_saved');
  }else if(state==='failed'){
    el.innerHTML=`<span>${esc(t('settings_autosave_failed'))}</span> <button type=\"button\" onclick=\"_retryPreferencesAutosave()\">${esc(t('settings_autosave_retry'))}</button>`;
  }
}

function _rememberPreferencesSaved(payload){
  if(!payload) return;
  if(payload.send_key!==undefined) localStorage.setItem('agy-pref-send_key',payload.send_key);
  if(payload.language!==undefined) localStorage.setItem('agy-pref-language',payload.language);
}

function _schedulePreferencesAutosave(){
  const payload=_preferencesPayloadFromUi();
  _rememberPreferencesSaved(payload);
  _settingsPreferencesAutosaveRetryPayload=payload;
  _setPreferencesAutosaveStatus('saving');
  if(_settingsPreferencesAutosaveTimer) clearTimeout(_settingsPreferencesAutosaveTimer);
  _settingsPreferencesAutosaveTimer=setTimeout(()=>_autosavePreferencesSettings(payload),350);
}

async function _autosavePreferencesSettings(payload){
  try{
    const saved=await _enqueueSettingsPost({method:'POST',body:JSON.stringify(payload)});
    if(payload&&payload.terminal_auto_expand_on_output!==undefined){
      window._terminalAutoExpandOnOutput=!!(saved&&saved.terminal_auto_expand_on_output);
    }
    if(payload&&payload.workspace_todos_tab!==undefined){
      window._workspaceTodosTab=!!(saved&&saved.workspace_todos_tab);
    }
    if(payload&&Object.prototype.hasOwnProperty.call(payload,'fade_text_effect')) window._fadeTextEffect=!!payload.fade_text_effect;
    if(saved&&Object.prototype.hasOwnProperty.call(saved,'pinned_sessions_limit')) window._pinnedSessionsLimit=parseInt(saved.pinned_sessions_limit,10)||3;
    if(payload&&payload.show_tps!==undefined){
      window._showTps=!!(saved&&saved.show_tps);
      if(typeof clearMessageRenderCache==='function') clearMessageRenderCache();
      if(typeof renderMessages==='function') renderMessages();
    }
    if(payload&&payload.hide_empty_state_suggestions!==undefined){
      window._hideEmptyStateSuggestions=!!(saved&&saved.hide_empty_state_suggestions);
      if(typeof applyEmptyStateSuggestionPref==='function') applyEmptyStateSuggestionPref();
    }
    if(payload&&payload.hide_empty_state_panel!==undefined){
      window._hideEmptyStatePanel=!!(saved&&saved.hide_empty_state_panel);
      if(typeof applyEmptyStatePanelPref==='function') applyEmptyStatePanelPref();
    }
    if(payload&&payload.show_conversation_outline!==undefined){
      window._showConversationOutline=!!(saved&&saved.show_conversation_outline);
      document.documentElement.dataset.conversationOutline=window._showConversationOutline?'enabled':'disabled';
      if(typeof applyConversationOutlinePreference==='function') applyConversationOutlinePreference();
    }
    if(payload&&payload.default_message_mode!==undefined){
      // #5170 mirror write on autosave, under the #5145 rename: persist the
      // saved mode so a reload/offline first-send honors it (legacy fallback).
      const _dmm=(saved&&saved.default_message_mode)||(saved&&saved.busy_input_mode);
      window._defaultMessageMode=(typeof _persistDefaultMessageMode==='function')?_persistDefaultMessageMode(_dmm):(_dmm||'steer');
      if(typeof _applyBusyComposerPlaceholder==='function') _applyBusyComposerPlaceholder();
    }
    if(payload&&payload.show_busy_placeholder_hint!==undefined){
      window._showBusyPlaceholderHint=!!(saved&&saved.show_busy_placeholder_hint);
      if(typeof _applyBusyComposerPlaceholder==='function') _applyBusyComposerPlaceholder();
    }
    if(payload&&payload.new_chat_on_workspace_switch!==undefined){
      window._newChatOnWorkspaceSwitch=!!(saved&&saved.new_chat_on_workspace_switch);  // #5473
    }
    _settingsPreferencesAutosaveRetryPayload=null;
    _setPreferencesAutosaveStatus('saved');
    // Only clear the global dirty flag and hide the unsaved-changes bar when
    // there is no pending edit on a manually-saved field. Password and model
    // are still committed via the explicit "Save Settings" button (password
    // for security; model goes through /api/default-model). Without this
    // guard, autosaving a checkbox right after a user typed in the password
    // field would silently dismiss the password edit. (Opus pre-release
    // review of v0.50.250, SHOULD-FIX Q1.)
    const pwField=$('settingsPassword');
    const pwDirty=!!(pwField&&pwField.value);
    const modelSel=$('settingsModel');
    const modelState=(typeof _captureModelDropdownSelection==='function'&&modelSel)
      ? (_captureModelDropdownSelection(modelSel)||{model:String((modelSel&&modelSel.value)||''),model_provider:null})
      : {model:String((modelSel&&modelSel.value)||''),model_provider:null};
    const modelDirty=!!(
      modelSel&&(
        (modelState.model||'')!==(_settingsAgyDefaultModelOnOpen||'')||
        ((modelState.model_provider||null)!==(_settingsAgyDefaultModelProviderOnOpen||null))
      )
    );
    if(!pwDirty&&!modelDirty){
      const maxTokensField=$('settingsMaxTokens');
      const maxTokensDirty=!!(
        maxTokensField&&
        String(maxTokensField.value||'')!==String(maxTokensField.dataset.initialValue||'')
      );
      if(!maxTokensDirty){
        _settingsDirty=false;
        const bar=$('settingsUnsavedBar');
        if(bar) bar.style.display='none';
      }
    }
  }catch(e){
    console.warn('[settings] preferences autosave failed', e);
    _setPreferencesAutosaveStatus('failed');
  }
}

function _retryPreferencesAutosave(){
  const payload=_settingsPreferencesAutosaveRetryPayload||_preferencesPayloadFromUi();
  _setPreferencesAutosaveStatus('saving');
  _autosavePreferencesSettings(payload);
}

let _channelSaveSeq=0;
// Last server-confirmed update_channel value. Seeded at panel hydration so the
// failure-revert path always has a known-good value. _confirmedUpdateChannel is
// the only reliable "previous" value: by the time a change event fires the
// browser has already applied the picked option to the <select>, so
// channelSel.value inside the handler IS the new value, not the old one.
let _confirmedUpdateChannel=null;

async function _saveUpdateChannelFromSelector(channelSel){
  // #6612: dedicated write path for update_channel so the generic preferences
  // autosave payload never carries this field. A stale tab toggling an unrelated
  // preference must not overwrite a newer channel selection from another tab.
  if(!channelSel) return;
  const val=channelSel.value==='experimental'?'experimental':'stable';
  const seq=++_channelSaveSeq;
  if(typeof _setPreferencesAutosaveStatus==='function') _setPreferencesAutosaveStatus('saving','channel');
  try{
    const saved=await _enqueueSettingsPost({method:'POST',body:JSON.stringify({update_channel:val})});
    const confirmed=(saved&&(saved.update_channel==='experimental'||saved.update_channel==='stable'))
      ?saved.update_channel:val;
    _confirmedUpdateChannel=confirmed;
    // The queue makes the server state FIFO; the sequence guard protects only
    // the selector and other response-driven UI from stale completions.
    if(seq!==_channelSaveSeq) return;
    channelSel.value=confirmed;
    if(typeof _setPreferencesAutosaveStatus==='function') _setPreferencesAutosaveStatus('saved','channel');
    // Run the update check and badge sync against the confirmed server value,
    // not the optimistic pre-save value.
    if(typeof checkUpdatesNow==='function'){
      try{checkUpdatesNow(confirmed);}catch(_){}
    }
    if(typeof _syncUpdateChannelBadge==='function') _syncUpdateChannelBadge(confirmed);
  }catch(e){
    console.warn('[settings] update_channel save failed',e);
    // Revert selector and badge to the last server-confirmed value so both
    // controls agree with what the server actually holds. Status clear and
    // revert are both inside the seq guard so a superseded in-flight failure
    // does not clear status that a newer write or the generic autosave owns.
    if(seq===_channelSaveSeq){
      const revertTo=_confirmedUpdateChannel||'stable';
      channelSel.value=revertTo;
      if(typeof _syncUpdateChannelBadge==='function') _syncUpdateChannelBadge(revertTo);
      // Do not call _setPreferencesAutosaveStatus('failed','channel'): its retry
      // button replays _retryPreferencesAutosave(), which cannot contain
      // update_channel (#6612). Clear the saving indicator instead; the selector
      // snap-back is the user's signal.
      if(typeof _setPreferencesAutosaveStatus==='function') _setPreferencesAutosaveStatus(null,'channel');
    }
  }
}

function _syncSettingsMaxTokensPlaceholder(field, fallbackValue){
  if(!field) return;
  const parsedFallback=parseInt(fallbackValue,10);
  if(Number.isFinite(parsedFallback)&&parsedFallback>0&&typeof t==='function'){
    field.placeholder=t('settings_placeholder_max_tokens_fallback', parsedFallback);
    return;
  }
  field.placeholder=(typeof t==='function')
    ? t('settings_placeholder_max_tokens_none')
    : 'No override';
}

function _normalizeWebUIVersion(value){
  if(!value) return '';
  const s=String(value).trim();
  if(!s) return '';
  const lower=s.toLowerCase();
  if(lower==='__webui_version__'||lower==='not detected'||lower==='unknown') return '';
  return s;
}

function _currentWebUIBundleVersion(){
  try{
    const raw=window.__AGY_WEBUI_BUNDLE_VERSION__||window.__HERMES_WEBUI_BUNDLE_VERSION__;
    if(!raw) return '';
    let s=String(raw);
    try{ s=decodeURIComponent(s.replace(/\+/g,' ')); }catch(_){}
    return _normalizeWebUIVersion(s);
  }catch(_){ return ''; }
}

function _showStaleWebUIClientBanner(clientVersion,serverVersion){
  const banner=document.getElementById('staleClientBanner');
  if(!banner) return;
  const msg=document.getElementById('staleClientMessage');
  const versions=document.getElementById('staleClientVersions');
  if(msg) msg.textContent='This tab is running a different WebUI version. Hard refresh to restore full functionality.';
  if(versions) versions.textContent='Running: '+clientVersion+' → Server: '+serverVersion;
  banner.style.display='flex';
}

function checkWebUIVersionSkew(settings){
  try{
    if(!settings) return;
    const client=_currentWebUIBundleVersion();
    const server=_normalizeWebUIVersion(settings.webui_version);
    if(!client||!server) return;
    if(client===server) return;
    _showStaleWebUIClientBanner(client,server);
  }catch(_){}
}
window.checkWebUIVersionSkew=checkWebUIVersionSkew;

async function loadSettingsPanel(){
  try{
    const settings=await api('/api/settings');
    checkWebUIVersionSkew(settings);
    // Populate the version badges from the server — keeps them in sync with git
    // tags automatically without any manual release step.
    //
    // The DISPLAY badge uses update_channel_version (a channel-scoped
    // `git describe --match`), which is SEPARATE from settings.webui_version.
    // webui_version is load-bearing for asset cache-busting / SW cache / stale-
    // client skew detection and must stay channel-neutral — never render it as
    // the channel badge. See api/updates.channel_version_badge().
    const webuiBadge = $('settings-webui-version-badge');
    if(webuiBadge){
      const chanVer = settings.update_channel_version || settings.webui_version || 'not detected';
      const chan = settings.update_channel==='experimental' ? 'experimental' : 'stable';
      // Only annotate the channel when on experimental — stable is the implicit
      // default and needs no extra chrome.
      webuiBadge.textContent = chan==='experimental'
        ? `WebUI: ${chanVer} · Experimental`
        : `WebUI: ${chanVer}`;
    }
    const agentBadge = $('settings-agent-version-badge');
    if(agentBadge){
      const agentVersion = (settings.agent_version || 'not detected').toString().trim() || 'not detected';
      agentBadge.textContent = `Agent: ${agentVersion}`;
    }
    // Hydrate appearance controls first so a slow /api/models request
    // cannot overwrite an in-progress theme/skin selection.
    const themeSel=$('settingsTheme');
    const themeVal=settings.theme||'dark';
    if(themeSel) themeSel.value=themeVal;
    if(typeof _syncThemePicker==='function') _syncThemePicker(themeVal);
    const skinVal=(localStorage.getItem('agy-skin')||settings.skin||'default').toLowerCase();
    const skinSel=$('settingsSkin');
    if(skinSel) skinSel.value=skinVal;
    if(typeof _buildSkinPicker==='function') _buildSkinPicker(skinVal);
    const fontSizeVal=settings.font_size||localStorage.getItem('agy-font-size')||'default';
    localStorage.setItem('agy-font-size',fontSizeVal);
    if(typeof _applyFontSize==='function') _applyFontSize(fontSizeVal);
    const fontSizeSel=$('settingsFontSize');
    if(fontSizeSel) fontSizeSel.value=fontSizeVal;
    if(typeof _syncFontSizePicker==='function') _syncFontSizePicker(fontSizeVal);
    const jumpButtonsCb=$('settingsSessionJumpButtons');
    if(jumpButtonsCb){
      jumpButtonsCb.checked=!!settings.session_jump_buttons;
      window._sessionJumpButtonsEnabled=jumpButtonsCb.checked;
      jumpButtonsCb.onchange=function(){
        window._sessionJumpButtonsEnabled=this.checked;
        if(typeof _applySessionNavigationPrefs==='function') _applySessionNavigationPrefs();
        _scheduleAppearanceAutosave();
      };
    }
    if(typeof _applySessionNavigationPrefs==='function') _applySessionNavigationPrefs();
    // Workspace panel default-open toggle (localStorage-backed)
    // Uses a separate key (agy-webui-workspace-panel-pref) so that
    // closing the panel via toolbar X does not clear the user's preference.
    const wsPanelCb=$('settingsWorkspacePanelOpen');
    if(wsPanelCb){
      wsPanelCb.checked=localStorage.getItem('agy-webui-workspace-panel-pref')==='open';
      wsPanelCb.onchange=function(){
        const open=this.checked;
        localStorage.setItem('agy-webui-workspace-panel-pref',open?'open':'closed');
        // Also sync the runtime key so the current session reflects the change
        localStorage.setItem('agy-webui-workspace-panel',open?'open':'closed');
        document.documentElement.dataset.workspacePanel=open?'open':'closed';
        if(open&&_workspacePanelMode==='closed') openWorkspacePanel('browse');
        else if(!open&&_workspacePanelMode!=='closed') toggleWorkspacePanel(false);
      };
    }
    const endlessScrollCb=$('settingsSessionEndlessScroll');
    if(endlessScrollCb){
      endlessScrollCb.checked=!!settings.session_endless_scroll;
      window._sessionEndlessScrollEnabled=endlessScrollCb.checked;
      endlessScrollCb.onchange=function(){
        window._sessionEndlessScrollEnabled=this.checked;
        _scheduleAppearanceAutosave();
      };
    }
    const autoScrollFollowCb=$('settingsAutoScrollFollow');
    if(autoScrollFollowCb){
      autoScrollFollowCb.checked=settings.auto_scroll_follow!==false;
      window._autoScrollFollow=autoScrollFollowCb.checked;
      // #6819/#6856: a successful settings GET is an authoritative resolve, so
      // sync the global mirror too — otherwise an explicit OFF applied here
      // leaves a stale ON mirror that a later boot-fetch failure would restore.
      // Persist ONLY when the server explicitly sent a boolean (never persist a
      // synthesized default from an absent field — matches the boot contract).
      if(typeof settings.auto_scroll_follow==='boolean'&&typeof _persistAutoScrollFollow==='function'){
        _persistAutoScrollFollow(settings.auto_scroll_follow);
      }
      autoScrollFollowCb.onchange=function(){
        window._autoScrollFollow=this.checked;
        _scheduleAppearanceAutosave();
      };
    }
    const worklogDetailsExpandedCb=$('settingsWorklogDetailsExpandedDefault');
    const chatActivityModeSel=$('settingsChatActivityDisplayMode');
    const transparentEventTimestampsCb=$('settingsTransparentEventTimestamps');
    if(chatActivityModeSel){
      _syncChatActivityDisplayModeControl(settings.chat_activity_display_mode);
      _syncTransparentEventTimestampsControl(settings.transparent_stream_event_timestamps, settings.chat_activity_display_mode);
      chatActivityModeSel.addEventListener('change',()=>{
        _pickChatActivityDisplayMode(chatActivityModeSel.value);
      },{once:false});
    }
    if(transparentEventTimestampsCb){
      transparentEventTimestampsCb.addEventListener('change',()=>{
        _pickTransparentEventTimestamps(transparentEventTimestampsCb.checked);
      },{once:false});
    }
    if(worklogDetailsExpandedCb){
      const worklogDetailsExpanded=Object.prototype.hasOwnProperty.call(settings,'worklog_details_expanded_default')
        ? settings.worklog_details_expanded_default
        : settings.activity_feed_expanded_default;
      worklogDetailsExpandedCb.checked=!!worklogDetailsExpanded;
      window._worklogDetailsExpandedByDefault=worklogDetailsExpandedCb.checked;
      worklogDetailsExpandedCb.onchange=function(){
        window._worklogDetailsExpandedByDefault=this.checked;
        if(typeof _applyWorklogDetailsExpandedDefault==='function') _applyWorklogDetailsExpandedDefault();
        _scheduleAppearanceAutosave();
      };
    }
    const renderUserMarkdownCb=$('settingsRenderUserMarkdown');
    if(renderUserMarkdownCb){
      renderUserMarkdownCb.checked=!!settings.render_user_markdown;
      window._renderUserMarkdown=renderUserMarkdownCb.checked;
      renderUserMarkdownCb.onchange=function(){
        window._renderUserMarkdown=this.checked;
        if(typeof clearMessageRenderCache==='function') clearMessageRenderCache();
        if(typeof renderMessages==='function') renderMessages();
        _scheduleAppearanceAutosave();
      };
    }
    const largeTextPasteCb=$('settingsLargeTextPasteAsAttachment');
    if(largeTextPasteCb){
      largeTextPasteCb.checked=settings.large_text_paste_as_attachment!==false;
      window._largeTextPasteAsAttachment=largeTextPasteCb.checked;
      largeTextPasteCb.onchange=function(){
        window._largeTextPasteAsAttachment=this.checked;
        _scheduleAppearanceAutosave();
      };
    }
    const pqcCb=$('settingsProjectQuickCreate');
    if(pqcCb){
      pqcCb.checked=!!(settings.project_quick_create_buttons);
      window._projectQuickCreate=pqcCb.checked;
      pqcCb.onchange=function(){
        window._projectQuickCreate=this.checked;
        // Rebuild the sidebar so the per-project + buttons appear/disappear
        // immediately, rather than only on the next render.
        try{ if(typeof renderSessionListFromCache==='function') renderSessionListFromCache(); }catch(_){}
        _scheduleAppearanceAutosave();
      };
    }
    const structuredCodeModeSel=$('settingsStructuredCodeMode');
    const structuredCodeLinesField=$('settingsStructuredCodeAutoLines');
    if(structuredCodeModeSel){
      const mode=['auto','on','off'].includes(settings.structured_code_default_view)?settings.structured_code_default_view:'auto';
      structuredCodeModeSel.value=mode;
      const lines=parseInt(settings.structured_code_auto_tree_lines,10);
      const safeLines=(Number.isFinite(lines)&&lines>=1&&lines<=1000)?lines:10;
      if(structuredCodeLinesField) structuredCodeLinesField.value=safeLines;
      _applyStructuredCodeViewSettings(mode,safeLines,false);
      _syncStructuredCodeLinesEnabled();
      structuredCodeModeSel.onchange=function(){
        const cfg=_structuredCodeViewFromUi();
        _applyStructuredCodeViewSettings(cfg.structured_code_default_view,cfg.structured_code_auto_tree_lines,true);
        _syncStructuredCodeLinesEnabled();
        _scheduleAppearanceAutosave();
      };
      if(structuredCodeLinesField){
        // Commit on change (blur / Enter / spinner) rather than every keystroke,
        // so a long transcript is not rebuilt per digit typed. Only re-render in
        // auto mode, where the threshold actually affects the default view.
        structuredCodeLinesField.addEventListener('change',function(){
          const cfg=_structuredCodeViewFromUi();
          structuredCodeLinesField.value=cfg.structured_code_auto_tree_lines;
          _applyStructuredCodeViewSettings(cfg.structured_code_default_view,cfg.structured_code_auto_tree_lines,cfg.structured_code_default_view==='auto');
          _scheduleAppearanceAutosave();
        },{once:false});
      }
    }
    const showTitlebarProfileCb=$('settingsShowTitlebarProfile');
    if(showTitlebarProfileCb){
      showTitlebarProfileCb.checked=!!settings.show_titlebar_profile;
      showTitlebarProfileCb.onchange=function(){
        window._showTitlebarProfile=this.checked;
        if(typeof _applyTitlebarProfileVisibility==='function') _applyTitlebarProfileVisibility();
        _scheduleAppearanceAutosave();
      };
    }
    _ensureComposerControlVisibilityState(settings);
    if(Array.isArray(settings.composer_control_order)){
      const composerOrder=_setComposerControlOrder(settings.composer_control_order);
      if(typeof window._applyComposerControlOrder==='function') window._applyComposerControlOrder(composerOrder);
    }
    _renderComposerControlChips();
    _renderComposerSituationalControlChips();
    if(typeof _applyComposerFooterVisibilitySettings==='function') _applyComposerFooterVisibilitySettings();
    // Tab visibility/order chips (dynamically populated from DOM)
    var hiddenTabs=[];
    if(Array.isArray(settings.hidden_tabs)){
      // Server value takes priority — even an empty array means "no tabs hidden"
      hiddenTabs=settings.hidden_tabs.filter(function(s){return typeof s==='string'&&s.trim();});
    }else{
      // Server has no hidden_tabs key — fall back to localStorage
      hiddenTabs=_getHiddenTabs();
    }
    var tabOrder=[];
    if(Array.isArray(settings.tab_order)){
      tabOrder=settings.tab_order.filter(function(s){return typeof s==='string'&&s.trim();});
    }else{
      tabOrder=_getTabOrder();
    }
    _setTabOrder(tabOrder);
    _applyTabOrder(tabOrder);
    _setHiddenTabs(hiddenTabs);
    _applyTabVisibility(hiddenTabs);
    _renderTabVisibilityChips();
    const resolvedLanguage=(typeof resolvePreferredLocale==='function')
      ? resolvePreferredLocale(settings.language, localStorage.getItem('agy-lang'))
      : (settings.language || localStorage.getItem('agy-lang') || 'en');
    // Keep settings modal and current page strings in sync with the resolved locale.
    if(typeof setLocale==='function'){
      setLocale(resolvedLanguage);
      if(typeof applyLocaleToDOM==='function') applyLocaleToDOM();
    }
    // Populate model dropdown from /api/models + live model fetch (#872)
    const modelSel=$('settingsModel');
    if(modelSel){
      modelSel.innerHTML='';
      let models=null;
      try{
        models=await api('/api/models');
        for(const g of ((models||{}).groups||[])){
          const og=document.createElement('optgroup');
          og.label=g.provider;
          if(g.provider_id) og.dataset.provider=g.provider_id;
          for(const m of [...(g.models||[]),...(g.extra_models||[])]){
            const opt=document.createElement('option');
            opt.value=m.id;opt.textContent=m.label;
            if(m && (m.supports_fast_tier === true || String(m.supports_fast_tier).toLowerCase()==='true')){
              opt.dataset.fast='1';
            }else if(m && (m.supports_fast_tier === false || String(m.supports_fast_tier).toLowerCase()==='false')){
              opt.dataset.fast='0';
            }
            og.appendChild(opt);
          }
          modelSel.appendChild(og);
        }
        // Append live-fetched models for the active provider, same as the
        // chat-header dropdown does via _fetchLiveModels() (#872).
        if(models.active_provider && typeof _fetchLiveModels==='function'){
          _fetchLiveModels(models.active_provider, modelSel);
        }
      }catch(e){}
      _settingsAgyDefaultModelOnOpen=(models&&models.default_model)||'';
      _settingsAgyDefaultModelProviderOnOpen=(models&&models.active_provider)||null;
      // Use the smart matcher so a saved bare form like "anthropic/claude-opus-4.6"
      // (what the CLI's `agy model` command writes) still selects the matching
      // `@nous:anthropic/claude-opus-4.6` option on a Nous setup. Without this, the
      // picker renders blank for any user whose default was persisted without the
      // @-prefix — CLI-first users, legacy installs, etc.
      if(typeof _applyModelToDropdown==='function'){
        _applyModelToDropdown(_settingsAgyDefaultModelOnOpen, modelSel, (models&&models.active_provider)||window._activeProvider||null);
      }else{
        modelSel.value=_settingsAgyDefaultModelOnOpen;
      }
      if(typeof closeSettingsModelDropdown==='function') closeSettingsModelDropdown();
      if(typeof mountSettingsModelPicker==='function') mountSettingsModelPicker();
      modelSel.addEventListener('change',_markSettingsDirty,{once:false});
      if(!modelSel._settingsChipSyncBound){
        modelSel._settingsChipSyncBound=true;
        modelSel.addEventListener('change',()=>{if(typeof syncSettingsModelChip==='function') syncSettingsModelChip();},{once:false});
      }
    }
    // Auxiliary models — load task assignments and provider/model options
    _bindMainAdvancedOptionsButton();
    _loadAuxiliaryModels();
    // Send key preference
    const sendKeySel=$('settingsSendKey');
    if(sendKeySel){sendKeySel.value=settings.send_key||'enter';sendKeySel.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    // Language preference — populate from LOCALES bundle
    const langSel=$('settingsLanguage');
    if(langSel){
      langSel.innerHTML='';
      if(typeof LOCALES!=='undefined'){
        for(const [code,bundle] of Object.entries(LOCALES)){
          const opt=document.createElement('option');
          opt.value=code;opt.textContent=bundle._label||code;
          langSel.appendChild(opt);
        }
      }
      langSel.value=resolvedLanguage;
      langSel.addEventListener('change',function(){
        if(typeof setLocale==='function'){setLocale(this.value);if(typeof applyLocaleToDOM==='function')applyLocaleToDOM();}
        _schedulePreferencesAutosave();
      },{once:false});
    }
    const showUsageCb=$('settingsShowTokenUsage');
    if(showUsageCb){showUsageCb.checked=!!settings.show_token_usage;showUsageCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const maxTokensField=$('settingsMaxTokens');
    if(maxTokensField){
      const rawMaxTokens=settings.max_tokens;
      const parsedMaxTokens=parseInt(rawMaxTokens,10);
      const hasRootOverride=Number.isFinite(parsedMaxTokens)&&parsedMaxTokens>0;
      maxTokensField.value=hasRootOverride
        ? String(parsedMaxTokens)
        : '';
      _syncSettingsMaxTokensPlaceholder(maxTokensField,settings.max_tokens_fallback);
      maxTokensField.dataset.initialValue=maxTokensField.value;
      maxTokensField.addEventListener('input',_markSettingsDirty,{once:false});
    }
    // Ambient provider quota chip toggle — default off; only shows at ≥1400px viewport
    // when enabled (see style.css @media (max-width:1399.98px) rule).
    const showQuotaChipCb=$('settingsShowQuotaChip');
    if(showQuotaChipCb){
      showQuotaChipCb.checked=settings.show_quota_chip===true;
      window._showQuotaChip=showQuotaChipCb.checked;
      showQuotaChipCb.addEventListener('change',()=>{
        window._showQuotaChip=showQuotaChipCb.checked;
        if(typeof refreshProviderQuotaIndicator==='function') refreshProviderQuotaIndicator();
        _schedulePreferencesAutosave();
      },{once:false});
    }
    const hideSuggestionsCb=$('settingsHideSuggestions');
    if(hideSuggestionsCb){
      hideSuggestionsCb.checked=settings.hide_empty_state_suggestions===true;
      window._hideEmptyStateSuggestions=hideSuggestionsCb.checked;
      if(typeof applyEmptyStateSuggestionPref==='function') applyEmptyStateSuggestionPref();
      hideSuggestionsCb.addEventListener('change',()=>{
        window._hideEmptyStateSuggestions=hideSuggestionsCb.checked;
        if(typeof applyEmptyStateSuggestionPref==='function') applyEmptyStateSuggestionPref();
        _schedulePreferencesAutosave();
      },{once:false});
    }
    const hideEmptyStatePanelCb=$('settingsHideEmptyStatePanel');
    if(hideEmptyStatePanelCb){
      hideEmptyStatePanelCb.checked=settings.hide_empty_state_panel===true;
      window._hideEmptyStatePanel=hideEmptyStatePanelCb.checked;
      if(typeof applyEmptyStatePanelPref==='function') applyEmptyStatePanelPref();
      hideEmptyStatePanelCb.addEventListener('change',()=>{
        window._hideEmptyStatePanel=hideEmptyStatePanelCb.checked;
        if(typeof applyEmptyStatePanelPref==='function') applyEmptyStatePanelPref();
        _schedulePreferencesAutosave();
      },{once:false});
    }
    const virtualizeTranscriptCb=$('settingsVirtualizeTranscript');
    if(virtualizeTranscriptCb){
      // #4343: EXPERIMENTAL/opt-IN, default OFF. Honor a stored true only when
      // it came from an explicit post-flip opt-in (===true); a pre-flip true is
      // already reset to false server-side by the load_settings migration.
      virtualizeTranscriptCb.checked=settings.virtualize_transcript===true;
      window._virtualizeTranscript=virtualizeTranscriptCb.checked;
      virtualizeTranscriptCb.addEventListener('change',()=>{
        window._virtualizeTranscript=virtualizeTranscriptCb.checked;
        // Re-render the open transcript so the change takes effect immediately
        // (full render when off, windowed when on).
        if(typeof renderMessages==='function'){ try{ renderMessages({preserveScroll:true}); }catch(e){ console.warn('[virtualize_transcript] renderMessages failed on toggle:',e); } }
        _schedulePreferencesAutosave();
      },{once:false});
    }
    const showConversationOutlineCb=$('settingsShowConversationOutline');
    if(showConversationOutlineCb){
      showConversationOutlineCb.checked=settings.show_conversation_outline===true;
      window._showConversationOutline=showConversationOutlineCb.checked;
      document.documentElement.dataset.conversationOutline=window._showConversationOutline?'enabled':'disabled';
      if(typeof applyConversationOutlinePreference==='function') applyConversationOutlinePreference();
      showConversationOutlineCb.addEventListener('change',()=>{
        _schedulePreferencesAutosave();
        window._showConversationOutline=showConversationOutlineCb.checked;
        document.documentElement.dataset.conversationOutline=window._showConversationOutline?'enabled':'disabled';
        if(typeof applyConversationOutlinePreference==='function') applyConversationOutlinePreference();
      },{once:false});
    }
    const showTpsCb=$('settingsShowTps');
    if(showTpsCb){showTpsCb.checked=!!settings.show_tps;showTpsCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const pinnedLimitField=$('settingsPinnedSessionsLimit');
    if(pinnedLimitField){
      pinnedLimitField.value=parseInt(settings.pinned_sessions_limit||3,10)||3;
      window._pinnedSessionsLimit=parseInt(pinnedLimitField.value,10)||3;
      pinnedLimitField.addEventListener('change',_schedulePreferencesAutosave,{once:false});
      pinnedLimitField.addEventListener('input',()=>{window._pinnedSessionsLimit=parseInt(pinnedLimitField.value,10)||3;_schedulePreferencesAutosave();},{once:false});
    }
    const fadeTextCb=$('settingsFadeTextEffect');
    if(fadeTextCb){
      fadeTextCb.checked=!!settings.fade_text_effect;
      window._fadeTextEffect=fadeTextCb.checked;
      fadeTextCb.addEventListener('change',()=>{
        window._fadeTextEffect=fadeTextCb.checked;
        _schedulePreferencesAutosave();
      },{once:false});
    }
    const terminalAutoExpandCb=$('settingsTerminalAutoExpand');
    if(terminalAutoExpandCb){terminalAutoExpandCb.checked=!!settings.terminal_auto_expand_on_output;window._terminalAutoExpandOnOutput=terminalAutoExpandCb.checked;terminalAutoExpandCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const workspaceTodosTabCb=$('settingsWorkspaceTodosTab');
    if(workspaceTodosTabCb){
      workspaceTodosTabCb.checked=!!settings.workspace_todos_tab;
      window._workspaceTodosTab=workspaceTodosTabCb.checked;      workspaceTodosTabCb.addEventListener('change',()=>{
        window._workspaceTodosTab=workspaceTodosTabCb.checked;        _schedulePreferencesAutosave();
      },{once:false});
    }
    const apiRedactCb=$('settingsApiRedact');
    if(apiRedactCb){apiRedactCb.checked=settings.api_redact_enabled!==false;apiRedactCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const showCliCb=$('settingsShowCliSessions');
    if(showCliCb){showCliCb.checked=settings.show_cli_sessions!==false;showCliCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const showClaudeCodeCb=$('settingsShowClaudeCodeSessions');
    if(showClaudeCodeCb){
      showClaudeCodeCb.checked=!!settings.show_claude_code_sessions;
      showClaudeCodeCb.disabled=showCliCb?!showCliCb.checked:true;
      showClaudeCodeCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});
    }
    if(showCliCb){showCliCb.addEventListener('change',function(){
      const enabled=!!showCliCb.checked;
      if(showCronCb) showCronCb.disabled=!enabled;
      if(showClaudeCodeCb) showClaudeCodeCb.disabled=!enabled;
      _schedulePreferencesAutosave();
    },{once:false});}
    const showCronCb=$('settingsShowCronSessions');
    if(showCronCb){
      showCronCb.checked=!!settings.show_cron_sessions;
      showCronCb.disabled=showCliCb?!showCliCb.checked:true;
      showCronCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});
    }
    const showWebhookCb=$('settingsShowWebhookSessions');
    if(showWebhookCb){
      showWebhookCb.checked=!!settings.show_webhook_sessions;
      showWebhookCb.disabled=showCliCb?!showCliCb.checked:true;
      showWebhookCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});
      if(showCliCb){showCliCb.addEventListener('change',function(){showWebhookCb.disabled=!showCliCb.checked;},{once:false});}
    }
    const showKanbanCb=$('settingsShowKanbanSessions');
    if(showKanbanCb){
      showKanbanCb.checked=!!settings.show_kanban_sessions;
      showKanbanCb.disabled=showCliCb?!showCliCb.checked:true;
      showKanbanCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});
      if(showCliCb){showCliCb.addEventListener('change',function(){showKanbanCb.disabled=!showCliCb.checked;},{once:false});}
    }
    const showPreviousMessagingCb=$('settingsShowPreviousMessagingSessions');
    if(showPreviousMessagingCb){showPreviousMessagingCb.checked=!!settings.show_previous_messaging_sessions;showPreviousMessagingCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const syncCb=$('settingsSyncInsights');
    if(syncCb){syncCb.checked=!!settings.sync_to_insights;syncCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const updateCb=$('settingsCheckUpdates');
    if(updateCb){updateCb.checked=settings.check_for_updates!==false;updateCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const updateChannelSel=$('settingsUpdateChannel');
    if(updateChannelSel){
      updateChannelSel.value=settings.update_channel==='experimental'?'experimental':'stable';
      _confirmedUpdateChannel=updateChannelSel.value; // #6612: seed revert baseline
      updateChannelSel.addEventListener('change',function(){
        // #6612: use the dedicated channel writer so generic preference autosaves
        // from a stale tab cannot overwrite a newer explicit channel selection.
        // Update check, badge sync, and failure status are handled inside
        // _saveUpdateChannelFromSelector after the POST is confirmed.
        _saveUpdateChannelFromSelector(updateChannelSel);
      },{once:false});
    }
    const ignoreAgentUpdatesCb=$('settingsIgnoreAgentUpdates');
    if(ignoreAgentUpdatesCb){ignoreAgentUpdatesCb.checked=!!settings.ignore_agent_updates;ignoreAgentUpdatesCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const whatsNewSummaryCb=$('settingsWhatsNewSummary');
    if(whatsNewSummaryCb){whatsNewSummaryCb.checked=!!settings.whats_new_summary_enabled;whatsNewSummaryCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const soundCb=$('settingsSoundEnabled');
    if(soundCb){soundCb.checked=!!settings.sound_enabled;soundCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    // Right-to-left chat layout (#1721 salvage) — Settings-only, no composer button.
    const rtlCb=$('settingsRtl');
    if(rtlCb){
      const saved=!!settings.rtl || localStorage.getItem('agy-rtl')==='true';
      rtlCb.checked=saved;
      try{localStorage.setItem('agy-rtl',saved?'true':'false');}catch(_){}
      document.documentElement.classList.toggle('chat-content-rtl',saved);
      rtlCb.addEventListener('change',()=>{
        const on=rtlCb.checked;
        try{localStorage.setItem('agy-rtl',on?'true':'false');}catch(_){}
        document.documentElement.classList.toggle('chat-content-rtl',on);
        _schedulePreferencesAutosave();
      },{once:false});
    }
    if(typeof window._mirrorSpeechSettingsFromServer==='function') window._mirrorSpeechSettingsFromServer(settings);
    const persistedSpeechKeys = new Set(
      Array.isArray(settings && settings.persisted_speech_keys)
        ? settings.persisted_speech_keys
        : []
    );
    _captureSpeechPreferenceOwnership(settings);
    const _speechSetting=function(key,storageKey,fallback,kind){
      const stored=localStorage.getItem(storageKey);
      if(settings&&persistedSpeechKeys.has(key)) return settings[key];
      return stored===null?fallback:stored;
    };
    const _speechBool=function(key,storageKey,fallback){
      const value=_speechSetting(key,storageKey,fallback,'bool');
      return value===true||value==='true';
    };
    const rawAudioCb=$('settingsRawAudio');
    if(rawAudioCb){
      rawAudioCb.checked=_speechBool('raw_audio_mode','agy-raw-audio-mode',false);
      rawAudioCb.onchange=function(){
        _markSpeechPreferenceChanged('raw_audio_mode');
        if(typeof window._applyRawAudioModePreference==='function') window._applyRawAudioModePreference(this.checked);
        else localStorage.setItem('agy-raw-audio-mode',this.checked?'true':'false');
        _schedulePreferencesAutosave();
      };
    }
    const voiceContinuous=_speechBool('voice_continuous','agy-voice-continuous',false);
    _syncSpeechPreferenceCache('voice_continuous',voiceContinuous?'true':'false');
    const voiceSilence=parseInt(_speechSetting('voice_silence_ms','agy-voice-silence-ms',1800),10);
    _syncSpeechPreferenceCache('voice_silence_ms',Number.isFinite(voiceSilence)&&voiceSilence>=200?String(voiceSilence):'1800');
    // TTS settings use /api/settings as the durable source and localStorage as the runtime cache.
    const ttsEnabledCb=$('settingsTtsEnabled');
    if(ttsEnabledCb){ttsEnabledCb.checked=_speechBool('tts_enabled','agy-tts-enabled',false);ttsEnabledCb.onchange=function(){_markSpeechPreferenceChanged('tts_enabled');localStorage.setItem('agy-tts-enabled',this.checked?'true':'false');_applyTtsEnabled(this.checked);_schedulePreferencesAutosave();};}
    const ttsAutoReadCb=$('settingsTtsAutoRead');
    if(ttsAutoReadCb){ttsAutoReadCb.checked=_speechBool('tts_auto_read','agy-tts-auto-read',false);ttsAutoReadCb.onchange=function(){_markSpeechPreferenceChanged('tts_auto_read');localStorage.setItem('agy-tts-auto-read',this.checked?'true':'false');_schedulePreferencesAutosave();};}
    // Voice-mode button visibility (#1488).
    // Toggling re-applies immediately via the boot.js helper so the user sees
    // the audio-waveform button appear/disappear without a reload.
    // Also recomputes composer footer visibility so the .composer-divider
    // (which tracks whether all left-group buttons are hidden, see #5451)
    // stays in sync when #btnVoiceMode appears or disappears here.
    const voiceModeCb=$('settingsVoiceModeEnabled');
    if(voiceModeCb){
      voiceModeCb.checked=_speechBool('voice_mode_button','agy-voice-mode-button',false);
      voiceModeCb.onchange=function(){
        _markSpeechPreferenceChanged('voice_mode_button');
        localStorage.setItem('agy-voice-mode-button',this.checked?'true':'false');
        if(typeof window._applyVoiceModePref==='function') window._applyVoiceModePref();
        if(typeof window._applyComposerFooterVisibilitySettings==='function') window._applyComposerFooterVisibilitySettings();
        _schedulePreferencesAutosave();
      };
    }
    // TTS engine selector
    const ttsEngineSel=$('settingsTtsEngine');
    if(ttsEngineSel){
      // Re-add any extension-registered TTS engines (window.registerAgyTtsEngine)
      // as options — the <select> markup only hardcodes the built-ins, and this
      // settings panel can render after an extension registered its engine.
      if(typeof window._agyTtsEngineOptions==='function'){
        window._agyTtsEngineOptions().forEach(function(e){
          if(!ttsEngineSel.querySelector('option[value="'+e.id+'"]')){
            var opt=document.createElement('option');
            opt.value=e.id; opt.textContent=e.label;
            ttsEngineSel.appendChild(opt);
          }
        });
      }
      const saved=String(_speechSetting('tts_engine','agy-tts-engine','browser')||'browser');
      if(!ttsEngineSel.querySelector('option[value="'+saved+'"]')){
        var savedOpt=document.createElement('option');
        savedOpt.value=saved; savedOpt.textContent=saved;
        ttsEngineSel.appendChild(savedOpt);
      }
      ttsEngineSel.value=saved;
      _syncSpeechPreferenceCache('tts_engine',saved);
      ttsEngineSel.onchange=function(){
        _markSpeechPreferenceChanged('tts_engine');
        localStorage.setItem('agy-tts-engine',this.value);
        window._populateTtsVoices();
        _schedulePreferencesAutosave();
      };
    }
    // Populate voice selector based on engine
    const ttsVoiceSel=$('settingsTtsVoice');
    window._populateTtsVoices=function(){
      if(!ttsVoiceSel) return;
      const engine=localStorage.getItem('agy-tts-engine')||'browser';
      const current=String(_speechSetting('tts_voice','agy-tts-voice','')||'');
      _syncSpeechPreferenceCache('tts_voice',current);
      if(engine==='elevenlabs'){
        ttsVoiceSel.innerHTML='<option value="">Hermy — ElevenLabs (server-configured)</option>';
      } else if(engine==='openai'){
        ttsVoiceSel.innerHTML='<option value="">OpenAI voice (server-configured)</option>';
      } else if(engine==='edge'){
        const edgeVoices=[
          {value:'zh-CN-XiaoxiaoNeural',label:'Xiaoxiao (Chinese, Female)'},
          {value:'zh-CN-XiaoyiNeural',label:'Xiaoyi (Chinese, Female)'},
          {value:'zh-CN-YunxiNeural',label:'Yunxi (Chinese, Male)'},
          {value:'zh-CN-YunjianNeural',label:'Yunjian (Chinese, Male)'},
          {value:'zh-CN-YunyangNeural',label:'Yunyang (Chinese, Male)'},
          {value:'en-US-AriaNeural',label:'Aria (English, Female)'},
          {value:'en-US-GuyNeural',label:'Guy (English, Male)'},
          {value:'id-ID-GadisNeural',label:'Gadis (Indonesian, Female)'},
        ];
        ttsVoiceSel.innerHTML='<option value="">Default (Xiaoxiao)</option>';
        edgeVoices.forEach(v=>{
          const opt=document.createElement('option');
          opt.value=v.value;opt.textContent=v.label;
          if(v.value===current) opt.selected=true;
          ttsVoiceSel.appendChild(opt);
        });
      } else {
        if(!('speechSynthesis' in window)){
          ttsVoiceSel.innerHTML='<option value="">Speech synthesis not available</option>';
          return;
        }
        const voices=speechSynthesis.getVoices();
        ttsVoiceSel.innerHTML='<option value="">Default system voice</option>';
        voices.forEach(v=>{
          const opt=document.createElement('option');
          opt.value=v.name;opt.textContent=v.name+(v.lang?' ('+v.lang+')':'');
          if(v.name===current) opt.selected=true;
          ttsVoiceSel.appendChild(opt);
        });
      }
    };
    if(ttsVoiceSel&&'speechSynthesis' in window){
      window._populateTtsVoices();
      speechSynthesis.addEventListener('voiceschanged',function(){
        const engine=localStorage.getItem('agy-tts-engine')||'browser';
        if(engine==='browser') window._populateTtsVoices();
      },{once:false});
      ttsVoiceSel.onchange=function(){_markSpeechPreferenceChanged('tts_voice');localStorage.setItem('agy-tts-voice',this.value);_schedulePreferencesAutosave();};
    }
    // TTS rate/pitch sliders
    const ttsRateSlider=$('settingsTtsRate');
    const ttsRateValue=$('settingsTtsRateValue');
    if(ttsRateSlider){
      const savedRate=_speechSetting('tts_rate','agy-tts-rate',1);
      ttsRateSlider.value=(savedRate===null||savedRate===undefined)?'1':String(savedRate);
      if(ttsRateValue) ttsRateValue.textContent=parseFloat(ttsRateSlider.value).toFixed(1)+'x';
      _syncSpeechPreferenceCache('tts_rate',ttsRateSlider.value);
      ttsRateSlider.oninput=function(){_markSpeechPreferenceChanged('tts_rate');if(ttsRateValue)ttsRateValue.textContent=parseFloat(this.value).toFixed(1)+'x';localStorage.setItem('agy-tts-rate',this.value);_schedulePreferencesAutosave();};
    }
    const ttsPitchSlider=$('settingsTtsPitch');
    const ttsPitchValue=$('settingsTtsPitchValue');
    if(ttsPitchSlider){
      const savedPitch=_speechSetting('tts_pitch','agy-tts-pitch',1);
      ttsPitchSlider.value=(savedPitch===null||savedPitch===undefined)?'1':String(savedPitch);
      if(ttsPitchValue) ttsPitchValue.textContent=parseFloat(ttsPitchSlider.value).toFixed(1);
      _syncSpeechPreferenceCache('tts_pitch',ttsPitchSlider.value);
      ttsPitchSlider.oninput=function(){_markSpeechPreferenceChanged('tts_pitch');if(ttsPitchValue)ttsPitchValue.textContent=parseFloat(this.value).toFixed(1);localStorage.setItem('agy-tts-pitch',this.value);_schedulePreferencesAutosave();};
    }
    const notifCb=$('settingsNotificationsEnabled');
    if(notifCb){notifCb.checked=!!settings.notifications_enabled;notifCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    // show_thinking has no settings panel checkbox — controlled via /reasoning show|hide
    const sidebarDensitySel=$('settingsSidebarDensity');
    if(sidebarDensitySel){
      sidebarDensitySel.value=settings.sidebar_density==='detailed'?'detailed':'compact';
      sidebarDensitySel.addEventListener('change',_schedulePreferencesAutosave,{once:false});
    }
    const autoTitleRefreshSel=$('settingsAutoTitleRefresh');
    if(autoTitleRefreshSel){
      const val=String(settings.auto_title_refresh_every||'0');
      autoTitleRefreshSel.value=['0','5','10','20'].includes(val)?val:'0';
      autoTitleRefreshSel.addEventListener('change',_schedulePreferencesAutosave,{once:false});
    }
    // Default message mode
    const defaultMessageModeSel=$('settingsDefaultMessageMode');
    if(defaultMessageModeSel){
      const val=String(settings.default_message_mode||settings.busy_input_mode||'steer');
      defaultMessageModeSel.value=['queue','interrupt','steer'].includes(val)?val:'steer';
      // #5170 mirror write on panel load, under the #5145 rename.
      window._defaultMessageMode=(typeof _persistDefaultMessageMode==='function')?_persistDefaultMessageMode(defaultMessageModeSel.value):defaultMessageModeSel.value;
      defaultMessageModeSel.addEventListener('change',_schedulePreferencesAutosave,{once:false});
    }
    const showBusyPlaceholderHintCb=$('settingsShowBusyPlaceholderHint');
    if(showBusyPlaceholderHintCb){
      showBusyPlaceholderHintCb.checked=!!settings.show_busy_placeholder_hint;
      window._showBusyPlaceholderHint=showBusyPlaceholderHintCb.checked;
      showBusyPlaceholderHintCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});
    }
    if(typeof _applyBusyComposerPlaceholder==='function') _applyBusyComposerPlaceholder();
    const newChatOnWorkspaceSwitchCb=$('settingsNewChatOnWorkspaceSwitch');
    if(newChatOnWorkspaceSwitchCb){
      newChatOnWorkspaceSwitchCb.checked=!!settings.new_chat_on_workspace_switch;
      window._newChatOnWorkspaceSwitch=newChatOnWorkspaceSwitchCb.checked;
      newChatOnWorkspaceSwitchCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});
    }
    // Bot name — debounced autosave (text input)
    const botNameField=$('settingsBotName');
    if(botNameField){
      botNameField.value=settings.bot_name||'AGY';
      let botNameTimer=null;
      botNameField.addEventListener('input',()=>{
        if(botNameTimer) clearTimeout(botNameTimer);
        botNameTimer=setTimeout(_schedulePreferencesAutosave,500);
      },{once:false});
    }
    // Password field: always blank (we don't send hash back)
    const pwField=$('settingsPassword');
    if(pwField){pwField.value='';pwField.addEventListener('input',_markSettingsDirty,{once:false});}
    // #1560: when AGY_WEBUI_PASSWORD / HERMES_WEBUI_PASSWORD env var is set, the settings password
    // field silently no-ops. Disable it + reveal the lock banner so the UI
    // tells the truth before a user tries (and the backend now also returns
    // 409 as defense-in-depth).
    const pwEnvLocked=!!settings.password_env_var;
    _settingsPasswordEnvLocked=pwEnvLocked;
    const pwLockBanner=$('settingsPasswordEnvLock');
    if(pwField){
      pwField.disabled=pwEnvLocked;
      if(pwEnvLocked){
        pwField.value='';
        pwField.placeholder=t('password_env_var_locked_placeholder')||pwField.placeholder;
      }
    }
    if(pwLockBanner) pwLockBanner.style.display=pwEnvLocked?'block':'none';
    // Show auth buttons only when auth is active
    try{
      const authStatus=await api('/api/auth/status');
      _settingsPasswordAuthEnabled=!!authStatus.password_auth_enabled;
      _setSettingsAuthButtonsVisible(!!authStatus.auth_enabled);
      _syncPasswordlessButton(authStatus);
      _renderSettingsAuthStatus(authStatus);
      _updateCurrentPasswordVisibility();
      _updateAuthWarningBadge(authStatus);
      _updateAuthDisabledWarning(authStatus);
    }catch(e){}
    // #1560: env-var-locked password also disables the Disable Auth button —
    // clearing settings.password_hash is silent no-op when the env var is set,
    // and the backend now returns 409 anyway, so don't offer the action.
    // Sign Out remains available since it only clears the session cookie.
    if(pwEnvLocked){
      const disableBtn=$('btnDisableAuth');
      if(disableBtn) disableBtn.style.display='none';
    }
    _syncAgyPanelSessionActions();
    if(typeof loadDashboardSettings==='function') loadDashboardSettings();
    switchSettingsSection(_settingsSection);
  }catch(e){
    showToast(t('settings_load_failed')+e.message);
  }
}


// Stubs for removed provider, plugin, and extension gallery panels
async function loadExtensionsPanel(){ return true; }
async function loadPluginsPanel(){ return true; }
async function loadProvidersPanel(){ return true; }

let _settingsPasswordEnvLocked=false;
let _settingsPasswordAuthEnabled=false;
function _setSettingsAuthButtonsVisible(active){
  const signOutBtn=$('btnSignOut');
  if(signOutBtn) signOutBtn.style.display=active?'':'none';
  const disableBtn=$('btnDisableAuth');
  if(disableBtn) disableBtn.style.display=active?'':'none';
  const passkeyBtn=$('btnRegisterPasskey');
  if(passkeyBtn) passkeyBtn.disabled=!active||!window.PublicKeyCredential||!navigator.credentials;
}
function _syncPasswordlessButton(authStatus){
  const btn=$('btnGoPasswordless');
  if(!btn) return;
  const can=!!(authStatus&&authStatus.auth_enabled&&authStatus.password_auth_enabled&&authStatus.passkeys_count>0&&!_settingsPasswordEnvLocked);
  btn.style.display=can?'':'none';
  btn.disabled=!can;
}

function _renderSettingsAuthStatus(authStatus){
  const el=$('settingsAuthStatus');
  if(!el) return;
  if(!authStatus) { el.style.display='none'; return; }
  el.style.display='block';
  let label='',cls='detail-badge ok';
  if(authStatus.auth_enabled && authStatus.password_auth_enabled){
    label=t('auth_status_password'); cls='detail-badge ok';
  }else if(authStatus.auth_enabled && !authStatus.password_auth_enabled){
    label=t('auth_status_passkey_only'); cls='detail-badge warn';
  }else{
    label=t('auth_status_unauthenticated'); cls='detail-badge err';
  }
  el.innerHTML='<span class="'+cls+'" style="font-size:11px">'+label+'</span>';
}

function _updateCurrentPasswordVisibility(){
  const block=$('settingsCurrentPasswordBlock');
  if(!block) return;
  block.style.display=_settingsPasswordAuthEnabled?'block':'none';
}

function _updateAuthWarningBadge(authStatus){
  const badges=['authWarningBadgeDesktop','authWarningBadgeMobile'];
  const authDisabled=!authStatus||!authStatus.auth_enabled;
  const acknowledged=!!(authStatus&&authStatus.auth_disabled_acknowledged);
  badges.forEach(function(id){
    const el=$(id);
    if(!el) return;
    if(!authDisabled){ el.style.display='none'; return; }
    el.style.display='block';
    el.style.background=acknowledged?'#e8a030':'#e05';
  });
}

function _updateAuthDisabledWarning(authStatus){
  const el=$('settingsAuthDisabledWarning');
  if(!el) return;
  const authDisabled=!authStatus||!authStatus.auth_enabled;
  if(!authDisabled){ el.style.display='none'; return; }
  el.style.display='block';
  const cb=$('settingsAuthDisabledAck');
  if(cb) cb.checked=!!(authStatus&&authStatus.auth_disabled_acknowledged);
}

async function _setAuthDisabledAck(checked){
  try{
    await _enqueueSettingsPost({method:'POST',body:JSON.stringify({_auth_disabled_acknowledged:!!checked})});
    try{
      const authStatus=await api('/api/auth/status');
      _updateAuthWarningBadge(authStatus);
    }catch(e){}
  }catch(e){
    showToast(t('auth_ack_save_failed')+e.message);
  }
}

async function loadPasskeys(){ return true; }

function _applySavedSettingsUi(saved, body, opts){
  const {sendKey,showTokenUsage,showQuotaChip,showConversationOutline,showBusyPlaceholderHint,showTps,fadeTextEffect,showCliSessions,theme,skin,language,sidebarDensity,fontSize}=opts;
  window._sendKey=sendKey||'enter';
  window._showTokenUsage=showTokenUsage;
  window._showQuotaChip=showQuotaChip===true;
  window._showConversationOutline=showConversationOutline===true;
  document.documentElement.dataset.conversationOutline=window._showConversationOutline?'enabled':'disabled';
  if(typeof applyConversationOutlinePreference==='function') applyConversationOutlinePreference();
  window._showBusyPlaceholderHint=showBusyPlaceholderHint===true;
  window._showTps=showTps;
  window._fadeTextEffect=!!fadeTextEffect;
  window._showCliSessions=showCliSessions;
  window._showPreviousMessagingSessions=!!body.show_previous_messaging_sessions;
  window._soundEnabled=body.sound_enabled;
  window._notificationsEnabled=body.notifications_enabled;
  window._whatsNewSummaryEnabled=!!body.whats_new_summary_enabled;
  window._showThinking=body.show_thinking!==false;
  window._simplifiedToolCalling=true;
  _syncChatActivityDisplayModeControl(body.chat_activity_display_mode);
  _syncTransparentEventTimestampsControl(body.transparent_stream_event_timestamps, body.chat_activity_display_mode);
  window._terminalAutoExpandOnOutput=!!body.terminal_auto_expand_on_output;
  window._workspaceTodosTab=!!body.workspace_todos_tab;
  if(typeof _applyWorkspaceTodosTabVisibility==='function')  window._sessionJumpButtonsEnabled=!!body.session_jump_buttons;
  if(typeof _applySessionNavigationPrefs==='function') _applySessionNavigationPrefs();
  window._sidebarDensity=sidebarDensity==='detailed'?'detailed':'compact';
  // #5170 mirror write in _applySavedSettingsUi, under the #5145 rename:
  // persist so a reload/offline first-send honors the resolved mode.
  window._defaultMessageMode=(typeof _persistDefaultMessageMode==='function')
    ? _persistDefaultMessageMode(body.default_message_mode||body.busy_input_mode)
    : (body.default_message_mode||body.busy_input_mode||'steer');
  window._sessionEndlessScrollEnabled=!!body.session_endless_scroll;
  // #6819: only override auto-follow when the body actually carries the key.
  // A partial settings body without it must not silently re-enable follow
  // (`undefined !== false` evaluates true — the old clobber).
  if(Object.prototype.hasOwnProperty.call(body,'auto_scroll_follow')){
    window._autoScrollFollow=body.auto_scroll_follow!==false;
    if(typeof _persistAutoScrollFollow==='function') _persistAutoScrollFollow(window._autoScrollFollow);
  }
  window._largeTextPasteAsAttachment=body.large_text_paste_as_attachment!==false;
  window._projectQuickCreate=!!body.project_quick_create_buttons;
  if(Object.prototype.hasOwnProperty.call(body,'structured_code_default_view')){
    _applyStructuredCodeViewSettings(body.structured_code_default_view,body.structured_code_auto_tree_lines,false);
  }
  window._botName=body.bot_name||'AGY';
  if(typeof applyBotName==='function') applyBotName();
  else if(typeof _applyBusyComposerPlaceholder==='function') _applyBusyComposerPlaceholder();
  if(typeof setLocale==='function') setLocale(language);
  if(typeof applyLocaleToDOM==='function') applyLocaleToDOM();
  _ensureComposerControlVisibilityState(saved||body||{});
  const composerOrderSource=(saved&&Array.isArray(saved.composer_control_order))
    ? saved.composer_control_order
    : (Array.isArray(body.composer_control_order)?body.composer_control_order:null);
  if(composerOrderSource){
    const composerOrder=_setComposerControlOrder(composerOrderSource);
    if(typeof window._applyComposerControlOrder==='function') window._applyComposerControlOrder(composerOrder);
  }
  _renderComposerControlChips();
  _renderComposerSituationalControlChips();
  if(typeof _applyComposerFooterVisibilitySettings==='function') _applyComposerFooterVisibilitySettings();
  const maxTokensField=$('settingsMaxTokens');
  if(maxTokensField){
    const savedRawMaxTokens=saved&&saved.max_tokens;
    const parsedSavedMaxTokens=parseInt(savedRawMaxTokens,10);
    maxTokensField.value=(Number.isFinite(parsedSavedMaxTokens)&&parsedSavedMaxTokens>0)
      ? String(parsedSavedMaxTokens)
      : '';
    _syncSettingsMaxTokensPlaceholder(maxTokensField,saved&&saved.max_tokens_fallback);
    maxTokensField.dataset.initialValue=maxTokensField.value;
  }
  if(typeof startGatewaySSE==='function'){
    if(showCliSessions) startGatewaySSE();
    else if(typeof stopGatewaySSE==='function') stopGatewaySSE();
  }
  _setSettingsAuthButtonsVisible(!!saved.auth_enabled);
  _settingsDirty=false;
  _settingsThemeOnOpen=theme;
  _settingsSkinOnOpen=skin||'default';
  _settingsFontSizeOnOpen=fontSize||localStorage.getItem('agy-font-size')||'default';
  const bar=$('settingsUnsavedBar');
  if(bar) bar.style.display='none';
  _settingsAgyDefaultModelOnOpen=body.default_model||_settingsAgyDefaultModelOnOpen||'';
  if(Object.prototype.hasOwnProperty.call(body,'default_model_provider')) _settingsAgyDefaultModelProviderOnOpen=body.default_model_provider||null;
  // Sync window._defaultModel so newSession() uses the just-saved default without a reload (#908).
  if(body.default_model) window._defaultModel=body.default_model;
  if(Object.prototype.hasOwnProperty.call(body,'default_model_provider')) window._activeProvider=body.default_model_provider||null;
  if(typeof clearMessageRenderCache==='function') clearMessageRenderCache();
  renderMessages();
  if(typeof syncTopbar==='function') syncTopbar();
  if(typeof renderSessionList==='function') renderSessionList();
}

// Instant client-side badge feedback when the update channel is toggled, before
// the server round-trip that authoritatively re-renders the badge from
// update_channel_version. Keeps the "· Experimental" suffix in sync immediately.
function _syncUpdateChannelBadge(channel){
  try{
    const badge=$('settings-webui-version-badge');
    if(!badge) return;
    let base=badge.textContent||'';
    // Strip any existing " · Experimental" suffix, then re-append if needed.
    base=base.replace(/\s·\sExperimental\s*$/,'');
    badge.textContent = channel==='experimental' ? (base+' · Experimental') : base;
  }catch(e){}
}

async function checkUpdatesNow(channelOverride){
  const btn=$('btnCheckUpdatesNow');
  const label=$('checkUpdatesLabel');
  const spinner=$('checkUpdatesSpinner');
  const status=$('checkUpdatesStatus');
  if(!btn||!label) return;
  // Disable button, show spinner
  btn.disabled=true;
  if(spinner) spinner.style.display='';
  if(label) label.textContent=t('settings_checking');
  if(status) status.textContent='';

  try {
    // Pass the channel explicitly when the caller has one (e.g. the dropdown
    // just switched) so the check cannot race the debounced settings autosave
    // and answer for the previous channel. Omit otherwise → server uses the
    // saved setting. (Fable UX gate.)
    const _checkBody={force:true};
    if(channelOverride==='stable'||channelOverride==='experimental') _checkBody.channel=channelOverride;
    const data=await api('/api/updates/check',{method:'POST',body:JSON.stringify(_checkBody),timeoutMs:300000});
    if(data.disabled){
      if(status){status.textContent=t('settings_updates_disabled');status.style.color='var(--muted)';}
    } else {
      const errorParts=[];
      const formatUpdateError=(typeof _formatUpdateCheckError==='function')
        ? _formatUpdateCheckError
        : ((label,info)=>info&&info.error?label:null);
      const webuiError=formatUpdateError('WebUI',data.webui);
      const agentError=formatUpdateError('Agent',data.agent);
      if(webuiError) errorParts.push(webuiError);
      if(agentError) errorParts.push(agentError);
      const parts=[];
      const formatUpdatePart=(typeof _formatUpdateTargetStatus==='function')
        ? _formatUpdateTargetStatus
        : ((label,info)=>info&&info.behind>0?label+': '+info.behind:null);
      const webuiPart=formatUpdatePart('WebUI',data.webui);
      const agentPart=formatUpdatePart('Agent',data.agent);
      if(webuiPart) parts.push(webuiPart);
      if(agentPart) parts.push(agentPart);
      const manualInstruction=(typeof _formatManualUpdateInstruction==='function')
        ? _formatManualUpdateInstruction(data.webui)
        : null;
      // Track non-git targets separately so a mixed deployment (one git
      // checkout + one no-git install) never hides the "can't check" state
      // behind an up-to-date summary (#4356).
      const noGitParts=[];
      if(data.webui&&data.webui.no_git&&!data.webui.manual_update) noGitParts.push('WebUI');
      if(data.agent&&data.agent.no_git&&!data.agent.ignored) noGitParts.push('Agent');
      if(parts.length){
        let txt=t('settings_updates_available').replace('{count}',parts.join(', '));
        if(manualInstruction) txt+=' · '+manualInstruction;
        if(noGitParts.length) txt+=' · '+t('settings_update_no_git');
        if(status){status.textContent=txt;status.style.color='var(--accent)';}
        // Also trigger the update banner
        if(typeof _showUpdateBanner==='function') _showUpdateBanner(data);
      } else if(errorParts.length){
        if(status){status.textContent=t('settings_update_check_failed')+': '+errorParts.join(', ');status.style.color='var(--error)';}
      } else if(noGitParts.length){
        if(status){status.textContent=t('settings_update_no_git');status.style.color='var(--muted)';}
      } else {
        if(status){status.textContent=t('settings_up_to_date');status.style.color='var(--success)';}
        if(typeof _showUpdateBanner==='function') _showUpdateBanner(data);
      }
    }
  } catch(e){
    // Never expose raw e.message in UI — log to console for debugging only
    console.warn('[checkUpdatesNow]', e);
    // Show a generic user-facing error; if the API returned a message body use it
    let userMsg=t('settings_update_check_failed');
    if(e&&e.response){
      try{
        const body=JSON.parse(e.response);
        if(body.error) userMsg=String(body.error).substring(0,120);
      }catch(_){}
    }
    if(status){status.textContent=userMsg;status.style.color='var(--error)';}
  } finally {
    btn.disabled=false;
    if(spinner) spinner.style.display='none';
    if(label) label.textContent=t('settings_check_now');
  }
}

// ── Auxiliary Models ──────────────────────────────────────────────────────────

let _auxProviders=[];       // cached provider list from /api/models
let _auxTasks=[];           // sanitized auxiliary task configs from /api/model/auxiliary
let _auxOriginalConfig=null; // snapshot of initial config for dirty detection
let _mainAdvancedConfig=null; // current advanced config for the default chat model

function _auxSelectStyle(){
 return 'width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px;box-sizing:border-box';
}

function _auxTaskLabelFromMeta(taskKey, taskCfg){
  const nameKey='settings_aux_task_'+taskKey;
  const descKey=nameKey+'_desc';
  const tName=t(nameKey);
  const tDesc=t(descKey);
  const name=(tName&&tName!==nameKey)?String(tName).trim():'';
  const description=(tDesc&&tDesc!==descKey)?String(tDesc).trim():'';
  const fallbackName=(taskCfg&&typeof taskCfg.label==='string'&&taskCfg.label.trim())?String(taskCfg.label).trim():taskKey;
  const fallbackDesc=(taskCfg&&typeof taskCfg.description==='string'&&taskCfg.description.trim())?String(taskCfg.description).trim():'';
  return {
    task: taskKey,
    label: name||fallbackName,
    description: description||fallbackDesc,
  };
}

function _normalizeAuxiliaryTasks(rawTasks){
  const tasks=Array.isArray(rawTasks)?rawTasks:[];
  const out=[];
  const seen=new Set();
  for(const rawTask of tasks){
    if(!rawTask||typeof rawTask!=='object') continue;
    const task=(typeof rawTask.task==='string'?String(rawTask.task).trim():'');
    if(!task||seen.has(task)) continue;
    seen.add(task);
    const meta=_auxTaskLabelFromMeta(task,rawTask);
    const entry={
      task,
      provider:String(rawTask.provider||'auto').trim()||'auto',
      model:String(rawTask.model||'').trim(),
      base_url:String(rawTask.base_url||'').trim(),
      timeout:rawTask.timeout,
      download_timeout:rawTask.download_timeout,
      max_concurrency:rawTask.max_concurrency,
      extra_body:rawTask.extra_body&&typeof rawTask.extra_body==='object'?rawTask.extra_body:{},
      api_key_set:!!rawTask.api_key_set,
      label:meta.label,
      description:meta.description,
    };
    out.push(entry);
  }
  return out;
}

function _buildAuxProviderOptions(sel,providers,currentProvider){
 sel.innerHTML='';
 // "auto" = use main model
 const autoOpt=document.createElement('option');
 autoOpt.value='auto';autoOpt.textContent='auto ('+t('settings_aux_provider_auto')+')';
 if(currentProvider==='auto'||!currentProvider) autoOpt.selected=true;
 sel.appendChild(autoOpt);
 for(const p of providers){
  const opt=document.createElement('option');
  opt.value=p.slug;opt.textContent=p.name;
  if(p.slug===currentProvider) opt.selected=true;
  sel.appendChild(opt);
 }
}

function _buildAuxModelOptions(sel,provider,providers,currentModel){
 sel.innerHTML='';
 const emptyOpt=document.createElement('option');
 emptyOpt.value='';emptyOpt.textContent=t('settings_aux_model_auto')||'auto (use provider default)';
 sel.appendChild(emptyOpt);
 if(!provider||provider==='auto'){
  sel.value=currentModel||'';
  return;
 }
 // Find matching provider in cached list
 const pData=providers.find(p=>p.slug===provider);
 if(pData&&pData.models){
  for(const mId of pData.models){
   const opt=document.createElement('option');
   opt.value=mId;opt.textContent=mId;
   if(mId===currentModel) opt.selected=true;
   sel.appendChild(opt);
  }
 }
 // Always allow custom model — add a text input option hint
 const customOpt=document.createElement('option');
 customOpt.value='__custom__';customOpt.textContent=t('settings_aux_model_custom')||'Custom model…';
 sel.appendChild(customOpt);
 // If currentModel not in list and not empty, add it as a custom option
 if(currentModel&&!pData?.models?.includes(currentModel)){
  const existingOpt=document.createElement('option');
  existingOpt.value=currentModel;existingOpt.textContent=currentModel+' (configured)';
  existingOpt.selected=true;
  sel.insertBefore(existingOpt,customOpt);
 }
}

function _onAuxProviderChange(taskKey,providers){
 const provSel=$('aux-prov-'+taskKey);
 const modelSel=$('aux-model-'+taskKey);
 if(!provSel||!modelSel) return;
 const provider=provSel.value;
 _buildAuxModelOptions(modelSel,provider,providers,'');
 _markAuxDirty();
}

async function _onAuxModelChange(taskKey){
 const modelSel=$('aux-model-'+taskKey);
 if(!modelSel) return;
 if(modelSel.value==='__custom__'){
  const customModel=await showPromptDialog({title:t('settings_aux_model_custom')||'Custom model',message:t('settings_aux_model_custom_prompt')||'Enter model ID:',placeholder:'model/provider:model-id',confirmLabel:t('settings_btn_apply_aux_models')||'Apply'});
  if(customModel&&customModel.trim()){
   // Insert custom model option before the __custom__ option
   const opt=document.createElement('option');
   opt.value=customModel.trim();opt.textContent=customModel.trim();
   // Remove __custom__ selection
   const customIdx=[...modelSel.options].findIndex(o=>o.value==='__custom__');
   if(customIdx>=0) modelSel.insertBefore(opt,modelSel.options[customIdx]);
   modelSel.value=customModel.trim();
  }else{
   modelSel.value='';
  }
 }
 _markAuxDirty();
}

function _markAuxDirty(){
 const applyBtn=$('btnApplyAuxModels');
 if(applyBtn) applyBtn.style.display='';
 _markSettingsDirty();
}

function _auxAdvancedValue(cfg,key){
 const v=cfg&&Object.prototype.hasOwnProperty.call(cfg,key)?cfg[key]:'';
 return v===null||v===undefined?'':String(v);
}

function _ensureAuxAdvancedModal(){
 let overlay=$('auxAdvancedOverlay');
 if(overlay) return overlay;
 overlay=document.createElement('div');
 overlay.id='auxAdvancedOverlay';
 overlay.style.cssText='position:fixed;inset:0;z-index:9999;background:rgba(5,7,15,.68);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;padding:20px';
 const neutralBtn='font-size:12px;padding:7px 12px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--text);cursor:pointer;font-weight:600';
 const primaryBtn='font-size:12px;padding:7px 12px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#1a1a1a;cursor:pointer;font-weight:700';
 overlay.innerHTML=`<div role="dialog" aria-modal="true" aria-labelledby="auxAdvancedTitle" style="width:min(620px,calc(100vw - 32px));max-height:calc(100vh - 48px);overflow:auto;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:14px;box-shadow:0 18px 60px rgba(0,0,0,.45);padding:16px">
  <style>#auxAdvancedOverlay input:-webkit-autofill,#auxAdvancedOverlay textarea:-webkit-autofill{box-shadow:0 0 0 1000px var(--code-bg) inset!important;-webkit-box-shadow:0 0 0 1000px var(--code-bg) inset!important;-webkit-text-fill-color:var(--text)!important;caret-color:var(--text)!important}</style>
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px">
   <div><div id="auxAdvancedTitle" style="font-weight:700;font-size:16px"></div><div id="auxAdvancedSubtitle" style="font-size:11px;color:var(--muted);margin-top:2px"></div></div>
   <button type="button" id="auxAdvancedClose" aria-label="${esc(t('terminal_close')||'Close')}" style="width:28px;height:28px;display:inline-flex;align-items:center;justify-content:center;border-radius:8px;border:1px solid var(--border);background:var(--input-bg);color:var(--text);cursor:pointer;font-size:18px;line-height:1">×</button>
  </div>
  <div id="auxAdvancedBody" style="display:grid;gap:10px"></div>
  <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
   <button type="button" id="auxAdvancedCancel" style="${neutralBtn}">${esc(t('cancel')||'Cancel')}</button>
   <button type="button" id="auxAdvancedSave" style="${primaryBtn}">${esc(t('settings_aux_advanced_save')||'Save options')}</button>
  </div>
 </div>`;
 document.body.appendChild(overlay);
 const close=()=>{overlay.style.display='none';overlay.dataset.task='';};
 $('auxAdvancedClose')?.addEventListener('click',close);
 $('auxAdvancedCancel')?.addEventListener('click',close);
 overlay.addEventListener('click',ev=>{if(ev.target===overlay) close();});
 return overlay;
}

function _auxAdvancedInputHtml(id,label,value,desc,type='text',extraAttrs='',extraStyle=''){
 const fieldName=id==='auxAdvancedApiKey'?'aux-manual-override-value':('aux-field-'+id.replace(/^auxAdvanced/,'').toLowerCase());
 const autocompleteAttr=/\bautocomplete=/.test(extraAttrs)?'':'autocomplete="off"';
 const inputAttrs=`id="${id}" name="${fieldName}" type="${type}" value="${esc(value)}" ${autocompleteAttr} autocapitalize="off" autocorrect="off" spellcheck="false" data-lpignore="true" data-1p-ignore="true" ${extraAttrs}`;
 return `<label style="display:grid;gap:4px;font-size:12px;color:var(--text)"><span style="font-weight:600">${esc(label)}</span><input ${inputAttrs} style="width:100%;box-sizing:border-box;padding:7px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px${extraStyle}"><span style="font-size:10px;color:var(--muted);line-height:1.35">${esc(desc)}</span></label>`;
}

function _mainModelSupportsServiceTier(cfg){
 const selected=$('settingsModel');
 const selectedOpt=selected&&selected.selectedIndex>=0?selected.options[selected.selectedIndex]:null;
 const optgroup=selectedOpt&&selectedOpt.parentElement&&selectedOpt.parentElement.tagName==='OPTGROUP'?selectedOpt.parentElement:null;
 const provider=((selectedOpt&&selectedOpt.dataset&&selectedOpt.dataset.provider)||(optgroup&&optgroup.dataset&&optgroup.dataset.provider)||(cfg&&cfg.provider)||'').trim().toLowerCase();
 if(provider!=='openai'&&provider!=='openai-api'&&provider!=='openai-codex') return false;
 const fastSupport = selectedOpt&&selectedOpt.dataset?selectedOpt.dataset.fast:'';
 if(fastSupport) return fastSupport==='1'||fastSupport==='true';
 return cfg&&cfg.supports_fast_tier===true;
}

function _openAuxAdvancedOptions(taskCfg,cfg){
 const isMain=taskCfg==='__main__';
 const taskKey=isMain?'__main__':(taskCfg&&typeof taskCfg==='object'&&typeof taskCfg.task==='string'?taskCfg.task:typeof taskCfg==='string'?taskCfg:'');
 const slot=isMain?{task:taskKey,label:(t('settings_label_model')||'Default model')}:_auxTaskLabelFromMeta(taskKey,taskCfg);
 const overlay=_ensureAuxAdvancedModal();
 overlay.dataset.task=taskKey;
 const title=$('auxAdvancedTitle'),sub=$('auxAdvancedSubtitle'),body=$('auxAdvancedBody');
 const slotName=isMain?(t('settings_label_model')||'Default model'):(slot&&slot.label)||taskKey;
 if(title) title.textContent=isMain?(t('settings_main_advanced_title')||'Main model options'):((t('settings_aux_advanced_title')||'{task} options').replace('{task}',slotName));
 if(sub) sub.textContent=isMain?(t('settings_main_advanced_subtitle')||'Advanced config for the default chat model.'):(t('settings_aux_advanced_subtitle')||'Advanced config for auxiliary.');
 const extraBody=cfg&&cfg.extra_body&&typeof cfg.extra_body==='object'&&Object.keys(cfg.extra_body).length?JSON.stringify(cfg.extra_body,null,2):'';
 const apiKeyHint=cfg&&cfg.api_key_set?(t('settings_aux_advanced_api_key_set_hint')||'API key is set. Leave blank to keep it, or use clear to remove it.'):(t('settings_aux_advanced_api_key_empty_hint')||'Leave blank to use provider/default credentials.');
 if(body){
  const selectedServiceTier=((cfg&&cfg.service_tier)||'').trim().toLowerCase()==='priority'?'priority':'';
  const serviceTierField=isMain&&_mainModelSupportsServiceTier(cfg)
   ? `<label style="display:grid;gap:4px;font-size:12px;color:var(--text)"><span style="font-weight:600">${esc(t('settings_main_advanced_service_tier')||'Service tier')}</span><select id="auxAdvancedServiceTier" style="width:100%;box-sizing:border-box;padding:7px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"><option value=""${selectedServiceTier?'':' selected'}>${esc(t('settings_main_advanced_service_tier_default')||'Default / off')}</option><option value="priority"${selectedServiceTier==='priority'?' selected':''}>${esc(t('settings_main_advanced_service_tier_priority')||'Priority (fast)')}</option></select><span style="font-size:10px;color:var(--muted);line-height:1.35">${esc(t('settings_main_advanced_service_tier_desc')||'Optional request setting for OpenAI-family providers.')}</span></label>`
   : '';
  const timingFields=isMain?'':(
   _auxAdvancedInputHtml('auxAdvancedTimeout',t('settings_aux_advanced_timeout')||'Timeout seconds',_auxAdvancedValue(cfg,'timeout'),t('settings_aux_advanced_timeout_desc')||'Request timeout for this auxiliary task. Blank uses AGY default.','number','inputmode="numeric" min="1" step="1"')+
   _auxAdvancedInputHtml('auxAdvancedDownloadTimeout',t('settings_aux_advanced_download_timeout')||'Download timeout seconds',_auxAdvancedValue(cfg,'download_timeout'),t('settings_aux_advanced_download_timeout_desc')||'Only relevant for tasks that download media/content, e.g. vision. Blank uses default.','number','inputmode="numeric" min="1" step="1"')+
   _auxAdvancedInputHtml('auxAdvancedMaxConcurrency',t('settings_aux_advanced_max_concurrency')||'Max concurrency',_auxAdvancedValue(cfg,'max_concurrency'),t('settings_aux_advanced_max_concurrency_desc')||'Optional per-task concurrency limit. Blank uses default.','number','inputmode="numeric" min="1" step="1"'));
  body.innerHTML=
   _auxAdvancedInputHtml('auxAdvancedBaseUrl',t('settings_aux_advanced_base_url')||'Base URL',_auxAdvancedValue(cfg,'base_url'),t('settings_aux_advanced_base_url_desc')||'Optional provider endpoint override.','text','inputmode="url"')+
   serviceTierField+
   timingFields+
    `<label style="display:grid;gap:4px;font-size:12px;color:var(--text)"><span style="font-weight:600">${esc(t('settings_aux_advanced_extra_body')||'Extra body JSON')}</span><textarea id="auxAdvancedExtraBody" rows="6" style="width:100%;box-sizing:border-box;padding:7px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px;font-family:var(--font-mono)">${esc(extraBody)}</textarea><span style="font-size:10px;color:var(--muted);line-height:1.35">${esc(t('settings_aux_advanced_extra_body_desc')||'Optional JSON object merged into the model request body.')}</span></label>`+
   _auxAdvancedInputHtml('auxAdvancedApiKey',t('settings_aux_advanced_api_key')||'API key override','',apiKeyHint,'text','autocomplete="one-time-code" inputmode="text" readonly onfocus="this.removeAttribute(&quot;readonly&quot;)"',';-webkit-text-security:disc')+
   `<label style="display:${cfg&&cfg.api_key_set?'flex':'none'};align-items:center;gap:8px;font-size:12px;color:var(--text)"><input id="auxAdvancedApiKeyClear" type="checkbox" style="width:15px;height:15px;accent-color:var(--accent)"><span>${esc(t('settings_aux_advanced_api_key_clear')||'Clear existing API key override')}</span></label>`;
 }
 const save=$('auxAdvancedSave');
 if(save){
  save.onclick=async()=>{
   let extra={};
   const extraText=($('auxAdvancedExtraBody')?.value||'').trim();
   if(extraText){
    try{extra=JSON.parse(extraText);}catch(e){if(typeof showToast==='function') showToast(t('settings_aux_advanced_extra_body_invalid_json')||'Extra body must be valid JSON');return;}
    if(!extra||Array.isArray(extra)||typeof extra!=='object'){if(typeof showToast==='function') showToast(t('settings_aux_advanced_extra_body_object_required')||'Extra body must be a JSON object');return;}
   }
   const provSel=isMain?null:$('aux-prov-'+taskKey),modelSel=isMain?$('settingsModel'):$('aux-model-'+taskKey);
   const provider=isMain?((cfg&&cfg.provider)||''):(provSel?provSel.value:((cfg&&cfg.provider)||'auto'));
   const model=modelSel&&modelSel.value!=='__custom__'?(modelSel.value||''):((cfg&&cfg.model)||'');
   const advanced={
    base_url:$('auxAdvancedBaseUrl')?.value||'',
    extra_body:extra,
    api_key:$('auxAdvancedApiKey')?.value||'',
    api_key_clear:!!($('auxAdvancedApiKeyClear')&&$('auxAdvancedApiKeyClear').checked),
   };
   if(isMain&&$('auxAdvancedServiceTier')){
    advanced.service_tier=$('auxAdvancedServiceTier')?.value||'';
   }
   if(!isMain){
    advanced.timeout=$('auxAdvancedTimeout')?.value||'';
    advanced.download_timeout=$('auxAdvancedDownloadTimeout')?.value||'';
    advanced.max_concurrency=$('auxAdvancedMaxConcurrency')?.value||'';
   }
   try{
    await api('/api/model/set',{method:'POST',body:JSON.stringify({scope:isMain?'main':'auxiliary',task:isMain?'':taskKey,provider,model,advanced})});
    if(typeof showToast==='function') showToast(isMain?(t('settings_main_advanced_saved')||'Main model options saved'):(t('settings_aux_advanced_saved')||'Auxiliary options saved'));
    overlay.style.display='none';
    _loadAuxiliaryModels();
    // #4650 review: a main-model advanced save can change base_url, which
    // /api/reasoning's answer depends on for some providers (e.g. LM Studio),
    // WITHOUT changing the model/provider cache key. Invalidate the reasoning
    // cache and refresh so the chip reflects the new config (one refetch).
    if(isMain){
      if(typeof _lastReasoningFetchKey!=='undefined') _lastReasoningFetchKey=null;
      if(typeof fetchReasoningChip==='function') fetchReasoningChip();
    }
   }catch(e){
    if(typeof showToast==='function') showToast(isMain?(t('settings_main_advanced_save_failed')||'Failed to save main model options'):(t('settings_aux_advanced_save_failed')||'Failed to save auxiliary options'));
   }
  };
 }
 overlay.style.display='flex';
 setTimeout(()=>$('auxAdvancedBaseUrl')?.focus(),0);
}

function _bindMainAdvancedOptionsButton(){
 const modelSel=$('settingsModel');
 let btn=$('mainAdvancedBtn');
 if(modelSel){
  const parent=modelSel.parentElement;
  let row=parent&&parent.classList&&parent.classList.contains('model-advanced-row')?parent:null;
  if(!row){
   row=document.createElement('div');
   row.className='model-advanced-row';
   parent.insertBefore(row,modelSel);
   row.appendChild(modelSel);
  }
  if(!btn){
   btn=document.createElement('button');
   btn.type='button';
   btn.id='mainAdvancedBtn';
  }
  if(btn.parentElement!==row) row.appendChild(btn);
  row.style.cssText='display:grid;grid-template-columns:minmax(0,1fr) 34px;gap:8px;align-items:center';
  modelSel.style.width='100%';
  modelSel.style.minWidth='0';
  modelSel.style.boxSizing='border-box';
 }
 if(!btn) return;
 btn.classList.add('model-advanced-btn');
 if(!btn.querySelector('svg')&&typeof li==='function') btn.innerHTML=li('settings',15);
 btn.style.position='';
 btn.style.right='';
 btn.style.top='';
 btn.style.transform='';
 btn.style.width='32px';
 btn.style.height='32px';
 btn.style.display='flex';
 btn.style.alignItems='center';
 btn.style.justifyContent='center';
 btn.style.flex='0 0 32px';
 btn.style.boxSizing='border-box';
 const title=t('settings_aux_advanced_button_title')||'Advanced options';
 btn.title=title;
 btn.setAttribute('aria-label',t('settings_main_advanced_button_aria')||'Advanced options for main model');
 btn.disabled=_mainAdvancedConfig===null;
 btn.style.opacity='';
 btn.style.cursor='';
 if(btn._bound) return;
 btn._bound=true;
 btn.addEventListener('click',()=>{if(_mainAdvancedConfig!==null)_openAuxAdvancedOptions('__main__',_mainAdvancedConfig||{});});
}

async function _loadAuxiliaryModels(){
 const container=$('auxModelsContainer');
 if(!container) return;
 container.innerHTML='<div style="color:var(--muted);font-size:12px">'+(t('settings_aux_loading')||'Loading…')+'</div>';

 try{
  // Fetch auxiliary config AND the WebUI's own /api/models for provider/model lists
  const [auxData,modelsData]=await Promise.all([
   api('/api/model/auxiliary').catch(()=>null),
   api('/api/models').catch(()=>null),
  ]);
  // Build provider list from /api/models groups
  // /api/models returns: { groups: [{ provider: str, provider_id: str, models: [{id,label}] }] }
  const groups=(modelsData&&modelsData.groups)||[];
  _auxProviders=groups.filter(g=>g.provider&&((g.models&&g.models.length>0)||(g.extra_models&&g.extra_models.length>0))).map(g=>({
   slug:g.provider_id||g.provider,
   name:g.provider,
   models:[...(g.models||[]),...(g.extra_models||[])].map(m=>m.id),
  }));
  if(auxData&&Object.prototype.hasOwnProperty.call(auxData,'main')){
   _mainAdvancedConfig=auxData.main||{};
  }else{
   _mainAdvancedConfig=null;
  }
  _bindMainAdvancedOptionsButton();
  _auxTasks=_normalizeAuxiliaryTasks((auxData&&auxData.tasks)||[]);
  // Build a quick lookup: taskKey → config
  const taskMap={};
  for(const task of _auxTasks) taskMap[task.task]=task;
  _auxOriginalConfig=JSON.parse(JSON.stringify(taskMap));

  container.innerHTML='';
  for(const task of _auxTasks){
   const cfg=taskMap[task.task]||{provider:'auto',model:''};
   const row=document.createElement('div');
   row.style.cssText='display:grid;grid-template-columns:120px 1fr 1fr 34px;gap:8px;align-items:center;margin-bottom:8px';

   // Task name + description
   const label=document.createElement('div');
   label.style.cssText='font-size:12px;font-weight:500;color:var(--text);line-height:1.3';
   label.innerHTML=esc(task.label||task.task)+'<div style="font-size:10px;color:var(--muted);font-weight:400">'+esc(task.description||'')+'</div>';
   row.appendChild(label);

   // Provider select
   const provSel=document.createElement('select');
   provSel.id='aux-prov-'+task.task;
   provSel.style.cssText=_auxSelectStyle();
   _buildAuxProviderOptions(provSel,_auxProviders,cfg.provider);
   provSel.addEventListener('change',()=>_onAuxProviderChange(task.task,_auxProviders));
   row.appendChild(provSel);

   // Model select
   const modelSel=document.createElement('select');
   modelSel.id='aux-model-'+task.task;
   modelSel.style.cssText=_auxSelectStyle();
   _buildAuxModelOptions(modelSel,cfg.provider,_auxProviders,cfg.model);
   modelSel.addEventListener('change',()=>_onAuxModelChange(task.task));
   row.appendChild(modelSel);

   const advancedBtn=document.createElement('button');
   advancedBtn.type='button';
   advancedBtn.className='aux-advanced-btn model-advanced-btn';
   const advTitle=t('settings_aux_advanced_button_title')||'Advanced options';
   const taskName=task.label||task.task;
   advancedBtn.title=advTitle;
   advancedBtn.setAttribute('aria-label',(t('settings_aux_advanced_button_aria')||'Advanced options for {task}').replace('{task}',taskName));
   advancedBtn.innerHTML=typeof li==='function'?li('settings',15):'⚙';
   advancedBtn.addEventListener('click',()=>_openAuxAdvancedOptions(task,cfg));
   row.appendChild(advancedBtn);

   container.appendChild(row);
  }
  // Hide apply button (no changes yet)
  const applyBtn=$('btnApplyAuxModels');
  if(applyBtn) applyBtn.style.display='none';

  // Reset button
  const resetBtn=$('btnResetAuxModels');
  if(resetBtn&&!resetBtn._bound){
   resetBtn._bound=true;
   resetBtn.addEventListener('click',async()=>{
    if(!(await showConfirmDialog({title:t('settings_aux_reset_confirm_title')||'Reset auxiliary models?',message:t('settings_aux_reset_confirm_msg')||'This will set all auxiliary tasks to auto (use main model).',confirmLabel:t('settings_btn_reset_aux_models')||'Reset',danger:true}))) return;
    try{
     await api('/api/model/set',{method:'POST',body:JSON.stringify({scope:'auxiliary',task:'__reset__',provider:'auto',model:''})});
     if(typeof showToast==='function') showToast(t('settings_aux_reset_done')||'Auxiliary models reset to auto');
     _loadAuxiliaryModels();
    }catch(e){
     if(typeof showToast==='function') showToast(t('settings_aux_save_failed')||'Failed to reset auxiliary models');
    }
   });
  }

  // Apply button
  if(applyBtn&&!applyBtn._bound){
   applyBtn._bound=true;
   applyBtn.addEventListener('click',_applyAuxModels);
  }
 }catch(e){
  console.warn('[settings] auxiliary models load failed',e);
  container.innerHTML='<div style="color:var(--muted);font-size:12px">'+(t('settings_aux_load_failed')||'Could not load auxiliary model settings. Make sure the agent API is available.')+'</div>';
 }
}

async function _applyAuxModels(){
 let saved=0;
 for(const task of _auxTasks){
  const provSel=$('aux-prov-'+task.task);
  const modelSel=$('aux-model-'+task.task);
  if(!provSel) continue;
  const provider=provSel.value;
  const model=(modelSel&&modelSel.value!=='__custom__')?(modelSel.value||''):'';
  const orig=_auxOriginalConfig?.[task.task]||{provider:'auto',model:''};
  // Only save if changed
  if(provider!==orig.provider||model!==orig.model){
   try{
    await api('/api/model/set',{method:'POST',body:JSON.stringify({scope:'auxiliary',task:task.task,provider,model})});
    saved++;
   }catch(e){
    console.warn('[settings] failed to save aux task',task.task,e);
    // Surface the server's actionable message (e.g. an ambiguous custom-provider
    // slug collision: rename one provider so its slug is unique) instead of a
    // generic failure, and abort the loop so the dirty selection is retained for
    // the user to fix and retry — the reload that would clear it is skipped.
    const _msg=(e&&e.message)?e.message:'';
    const _base=t('settings_aux_save_failed')||'Failed to save auxiliary model';
    if(typeof showToast==='function') showToast(_msg?(_base+': '+_msg):_base,6000,'error');
    return;
   }
  }
 }
 if(typeof showToast==='function') showToast(saved?(t('settings_aux_saved')||'Auxiliary models updated'):(t('settings_aux_no_changes')||'No changes to apply'));
 // Reload to refresh state
 _loadAuxiliaryModels();
}

async function saveSettings(andClose){
  const model=($('settingsModel')||{}).value;
  const modelState=(typeof _captureModelDropdownSelection==='function'&&$('settingsModel'))
    ? (_captureModelDropdownSelection($('settingsModel'))||{model:String(model||''),model_provider:null})
    : {model:String(model||''),model_provider:null};
  const modelChanged=(model||'')!==(_settingsAgyDefaultModelOnOpen||'')||((modelState.model_provider||null)!==(_settingsAgyDefaultModelProviderOnOpen||null));
  const sendKey=($('settingsSendKey')||{}).value;
  const showTokenUsage=!!($('settingsShowTokenUsage')||{}).checked;
  const showQuotaChip=!!($('settingsShowQuotaChip')||{}).checked;
  const showConversationOutline=!!($('settingsShowConversationOutline')||{}).checked;
  const showTps=!!($('settingsShowTps')||{}).checked;
  const fadeTextEffect=!!($('settingsFadeTextEffect')||{}).checked;
  const showCliSessions=!!($('settingsShowCliSessions')||{}).checked;
  const showClaudeCodeSessions=!!($('settingsShowClaudeCodeSessions')||{}).checked;
  const showCronSessions=!!($('settingsShowCronSessions')||{}).checked;
  const showWebhookSessions=!!($('settingsShowWebhookSessions')||{}).checked;
  const showKanbanSessions=!!($('settingsShowKanbanSessions')||{}).checked;
  const showPreviousMessagingSessions=!!($('settingsShowPreviousMessagingSessions')||{}).checked;
  const pinnedSessionsLimit=parseInt(($('settingsPinnedSessionsLimit')||{}).value,10)||3;
  const pw=($('settingsPassword')||{}).value;
  const theme=($('settingsTheme')||{}).value||'dark';
  const skin=($('settingsSkin')||{}).value||'default';
  const fontSize=($('settingsFontSize')||{}).value||localStorage.getItem('agy-font-size')||'default';
  const language=($('settingsLanguage')||{}).value||'en';
  const sidebarDensity=($('settingsSidebarDensity')||{}).value==='detailed'?'detailed':'compact';
  const defaultMessageMode=($('settingsDefaultMessageMode')||{}).value||'steer';
  const showBusyPlaceholderHint=!!($('settingsShowBusyPlaceholderHint')||{}).checked;
  const body={};
  Object.assign(body,_speechPreferencesPayloadFromUi());

  if(sendKey) body.send_key=sendKey;
  body.theme=theme;
  body.skin=skin;
  body.font_size=fontSize;
  body.session_jump_buttons=!!($('settingsSessionJumpButtons')||{}).checked;
  body.session_endless_scroll=!!($('settingsSessionEndlessScroll')||{}).checked;
  body.chat_activity_display_mode=((($('settingsChatActivityDisplayMode')||{}).value==='transparent_stream')
    ||(($('settingsChatActivityDisplayMode')||{}).value==='hide_all_activity'))
    ? ($('settingsChatActivityDisplayMode')||{}).value
    : 'compact_worklog';
  body.transparent_stream_event_timestamps=(($('settingsTransparentEventTimestamps')||{}).checked)!==false;
  body.auto_scroll_follow=!!($('settingsAutoScrollFollow')||{}).checked;
  body.render_user_markdown=!!($('settingsRenderUserMarkdown')||{}).checked;
  body.large_text_paste_as_attachment=!!($('settingsLargeTextPasteAsAttachment')||{}).checked;
  body.project_quick_create_buttons=!!($('settingsProjectQuickCreate')||{}).checked;
  Object.assign(body,_structuredCodeViewFromUi());
  Object.assign(body,_composerControlVisibilityPayload());
  body.composer_control_order=_getComposerControlOrder();
  body.language=language;
  body.show_token_usage=showTokenUsage;
  const maxTokensField=$('settingsMaxTokens');
  if(maxTokensField){
    const maxTokensRaw=String(maxTokensField.value||'').trim();
    const initialMaxTokens=String(maxTokensField.dataset.initialValue||'').trim();
    if(maxTokensRaw!==initialMaxTokens){
      body.max_tokens=maxTokensRaw===''?null:maxTokensRaw;
    }
  }
  body.show_quota_chip=showQuotaChip===true;
  body.show_conversation_outline=showConversationOutline===true;
  body.show_busy_placeholder_hint=showBusyPlaceholderHint===true;
  body.show_tps=showTps;
  body.fade_text_effect=fadeTextEffect;
  body.terminal_auto_expand_on_output=!!($('settingsTerminalAutoExpand')||{}).checked;
  body.workspace_todos_tab=!!window._workspaceTodosTab;
  body.api_redact_enabled=!!($('settingsApiRedact')||{}).checked;
  body.show_cli_sessions=showCliSessions;
  // Persist the opt-out child independently; the read path applies the parent gate.
  body.show_claude_code_sessions=showClaudeCodeSessions;
  // Cron and webhook sessions are gated on CLI sessions (server short-circuits otherwise);
  // mirror the autosave path so the explicit Save Settings button persists them too. (#3514)
  body.show_cron_sessions=showCliSessions&&showCronSessions;
  body.show_webhook_sessions=showCliSessions&&showWebhookSessions;
  body.show_kanban_sessions=showCliSessions&&showKanbanSessions;
  body.show_previous_messaging_sessions=showPreviousMessagingSessions;
  body.pinned_sessions_limit=pinnedSessionsLimit;
  body.sync_to_insights=!!($('settingsSyncInsights')||{}).checked;
  body.check_for_updates=!!($('settingsCheckUpdates')||{}).checked;
  body.ignore_agent_updates=!!($('settingsIgnoreAgentUpdates')||{}).checked;
  body.whats_new_summary_enabled=!!($('settingsWhatsNewSummary')||{}).checked;
  body.sound_enabled=!!($('settingsSoundEnabled')||{}).checked;
  body.rtl=!!($('settingsRtl')||{}).checked;
  body.notifications_enabled=!!($('settingsNotificationsEnabled')||{}).checked;
  body.show_thinking=window._showThinking!==false;
  body.sidebar_density=sidebarDensity;
  body.default_message_mode=defaultMessageMode;
  body.auto_title_refresh_every=(($('settingsAutoTitleRefresh')||{}).value||'0');
  const botName=(($('settingsBotName')||{}).value||'').trim();
  body.bot_name=botName||'AGY';
  // Password: only act if the field has content; blank = leave auth unchanged
  if(pw && pw.trim()){
    const currentPwField=$('settingsCurrentPassword');
    const currentPw=(currentPwField||{}).value||'';
    if(_settingsPasswordAuthEnabled && !currentPw.trim()){
      if(currentPwField) currentPwField.focus();
      showToast(t('current_password_required'));
      return;
    }
    const payload={...body,_set_password:pw.trim()};
    if(_settingsPasswordAuthEnabled) payload._current_password=currentPw;
    try{
      const saved=await _enqueueSettingsPost({method:'POST',body:JSON.stringify(payload)});
      if(modelChanged && model){
        try{
        await api('/api/default-model',{method:'POST',body:JSON.stringify({model,provider:modelState.model_provider||null})});
        body.default_model=model;
        body.default_model_provider=(modelState&&modelState.model===model)?(modelState.model_provider||null):null;
        }catch(_modelErr){
          // A 400 here (e.g. an ambiguous custom-provider slug collision: rename
          // one provider) is user-fixable, not a partial success. Surface the
          // message, abort before "settings saved", and retain dirty state so the
          // user can fix and retry instead of the error being swallowed.
          const _msg=(_modelErr&&_modelErr.message)?_modelErr.message:'';
          if(typeof showToast==='function') showToast('Failed to update default model'+(_msg?(': '+_msg):''),6000,'error');
          return;
        }
      }
      _applySavedSettingsUi(saved, body, {sendKey,showTokenUsage,showQuotaChip,showConversationOutline,showBusyPlaceholderHint,showTps,fadeTextEffect,showCliSessions,theme,skin,language,sidebarDensity,fontSize});
      showToast(t(saved.auth_just_enabled?'settings_saved_pw':'settings_saved_pw_updated'));
      const cpField=$('settingsCurrentPassword'); if(cpField) cpField.value='';
      const pwField=$('settingsPassword'); if(pwField) pwField.value='';
      _settingsPasswordAuthEnabled=!!saved.password_auth_enabled;
      _updateCurrentPasswordVisibility();
      try{
        const authStatus=await api('/api/auth/status');
        _renderSettingsAuthStatus(authStatus);
        _updateAuthWarningBadge(authStatus);
        _updateAuthDisabledWarning(authStatus);
      }catch(e){}
      _settingsDirty=false;
      _resetSettingsPanelState();
      if(!andClose) _pendingSettingsTargetPanel = null;
      if(andClose) _hideSettingsPanel();
      return;
    }catch(e){showToast(t('settings_save_failed')+e.message);return;}
  }
  try{
    const saved=await _enqueueSettingsPost({method:'POST',body:JSON.stringify(body)});
    if(modelChanged && model){
      try{
        await api('/api/default-model',{method:'POST',body:JSON.stringify({model,provider:modelState.model_provider||null})});
        body.default_model=model;
        body.default_model_provider=(modelState&&modelState.model===model)?(modelState.model_provider||null):null;
        }catch(_modelErr){
          // A 400 here (e.g. an ambiguous custom-provider slug collision: rename
          // one provider) is user-fixable, not a partial success. Surface the
          // message, abort before "settings saved", and retain dirty state so the
          // user can fix and retry instead of the error being swallowed.
          const _msg=(_modelErr&&_modelErr.message)?_modelErr.message:'';
          if(typeof showToast==='function') showToast('Failed to update default model'+(_msg?(': '+_msg):''),6000,'error');
          return;
        }
    }
    _applySavedSettingsUi(saved, body, {sendKey,showTokenUsage,showQuotaChip,showConversationOutline,showBusyPlaceholderHint,showTps,fadeTextEffect,showCliSessions,theme,skin,language,sidebarDensity,fontSize});
    showToast(t('settings_saved'));
    _settingsDirty=false;
    _resetSettingsPanelState();
    if(!andClose) _pendingSettingsTargetPanel = null;
    if(andClose) _hideSettingsPanel();
  }catch(e){
    showToast(t('settings_save_failed')+e.message);
  }
}

async function signOut(){
  try{
    const response=await api('/api/auth/logout',{method:'POST',body:'{}'});
    window.location.href=response.trusted_logout_url||'login';
  }catch(e){
    showToast(t('sign_out_failed')+e.message);
  }
}

async function goPasswordless(){
  const ok=await showConfirmDialog({title:'Go passwordless?',message:'This removes the password and keeps passkey sign-in enabled. Keep at least one passkey registered or you could lose access.',confirmLabel:'Go passwordless',danger:false,focusCancel:true});
  if(!ok) return;
  const currentPw=($('settingsCurrentPassword')||{}).value;
  const payload={_passwordless:true};
  if(_settingsPasswordAuthEnabled && currentPw) payload._current_password=currentPw;
  try{
    const saved=await _enqueueSettingsPost({method:'POST',body:JSON.stringify(payload)});
    showToast('Password removed. Passkey sign-in remains enabled.');
    _setSettingsAuthButtonsVisible(!!saved.auth_enabled);
    _syncPasswordlessButton({auth_enabled:saved.auth_enabled,password_auth_enabled:false,passkeys_count:1});
    const pwField=$('settingsPassword'); if(pwField) pwField.value='';
    const cpField=$('settingsCurrentPassword'); if(cpField) cpField.value='';
    _settingsPasswordAuthEnabled=false;
    _updateCurrentPasswordVisibility();
    try{
      const authStatus=await api('/api/auth/status');
      _renderSettingsAuthStatus(authStatus);
      _updateAuthWarningBadge(authStatus);
    }catch(e){}
  }catch(e){showToast('Failed to go passwordless: '+e.message);}
}

async function disableAuth(){
  const currentPwField=$('settingsCurrentPassword');
  const currentPw=(currentPwField||{}).value||'';
  if(_settingsPasswordAuthEnabled && !currentPw.trim()){
    if(currentPwField) currentPwField.focus();
    showToast(t('current_password_required'));
    return;
  }
  const confirmText='DISABLE AUTH';
  const userInput=await showPromptDialog({title:t('disable_auth_confirm_title'),message:t('disable_auth_confirm_message')+' '+t('disable_auth_typed_confirm'),placeholder:confirmText,confirmLabel:t('disable_auth'),danger:true});
  if(!userInput || userInput.trim()!==confirmText) return;
  const payload={_clear_password:true};
  if(_settingsPasswordAuthEnabled) payload._current_password=currentPw;
  try{
    const saved=await _enqueueSettingsPost({method:'POST',body:JSON.stringify(payload)});
    showToast(t('auth_disabled'));
    const disableBtn=$('btnDisableAuth');
    if(disableBtn) disableBtn.style.display='none';
    const signOutBtn=$('btnSignOut');
    if(signOutBtn) signOutBtn.style.display='none';
    _syncPasswordlessButton({auth_enabled:false,password_auth_enabled:false,passkeys_count:0});
    _settingsPasswordAuthEnabled=false;
    _updateCurrentPasswordVisibility();
    const cpField=$('settingsCurrentPassword'); if(cpField) cpField.value='';
    loadPasskeys();
    try{
      const authStatus=await api('/api/auth/status');
      _renderSettingsAuthStatus(authStatus);
      _updateAuthWarningBadge(authStatus);
      _updateAuthDisabledWarning(authStatus);
    }catch(e){}
  }catch(e){
    showToast(t('disable_auth_failed')+e.message);
  }
}


// ── Background agent error tracking ──────────────────────────────────────────

const _backgroundErrors=[];  // {session_id, title, message, ts}

function trackBackgroundError(sessionId, title, message){
  // Only track if user is NOT currently viewing this session
  if(S.session&&S.session.session_id===sessionId) return;
  _backgroundErrors.push({session_id:sessionId, title:title||t('untitled'), message, ts:Date.now()});
  showErrorBanner();
}

function showErrorBanner(){
  let banner=$('bgErrorBanner');
  if(!banner){
    banner=document.createElement('div');
    banner.id='bgErrorBanner';
    banner.className='bg-error-banner';
    const msgs=document.querySelector('.messages');
    if(msgs) msgs.parentNode.insertBefore(banner,msgs);
    else document.body.appendChild(banner);
  }
  const latest=_backgroundErrors[0];  // FIFO: show oldest (first) error
  if(!latest){banner.style.display='none';return;}
  const count=_backgroundErrors.length;
  const msg=count>1?t('bg_error_multi',count):t('bg_error_single',latest.title);
  banner.innerHTML=`<span>\u26a0 ${esc(msg)}</span><div style="display:flex;gap:6px;flex-shrink:0"><button class="reconnect-btn" onclick="navigateToErrorSession()">${esc(t('view'))}</button><button class="reconnect-btn" onclick="dismissErrorBanner()">${esc(t('dismiss'))}</button></div>`;
  banner.style.display='';
}

function navigateToErrorSession(){
  const latest=_backgroundErrors.shift();  // FIFO: show oldest error first
  if(latest){
    loadSession(latest.session_id);renderSessionList();
  }
  if(_backgroundErrors.length===0) dismissErrorBanner();
  else showErrorBanner();
}

function dismissErrorBanner(){
  _backgroundErrors.length=0;
  const banner=$('bgErrorBanner');
  if(banner) banner.style.display='none';
}

// Event wiring


// ── MCP Server Management ──
function _mcpStatusLabel(status){
  const key={
    active:'mcp_status_active',
    configured:'mcp_status_configured',
    disabled:'mcp_status_disabled',
    invalid_config:'mcp_status_invalid_config',
  }[status]||'mcp_status_unknown';
  return t(key);
}
function toggleMcpServer(name, enabled){
  api('/api/mcp/servers/'+encodeURIComponent(name),{
    method:'PATCH',
    body:JSON.stringify({enabled:enabled}),
  }).then(r=>{
    if(r&&r.ok){
      _refreshMcpToolsetsCatalog();
      showToast(t(enabled?'mcp_enabled_toast':'mcp_disabled_toast',name));
    }
    else showToast(t('mcp_toggle_failed'),'error');
    loadMcpServers();
  }).catch(()=>{showToast(t('mcp_toggle_failed'),'error');loadMcpServers();});
}
function _refreshMcpToolsetsCatalog(payload){
  if(typeof window.invalidateToolsetsCatalog==='function') window.invalidateToolsetsCatalog(payload);
}
function loadMcpServers(){
  const list=$('mcpServerList');
  if(!list) return;
  list.innerHTML=`<div style="color:var(--muted);font-size:12px;padding:6px 0">${esc(t('loading'))}</div>`;
  api('/api/mcp/servers').then(r=>{
    if(!r||!Array.isArray(r.servers)) return;
    _refreshMcpToolsetsCatalog(r);
    if(!r.servers.length){
      list.innerHTML=`<div class="mcp-empty-state" style="color:var(--muted);font-size:12px;padding:6px 0">${esc(t('mcp_no_servers'))}</div>`;
      return;
    }
    list.innerHTML=r.servers.map(s=>{
      const transportLabel=s.transport==='http'?'HTTP':s.transport==='stdio'?'stdio':(''+(s.transport||'unknown'));
      const transportClass=s.transport==='http'?'mcp-http':s.transport==='stdio'?'mcp-stdio':'mcp-unknown';
      const transportBadge=`<span class="mcp-transport-badge ${transportClass}">${esc(transportLabel)}</span>`;
      const status=s.status||'configured';
      const statusBadge=`<span class="mcp-status-badge mcp-status-${esc(status)}">${esc(_mcpStatusLabel(status))}</span>`;
      const toolCount=s.tool_count===null||typeof s.tool_count==='undefined'?'—':String(s.tool_count);
      const detail=s.transport==='http'
        ? (s.url||'')
        : (s.transport==='stdio'?`${s.command||''} ${Array.isArray(s.args)?s.args.join(' '):''}`:t('mcp_status_invalid_config'));
      const envInfo=s.env?Object.entries(s.env).map(([k,v])=>`${k}=${v}`).join(', '):'';
      const headersInfo=s.headers?Object.entries(s.headers).map(([k,v])=>`${k}=${v}`).join(', '):'';
      const secretInfo=[envInfo,headersInfo].filter(Boolean).join(' | ');
      const isEnabled=s.enabled!==false;
      const encodedName=encodeURIComponent(s.name).replace(/'/g,"\\'");
      const toggleBtn=r.toggle_supported
        ?`<button type="button" class="mcp-toggle-btn ${isEnabled?'mcp-toggle-enabled':'mcp-toggle-disabled'}" title="${esc(t(isEnabled?'mcp_disable_server':'mcp_enable_server'))}" onclick="toggleMcpServer('${encodedName}',${!isEnabled})">${esc(t(isEnabled?'mcp_enabled_yes':'mcp_enabled_no'))}</button>`
        :`<span>${esc(t(isEnabled?'mcp_enabled_yes':'mcp_enabled_no'))}</span>`;
      return `<div class="mcp-server-row">
        <div class="mcp-server-row-head">
          <span class="mcp-server-name">${esc(s.name)}</span>
          ${transportBadge}
          ${statusBadge}
        </div>
        <div class="mcp-server-detail">${esc(detail)}${secretInfo?' | '+esc(secretInfo):''}</div>
        <div class="mcp-server-meta"><span class="mcp-tool-count">${esc(t('mcp_tool_count',toolCount))}</span>${toggleBtn}</div>
      </div>`;
    }).join('');
  }).catch(()=>{list.innerHTML=`<div class="mcp-error-state" style="color:#ef4444;font-size:12px;padding:6px 0">${esc(t('mcp_load_failed'))}</div>`});
}
let _mcpToolsCache=[];
let _mcpToolsMeta={};
let _mcpToolsPage=1;
let _mcpToolsPageSize=5;
const MCP_TOOLS_PAGE_SIZE_OPTIONS=[5,10,20,40];
function _filterMcpToolsForSearch(tools, query){
  const q=(query||'').trim().toLowerCase();
  if(!q) return Array.isArray(tools)?tools:[];
  return (Array.isArray(tools)?tools:[]).filter(tool=>{
    const hay=[tool.name,tool.server,tool.description].map(v=>String(v||'').toLowerCase()).join(' ');
    return hay.includes(q);
  });
}
function _mcpToolSchemaText(schemaSummary){
  if(!Array.isArray(schemaSummary)||!schemaSummary.length) return t('mcp_tools_schema_empty');
  return schemaSummary.map(p=>{
    const req=p.required?'*':'';
    const desc=p.description?` — ${p.description}`:'';
    return `${p.name}${req}: ${p.type||'unknown'}${desc}`;
  }).join('\n');
}
function _mcpToolsSummary(total, filtered, page, pages, query){
  const trimmedQuery=(query||'').trim();
  if(!filtered){
    if(trimmedQuery) return t('mcp_tools_summary_no_matches',trimmedQuery,total);
    return total?t('mcp_tools_summary_none'):'';
  }
  const pageSize=_mcpToolsPageSize||5;
  const start=(page-1)*pageSize+1;
  const end=Math.min(filtered,page*pageSize);
  const searchNote=trimmedQuery?t('mcp_tools_summary_matching',trimmedQuery):'';
  const totalNote=filtered===total?'':t('mcp_tools_summary_total_note',total);
  return t('mcp_tools_summary_showing',start,end,filtered,searchNote,totalNote,page,pages);
}
function _mcpToolPageSizeControl(){
  const options=MCP_TOOLS_PAGE_SIZE_OPTIONS.map(size=>`<option value="${size}" ${size===_mcpToolsPageSize?'selected':''}>${size}</option>`).join('');
  return `<label class="mcp-tool-page-size">${esc(t('mcp_tools_page_size_prefix'))} <select aria-label="${esc(t('mcp_tools_per_page_aria'))}" onchange="setMcpToolsPageSize(this.value)">${options}</select> ${esc(t('mcp_tools_page_size_suffix'))}</label>`;
}
function _mcpToolsEmptyMessage(query){
  const base=esc(t(query?'mcp_tools_no_matches':'mcp_tools_no_tools'));
  const unavailable=Array.isArray(_mcpToolsMeta.unavailable_servers)?_mcpToolsMeta.unavailable_servers:[];
  if(query||!unavailable.length) return base;
  return `${base}<br><span class="mcp-tool-empty-detail">${esc(t('mcp_tools_inactive_configured_servers',unavailable.join(', ')))}</span>`;
}
function _renderMcpToolPager(filteredCount, page, pages){
  const pager=$('mcpToolPager');
  if(!pager) return;
  if(pages<=1){
    pager.innerHTML='';
    return;
  }
  pager.innerHTML=`<button type="button" class="mcp-tool-page-btn" onclick="setMcpToolsPage(${page-1})" ${page<=1?'disabled':''} aria-label="${esc(t('mcp_tools_previous_page_aria'))}">${esc(t('mcp_tools_previous_page'))}</button>
    <span class="mcp-tool-page-label">${page} / ${pages}</span>
    <button type="button" class="mcp-tool-page-btn" onclick="setMcpToolsPage(${page+1})" ${page>=pages?'disabled':''} aria-label="${esc(t('mcp_tools_next_page_aria'))}">${esc(t('mcp_tools_next_page'))}</button>`;
}
function _renderMcpTools(tools, query){
  const list=$('mcpToolList');
  const toolbar=$('mcpToolToolbar');
  if(!list) return;
  const filtered=_filterMcpToolsForSearch(tools, query);
  const total=Array.isArray(tools)?tools.length:0;
  const pages=Math.max(1,Math.ceil(filtered.length/_mcpToolsPageSize));
  _mcpToolsPage=Math.min(Math.max(1,_mcpToolsPage||1),pages);
  if(toolbar) toolbar.innerHTML=`<span class="mcp-tool-summary">${esc(_mcpToolsSummary(total,filtered.length,_mcpToolsPage,pages,query))}</span>${_mcpToolPageSizeControl()}`;
  _renderMcpToolPager(filtered.length,_mcpToolsPage,pages);
  if(!filtered.length){
    list.innerHTML=`<div class="mcp-tool-empty-state" style="color:var(--muted);font-size:12px;padding:6px 0">${_mcpToolsEmptyMessage(query)}</div>`;
    return;
  }
  const visible=filtered.slice((_mcpToolsPage-1)*_mcpToolsPageSize,_mcpToolsPage*_mcpToolsPageSize);
  list.innerHTML=visible.map(tool=>{
    const status=tool.status||'unknown';
    const statusBadge=`<span class="mcp-status-badge mcp-status-${esc(status)}">${esc(_mcpStatusLabel(status))}</span>`;
    const schemaText=_mcpToolSchemaText(tool.schema_summary);
    return `<div class="mcp-tool-row">
      <div class="mcp-server-row-head">
        <span class="mcp-tool-name">${esc(tool.name)}</span>
        <span class="mcp-tool-server">${esc(tool.server||'unknown')}</span>
        ${statusBadge}
      </div>
      <div class="mcp-server-detail">${esc(tool.description||'')}</div>
      <pre class="mcp-tool-schema">${esc(schemaText)}</pre>
    </div>`;
  }).join('');
}
function setMcpToolsPage(page){
  _mcpToolsPage=page;
  const input=$('mcpToolSearch');
  _renderMcpTools(_mcpToolsCache,input?input.value:'');
  const list=$('mcpToolList');
  if(list) list.scrollTop=0;
}
function setMcpToolsPageSize(size){
  const next=Number(size);
  if(!MCP_TOOLS_PAGE_SIZE_OPTIONS.includes(next)) return;
  _mcpToolsPageSize=next;
  _mcpToolsPage=1;
  const input=$('mcpToolSearch');
  _renderMcpTools(_mcpToolsCache,input?input.value:'');
  const list=$('mcpToolList');
  if(list) list.scrollTop=0;
}
function filterMcpTools(){
  _mcpToolsPage=1;
  const input=$('mcpToolSearch');
  _renderMcpTools(_mcpToolsCache,input?input.value:'');
  const list=$('mcpToolList');
  if(list) list.scrollTop=0;
}
function loadMcpTools(){
  const list=$('mcpToolList');
  const toolbar=$('mcpToolToolbar');
  const pager=$('mcpToolPager');
  if(!list) return;
  if(toolbar) toolbar.textContent='';
  if(pager) pager.innerHTML='';
  list.innerHTML=`<div style="color:var(--muted);font-size:12px;padding:6px 0">${esc(t('loading'))}</div>`;
  api('/api/mcp/tools').then(r=>{
    _mcpToolsCache=(r&&Array.isArray(r.tools))?r.tools:[];
    _mcpToolsMeta=r||{};
    _mcpToolsPage=1;
    filterMcpTools();
  }).catch(()=>{list.innerHTML=`<div class="mcp-tool-error-state" style="color:#ef4444;font-size:12px;padding:6px 0">${esc(t('mcp_tools_load_failed'))}</div>`});
}
let _gatewayActionInFlight=false;
function _gatewayActionButton(action){
  const labels={start:t('gateway_start'),stop:t('gateway_stop'),restart:t('gateway_restart')};
  return `<button class="sm-btn gateway-action-btn" data-gateway-action="${esc(action)}" onclick="_gatewayAction('${esc(action)}')" ${_gatewayActionInFlight?'disabled':''} style="padding:5px 10px;font-size:12px">${esc(labels[action]||action)}</button>`;
}
function _gatewayActionControls(r){
  const actions=(r&&r.running)?['stop','restart']:['start'];
  return `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">${actions.map(_gatewayActionButton).join('')}</div>`;
}
function _renderGatewayStatus(r){
  const card=$('gatewayStatusCard');
  if(!card||!r) return;
  if(!r.configured){
    card.innerHTML=`<div style="color:var(--muted);font-size:12px;display:flex;align-items:center;gap:6px"><span style="width:8px;height:8px;border-radius:50%;background:#f59e0b;display:inline-block"></span>${esc(t('gateway_not_configured'))}</div>${_gatewayActionControls(r)}`;
    return;
  }
  if(!r.running){
    const reason = _gatewayStatusReason(r);
    const statusLabel = reason === 'gateway_stale_running_state'
      ? t('gateway_metadata_stale')
      : reason === 'remote_gateway_unreachable'
        ? t('gateway_endpoint_unreachable')
        : t('gateway_not_running');
    card.innerHTML=`<div style="color:var(--muted);font-size:12px;display:flex;align-items:center;gap:6px"><span style="width:8px;height:8px;border-radius:50%;background:#ef4444;display:inline-block"></span>${esc(statusLabel)}</div>${_gatewayActionControls(r)}`;
    return;
  }
  const platformIcons={telegram:'💬',discord:'🎮',slack:'📝',web:'🌐',api:'🔌'};
  let badges='';
  if(r.platforms&&r.platforms.length){
    badges=r.platforms.map(p=>{
      const icon=platformIcons[p.name]||'📡';
      return `<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;background:var(--code-bg);border:1px solid var(--border2);border-radius:12px;font-size:12px;font-weight:500">${icon} ${esc(p.label)}</span>`;
    }).join(' ');
  }
  const lastActive=r.last_active?`<span style="font-size:11px;color:var(--muted)">${esc(t('gateway_last_active'))}: ${esc(new Date(r.last_active).toLocaleString())}</span>`:'';
  const sessionInfo=r.session_count?`<span style="font-size:11px;color:var(--muted)">${r.session_count} ${esc(r.session_count!==1?t('gateway_sessions'):t('gateway_session'))}</span>`:'';
  card.innerHTML=`<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px"><span style="width:8px;height:8px;border-radius:50%;background:#22c55e;display:inline-block"></span><span style="font-size:13px;font-weight:500;color:#22c55e">${esc(t('gateway_running'))}</span></div>${badges?`<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">${badges}</div>`:''}<div style="display:flex;gap:12px">${sessionInfo}${lastActive}</div>${_gatewayActionControls(r)}`;
}
function loadGatewayStatus(){
  const card=$('gatewayStatusCard');
  if(!card) return;
  return api('/api/gateway/status').then(r=>_renderGatewayStatus(r)).catch(()=>{card.innerHTML=`<div style="color:#ef4444;font-size:12px">${esc(t('gateway_status_load_failed'))}</div>`});
}
async function _gatewayAction(action){
  if(_gatewayActionInFlight) return;
  _gatewayActionInFlight=true;
  const buttons=[...document.querySelectorAll('.gateway-action-btn')];
  buttons.forEach(btn=>{btn.disabled=true;});
  try{
    const result=await api(`/api/gateway/${encodeURIComponent(action)}`,{method:'POST',body:JSON.stringify({}),timeoutMs:70000,timeoutToast:false});
    if(typeof showToast==='function') showToast(result&&result.message?result.message:t(`gateway_${action}_success`),3000,'success');
  }catch(e){
    const msg=e&&e.message?e.message:String(e||'');
    if(typeof showToast==='function') showToast(`${t(`gateway_${action}_failed`)}${msg?': '+msg:''}`,5000,'error');
  }finally{
    _gatewayActionInFlight=false;
    await loadGatewayStatus();
  }
}
// Load MCP servers when system settings tab opens
const _origSwitchSettings=switchSettingsSection;
switchSettingsSection=function(name, opts){
  _origSwitchSettings(name, opts);
  if(name==='preferences') updateNotificationPermissionStatus();
  if(name==='system'){loadMcpServers();loadMcpTools();loadGatewayStatus();}
};

// ── Checkpoints / Rollback ──────────────────────────────────────────────────

async function _loadCheckpoints(workspace){
  const container=$('checkpointListContainer');
  if(!container) return;
  try{
    const data=await api(`/api/rollback/list?workspace=${encodeURIComponent(workspace)}`);
    const checkpoints=data.checkpoints||[];
    if(!checkpoints.length){
      container.innerHTML=`<div style="color:var(--muted);font-size:12px;padding:8px 0">${esc(t('checkpoint_empty'))}</div>`;
      return;
    }
    let html='';
    for(const ck of checkpoints){
      const shortId=ck.id||ck.commit||'?';
      const msg=ck.message||'checkpoint';
      const date=ck.date_display||ck.date||'';
      const files=ck.files||0;
      html+=`
        <div class="detail-row" style="align-items:center;padding:6px 0;border-bottom:1px solid var(--border,rgba(255,255,255,0.08))">
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(msg)}">${esc(msg)}</div>
            <div style="font-size:11px;color:var(--muted);margin-top:2px">
              <code style="font-size:10px">${esc(shortId)}</code>
              ${date ? ` · ${esc(date)}` : ''}
              ${files ? ` · ${esc(t('checkpoint_files'))}: ${files}` : ''}
            </div>
          </div>
          <div style="display:flex;gap:4px;flex-shrink:0;margin-left:8px">
            <button class="panel-head-btn" title="${esc(t('checkpoint_view_diff'))}" onclick="event.stopPropagation();_viewCheckpointDiff('${esc(workspace)}','${esc(ck.id)}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
            </button>
            <button class="panel-head-btn" title="${esc(t('checkpoint_restore'))}" onclick="event.stopPropagation();_restoreCheckpoint('${esc(workspace)}','${esc(ck.id)}','${esc(msg.replace(/'/g,"\\'"))}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
            </button>
          </div>
        </div>`;
    }
    container.innerHTML=html;
  }catch(e){
    container.innerHTML=`<div style="color:var(--error,#f87171);font-size:12px;padding:8px 0">${esc(t('checkpoint_error'))}: ${esc(e.message)}</div>`;
  }
}

async function _viewCheckpointDiff(workspace,checkpoint){
  const modal=document.getElementById('checkpointDiffModal');
  if(!modal){
    const m=document.createElement('div');
    m.id='checkpointDiffModal';
    m.style.cssText='position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6)';
    m.innerHTML=`
      <div style="background:var(--bg,${getComputedStyle(document.documentElement).getPropertyValue('--bg')||'#1a1a2e'});border:1px solid var(--border,rgba(255,255,255,0.12));border-radius:12px;width:90vw;max-width:800px;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,0.4)">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--border,rgba(255,255,255,0.08))">
          <div id="checkpointDiffModalTitle" style="font-weight:600;font-size:14px"></div>
          <button onclick="document.getElementById('checkpointDiffModal').style.display='none'" style="background:none;border:none;color:var(--fg);cursor:pointer;font-size:18px;padding:0 4px">&times;</button>
        </div>
        <div id="checkpointDiffModalBody" style="flex:1;overflow:auto;padding:12px 16px">
          <div style="color:var(--muted);font-size:12px">${esc(t('checkpoint_loading'))}</div>
        </div>
      </div>`;
    m.onclick=(e)=>{if(e.target===m) m.style.display='none';};
    document.body.appendChild(m);
  }
  modal.style.display='flex';
  $('checkpointDiffModalTitle').textContent=t('checkpoint_diff_title');
  $('checkpointDiffModalBody').innerHTML=`<div style="color:var(--muted);font-size:12px">${esc(t('checkpoint_loading'))}</div>`;
  try{
    const data=await api(`/api/rollback/diff?workspace=${encodeURIComponent(workspace)}&checkpoint=${encodeURIComponent(checkpoint)}`);
    const body=$('checkpointDiffModalBody');
    if(!data.total_changes){
      body.innerHTML=`<div style="color:var(--muted);font-size:12px">${esc(t('checkpoint_diff_no_changes'))}</div>`;
      return;
    }
    let html=`<div style="font-size:12px;margin-bottom:8px">${esc(t('checkpoint_diff_files_changed',data.total_changes))}</div>`;
    if(data.files_changed){
      html+='<div style="margin-bottom:8px">';
      for(const f of data.files_changed){
        const icon=f.status==='deleted'?'−':'~';
        const color=f.status==='deleted'?'var(--error,#f87171)':'var(--accent,#60a5fa)';
        html+=`<div style="font-size:12px;padding:2px 0"><span style="color:${color};font-weight:bold;margin-right:6px">${icon}</span><code style="font-size:11px">${esc(f.file)}</code></div>`;
      }
      html+='</div>';
    }
    if(data.diff){
      html+=`<pre style="background:var(--bg-secondary,rgba(0,0,0,0.3));border:1px solid var(--border,rgba(255,255,255,0.08));border-radius:8px;padding:12px;font-size:11px;line-height:1.4;overflow-x:auto;white-space:pre-wrap;word-break:break-all;max-height:50vh;overflow-y:auto;color:var(--fg)">${esc(data.diff)}</pre>`;
    }
    body.innerHTML=html;
  }catch(e){
    $('checkpointDiffModalBody').innerHTML=`<div style="color:var(--error,#f87171);font-size:12px">${esc(e.message)}</div>`;
  }
}

async function _restoreCheckpoint(workspace,checkpoint,message){
  const label=message||checkpoint;
  const ok=await showConfirmDialog({title:t('checkpoint_restore_confirm_title'),message:t('checkpoint_restore_confirm_message',label),confirmLabel:t('checkpoint_restore'),danger:true,focusCancel:true});
  if(!ok) return;
  try{
    const data=await api('/api/rollback/restore',{method:'POST',body:JSON.stringify({workspace,checkpoint})});
    if(data&&data.ok){
      showToast(t('checkpoint_restored')+(data.files_restored_count?` (${data.files_restored_count} ${t('checkpoint_files').toLowerCase()})`:''));
    }else{
      showToast((data&&data.error)||'Restore failed','error');
    }
  }catch(e){
    showToast(t('checkpoint_restore')+': '+e.message,'error');
  }
}


function updateNotificationPermissionStatus(){
  const el=$('notificationPermissionStatus');
  const btn=$('notificationPermissionButton');
  const btnWrap=$('notificationPermissionButtonWrap');
  if(!el) return;
  if(!('Notification' in window)){
    const unsupported=t('notifications_unsupported');
    el.textContent=unsupported;
    if(btn){
      btn.disabled=true;
      btn.title='';
      btn.setAttribute('aria-label', unsupported);
      btn.setAttribute('aria-disabled','true');
    }
    if(btnWrap) btnWrap.title=unsupported;
    return;
  }
  const perm=Notification.permission||'default';
  const label=t('notifications_permission_status', perm);
  el.textContent=label;
  if(btn){
    const granted=perm==='granted';
    btn.disabled=granted;
    btn.title=granted?'':label;
    btn.setAttribute('aria-label', label);
    btn.setAttribute('aria-disabled', granted?'true':'false');
  }
  if(btnWrap) btnWrap.title=label;
}
