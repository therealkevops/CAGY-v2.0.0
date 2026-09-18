/**
 * ui-layout.js — Centralized Layout, Responsive Viewport, Sidebar,
 * Workspace Panel, PWA Mobile Gestures, and Split-Pane Resizers.
 *
 * Provides:
 *   - Viewport detection (_isCompactWorkspaceViewport, _isPhoneWidthViewport, _isDesktopWidth, _isTouchKeyboardViewport)
 *   - VisualViewport inset synchronization & mobile reflow guards (_syncKeyboardBottomInset, _forceMobileViewportReflow)
 *   - Workspace panel state & modes (openWorkspacePanel, closeWorkspacePanel, toggleWorkspacePanel, syncWorkspacePanelState, syncWorkspacePanelUI)
 *   - Desktop sidebar toggle, collapse state, and ARIA announcement (toggleSidebar, expandSidebar, _syncSidebarAria)
 *   - Mobile sidebar drawer and PWA edge-swipe gestures (toggleMobileSidebar, closeMobileSidebar, _installPwaSidebarSwipeGesture)
 *   - Resizable split panes for sidebar and right panel (initResize, _initResizePanels)
 *
 * Loaded early in boot sequence (see index.html).
 */

// ── Layout Constants ──────────────────────────────────────────────────────────
const _SIDEBAR_COLLAPSED_KEY = 'agy-webui-sidebar-collapsed';
const _PWA_SIDEBAR_SWIPE_EDGE = 80;
const _PWA_SIDEBAR_SWIPE_CLAIM = 10;
const _PWA_SIDEBAR_SWIPE_TRIGGER = 64;
const _PWA_SIDEBAR_SWIPE_MAX_VERTICAL = 56;

const SIDEBAR_MIN = 180;
const SIDEBAR_MAX = 420;
const PANEL_MIN = 180;
const PANEL_MAX = 1200;

// ── Layout Internal State ───────────────────────────────────────────────────
let _workspacePanelMode = 'closed'; // 'closed' | 'browse' | 'preview'
let _pwaSidebarSwipe = null;
let _mobileViewportReflowTimer = 0;
let _pwaGesturesInstalled = false;
let _viewportListenersInstalled = false;

// ── Safe Helpers ─────────────────────────────────────────────────────────────
function _getEl(id) {
  if (typeof $ === 'function') return $(id);
  if (typeof document !== 'undefined') return document.getElementById(id);
  return null;
}

function _getState() {
  if (typeof window !== 'undefined' && window.S) return window.S;
  if (typeof global !== 'undefined' && global.S) return global.S;
  return null;
}

function _uiText(key, fallback) {
  if (typeof t === 'function') {
    const val = t(key);
    if (val && val !== key) return val;
  }
  return fallback;
}

/**
 * Set a tooltip on a button, preferring custom CSS tooltip (data-tooltip)
 * when the element has the 'has-tooltip' class, falling back to title.
 * Always clears title when data-tooltip is present to prevent double tooltips.
 */
function _setButtonTooltip(btn, text) {
  if (!btn) return;
  if (btn.hasAttribute('data-tooltip')) {
    btn.setAttribute('data-tooltip', text);
    if (btn.hasAttribute('title')) btn.removeAttribute('title');
  } else {
    btn.title = text;
  }
}

function _hasFinePointerCoexisting() {
  if (typeof window !== 'undefined' && typeof window._hasFinePointerCoexisting === 'function') {
    return window._hasFinePointerCoexisting();
  }
  try {
    return (typeof matchMedia === 'function') && (
      matchMedia('(pointer:fine)').matches || matchMedia('(any-pointer:fine)').matches
    );
  } catch (_) {
    return false;
  }
}

// ── Viewport Geometry & Detection ───────────────────────────────────────────
function _isCompactWorkspaceViewport() {
  try {
    return typeof window !== 'undefined' && window.matchMedia('(max-width: 900px)').matches;
  } catch (_) {
    return false;
  }
}

function _isPhoneWidthViewport() {
  try {
    return typeof window !== 'undefined' && window.matchMedia('(max-width: 640px)').matches;
  } catch (_) {
    return false;
  }
}

