const FileExplorer = {
  treeEl: document.getElementById("file-tree"),
  viewerDrawer: document.getElementById("file-viewer-drawer"),
  viewerFilename: document.getElementById("viewer-filename"),
  viewerContent: document.getElementById("file-viewer-content").querySelector("code"),
  closeViewerBtn: document.getElementById("btn-close-viewer"),
  refreshBtn: document.getElementById("btn-refresh-files"),

  init() {
    this.refreshBtn.addEventListener("click", () => this.loadTree());
    this.closeViewerBtn.addEventListener("click", () => this.closeViewer());
    this.loadTree();
  },

  async loadTree(dirPath = ".") {
    this.treeEl.innerHTML = "<div style='color: var(--text-muted); font-size: 11px;'>Loading directory...</div>";
    try {
      const res = await fetch(`/api/workspace/tree?dir=${encodeURIComponent(dirPath)}`);
      const data = await res.json();
      if (!data.success) {
        this.treeEl.innerHTML = `<div style='color: var(--danger);'>Error: ${data.error}</div>`;
        return;
      }

      this.treeEl.innerHTML = "";
      if (data.items.length === 0) {
        this.treeEl.innerHTML = "<div style='color: var(--text-muted); font-size: 11px;'>(Empty directory)</div>";
        return;
      }

      data.items.forEach(item => {
        const itemEl = document.createElement("div");
        itemEl.className = `file-tree-item ${item.is_dir ? "dir" : "file"}`;
        
        const icon = item.is_dir ? "📁" : "📄";
        const sizeStr = item.is_dir ? "" : this.formatSize(item.size);
        
        itemEl.innerHTML = `
          <span>${icon}</span>
          <span>${item.name}</span>
          <span class="file-size">${sizeStr}</span>
        `;

        itemEl.addEventListener("click", () => {
          if (item.is_dir) {
            this.loadTree(item.path);
          } else {
            this.openFile(item.path);
          }
        });

        this.treeEl.appendChild(itemEl);
      });
    } catch (e) {
      this.treeEl.innerHTML = `<div style='color: var(--danger);'>Fetch error: ${e.message}</div>`;
    }
  },

  async openFile(filePath) {
    this.viewerFilename.textContent = filePath;
    this.viewerContent.textContent = "Loading file content...";
    this.viewerDrawer.classList.remove("hidden");

    try {
      const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(filePath)}`);
      const data = await res.json();
      if (data.success) {
        this.viewerContent.textContent = data.raw_content;
        if (window.hljs) {
          hljs.highlightElement(this.viewerContent);
        }
      } else {
        this.viewerContent.textContent = `Error: ${data.error}`;
      }
    } catch (e) {
      this.viewerContent.textContent = `Failed to open file: ${e.message}`;
    }
  },

  closeViewer() {
    this.viewerDrawer.classList.add("hidden");
  },

  formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
};
