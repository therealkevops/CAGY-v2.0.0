/**
 * Antigravity (CAGY) Visual Diff Engine & Live Canvas Preview
 * Provides dual-column synchronized side-by-side diffing and sandboxed live execution.
 */

let _currentDiffData = null;
let _previewCanvasMode = 'source'; // 'source' | 'diff' | 'preview'

/**
 * Switch preview pane display mode: 'source' (raw code), 'diff' (side-by-side visual diff), 'preview' (live execution).
 */
function switchPreviewCanvasMode(mode) {
  _previewCanvasMode = mode;
  const switcher = document.getElementById('previewModeSwitcher');
  if (switcher) {
    switcher.querySelectorAll('.preview-mode-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.mode === mode);
    });
  }

  const codeEl = document.getElementById('previewCode');
  const mdEl = document.getElementById('previewMd');
  const imgWrap = document.getElementById('previewImgWrap');
  const htmlWrap = document.getElementById('previewHtmlWrap');
  const diffWrap = document.getElementById('previewDiffWrap');
  const liveWrap = document.getElementById('previewLiveWrap');
  const editArea = document.getElementById('previewEditArea');

  if (diffWrap) diffWrap.style.display = mode === 'diff' ? 'flex' : 'none';
  if (liveWrap) liveWrap.style.display = mode === 'preview' ? 'flex' : 'none';

  if (mode === 'source') {
    if (diffWrap) diffWrap.style.display = 'none';
    if (liveWrap) liveWrap.style.display = 'none';
    // Restore default preview elements based on file kind
    if (typeof showPreview === 'function' && typeof _previewCurrentMode === 'string') {
      showPreview(_previewCurrentMode);
    }
  } else if (mode === 'diff') {
    if (codeEl) codeEl.style.display = 'none';
    if (mdEl) mdEl.style.display = 'none';
    if (imgWrap) imgWrap.style.display = 'none';
    if (htmlWrap) htmlWrap.style.display = 'none';
    if (editArea) editArea.style.display = 'none';

    // Load diff if not loaded yet
    if (!_currentDiffData && typeof _previewCurrentPath === 'string' && _previewCurrentPath) {
      loadAndRenderFileDiff(_previewCurrentPath);
    }
  } else if (mode === 'preview') {
    if (codeEl) codeEl.style.display = 'none';
    if (diffWrap) diffWrap.style.display = 'none';
    if (editArea) editArea.style.display = 'none';
    renderCurrentLivePreview();
  }
}

/**
 * Fetch and render git diff against HEAD for current workspace file.
 */