function _isDesktopWidth() {
  try {
    return typeof window !== 'undefined' && window.matchMedia('(min-width: 641px)').matches;
  } catch (_) {
    return true;
  }
}

function _isTouchKeyboardViewport() {
  try {
    return (typeof matchMedia === 'function') &&
      matchMedia('(hover:none) and (pointer:coarse)').matches &&
      !_hasFinePointerCoexisting();
  } catch (_) {
    return false;
  }
}

function _syncKeyboardBottomInset() {
  if (typeof document === 'undefined' || typeof window === 'undefined') return;
  const root = document.documentElement;
  if (!root) return;
  if (!window.visualViewport || !_isTouchKeyboardViewport()) {
    root.style.removeProperty('--keyboard-bottom-inset');
    return;
  }
  const vv = window.visualViewport;
  // A pinch-zoomed viewport (vv.scale != 1) makes innerHeight - vv.height
  // reflect zoom, not keyboard. Treat only unzoomed state as keyboard occlusion.
  if (Math.abs((vv.scale || 1) - 1) > 0.05) {
    root.style.removeProperty('--keyboard-bottom-inset');
    return;
  }
  const inset = Math.max(0, Math.ceil(window.innerHeight - (vv.height + vv.offsetTop)));
  if (inset > 0) {
    root.style.setProperty('--keyboard-bottom-inset', `${inset}px`);
  } else {
    root.style.removeProperty('--keyboard-bottom-inset');
  }
}

function _forceMobileViewportReflow() {
  _syncKeyboardBottomInset();
  if (!_isPhoneWidthViewport()) return;
  if (typeof document === 'undefined') return;
  const layout = document.querySelector('.layout');
  if (!layout) return;
  document.documentElement.classList.add('viewport-reflow');
  void layout.offsetWidth;
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => {
      document.documentElement.classList.remove('viewport-reflow');
      try { syncWorkspacePanelState(); } catch (_) {}
      try { if (typeof _syncSidebarAria === 'function') _syncSidebarAria(); } catch (_) {}
    });
  } else {
    document.documentElement.classList.remove('viewport-reflow');
    try { syncWorkspacePanelState(); } catch (_) {}
    try { if (typeof _syncSidebarAria === 'function') _syncSidebarAria(); } catch (_) {}
  }
}

function _syncWorkspacePanelInlineWidth() {
  const { panel } = _workspacePanelEls();
  if (!panel) return;

  const isCompact = _isCompactWorkspaceViewport();
  if (isCompact) {
    if (panel.style.width) panel.style.removeProperty('width');
    return;
  }

  try {
    const saved = localStorage.getItem('agy-panel-w');
    if (!saved) return;
    const parsed = parseInt(saved, 10);
    if (Number.isNaN(parsed) || parsed <= 0) return;
    panel.style.width = `${parsed}px`;
  } catch (_) {}
}

function _installViewportResizeListeners() {
  if (typeof window === 'undefined' || _viewportListenersInstalled) return;
  _viewportListenersInstalled = true;

  window.addEventListener('resize', () => {
    _syncWorkspacePanelInlineWidth();
    syncWorkspacePanelState();
    if (!window.visualViewport) _forceMobileViewportReflow();
  });

  if (window.visualViewport) {
    _syncKeyboardBottomInset();
    const _scheduleMobileViewportReflow = () => {
      if (_mobileViewportReflowTimer) clearTimeout(_mobileViewportReflowTimer);
      _mobileViewportReflowTimer = setTimeout(() => {
        _mobileViewportReflowTimer = 0;
        _forceMobileViewportReflow();
      }, 60);
    };
    window.visualViewport.addEventListener('resize', _scheduleMobileViewportReflow);
    window.visualViewport.addEventListener('scroll', _scheduleMobileViewportReflow);
  }
}

