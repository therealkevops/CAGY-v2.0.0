/**
 * ui-media-viewer.js — Media, Image Lightbox, and Interactive Mermaid Diagram Viewer.
 *
 * Extracted from ui.js as part of Sprint F-M1 (ADR 016).
 *
 * Provides:
 *   - Image lightbox with next/prev gallery navigation and keyboard shortcuts
 *   - Interactive Mermaid diagram viewer (pan, zoom, reset, fit-to-screen, touch pinch)
 *   - Standalone Mermaid diagram lightbox
 *   - Audio and video playback speed controls and persistence
 *   - Video preload visibility observer (IntersectionObserver)
 *   - Inline media renderers for images, audio, video, SVG, PDF, and diff previews
 *   - Safe data:image/* URI validation and renderer
 *
 * Loaded before ui.js in index.html with defer.
 */

/* ── Constants & Regular Expressions ───────────────────────────────────────── */
const _MERMAID_VIEWER_MIN_SCALE = 0.25;
const _MERMAID_VIEWER_MAX_SCALE = 8;
const _MERMAID_VIEWER_ZOOM_STEP = 1.2;
const _MERMAID_VIEWER_INLINE_MIN_HEIGHT = 220;

const _IMAGE_EXTS = /\.(png|jpg|jpeg|gif|webp|bmp|ico|avif)$/i;
const _PDF_EXTS = /\.pdf$/i;
const _HTML_EXTS = /\.(html?|htm)$/i;
const _ARCHIVE_EXTS = /\.(zip|tar|tar\.gz|tgz|tar\.bz2|tbz2|tar\.xz|txz)$/i;
const _SVG_EXTS = /\.svg$/i;
const _AUDIO_EXTS = /\.(mp3|ogg|wav|m4a|aac|flac|wma|opus|webm|oga)$/i;
const _VIDEO_EXTS = /\.(mp4|webm|mkv|mov|avi|ogv|m4v)$/i;
const _CSV_EXTS = /\.csv$/i;
const _EXCALIDRAW_EXTS = /\.excalidraw$/i;

const MEDIA_PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2];
const MEDIA_PLAYBACK_STORAGE_KEY = 'agy-media-playback-rate';