async function loadAndRenderFileDiff(filePath) {
  const diffWrap = document.getElementById('previewDiffWrap');
  if (!diffWrap) return;

  diffWrap.innerHTML = '<div class="diff-loading"><span class="loading-spinner"></span> Loading visual diff...</div>';

  try {
    const res = await fetch(`/api/diff/file?path=${encodeURIComponent(filePath)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _currentDiffData = data;
    renderSideBySideDiff(diffWrap, data);
  } catch (err) {
    diffWrap.innerHTML = `
      <div class="diff-error">
        <div class="diff-error-title">Could not compute visual diff</div>
        <div class="diff-error-msg">${escapeHtml(err.message || 'Unknown error')}</div>
      </div>
    `;
  }
}

/**
 * Render side-by-side dual-column diff table.
 */
function renderSideBySideDiff(containerEl, diffData) {
  if (!containerEl) return;
  if (!diffData || !Array.isArray(diffData.rows)) {
    containerEl.innerHTML = '<div class="diff-empty">No diff data available.</div>';
    return;
  }

  const stats = diffData.stats || {};
  const additions = stats.additions || 0;
  const deletions = stats.deletions || 0;
  const filename = diffData.filename || 'Diff';

  let headerHtml = `
    <div class="diff-header-bar">
      <span class="diff-file-title">${escapeHtml(filename)}</span>
      <div class="diff-stats-badge">
        <span class="diff-stat-add">+${additions}</span>
        <span class="diff-stat-del">-${deletions}</span>
      </div>
    </div>
  `;

  if (!diffData.has_changes && additions === 0 && deletions === 0) {
    containerEl.innerHTML = headerHtml + `
      <div class="diff-clean-state">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>
        <span>No uncommitted changes detected. File matches HEAD.</span>
      </div>
    `;
    return;
  }

  let rowsHtml = '';
  diffData.rows.forEach(r => {
    const left = r.left || { no: null, text: '', type: 'empty' };
    const right = r.right || { no: null, text: '', type: 'empty' };

    const leftTypeClass = left.type === 'delete' ? 'diff-del' : (left.type === 'empty' ? 'diff-empty' : 'diff-ctx');
    const rightTypeClass = right.type === 'insert' ? 'diff-ins' : (right.type === 'empty' ? 'diff-empty' : 'diff-ctx');

    const leftNo = left.no !== null ? left.no : '';
    const rightNo = right.no !== null ? right.no : '';

    const leftPrefix = left.type === 'delete' ? '-' : ' ';
    const rightPrefix = right.type === 'insert' ? '+' : ' ';

    rowsHtml += `
      <tr class="diff-row">
        <td class="diff-gutter diff-gutter-left ${leftTypeClass}">${leftNo}</td>
        <td class="diff-code diff-code-left ${leftTypeClass}">
          <span class="diff-marker">${leftPrefix}</span>
          <span>${escapeHtml(left.text)}</span>
        </td>
        <td class="diff-gutter diff-gutter-right ${rightTypeClass}">${rightNo}</td>
        <td class="diff-code diff-code-right ${rightTypeClass}">
          <span class="diff-marker">${rightPrefix}</span>
          <span>${escapeHtml(right.text)}</span>
        </td>
      </tr>
    `;
  });

  containerEl.innerHTML = `
    ${headerHtml}
    <div class="diff-table-container">
      <table class="diff-table">
        <colgroup>
          <col class="col-gutter">
          <col class="col-content">
          <col class="col-gutter">
          <col class="col-content">
        </colgroup>
        <thead>
          <tr class="diff-col-header">
            <th colspan="2" class="diff-head-left">Original (HEAD)</th>
            <th colspan="2" class="diff-head-right">Modified (Working Tree)</th>
          </tr>
        </thead>
        <tbody>
          ${rowsHtml}
        </tbody>
      </table>
    </div>
  `;
}

/**
 * Render live sandbox preview for HTML, SVG, Mermaid, or Markdown.
 */
function renderCurrentLivePreview() {
  const liveWrap = document.getElementById('previewLiveWrap');
  if (!liveWrap) return;

  const path = typeof _previewCurrentPath === 'string' ? _previewCurrentPath : '';
  const ext = path.split('.').pop().toLowerCase();

  let content = '';
  const editArea = document.getElementById('previewEditArea');
  if (editArea && editArea.value) {
    content = editArea.value;
  } else if (typeof _previewRawContent === 'string') {
    content = _previewRawContent;
  } else {
    const codeEl = document.getElementById('previewCode');
    content = codeEl ? codeEl.textContent : '';
  }

  if (ext === 'html' || ext === 'htm') {
    liveWrap.innerHTML = `
      <iframe class="canvas-live-frame" sandbox="allow-scripts allow-popups allow-modals" srcdoc="${escapeAttr(content)}"></iframe>
    `;
  } else if (ext === 'svg') {
    liveWrap.innerHTML = `
      <div class="canvas-svg-container">
        ${content}
      </div>
    `;
  } else if (ext === 'mermaid' || (ext === 'md' && content.includes('```mermaid'))) {
    renderMermaidLive(liveWrap, content);
  } else if (ext === 'md' || ext === 'markdown') {
    liveWrap.innerHTML = '<div class="canvas-markdown-container preview-md"></div>';
    const mdContainer = liveWrap.querySelector('.canvas-markdown-container');
    if (mdContainer && typeof renderMarkdownPreviewContent === 'function') {
      renderMarkdownPreviewContent({ content: content }, mdContainer);
    } else if (mdContainer) {
      mdContainer.textContent = content;
    }
  } else {
    liveWrap.innerHTML = `
      <div class="canvas-generic-info">
        <span>Live preview is optimized for HTML, SVG, Markdown, and Mermaid diagrams.</span>
      </div>
    `;
  }
}

/**
 * Render Mermaid diagram safely.
 */
function renderMermaidLive(containerEl, content) {
  let diagramText = content;
  const match = content.match(/```mermaid([\s\S]*?)```/);
  if (match) diagramText = match[1];

  const chartId = 'mermaid-' + Math.random().toString(36).substring(2, 9);
  containerEl.innerHTML = `<div class="mermaid" id="${chartId}">${escapeHtml(diagramText.trim())}</div>`;

  if (window.mermaid && typeof window.mermaid.run === 'function') {
    try {
      window.mermaid.run({ nodes: [document.getElementById(chartId)] });
    } catch (_) {}
  }
}

/**
 * Open full-screen Canvas Modal for expanded artifact inspection.
 */
function openCanvasModal() {
  const modal = document.getElementById('canvasModal');
  const titleEl = document.getElementById('canvasModalTitle');
  const bodyEl = document.getElementById('canvasModalBody');
  if (!modal || !bodyEl) return;

  const path = typeof _previewCurrentPath === 'string' ? _previewCurrentPath : 'Artifact Canvas';
  if (titleEl) titleEl.textContent = path.split('/').pop() || path;

  modal.style.display = 'flex';

  const ext = path.split('.').pop().toLowerCase();
  let content = '';
  const editArea = document.getElementById('previewEditArea');
  if (editArea && editArea.value) {
    content = editArea.value;
  } else if (typeof _previewRawContent === 'string') {
    content = _previewRawContent;
  } else {
    const codeEl = document.getElementById('previewCode');
    content = codeEl ? codeEl.textContent : '';
  }

  if (ext === 'html' || ext === 'htm') {
    bodyEl.innerHTML = `<iframe class="canvas-modal-frame" sandbox="allow-scripts allow-popups allow-modals" srcdoc="${escapeAttr(content)}"></iframe>`;
  } else if (ext === 'svg') {
    bodyEl.innerHTML = `<div class="canvas-modal-svg">${content}</div>`;
  } else if (ext === 'md' || ext === 'markdown') {
    bodyEl.innerHTML = '<div class="canvas-modal-md preview-md"></div>';
    const mdContainer = bodyEl.querySelector('.canvas-modal-md');
    if (mdContainer && typeof renderMarkdownPreviewContent === 'function') {
      renderMarkdownPreviewContent({ content: content }, mdContainer);
    } else if (mdContainer) {
      mdContainer.textContent = content;
    }
  } else {
    bodyEl.innerHTML = `<pre class="canvas-modal-code"><code>${escapeHtml(content)}</code></pre>`;
  }
}

function closeCanvasModal() {
  const modal = document.getElementById('canvasModal');
  if (modal) modal.style.display = 'none';
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

// Reset diff data whenever a new file is opened
window.resetPreviewDiff = function() {
  _currentDiffData = null;
  const diffWrap = document.getElementById('previewDiffWrap');
  if (diffWrap) diffWrap.innerHTML = '';
  switchPreviewCanvasMode('source');
};