// ── Workspace Panel Management ──────────────────────────────────────────────
function _workspacePanelEls() {
  if (typeof document === 'undefined') {
    return { layout: null, panel: null, toggleBtn: null, edgeToggleBtn: null, collapseBtn: null };
  }
  return {
    layout: document.querySelector('.layout'),
    panel: document.querySelector('.rightpanel'),
    toggleBtn: _getEl('btnWorkspacePanelToggle'),
    edgeToggleBtn: _getEl('btnWorkspacePanelEdgeToggle'),
    collapseBtn: _getEl('btnCollapseWorkspacePanel'),
  };
}

function _hasWorkspacePreviewVisible() {
  const preview = _getEl('previewArea');
  return !!(preview && preview.classList.contains('visible'));
}

function getWorkspacePanelMode() {
  return _workspacePanelMode;
}

function setWorkspacePanelMode(mode) {
  const { layout, panel } = _workspacePanelEls();
  _workspacePanelMode = (mode === 'browse' || mode === 'preview') ? mode : 'closed';
  if (typeof window !== 'undefined') {
    window._workspacePanelMode = _workspacePanelMode;
  }
  if (!layout || !panel) return _workspacePanelMode;
  const open = _workspacePanelMode !== 'closed';
  if (typeof document !== 'undefined' && document.documentElement) {
    document.documentElement.dataset.workspacePanel = open ? 'open' : 'closed';
  }
  try {
    localStorage.setItem('agy-webui-workspace-panel', open ? 'open' : 'closed');
  } catch (_) {}
  layout.classList.toggle('workspace-panel-collapsed', !open);
  if (_isCompactWorkspaceViewport()) {
    panel.classList.toggle('mobile-open', open);
  } else {
    panel.classList.remove('mobile-open');
  }
  syncWorkspacePanelUI();
  return _workspacePanelMode;
}

const _setWorkspacePanelMode = setWorkspacePanelMode;

function syncWorkspacePanelState() {
  const S = _getState();
  const previewPath = (typeof _previewCurrentPath !== 'undefined' && _previewCurrentPath) ||
                      (typeof window !== 'undefined' && window._previewCurrentPath);
  const hasPreview = _hasWorkspacePreviewVisible() || !!previewPath;
  if (hasPreview) {
    if (_workspacePanelMode === 'closed') _setWorkspacePanelMode('preview');
    else syncWorkspacePanelUI();
    return;
  }
  if (!S || !S.session) {
    if (_workspacePanelMode === 'preview') _setWorkspacePanelMode('closed');
    else syncWorkspacePanelUI();
    return;
  }
  _setWorkspacePanelMode(_workspacePanelMode === 'preview' ? 'closed' : _workspacePanelMode);
}

function syncWorkspacePanelUI() {
  const { layout, panel, toggleBtn, edgeToggleBtn, collapseBtn } = _workspacePanelEls();
  if (!layout || !panel) return;
  const desktopOpen = _workspacePanelMode !== 'closed';
  const mobileOpen = panel.classList.contains('mobile-open');
  const isCompact = _isCompactWorkspaceViewport();
  const isOpen = isCompact ? mobileOpen : desktopOpen;
  const S = _getState();
  const canBrowse = !!(S && S.session) || _hasWorkspacePreviewVisible() || !!(S && S._profileDefaultWorkspace);
  const hasPreview = _hasWorkspacePreviewVisible();

  if (toggleBtn) {
    toggleBtn.classList.toggle('active', isOpen);
    toggleBtn.setAttribute('aria-pressed', isOpen ? 'true' : 'false');
    const label = _uiText(isOpen ? 'workspace_panel_hide' : 'workspace_panel_show', isOpen ? 'Hide workspace panel' : 'Show workspace panel');
    _setButtonTooltip(toggleBtn, label);
    toggleBtn.setAttribute('aria-label', label);
    toggleBtn.disabled = !canBrowse;
  }
  if (edgeToggleBtn) {
    edgeToggleBtn.classList.toggle('active', isOpen);
    edgeToggleBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    const label = _uiText(isOpen ? 'workspace_panel_hide' : 'workspace_panel_show', isOpen ? 'Hide workspace panel' : 'Show workspace panel');
    _setButtonTooltip(edgeToggleBtn, label);
    edgeToggleBtn.setAttribute('aria-label', label);
    edgeToggleBtn.disabled = !canBrowse;
  }
  if (collapseBtn) {
    _setButtonTooltip(collapseBtn, isCompact ? _uiText('workspace_panel_close', 'Close workspace panel') : _uiText('workspace_panel_hide', 'Hide workspace panel'));
  }
  const hasSession = !!(S && S.session);
  ['btnUpDir', 'btnNewFile', 'btnNewFolder', 'btnRefreshPanel'].forEach(id => {
    const el = _getEl(id);
    if (el) el.disabled = !hasSession;
  });
  const clearBtn = _getEl('btnClearPreview');
  if (clearBtn) {
    clearBtn.disabled = !isOpen;
    const label = hasPreview ? _uiText('workspace_close_preview', 'Close preview') : _uiText('terminal_close', 'Close');
    _setButtonTooltip(clearBtn, label);
    clearBtn.setAttribute('aria-label', label);
    if (!isCompact) clearBtn.style.display = '';
  }
}