const _DATA_IMAGE_RE = /^data:image\/(?:png|jpe?g|gif|webp|avif)(?:;base64)?,[a-z0-9+/=%._~:@!$&'()*+,;-]*$/i;
const _DATA_IMAGE_SVG_RE = /^data:image\/svg\+xml;base64,[a-z0-9+/=]+$/i;
const _DATA_IMAGE_MAX_LEN = 2 * 1024 * 1024;

let _mediaVisibilityObserver = null;

/* ── Internal Utilities ────────────────────────────────────────────────────── */
function _escMedia(s) {
  if (typeof esc === 'function') return esc(s);
  if (typeof window !== 'undefined' && typeof window.esc === 'function') return window.esc(s);
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _tMedia(key) {
  if (typeof t === 'function') return t(key);
  if (typeof window !== 'undefined' && typeof window.t === 'function') return window.t(key);
  const fallbacks = {
    media_svg_label: 'svg',
    media_download: 'Download',
    pdf_loading: 'Loading',
    html_loading: 'Loading',
    diff_loading: 'Loading diff',
    csv_loading: 'Loading',
    excalidraw_loading: 'Loading',
  };
  return fallbacks[key] || key;
}

function _liMedia(name, size = 16) {
  if (typeof li === 'function') return li(name, size);
  if (typeof window !== 'undefined' && typeof window.li === 'function') return window.li(name, size);
  return '';
}

/* ── Image Lightbox ────────────────────────────────────────────────────────── */
function _openImgLightbox(imgEl) {
  if (!imgEl || !imgEl.src) return;
  const src = imgEl.src, alt = imgEl.alt || '';
  let allImages = [];
  let startIndex = 0;
  if (!imgEl.closest('.attach-tray')) {
    let container = imgEl.closest('.msg-row, .assistant-turn-blocks, .assistant-turn, .user-turn');
    if (!container) container = imgEl.parentElement;
    if (container) {
      const siblings = container.querySelectorAll('.msg-media-img');
      if (siblings.length > 1) {
        allImages = Array.from(siblings);
        startIndex = allImages.indexOf(imgEl);
        if (startIndex === -1) startIndex = 0;
      }
    }
  }
  _openImgLightboxWithNav(src, alt, allImages, startIndex);
}

function _openImgLightboxWithNav(src, alt, images, index) {
  if (typeof document === 'undefined') return null;
  const lb = document.createElement('div');
  lb.className = 'img-lightbox';
  lb.setAttribute('role', 'dialog');
  lb.setAttribute('aria-modal', 'true');
  lb.setAttribute('aria-label', alt || 'Image');
  const img = document.createElement('img');
  img.src = src;
  img.alt = alt || '';
  img.onclick = e => e.stopPropagation();
  const cls = document.createElement('button');
  cls.className = 'img-lightbox-close';
  cls.setAttribute('aria-label', 'Close');
  cls.textContent = '×';
  cls.onclick = () => _closeImgLightbox(lb);
  lb.appendChild(img);
  lb.appendChild(cls);

  lb._navIndex = index;
  lb._navImages = (images && images.length > 1) ? images : null;
  if (lb._navImages) {
    const prevBtn = document.createElement('button');
    prevBtn.className = 'img-lightbox-nav img-lightbox-nav-prev';
    prevBtn.setAttribute('aria-label', 'Previous image');
    prevBtn.innerHTML = '‹';
    prevBtn.onclick = e => { e.stopPropagation(); _navigateLightbox(lb, -1); };
    lb.appendChild(prevBtn);
    const nextBtn = document.createElement('button');
    nextBtn.className = 'img-lightbox-nav img-lightbox-nav-next';
    nextBtn.setAttribute('aria-label', 'Next image');
    nextBtn.innerHTML = '›';
    nextBtn.onclick = e => { e.stopPropagation(); _navigateLightbox(lb, 1); };
    lb.appendChild(nextBtn);
    lb._counterEl = document.createElement('div');
    lb._counterEl.className = 'img-lightbox-counter';
    lb.appendChild(lb._counterEl);
    lb._counterEl.textContent = (index + 1) + ' / ' + images.length;
  }
  lb.onclick = () => _closeImgLightbox(lb);
  document.body.appendChild(lb);

  lb._keyHandler = e => {
    if (e.key === 'Escape') { _closeImgLightbox(lb); return; }
    if (lb._navImages) {
      if (e.key === 'ArrowLeft') { e.preventDefault(); _navigateLightbox(lb, -1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); _navigateLightbox(lb, 1); }
    }
  };
  document.addEventListener('keydown', lb._keyHandler);
  return lb;
}

function _navigateLightbox(lb, direction) {
  const images = lb._navImages;
  if (!images) return;
  const newIndex = lb._navIndex + direction;
  if (newIndex < 0 || newIndex >= images.length) return;
  lb._navIndex = newIndex;
  const nextImg = images[newIndex];
  const lbImg = lb.querySelector('img');
  if (!lbImg) return;
  lbImg.src = nextImg.src;
  lbImg.alt = nextImg.alt || '';
  lb.setAttribute('aria-label', nextImg.alt || 'Image');
  if (lb._counterEl) lb._counterEl.textContent = (newIndex + 1) + ' / ' + images.length;
}

function _closeImgLightbox(lb) {
  if (!lb || !lb.parentNode) return;
  if (typeof document !== 'undefined') {
    document.removeEventListener('keydown', lb._keyHandler);
  }
  if (lb._mermaidResizeHandler && typeof window !== 'undefined' && typeof window.removeEventListener === 'function') {
    window.removeEventListener('resize', lb._mermaidResizeHandler);
  }
  if (lb._mermaidResizeTimer && typeof clearTimeout === 'function') {
    clearTimeout(lb._mermaidResizeTimer);
    lb._mermaidResizeTimer = null;
  }
  lb.style.animation = 'lb-in .12s ease reverse';
  setTimeout(() => lb.parentNode && lb.parentNode.removeChild(lb), 120);
}

/* ── Interactive Mermaid Viewer ────────────────────────────────────────────── */
function _mermaidViewerIcon(kind) {
  const icons = {
    zoomIn: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="6"></circle><path d="M10 7v6M7 10h6"></path><path d="M15 15l4 4"></path></svg>',
    zoomOut: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="6"></circle><path d="M7 10h6"></path><path d="M15 15l4 4"></path></svg>',
    reset: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9V4H1"></path><path d="M1 4l4 4"></path><path d="M10 4a8 8 0 1 1-5.66 13.66"></path></svg>',
    fit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"></path></svg>',
    fullscreen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"></path><path d="M4 4l5 5M20 4l-5 5M4 20l5-5M20 20l-5-5"></path></svg>',
  };
  return icons[kind] || '';
}

function _createMermaidViewerButton(label, iconKind, onClick) {
  if (typeof document === 'undefined') return null;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'mermaid-viewer-btn';
  btn.setAttribute('aria-label', label);
  btn.setAttribute('title', label);
  btn.innerHTML = _mermaidViewerIcon(iconKind);
  btn.onclick = e => {
    e.preventDefault();
    e.stopPropagation();
    onClick(e);
  };
  return btn;
}

function _mermaidSvgBox(svgEl) {
  const box = { x: 0, y: 0, width: 0, height: 0 };
  if (!svgEl) return box;
  const viewBox = svgEl.viewBox && svgEl.viewBox.baseVal;
  if (viewBox && viewBox.width && viewBox.height) {
    box.x = Number(viewBox.x) || 0;
    box.y = Number(viewBox.y) || 0;
    box.width = Number(viewBox.width) || 0;
    box.height = Number(viewBox.height) || 0;
    return box;
  }
  const rawViewBox = svgEl.getAttribute && svgEl.getAttribute('viewBox');
  if (rawViewBox) {
    const parts = rawViewBox.trim().split(/[,\s]+/).map(Number);
    if (parts.length >= 4 && parts.every(n => Number.isFinite(n))) {
      box.x = parts[0] || 0;
      box.y = parts[1] || 0;
      box.width = parts[2] || 0;
      box.height = parts[3] || 0;
      return box;
    }
  }
  const width = Number.parseFloat(svgEl.getAttribute && svgEl.getAttribute('width')) || (svgEl.getBoundingClientRect ? svgEl.getBoundingClientRect().width : 0) || 0;
  const height = Number.parseFloat(svgEl.getAttribute && svgEl.getAttribute('height')) || (svgEl.getBoundingClientRect ? svgEl.getBoundingClientRect().height : 0) || 0;
  box.width = width || 800;
  box.height = height || 450;
  return box;
}

function _mountMermaidViewer(svgEl, options = {}) {
  if (!svgEl || typeof document === 'undefined') return null;
  const mode = options.mode === 'lightbox' ? 'lightbox' : 'inline';
  const openLightbox = typeof options.openLightbox === 'function' ? options.openLightbox : () => _openMermaidLightbox(svgEl);
  const box = _mermaidSvgBox(svgEl);
  const host = svgEl.parentNode;
  const root = document.createElement('div');
  root.className = 'mermaid-viewer mermaid-viewer--' + mode;
  const toolbar = document.createElement('div');
  toolbar.className = 'mermaid-viewer-toolbar';
  const viewport = document.createElement('div');
  viewport.className = 'mermaid-viewer-viewport';
  const canvas = document.createElement('div');
  canvas.className = 'mermaid-viewer-canvas';
  canvas.style.width = Math.max(1, Math.round(box.width)) + 'px';
  canvas.style.height = Math.max(1, Math.round(box.height)) + 'px';
  svgEl.classList.add('mermaid-viewer-svg');
  if (mode === 'lightbox') svgEl.classList.add('mermaid-lightbox-svg');
  svgEl.style.width = '100%';
  svgEl.style.height = '100%';
  svgEl.style.display = 'block';
  viewport.appendChild(canvas);
  root.appendChild(toolbar);
  root.appendChild(viewport);
  if (host) host.replaceChild(root, svgEl);
  canvas.appendChild(svgEl);

  const state = {
    box,
    canvas,
    dragging: false,
    dragOriginX: 0,
    dragOriginY: 0,
    dragPointerId: null,
    dragStartX: 0,
    dragStartY: 0,
    dragged: false,
    mode,
    root,
    toolbar,
    scale: 1,
    svg: svgEl,
    viewport,
    x: 0,
    y: 0,
    pinching: false,
    pinchStartDist: 0,
    pinchStartScale: 1,
    pinchStartCX: 0,
    pinchStartCY: 0,
    pinchStartX: 0,
    pinchStartY: 0,
  };
  root._mermaidViewer = state;

  function _lightboxViewportEnvelope() {
    const width = Math.round(((typeof window !== 'undefined' && window.innerWidth) || box.width) * 0.9);
    const height = Math.round(((typeof window !== 'undefined' && window.innerHeight) || box.height) * 0.9);
    return {
      width: Math.max(1, Number.isFinite(width) ? width : 1),
      height: Math.max(1, Number.isFinite(height) ? height : 1),
    };
  }

  function _viewportFallbackSize() {
    if (mode === 'lightbox') return _lightboxViewportEnvelope();
    const width = Math.round((typeof window !== 'undefined' && window.innerWidth) || box.width);
    const height = Math.round(((typeof window !== 'undefined' && window.innerHeight) || box.height) * 0.7);
    return {
      width: Math.max(1, Number.isFinite(width) ? width : 1),
      height: Math.max(1, Number.isFinite(height) ? height : 1),
    };
  }

  function _viewportSize() {
    const rect = viewport.getBoundingClientRect ? viewport.getBoundingClientRect() : null;
    const fallback = _viewportFallbackSize();
    const width = mode === 'lightbox'
      ? fallback.width
      : (viewport.clientWidth || (rect && rect.width) || fallback.width);
    const height = mode === 'lightbox'
      ? fallback.height
      : (viewport.clientHeight || (rect && rect.height) || fallback.height);
    return {
      width: Math.max(1, Number(width) || box.width || 1),
      height: Math.max(1, Number(height) || box.height || 1),
    };
  }

  function _rawFitScale(size) {
    return Math.min(size.width / Math.max(1, box.width), size.height / Math.max(1, box.height));
  }

  function _minScale() {
    if (mode === 'lightbox') return Math.min(_MERMAID_VIEWER_MIN_SCALE, _rawFitScale(_viewportSize()));
    return Math.min(_MERMAID_VIEWER_MIN_SCALE, _inlineViewportHeight() / Math.max(1, box.height));
  }

  function _inlineViewportHeight() {
    const size = _viewportSize();
    const widthFitScale = size.width / Math.max(1, box.width);
    const widthBasedHeight = Math.max(1, Math.round(box.height * widthFitScale));
    const fallback = _viewportFallbackSize();
    return Math.min(fallback.height, Math.max(_MERMAID_VIEWER_INLINE_MIN_HEIGHT, widthBasedHeight));
  }

  function _applyTransform() {
    canvas.style.transform = `translate(${Math.round(state.x)}px, ${Math.round(state.y)}px) scale(${state.scale})`;
    canvas.style.transformOrigin = '0 0';
  }

  function _centerForScale(nextScale) {
    const size = _viewportSize();
    const scaledWidth = box.width * nextScale;
    const scaledHeight = box.height * nextScale;
    state.x = scaledWidth < size.width ? Math.round((size.width - scaledWidth) / 2) : 0;
    state.y = scaledHeight < size.height ? Math.round((size.height - scaledHeight) / 2) : 0;
  }

  function _fitScale() {
    const size = _viewportSize();
    return Math.max(_minScale(), Math.min(_MERMAID_VIEWER_MAX_SCALE, _rawFitScale(size)));
  }

  function _setScale(nextScale, anchorX, anchorY) {
    const bounded = Math.max(_minScale(), Math.min(_MERMAID_VIEWER_MAX_SCALE, nextScale));
    if (!Number.isFinite(bounded) || !box.width || !box.height) return;
    const focusX = Number.isFinite(anchorX) ? anchorX : _viewportSize().width / 2;
    const focusY = Number.isFinite(anchorY) ? anchorY : _viewportSize().height / 2;
    if (state.scale) {
      const ratio = bounded / state.scale;
      state.x = focusX - (focusX - state.x) * ratio;
      state.y = focusY - (focusY - state.y) * ratio;
    }
    state.scale = bounded;
    _applyTransform();
  }

  function _fitViewer() {
    const nextScale = _fitScale();
    state.fitScale = nextScale;
    state.scale = nextScale;
    _centerForScale(nextScale);
    _applyTransform();
  }

  function _resizeToEnvelope() {
    if (mode !== 'lightbox') return;
    const hadFitScale = Number.isFinite(state.fitScale);
    const previousFitScale = hadFitScale ? state.fitScale : _fitScale();
    const wasAtFit = !hadFitScale || Math.abs(state.scale - previousFitScale) < 1e-9;
    const envelope = _lightboxViewportEnvelope();
    viewport.style.width = Math.max(1, Math.round(envelope.width)) + 'px';
    viewport.style.height = Math.max(1, Math.round(envelope.height)) + 'px';
    const nextFitScale = _fitScale();
    state.fitScale = nextFitScale;
    if (wasAtFit) {
      state.scale = nextFitScale;
      _centerForScale(state.scale);
    } else {
      state.scale = Math.max(_minScale(), Math.min(_MERMAID_VIEWER_MAX_SCALE, state.scale));
    }
    _applyTransform();
  }

  function _resetViewer() {
    state.scale = 1;
    _centerForScale(1);
    _applyTransform();
  }

  function _zoomIn() {
    const size = _viewportSize();
    _setScale(state.scale * _MERMAID_VIEWER_ZOOM_STEP, size.width / 2, size.height / 2);
  }

  function _zoomOut() {
    const size = _viewportSize();
    _setScale(state.scale / _MERMAID_VIEWER_ZOOM_STEP, size.width / 2, size.height / 2);
  }

  function _zoomFromWheel(e) {
    if (e.preventDefault) e.preventDefault();
    const rect = viewport.getBoundingClientRect ? viewport.getBoundingClientRect() : { left: 0, top: 0 };
    const anchorX = Number.isFinite(e.clientX) ? e.clientX - rect.left : undefined;
    const anchorY = Number.isFinite(e.clientY) ? e.clientY - rect.top : undefined;
    const deltaMode = Number(e.deltaMode) || 0;
    const lineScale = deltaMode === 1 ? 30 : deltaMode === 2 ? 600 : 1;
    const factor = Math.exp((-(Number(e.deltaY) || 0)) * lineScale * 0.0015);
    _setScale(state.scale * factor, anchorX, anchorY);
  }

  function _onPointerDown(e) {
    if (state.pinching) return;
    if (e.button != null && e.button !== 0) return;
    state.dragging = true;
    state.dragged = false;
    state.dragOriginX = Number(e.clientX) || 0;
    state.dragOriginY = Number(e.clientY) || 0;
    state.dragPointerId = e.pointerId != null ? e.pointerId : null;
    state.dragStartX = state.x;
    state.dragStartY = state.y;
    viewport.classList.add('is-panning');
    if (state.dragPointerId != null && viewport.setPointerCapture) viewport.setPointerCapture(state.dragPointerId);
    if (e.preventDefault) e.preventDefault();
  }

  function _onPointerMove(e) {
    if (state.pinching) return;
    if (!state.dragging) return;
    const dx = (Number(e.clientX) || 0) - state.dragOriginX;
    const dy = (Number(e.clientY) || 0) - state.dragOriginY;
    if (Math.abs(dx) + Math.abs(dy) > 3) state.dragged = true;
    state.x = state.dragStartX + dx;
    state.y = state.dragStartY + dy;
    _applyTransform();
  }

  function _endPointerDrag() {
    if (!state.dragging) return;
    state.dragging = false;
    if (state.dragPointerId != null && viewport.releasePointerCapture) {
      try { viewport.releasePointerCapture(state.dragPointerId); } catch (_) {}
    }
    state.dragPointerId = null;
    viewport.classList.remove('is-panning');
  }

  function _openViewerOnClick(e) {
    if (state.pinching) return;
    if (mode !== 'inline') return;
    if (state.dragged) {
      state.dragged = false;
      return;
    }
    if (e.preventDefault) e.preventDefault();
    if (e.stopPropagation) e.stopPropagation();
    openLightbox();
  }

  function _touchDist(touches) {
    if (!touches || touches.length < 2) return 0;
    const dx = touches[0].clientX - touches[1].clientX;
    const dy = touches[0].clientY - touches[1].clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  function _onTouchStart(e) {
    if (e.touches.length === 2) {
      state.pinching = true;
      state.pinchStartDist = _touchDist(e.touches);
      state.pinchStartScale = state.scale;
      state.pinchStartX = state.x;
      state.pinchStartY = state.y;
      const rect = viewport.getBoundingClientRect();
      state.pinchStartCX = (e.touches[0].clientX + e.touches[1].clientX) / 2 - (rect.left || 0);
      state.pinchStartCY = (e.touches[0].clientY + e.touches[1].clientY) / 2 - (rect.top || 0);
      _endPointerDrag();
      if (e.preventDefault) e.preventDefault();
    }
  }

  function _onTouchMove(e) {
    if (!state.pinching || e.touches.length < 2) return;
    const rect = viewport.getBoundingClientRect();
    const cx = (e.touches[0].clientX + e.touches[1].clientX) / 2 - (rect.left || 0);
    const cy = (e.touches[0].clientY + e.touches[1].clientY) / 2 - (rect.top || 0);
    const currDist = _touchDist(e.touches);
    if (state.pinchStartDist > 0 && state.pinchStartScale > 0) {
      const rawScale = state.pinchStartScale * (currDist / state.pinchStartDist);
      const boundedScale = Math.max(_minScale(), Math.min(_MERMAID_VIEWER_MAX_SCALE, rawScale));
      const ratio = boundedScale / state.pinchStartScale;
      state.scale = boundedScale;
      state.x = cx - (state.pinchStartCX - state.pinchStartX) * ratio;
      state.y = cy - (state.pinchStartCY - state.pinchStartY) * ratio;
      _applyTransform();
    }
    if (e.preventDefault) e.preventDefault();
  }

  function _onTouchEnd(e) {
    if (e.touches.length < 2 && state.pinching) {
      state.pinching = false;
      state.dragged = true;
    }
  }

  viewport.onpointerdown = _onPointerDown;
  viewport.onpointermove = _onPointerMove;
  viewport.onpointerup = _endPointerDrag;
  viewport.onpointercancel = _endPointerDrag;
  viewport.onpointerleave = _endPointerDrag;
  viewport.onwheel = _zoomFromWheel;
  viewport.onclick = _openViewerOnClick;
  viewport.addEventListener('touchstart', _onTouchStart, { passive: false });
  viewport.addEventListener('touchmove', _onTouchMove, { passive: false });
  viewport.addEventListener('touchend', _onTouchEnd);
  viewport.addEventListener('touchcancel', function _onTouchCancel() { state.pinching = false; });
  root.onclick = e => e.stopPropagation();
  state.fit = _fitViewer;
  state.reset = _resetViewer;
  state.zoomIn = _zoomIn;
  state.zoomOut = _zoomOut;
  state.zoomAt = _setScale;
  state.applyTransform = _applyTransform;
  state.resizeToEnvelope = _resizeToEnvelope;
  state.openLightbox = openLightbox;

  toolbar.appendChild(_createMermaidViewerButton('Zoom in', 'zoomIn', _zoomIn));
  toolbar.appendChild(_createMermaidViewerButton('Zoom out', 'zoomOut', _zoomOut));
  toolbar.appendChild(_createMermaidViewerButton('Reset view', 'reset', _resetViewer));
  toolbar.appendChild(_createMermaidViewerButton('Fit to screen', 'fit', _fitViewer));
  if (mode === 'inline') {
    toolbar.appendChild(_createMermaidViewerButton('Fullscreen', 'fullscreen', openLightbox));
  }

  if (mode === 'lightbox') {
    state.resizeToEnvelope();
  } else {
    const initialHeight = _inlineViewportHeight();
    const readableScale = initialHeight / Math.max(1, box.height);
    state.scale = Math.max(_minScale(), Math.min(_MERMAID_VIEWER_MAX_SCALE, readableScale));
    viewport.style.width = '100%';
    viewport.style.height = Math.max(1, Math.round(initialHeight)) + 'px';
    _centerForScale(state.scale);
    _applyTransform();
  }

  return root;
}

function _openMermaidLightbox(svgEl) {
  if (!svgEl || typeof document === 'undefined') return null;
  const lb = document.createElement('div');
  lb.className = 'img-lightbox';
  lb.setAttribute('role', 'dialog');
  lb.setAttribute('aria-modal', 'true');
  lb.setAttribute('aria-label', 'Mermaid diagram');
  const clone = svgEl.cloneNode(true);
  const idMap = new Map();
  const idPrefix = 'mermaid-lightbox-' + Math.random().toString(36).slice(2, 10) + '-';
  const idNodes = [clone, ...clone.querySelectorAll('[id]')].filter(el => el.id);
  idNodes.forEach(el => {
    const nextId = idPrefix + el.id;
    idMap.set(el.id, nextId);
    el.id = nextId;
  });
  if (idMap.size) {
    const refAttrs = ['href', 'xlink:href', 'fill', 'stroke', 'filter', 'clip-path', 'mask', 'marker-start', 'marker-mid', 'marker-end', 'aria-labelledby', 'aria-describedby'];
    [clone, ...clone.querySelectorAll('*')].forEach(el => {
      refAttrs.forEach(attr => {
        const value = el.getAttribute(attr);
        if (!value) return;
        let nextValue = value.replace(/url\(#([^)]+)\)/g, (match, refId) => idMap.has(refId) ? `url(#${idMap.get(refId)})` : match);
        if (nextValue.startsWith('#') && idMap.has(nextValue.slice(1))) {
          nextValue = '#' + idMap.get(nextValue.slice(1));
        }
        if (nextValue !== value) {
          el.setAttribute(attr, nextValue);
        }
      });
    });
    clone.querySelectorAll('style').forEach(styleEl => {
      let styleText = styleEl.textContent || '';
      idMap.forEach((nextId, originalId) => {
        const escapedId = originalId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        styleText = styleText.replace(new RegExp(`url\\(#${escapedId}\\)`, 'g'), `url(#${nextId})`);
        styleText = styleText.replace(new RegExp(`(^|[^\\w-])#${escapedId}(?=$|[^\\w-])`, 'g'), (match, prefix) => `${prefix}#${nextId}`);
      });
      styleEl.textContent = styleText;
    });
  }
  clone.removeAttribute('width');
  clone.removeAttribute('height');
  const viewer = _mountMermaidViewer(clone, { mode: 'lightbox' });
  if (viewer && viewer._mermaidViewer && typeof viewer._mermaidViewer.resizeToEnvelope === 'function') {
    lb._mermaidResizeHandler = () => {
      if (lb._mermaidResizeTimer && typeof clearTimeout === 'function') clearTimeout(lb._mermaidResizeTimer);
      lb._mermaidResizeTimer = setTimeout(() => {
        lb._mermaidResizeTimer = null;
        viewer._mermaidViewer.resizeToEnvelope();
      }, 120);
    };
    if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
      window.addEventListener('resize', lb._mermaidResizeHandler);
    }
  }
  const cls = document.createElement('button');
  cls.className = 'img-lightbox-close';
  cls.setAttribute('aria-label', 'Close');
  cls.textContent = '×';
  cls.onclick = () => _closeImgLightbox(lb);
  lb.appendChild(viewer);
  lb.appendChild(cls);
  lb.onclick = () => _closeImgLightbox(lb);
  lb._keyHandler = e => {
    if (e.key === 'Escape') _closeImgLightbox(lb);
  };
  document.body.appendChild(lb);
  document.addEventListener('keydown', lb._keyHandler);
  return lb;
}

/* ── Media Playback Speed & Observers ──────────────────────────────────────── */
function _getStoredMediaPlaybackRate() {
  try {
    if (typeof localStorage === 'undefined') return 1;
    const raw = localStorage.getItem(MEDIA_PLAYBACK_STORAGE_KEY);
    const rate = Number(raw);
    return MEDIA_PLAYBACK_RATES.includes(rate) ? rate : 1;
  } catch (_) { return 1; }
}

function _setStoredMediaPlaybackRate(rate) {
  if (!MEDIA_PLAYBACK_RATES.includes(rate)) return;
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem(MEDIA_PLAYBACK_STORAGE_KEY, String(rate));
  } catch (_) {}
}

function _syncMediaSpeedButtons(editor, rate) {
  if (!editor) return;
  editor.querySelectorAll('.media-speed-btn').forEach(b => {
    const active = Number(b.dataset.rate) === rate;
    b.classList.toggle('active', active);
    b.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function _applyMediaPlaybackRate(media, rate = _getStoredMediaPlaybackRate()) {
  if (!media) return;
  media.playbackRate = rate;
  _syncMediaSpeedButtons(media.closest('.msg-media-editor,.preview-media-wrap'), rate);
}

function _promoteVisibleVideoPreload(video) {
  if (!video || !video.matches || !video.matches('.msg-media-video')) return;
  if (video.isConnected === false) return;
  if (video.dataset && video.dataset.visiblePreload === '1') return;
  if (video.dataset) video.dataset.visiblePreload = '1';
  video.preload = 'auto';
  if (video.paused && video.readyState < 4 && typeof video.load === 'function') video.load();
}

function _observeVideoPreload(video) {
  if (!video || !video.matches || !video.matches('.msg-media-video')) return;
  if (video.dataset && video.dataset.visiblePreload === '1') return;
  if (_mediaVisibilityObserver) _mediaVisibilityObserver.observe(video);
}

function _unobserveVideoPreload(video) {
  if (!video || !_mediaVisibilityObserver) return;
  _mediaVisibilityObserver.unobserve(video);
}

function _initMediaVisibilityObserver() {
  if (_mediaVisibilityObserver || typeof IntersectionObserver === 'undefined') return;
  _mediaVisibilityObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const video = entry.target;
      _unobserveVideoPreload(video);
      _promoteVisibleVideoPreload(video);
    }
  }, { root: null, rootMargin: '300px 0px', threshold: 0.01 });
}

function _mediaKindForName(name = '') {
  const clean = String(name || '').split('?')[0].toLowerCase();
  if (_VIDEO_EXTS.test(clean)) return 'video';
  if (_AUDIO_EXTS.test(clean)) return 'audio';
  if (_IMAGE_EXTS.test(clean)) return 'image';
  return '';
}

function _mediaSpeedControlsHtml(kind, label) {
  const safeLabel = _escMedia(label || kind || 'media');
  const current = _getStoredMediaPlaybackRate();
  return `<div class="media-speed-controls" role="group" aria-label="Playback speed for ${safeLabel}">${MEDIA_PLAYBACK_RATES.map(rate => `<button type="button" class="media-speed-btn${rate === current ? ' active' : ''}" data-rate="${rate}" aria-pressed="${rate === current ? 'true' : 'false'}">${rate}×</button>`).join('')}</div>`;
}

function _mediaPlayerHtml(kind, src, name, extra = '') {
  const safeName = _escMedia(name || 'media');
  const safeSrc = _escMedia(src);
  const tag = kind === 'video'
    ? `<video class="msg-media-player msg-media-video" src="${safeSrc}" controls preload="metadata" playsinline title="${safeName}"></video>`
    : `<audio class="msg-media-player msg-media-audio" src="${safeSrc}" controls preload="metadata" title="${safeName}"></audio>`;
  return `<div class="msg-media-editor msg-media-editor--${kind}" data-media-kind="${kind}">${tag}<div class="msg-media-meta"><span class="msg-media-name">${safeName}</span>${extra}</div>${_mediaSpeedControlsHtml(kind, safeName)}</div>`;
}

function _isSafeDataImageUri(ref) {
  const value = String(ref || '');
  return value.length <= _DATA_IMAGE_MAX_LEN
    && (_DATA_IMAGE_RE.test(value) || _DATA_IMAGE_SVG_RE.test(value));
}

function _dataImageHtml(ref, altText) {
  if (!_isSafeDataImageUri(ref)) return null;
  return `<img class="msg-media-img" src="${_escMedia(ref)}" alt="${_escMedia(altText || 'image')}" loading="lazy">`;
}

function _mdImageHtml(alt, url) {
  if (/^data:/i.test(url)) {
    const img = _dataImageHtml(url, alt);
    if (img) return img;
    return _escMedia(`![${alt}](${String(url).slice(0, 64)}…)`);
  }
  if (/^file:\/\//i.test(url)) return _inlineMediaHtmlForRef(url, undefined, alt);
  return `<img src="${url.replace(/"/g, '%22')}" alt="${_escMedia(alt)}" class="msg-media-img" loading="lazy">`;
}

function _inlineMediaHtmlForRef(ref, sessionId, altText) {
  if (ref == null) return '';
  if (/^data:/i.test(ref)) {
    const img = _dataImageHtml(ref, altText === undefined ? 'image' : altText);
    if (img) return img;
    return `<code>${_escMedia(String(ref).slice(0, 64))}…</code>`;
  }
  if (/^file:\/\//i.test(ref)) {
    try {
      const u = new URL(ref);
      ref = decodeURIComponent(u.pathname || ref.replace(/^file:\/\//i, ''));
    } catch (_) {
      try { ref = decodeURIComponent(ref.replace(/^file:\/\//i, '')); }
      catch (__) { ref = ref.replace(/^file:\/\//i, ''); }
    }
  }
  if (/^https?:\/\//i.test(ref)) {
    let src = ref;
    if (/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?/i.test(src)) {
      const base = (typeof document !== 'undefined' && document.baseURI || '').replace(/\/$/, '');
      src = src.replace(/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?/i, base);
    }
    const urlPath = src.split('?')[0];
    if (_SVG_EXTS.test(urlPath)) {
      return `<img class="msg-media-svg" src="${_escMedia(src)}" alt="${_escMedia(_tMedia('media_svg_label'))}" loading="lazy">`;
    }
    const mediaKind = _mediaKindForName(urlPath);
    if (mediaKind === 'audio' || mediaKind === 'video') return _mediaPlayerHtml(mediaKind, src, urlPath.split('/').pop() || mediaKind);
    if (_IMAGE_EXTS.test(urlPath) || /^https?:\/\//i.test(src)) {
      return `<img class="msg-media-img" src="${_escMedia(src)}" alt="image" loading="lazy">`;
    }
    return `<a href="${_escMedia(src)}" target="_blank" rel="noopener">${_escMedia(src)}</a>`;
  }

  const sid = sessionId
    || (typeof window !== 'undefined' && window.S && window.S.session && window.S.session.session_id ? String(window.S.session.session_id) : '')
    || (typeof S !== 'undefined' && S && S.session && S.session.session_id ? String(S.session.session_id) : '')
    || '';
  const apiUrl = 'api/media?path=' + encodeURIComponent(ref) + (sid ? '&session_id=' + encodeURIComponent(sid) : '');
  const localKind = _mediaKindForName(ref);
  if (localKind === 'image') {
    const safeName = _escMedia(altText === undefined ? (ref.split('/').pop() || 'image') : altText);
    const dlLabel = _escMedia(_tMedia('media_download'));
    const dlSvg = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>';
    return `<span class="msg-artifact-image"><img class="msg-media-img" src="${_escMedia(apiUrl)}" alt="${safeName}" loading="lazy"><a class="msg-artifact-download" href="${_escMedia(apiUrl)}" download="${safeName}" title="${dlLabel}" aria-label="${dlLabel}" onclick="event.stopPropagation()">${dlSvg}</a></span>`;
  }
  if (_SVG_EXTS.test(ref)) return `<img class="msg-media-svg" src="${_escMedia(apiUrl)}" alt="${_escMedia(altText === undefined ? _tMedia('media_svg_label') : altText)}" loading="lazy">`;
  if (localKind === 'audio' || localKind === 'video') {
    return _mediaPlayerHtml(localKind, apiUrl + '&inline=1', ref.split('/').pop() || ref);
  }
  if (_PDF_EXTS.test(ref)) {
    const fname = _escMedia(ref.split('/').pop() || ref);
    return `<div class="pdf-preview-load" data-path="${_escMedia(ref)}"><span class="pdf-preview-spinner">⏳</span> ${_escMedia(_tMedia('pdf_loading'))} ${fname}...</div>`;
  }
  if (_HTML_EXTS.test(ref)) {
    return `<div class="html-preview-load" data-path="${_escMedia(ref)}"><span class="html-preview-spinner">⏳</span> ${_escMedia(_tMedia('html_loading'))}...</div>`;
  }
  const fname = _escMedia(ref.split('/').pop() || ref);
  if (/\.(patch|diff)$/i.test(ref)) return `<div class="diff-inline-load" data-path="${_escMedia(ref)}">${_escMedia(_tMedia('diff_loading'))} ${fname}...</div>`;
  if (_CSV_EXTS.test(ref)) return `<div class="csv-inline-load" data-path="${_escMedia(ref)}">${_escMedia(_tMedia('csv_loading'))} ${fname}...</div>`;
  if (_EXCALIDRAW_EXTS.test(ref)) return `<div class="excalidraw-inline-load" data-path="${_escMedia(ref)}">${_escMedia(_tMedia('excalidraw_loading'))} ${fname}...</div>`;
  return `<a class="msg-media-link" href="${_escMedia(apiUrl + '&download=1')}" download="${fname}">📎 ${fname}</a>`;
}

function _renderAttachmentHtml(fname, url) {
  const kind = _mediaKindForName(fname);
  if (kind === 'image') return `<img class="msg-media-img" src="${_escMedia(url)}" alt="${_escMedia(fname)}" loading="lazy">`;
  if (kind === 'audio' || kind === 'video') return _mediaPlayerHtml(kind, url, fname);
  if (_HTML_EXTS.test(fname)) {
    const inlineUrl = url + (String(url).includes('?') ? '&' : '?') + 'inline=1';
    return `<a class="msg-file-badge msg-file-badge--html" href="${_escMedia(inlineUrl)}" target="_blank" rel="noopener">${_liMedia('file-code', 12)} ${_escMedia(fname)}</a>`;
  }
  return `<div class="msg-file-badge">${_liMedia('paperclip', 12)} ${_escMedia(fname)}</div>`;
}

function _initMediaPlaybackObserver() {
  if (typeof document === 'undefined' || !document.body || (typeof window !== 'undefined' && window._mediaPlaybackObserver)) return;
  _initMediaVisibilityObserver();
  const obs = new MutationObserver(records => {
    for (const rec of records) {
      for (const node of rec.removedNodes || []) {
        if (!node || node.nodeType !== 1) continue;
        const videos = [];
        if (node.matches && node.matches('.msg-media-video')) videos.push(node);
        if (node.querySelectorAll) videos.push(...node.querySelectorAll('.msg-media-video'));
        videos.forEach(_unobserveVideoPreload);
      }
      for (const node of rec.addedNodes || []) {
        if (!node || node.nodeType !== 1) continue;
        const media = [];
        if (node.matches && node.matches('audio,video')) media.push(node);
        if (node.querySelectorAll) media.push(...node.querySelectorAll('audio,video'));
        media.forEach(m => {
          _applyMediaPlaybackRate(m);
          _observeVideoPreload(m);
        });
      }
    }
  });
  if (typeof window !== 'undefined') window._mediaPlaybackObserver = obs;
  obs.observe(document.body, { childList: true, subtree: true });
  document.querySelectorAll('audio,video').forEach(m => {
    _applyMediaPlaybackRate(m);
    _observeVideoPreload(m);
  });
}

/* ── DOM Event Listeners ───────────────────────────────────────────────────── */
if (typeof document !== 'undefined') {
  document.addEventListener('click', e => {
    if (!e.target || !e.target.closest) return;
    const btn = e.target.closest('.media-speed-btn');
    if (btn) {
      const editor = btn.closest('.msg-media-editor,.preview-media-wrap');
      if (!editor) return;
      const media = editor.querySelector('audio,video');
      if (!media) return;
      const rate = Number(btn.dataset.rate) || 1;
      _setStoredMediaPlaybackRate(rate);
      _applyMediaPlaybackRate(media, rate);
      return;
    }
    const img = e.target.closest('.msg-media-img');
    if (img) {
      _openImgLightbox(img);
      return;
    }
    const mermaidSvg = e.target.closest('.mermaid-rendered svg');
    if (mermaidSvg) {
      _openMermaidLightbox(mermaidSvg);
      return;
    }
    const thumb = e.target.closest('.attach-thumb');
    if (thumb && thumb.tagName === 'IMG') {
      _openImgLightbox(thumb);
      return;
    }
  });

  document.addEventListener('loadedmetadata', e => {
    if (e.target && e.target.matches && e.target.matches('.msg-media-player,audio,video')) {
      _applyMediaPlaybackRate(e.target);
    }
  }, true);

  document.addEventListener('play', e => {
    if (e.target && e.target.matches && e.target.matches('.msg-media-video')) {
      _promoteVisibleVideoPreload(e.target);
    }
  }, true);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initMediaPlaybackObserver);
  } else {
    _initMediaPlaybackObserver();
  }
  setTimeout(_initMediaPlaybackObserver, 0);
}

/* ── Global Window and Module Exports ──────────────────────────────────────── */
if (typeof window !== 'undefined') {
  window._MERMAID_VIEWER_MIN_SCALE = _MERMAID_VIEWER_MIN_SCALE;
  window._MERMAID_VIEWER_MAX_SCALE = _MERMAID_VIEWER_MAX_SCALE;
  window._MERMAID_VIEWER_ZOOM_STEP = _MERMAID_VIEWER_ZOOM_STEP;
  window._MERMAID_VIEWER_INLINE_MIN_HEIGHT = _MERMAID_VIEWER_INLINE_MIN_HEIGHT;
  window._IMAGE_EXTS = _IMAGE_EXTS;
  window._PDF_EXTS = _PDF_EXTS;
  window._HTML_EXTS = _HTML_EXTS;
  window._ARCHIVE_EXTS = _ARCHIVE_EXTS;
  window._SVG_EXTS = _SVG_EXTS;
  window._AUDIO_EXTS = _AUDIO_EXTS;
  window._VIDEO_EXTS = _VIDEO_EXTS;
  window._CSV_EXTS = _CSV_EXTS;
  window._EXCALIDRAW_EXTS = _EXCALIDRAW_EXTS;
  window.MEDIA_PLAYBACK_RATES = MEDIA_PLAYBACK_RATES;
  window.MEDIA_PLAYBACK_STORAGE_KEY = MEDIA_PLAYBACK_STORAGE_KEY;
  window._openImgLightbox = _openImgLightbox;
  window._openImgLightboxWithNav = _openImgLightboxWithNav;
  window._navigateLightbox = _navigateLightbox;
  window._closeImgLightbox = _closeImgLightbox;
  window._mermaidViewerIcon = _mermaidViewerIcon;
  window._createMermaidViewerButton = _createMermaidViewerButton;
  window._mermaidSvgBox = _mermaidSvgBox;
  window._mountMermaidViewer = _mountMermaidViewer;
  window._openMermaidLightbox = _openMermaidLightbox;
  window._getStoredMediaPlaybackRate = _getStoredMediaPlaybackRate;
  window._setStoredMediaPlaybackRate = _setStoredMediaPlaybackRate;
  window._syncMediaSpeedButtons = _syncMediaSpeedButtons;
  window._applyMediaPlaybackRate = _applyMediaPlaybackRate;
  window._promoteVisibleVideoPreload = _promoteVisibleVideoPreload;
  window._observeVideoPreload = _observeVideoPreload;
  window._unobserveVideoPreload = _unobserveVideoPreload;
  window._initMediaVisibilityObserver = _initMediaVisibilityObserver;
  window._mediaKindForName = _mediaKindForName;
  window._mediaSpeedControlsHtml = _mediaSpeedControlsHtml;
  window._mediaPlayerHtml = _mediaPlayerHtml;
  window._isSafeDataImageUri = _isSafeDataImageUri;
  window._dataImageHtml = _dataImageHtml;
  window._mdImageHtml = _mdImageHtml;
  window._inlineMediaHtmlForRef = _inlineMediaHtmlForRef;
  window._renderAttachmentHtml = _renderAttachmentHtml;
  window._initMediaPlaybackObserver = _initMediaPlaybackObserver;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    _MERMAID_VIEWER_MIN_SCALE,
    _MERMAID_VIEWER_MAX_SCALE,
    _MERMAID_VIEWER_ZOOM_STEP,
    _MERMAID_VIEWER_INLINE_MIN_HEIGHT,
    _IMAGE_EXTS,
    _PDF_EXTS,
    _HTML_EXTS,
    _ARCHIVE_EXTS,
    _SVG_EXTS,
    _AUDIO_EXTS,
    _VIDEO_EXTS,
    _CSV_EXTS,
    _EXCALIDRAW_EXTS,
    MEDIA_PLAYBACK_RATES,
    MEDIA_PLAYBACK_STORAGE_KEY,
    _openImgLightbox,
    _openImgLightboxWithNav,
    _navigateLightbox,
    _closeImgLightbox,
    _mermaidViewerIcon,
    _createMermaidViewerButton,
    _mermaidSvgBox,
    _mountMermaidViewer,
    _openMermaidLightbox,
    _getStoredMediaPlaybackRate,
    _setStoredMediaPlaybackRate,
    _syncMediaSpeedButtons,
    _applyMediaPlaybackRate,
    _promoteVisibleVideoPreload,
    _observeVideoPreload,
    _unobserveVideoPreload,
    _initMediaVisibilityObserver,
    _mediaKindForName,
    _mediaSpeedControlsHtml,
    _mediaPlayerHtml,
    _isSafeDataImageUri,
    _dataImageHtml,
    _mdImageHtml,
    _inlineMediaHtmlForRef,
    _renderAttachmentHtml,
    _initMediaPlaybackObserver,
  };
}