function openWorkspacePanel(mode = 'browse') {
  const S = _getState();
  if (mode === 'browse' && (!S || !S.session) && !_hasWorkspacePreviewVisible() && (!S || !S._profileDefaultWorkspace)) return;
  _setWorkspacePanelMode(mode);
}

function closeWorkspacePanel() {
  _setWorkspacePanelMode('closed');
}

function ensureWorkspacePreviewVisible() {
  if (_workspacePanelMode === 'closed') _setWorkspacePanelMode('preview');
  else syncWorkspacePanelUI();
}

function handleWorkspaceClose() {
  if (_hasWorkspacePreviewVisible()) {
    if (typeof clearPreview === 'function') {
      clearPreview();
    } else if (typeof window !== 'undefined' && typeof window.clearPreview === 'function') {
      window.clearPreview();
    }
    return;
  }
  closeWorkspacePanel();
}

function toggleWorkspacePanel(force) {
  const { panel } = _workspacePanelEls();
  if (!panel) return;
  const currentlyOpen = _workspacePanelMode !== 'closed';
  const nextOpen = typeof force === 'boolean' ? force : !currentlyOpen;
  if (!nextOpen) {
    closeWorkspacePanel();
    return;
  }
  const previewPath = (typeof _previewCurrentPath !== 'undefined' && _previewCurrentPath) ||
                      (typeof window !== 'undefined' && window._previewCurrentPath);
  const nextMode = (_hasWorkspacePreviewVisible() || !!previewPath) ? 'preview' : 'browse';
  _setWorkspacePanelMode(nextMode);
}

function toggleMobileFiles() {
  toggleWorkspacePanel();
}

function closeMobileWorkspacePanelFromChat(e) {
  if (!_isCompactWorkspaceViewport() || _workspacePanelMode === 'closed') return;
  if (typeof document === 'undefined') return;
  const panel = document.querySelector('.rightpanel');
  if (panel && e && e.target && panel.contains(e.target)) return;
  closeWorkspacePanel();
}

function mobileSwitchPanel(name) {
  if (typeof switchPanel === 'function') {
    switchPanel(name);
  } else if (typeof window !== 'undefined' && typeof window.switchPanel === 'function') {
    window.switchPanel(name);
  }
  if (name === 'chat') {
    closeMobileSidebar();
  } else if (typeof document !== 'undefined') {
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
      sidebar.classList.remove('mobile-session-page');
      sidebar.classList.add('mobile-panel-drawer', 'mobile-open');
    }
  }
}

// ── Mobile Sidebar & PWA Edge Gestures ──────────────────────────────────────
function toggleMobileSidebar() {
  if (typeof document === 'undefined') return;
  const sidebar = document.querySelector('.sidebar');
  if (!sidebar) return;
  const isOpen = sidebar.classList.contains('mobile-open');
  if (isOpen) {
    closeMobileSidebar();
  } else {
    try {
      if (typeof _syncMobileSidebarPanelFromMainView === 'function') {
        _syncMobileSidebarPanelFromMainView();
      } else if (typeof window !== 'undefined' && typeof window._syncMobileSidebarPanelFromMainView === 'function') {
        window._syncMobileSidebarPanelFromMainView();
      }
    } catch (_) {}
    sidebar.classList.remove('mobile-session-page');
    sidebar.classList.add('mobile-panel-drawer', 'mobile-open');
  }
}

function closeMobileSidebar() {
  if (typeof document === 'undefined') return;
  const sidebar = document.querySelector('.sidebar');
  const overlay = _getEl('mobileOverlay');
  if (sidebar) sidebar.classList.remove('mobile-open', 'mobile-session-page', 'mobile-panel-drawer');
  if (overlay) overlay.classList.remove('visible');
}

function _isPwaStandalone() {
  try {
    if (typeof document !== 'undefined' && document.documentElement.classList.contains('pwa-standalone')) return true;
    if (typeof window !== 'undefined' && window.matchMedia('(display-mode: standalone)').matches) return true;
    if (typeof window !== 'undefined' && window.navigator && window.navigator.standalone === true) return true;
    return false;
  } catch (_) {
    return false;
  }
}

function _isInteractiveSwipeTarget(target) {
  try {
    return !!(target && target.closest && target.closest('input,textarea,select,button,a,[contenteditable="true"],.topbar-chips,.composer-left,.sidebar,.rightpanel'));
  } catch (_) {
    return false;
  }
}

function _pwaSidebarSwipePoint(e) {
  const touch = (e && e.touches && e.touches[0]) || (e && e.changedTouches && e.changedTouches[0]);
  const src = touch || e;
  if (!src) return null;
  return { clientX: Number(src.clientX) || 0, clientY: Number(src.clientY) || 0 };
}

function _isTouchPointerEvent(e) {
  return !!(e && e.pointerType === 'touch');
}

function _openMobileSidebarFromGesture() {
  if (_isDesktopWidth()) return;
  if (typeof document === 'undefined') return;
  const sidebar = document.querySelector('.sidebar');
  if (!sidebar) return;
  try {
    if (typeof _syncMobileSidebarPanelFromMainView === 'function') {
      _syncMobileSidebarPanelFromMainView();
    } else if (typeof window !== 'undefined' && typeof window._syncMobileSidebarPanelFromMainView === 'function') {
      window._syncMobileSidebarPanelFromMainView();
    }
  } catch (_) {}
  const layout = document.querySelector('.layout');
  if (layout) layout.classList.remove('sidebar-collapsed');
  sidebar.classList.remove('sidebar-collapsed');
  try { document.documentElement.removeAttribute('data-sidebar-collapsed'); } catch (_) {}
  sidebar.classList.remove('mobile-session-page');
  sidebar.classList.add('mobile-panel-drawer');
  sidebar.classList.add('mobile-open');
}

function _onPwaSidebarSwipeStart(e) {
  if (_isDesktopWidth()) return;
  if (_isTouchPointerEvent(e)) return;
  if (e.pointerType === 'mouse' || (e.pointerType && e.pointerType !== 'touch' && e.pointerType !== 'pen')) return;
  if (typeof document !== 'undefined' && document.querySelector('.sidebar')?.classList.contains('mobile-open')) return;
  const point = _pwaSidebarSwipePoint(e);
  if (!point) return;
  if (point.clientX > _PWA_SIDEBAR_SWIPE_EDGE) return;
  if (_isInteractiveSwipeTarget(e.target)) return;
  _pwaSidebarSwipe = { startX: point.clientX, startY: point.clientY, active: true, opened: false };
}

function _onPwaSidebarSwipeMove(e) {
  if (_isTouchPointerEvent(e)) return;
  const swipe = _pwaSidebarSwipe;
  if (!swipe || !swipe.active || swipe.opened) return;
  const point = _pwaSidebarSwipePoint(e);
  if (!point) return;
  const dx = point.clientX - swipe.startX;
  const dy = point.clientY - swipe.startY;
  if (dx < 0 || Math.abs(dy) > _PWA_SIDEBAR_SWIPE_MAX_VERTICAL * 1.5) {
    _pwaSidebarSwipe = null;
    return;
  }
  if (dx >= _PWA_SIDEBAR_SWIPE_CLAIM && dx > Math.abs(dy) * 1.2) {
    if (e.cancelable) e.preventDefault();
  }
  if (dx >= _PWA_SIDEBAR_SWIPE_TRIGGER && Math.abs(dy) <= _PWA_SIDEBAR_SWIPE_MAX_VERTICAL && dx > Math.abs(dy) * 1.5) {
    if (e.cancelable) e.preventDefault();
    swipe.opened = true;
    _openMobileSidebarFromGesture();
  }
}

function _onPwaSidebarSwipeEnd(e) {
  if (_isTouchPointerEvent(e)) return;
  _pwaSidebarSwipe = null;
}

function _onPwaSidebarSwipeCancel(e) {
  if (_isTouchPointerEvent(e)) return;
  _pwaSidebarSwipe = null;
}

function _installPwaSidebarSwipeGesture() {
  if (typeof window === 'undefined' || _pwaGesturesInstalled) return;
  _pwaGesturesInstalled = true;
  window.addEventListener('touchstart', _onPwaSidebarSwipeStart, { capture: true, passive: true });
  window.addEventListener('touchmove', _onPwaSidebarSwipeMove, { capture: true, passive: false });
  window.addEventListener('touchend', _onPwaSidebarSwipeEnd, { capture: true, passive: true });
  window.addEventListener('touchcancel', _onPwaSidebarSwipeCancel, { capture: true, passive: true });
  window.addEventListener('pointerdown', _onPwaSidebarSwipeStart, { passive: true });
  window.addEventListener('pointermove', _onPwaSidebarSwipeMove, { passive: false });
  window.addEventListener('pointerup', _onPwaSidebarSwipeEnd, { passive: true });
  window.addEventListener('pointercancel', _onPwaSidebarSwipeCancel, { passive: true });
}

// ── Desktop Sidebar Collapse Toggle ─────────────────────────────────────────
function _isSidebarCollapsed() {
  if (typeof document === 'undefined') return false;
  return document.querySelector('.layout')?.classList.contains('sidebar-collapsed') || false;
}

function _syncSidebarAria() {
  if (typeof document === 'undefined') return;
  document.querySelectorAll('.rail .rail-btn.nav-tab[data-panel][aria-expanded]')
    .forEach(function(btn) { btn.setAttribute('aria-expanded', 'false'); });
  const active = document.querySelector('.rail .rail-btn.nav-tab.active[data-panel]');
  if (active) active.setAttribute('aria-expanded', !_isSidebarCollapsed());
}

function toggleSidebar(forceState) {
  if (!_isDesktopWidth()) return; // mobile uses an overlay; never collapse there
  if (typeof document === 'undefined') return;
  const layout = document.querySelector('.layout');
  if (!layout) return;
  const next = typeof forceState === 'boolean' ? forceState : !_isSidebarCollapsed();
  layout.classList.toggle('sidebar-collapsed', next);
  try { document.documentElement.removeAttribute('data-sidebar-collapsed'); } catch (_) {}
  try { localStorage.setItem(_SIDEBAR_COLLAPSED_KEY, next ? '1' : '0'); } catch (_) {}
  _syncSidebarAria();
}

function expandSidebar() {
  if (_isSidebarCollapsed()) toggleSidebar(false);
}

function _restoreSidebarState() {
  if (typeof document === 'undefined') return;
  try { document.documentElement.removeAttribute('data-sidebar-collapsed'); } catch (_) {}
  if (!_isDesktopWidth()) return;
  try {
    if (localStorage.getItem(_SIDEBAR_COLLAPSED_KEY) === '1') {
      const layout = document.querySelector('.layout');
      if (layout) layout.classList.add('sidebar-collapsed');
    }
  } catch (_) {}
  _syncSidebarAria();
}

// ── Resizable Split Panes ───────────────────────────────────────────────────
function initResize(handleId, targetEl, edge, minW, maxW, storageKey) {
  const handle = _getEl(handleId);
  if (!handle || !targetEl) return;

  // Restore saved width
  if (storageKey === 'agy-panel-w') {
    _syncWorkspacePanelInlineWidth();
  } else {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) targetEl.style.width = saved + 'px';
    } catch (_) {}
  }

  let startX = 0;
  let startW = 0;

  handle.addEventListener('mousedown', e => {
    e.preventDefault();
    startX = e.clientX;
    startW = targetEl.getBoundingClientRect().width;
    handle.classList.add('dragging');
    if (typeof document !== 'undefined' && document.body) {
      document.body.classList.add('resizing');
    }

    const onMove = ev => {
      const delta = edge === 'right' ? ev.clientX - startX : startX - ev.clientX;
      const newW = Math.min(maxW, Math.max(minW, startW + delta));
      targetEl.style.width = newW + 'px';
    };

    const onUp = () => {
      handle.classList.remove('dragging');
      if (typeof document !== 'undefined' && document.body) {
        document.body.classList.remove('resizing');
      }
      try {
        localStorage.setItem(storageKey, parseInt(targetEl.style.width, 10));
      } catch (_) {}
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

function _initResizePanels() {
  if (typeof document === 'undefined') return;
  const sidebar = document.querySelector('.sidebar');
  const rightpanel = document.querySelector('.rightpanel');
  initResize('sidebarResize', sidebar, 'right', SIDEBAR_MIN, SIDEBAR_MAX, 'agy-sidebar-w');
  initResize('rightpanelResize', rightpanel, 'left', PANEL_MIN, PANEL_MAX, 'agy-panel-w');
}

// ── Global Window Registration ──────────────────────────────────────────────
if (typeof window !== 'undefined') {
  // Constants
  window._SIDEBAR_COLLAPSED_KEY = _SIDEBAR_COLLAPSED_KEY;
  window._PWA_SIDEBAR_SWIPE_EDGE = _PWA_SIDEBAR_SWIPE_EDGE;
  window._PWA_SIDEBAR_SWIPE_CLAIM = _PWA_SIDEBAR_SWIPE_CLAIM;
  window._PWA_SIDEBAR_SWIPE_TRIGGER = _PWA_SIDEBAR_SWIPE_TRIGGER;
  window._PWA_SIDEBAR_SWIPE_MAX_VERTICAL = _PWA_SIDEBAR_SWIPE_MAX_VERTICAL;
  window.SIDEBAR_MIN = SIDEBAR_MIN;
  window.SIDEBAR_MAX = SIDEBAR_MAX;
  window.PANEL_MIN = PANEL_MIN;
  window.PANEL_MAX = PANEL_MAX;

  // Viewport & Layout Helpers
  window._uiText = _uiText;
  window._setButtonTooltip = _setButtonTooltip;
  window._hasFinePointerCoexisting = _hasFinePointerCoexisting;
  window._isCompactWorkspaceViewport = _isCompactWorkspaceViewport;
  window._isPhoneWidthViewport = _isPhoneWidthViewport;
  window._isDesktopWidth = _isDesktopWidth;
  window._isTouchKeyboardViewport = _isTouchKeyboardViewport;
  window._syncKeyboardBottomInset = _syncKeyboardBottomInset;
  window._forceMobileViewportReflow = _forceMobileViewportReflow;
  window._syncWorkspacePanelInlineWidth = _syncWorkspacePanelInlineWidth;
  window._installViewportResizeListeners = _installViewportResizeListeners;

  // Workspace Panel State & Functions
  window.getWorkspacePanelMode = getWorkspacePanelMode;
  window.setWorkspacePanelMode = setWorkspacePanelMode;
  window._workspacePanelEls = _workspacePanelEls;
  window._hasWorkspacePreviewVisible = _hasWorkspacePreviewVisible;
  window._setWorkspacePanelMode = _setWorkspacePanelMode;
  window.syncWorkspacePanelState = syncWorkspacePanelState;
  window.syncWorkspacePanelUI = syncWorkspacePanelUI;
  window.openWorkspacePanel = openWorkspacePanel;
  window.closeWorkspacePanel = closeWorkspacePanel;
  window.toggleWorkspacePanel = toggleWorkspacePanel;
  window.ensureWorkspacePreviewVisible = ensureWorkspacePreviewVisible;
  window.handleWorkspaceClose = handleWorkspaceClose;
  window.toggleMobileFiles = toggleMobileFiles;
  window.closeMobileWorkspacePanelFromChat = closeMobileWorkspacePanelFromChat;
  window.mobileSwitchPanel = mobileSwitchPanel;

  // Desktop Sidebar
  window._isSidebarCollapsed = _isSidebarCollapsed;
  window._syncSidebarAria = _syncSidebarAria;
  window.toggleSidebar = toggleSidebar;
  window.expandSidebar = expandSidebar;
  window._restoreSidebarState = _restoreSidebarState;

  // Mobile Sidebar & Gestures
  window.toggleMobileSidebar = toggleMobileSidebar;
  window.closeMobileSidebar = closeMobileSidebar;
  window._isPwaStandalone = _isPwaStandalone;
  window._isInteractiveSwipeTarget = _isInteractiveSwipeTarget;
  window._pwaSidebarSwipePoint = _pwaSidebarSwipePoint;
  window._isTouchPointerEvent = _isTouchPointerEvent;
  window._openMobileSidebarFromGesture = _openMobileSidebarFromGesture;
  window._installPwaSidebarSwipeGesture = _installPwaSidebarSwipeGesture;

  // Resizers
  window.initResize = initResize;
  window._initResizePanels = _initResizePanels;

  // Define _workspacePanelMode property on window with getter/setter for seamless interoperability
  try {
    Object.defineProperty(window, '_workspacePanelMode', {
      get() { return _workspacePanelMode; },
      set(v) { setWorkspacePanelMode(v); },
      configurable: true,
      enumerable: true
    });
  } catch (_) {
    window._workspacePanelMode = _workspacePanelMode;
  }

  // Auto-initialize browser gestures and listeners
  if (typeof document !== 'undefined') {
    _installPwaSidebarSwipeGesture();
    _restoreSidebarState();
    _installViewportResizeListeners();
  }
}

// ── Node.js Module Export for Tests ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    _SIDEBAR_COLLAPSED_KEY,
    _PWA_SIDEBAR_SWIPE_EDGE,
    _PWA_SIDEBAR_SWIPE_CLAIM,
    _PWA_SIDEBAR_SWIPE_TRIGGER,
    _PWA_SIDEBAR_SWIPE_MAX_VERTICAL,
    SIDEBAR_MIN,
    SIDEBAR_MAX,
    PANEL_MIN,
    PANEL_MAX,
    getWorkspacePanelMode,
    setWorkspacePanelMode,
    _isCompactWorkspaceViewport,
    _isPhoneWidthViewport,
    _isDesktopWidth,
    _isTouchKeyboardViewport,
    _hasFinePointerCoexisting,
    _syncKeyboardBottomInset,
    _forceMobileViewportReflow,
    _syncWorkspacePanelInlineWidth,
    _workspacePanelEls,
    _hasWorkspacePreviewVisible,
    _setWorkspacePanelMode,
    syncWorkspacePanelState,
    syncWorkspacePanelUI,
    openWorkspacePanel,
    closeWorkspacePanel,
    toggleWorkspacePanel,
    ensureWorkspacePreviewVisible,
    handleWorkspaceClose,
    toggleMobileFiles,
    closeMobileWorkspacePanelFromChat,
    mobileSwitchPanel,
    toggleMobileSidebar,
    closeMobileSidebar,
    _isPwaStandalone,
    _isInteractiveSwipeTarget,
    _pwaSidebarSwipePoint,
    _isTouchPointerEvent,
    _openMobileSidebarFromGesture,
    _installPwaSidebarSwipeGesture,
    _isSidebarCollapsed,
    _syncSidebarAria,
    toggleSidebar,
    expandSidebar,
    _restoreSidebarState,
    initResize,
    _initResizePanels,
    _setButtonTooltip,
    _uiText,
  };
}
