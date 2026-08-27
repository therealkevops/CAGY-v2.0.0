/**
 * Model Context Protocol (MCP) Server Hub Frontend Module
 * Conforms to .gemini/rules/webui_design_system.md
 */

let _mcpHubData = null;
let _selectedMcpServer = null;
let _mcpToolFilterQuery = '';
let _mcpToolCategoryFilter = 'all';

async function loadMcpHub(force = false) {
  const listEl = document.getElementById('mcpHubServerList');
  if (!listEl) return;

  try {
    const res = await fetch('/api/mcp/hub');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _mcpHubData = data;
    renderMcpHubView(data);
  } catch (err) {
    if (listEl) {
      listEl.innerHTML = `<div style="padding:16px;color:var(--muted);font-size:12px">Unable to load MCP Hub (${escapeHtml(err.message)}).</div>`;
    }
  }
}

function renderMcpHubView(data) {
  const totalServersEl = document.getElementById('mcpMetricServers');
  const activeServersEl = document.getElementById('mcpMetricActive');
  const totalToolsEl = document.getElementById('mcpMetricTools');
  const listEl = document.getElementById('mcpHubServerList');

  const stats = data.stats || {};
  if (totalServersEl) totalServersEl.textContent = String(stats.total_servers || 0);
  if (activeServersEl) activeServersEl.textContent = String(stats.active_servers || 0);
  if (totalToolsEl) totalToolsEl.textContent = String((stats.total_builtin_tools || 0) + (stats.total_mcp_tools || 0));

  // Render Sidebar Server List
  if (listEl) {
    const servers = data.servers || [];
    if (servers.length === 0) {
      listEl.innerHTML = `
        <div class="mcp-empty-list">
          <div style="font-weight:600;margin-bottom:4px;color:var(--text)">No External MCP Servers</div>
          <div style="font-size:11.5px;color:var(--muted);margin-bottom:12px">Antigravity is currently running with standard built-in tools.</div>
          <button type="button" class="btn-mcp-action primary" onclick="openAddMcpServerModal()">+ Add MCP Server</button>
        </div>
      `;
    } else {
      listEl.innerHTML = servers.map((s, idx) => {
        const isSelected = _selectedMcpServer && _selectedMcpServer.name === s.name;
        const isEnabled = s.enabled !== false;
        return `
          <div class="mcp-server-card ${isSelected ? 'selected' : ''}" onclick="selectMcpServerByIndex(${idx})">
            <div class="mcp-server-card-top">
              <span class="mcp-status-dot ${isEnabled ? 'active' : 'disabled'}"></span>
              <span class="mcp-server-title">${escapeHtml(s.name)}</span>
              <span class="mcp-transport-badge ${s.transport}">${escapeHtml(s.transport.toUpperCase())}</span>
            </div>
            <div class="mcp-server-target monospace">${escapeHtml(s.transport === 'http' ? s.url : (s.command || 'stdio process'))}</div>
            <div class="mcp-server-card-bottom">
              <span class="mcp-tool-count-pill">${s.tool_count || 0} tools</span>
              <label class="mcp-switch" onclick="event.stopPropagation()">
                <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleMcpServerState('${escapeHtml(s.name)}', this.checked)">
                <span class="mcp-slider"></span>
              </label>
            </div>
          </div>
        `;
      }).join('');
    }
  }

  // Render Tool Catalog in Main View
  renderMcpToolCatalog(data);
}

function selectMcpServerByIndex(idx) {
  if (!_mcpHubData || !_mcpHubData.servers || !_mcpHubData.servers[idx]) return;
  _selectedMcpServer = _mcpHubData.servers[idx];
  renderMcpHubView(_mcpHubData);
}

function renderMcpToolCatalog(data) {
  const container = document.getElementById('mcpToolsContainer');
  if (!container) return;

  const builtIn = data.built_in_tools || [];
  const servers = data.servers || [];

  let allTools = [];
  builtIn.forEach(t => {
    allTools.push({ ...t, source: 'Built-in (AGY Core)', isBuiltIn: true });
  });

  servers.forEach(s => {
    (s.tools || []).forEach(t => {
      allTools.push({ ...t, source: s.name, isBuiltIn: false, category: `MCP: ${s.name}` });
    });
  });

  // Apply filters
  let filtered = allTools;
  if (_mcpToolCategoryFilter === 'builtin') {
    filtered = filtered.filter(t => t.isBuiltIn);
  } else if (_mcpToolCategoryFilter === 'mcp') {
    filtered = filtered.filter(t => !t.isBuiltIn);
  }

  if (_mcpToolFilterQuery) {
    const q = _mcpToolFilterQuery.toLowerCase();
    filtered = filtered.filter(t => (t.name && t.name.toLowerCase().includes(q)) || (t.description && t.description.toLowerCase().includes(q)) || (t.category && t.category.toLowerCase().includes(q)));
  }

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="mcp-no-tools">
        <div>No tools match the filter "${escapeHtml(_mcpToolFilterQuery)}".</div>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered.map((tool, idx) => {
    const params = tool.parameters || {};
    const paramEntries = Object.entries(params);

    let paramsHtml = '';
    if (paramEntries.length > 0) {
      paramsHtml = `
        <div class="mcp-tool-schema-wrap">
          <table class="mcp-schema-table">
            <thead>
              <tr>
                <th>Parameter</th>
                <th>Type</th>
                <th>Required</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              ${paramEntries.map(([pName, pMeta]) => `
                <tr>
                  <td><code>${escapeHtml(pName)}</code></td>
                  <td><span class="schema-type">${escapeHtml(pMeta.type || 'any')}</span></td>
                  <td>${pMeta.required ? '<span class="schema-req-badge">required</span>' : '<span class="schema-opt">optional</span>'}</td>
                  <td>${escapeHtml(pMeta.description || '—')}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      `;
    }

    return `
      <div class="mcp-tool-card">
        <div class="mcp-tool-head">
          <div class="mcp-tool-title-block">
            <span class="mcp-tool-name"><code>${escapeHtml(tool.name)}</code></span>
            <span class="mcp-category-badge ${tool.isBuiltIn ? 'builtin' : 'external'}">${escapeHtml(tool.category || tool.source)}</span>
          </div>
          <span class="mcp-source-label">${escapeHtml(tool.source)}</span>
        </div>
        <div class="mcp-tool-desc">${escapeHtml(tool.description || 'No description provided.')}</div>
        ${paramsHtml}
      </div>
    `;
  }).join('');
}

function filterMcpTools(query) {
  _mcpToolFilterQuery = (query || '').trim();
  if (_mcpHubData) renderMcpToolCatalog(_mcpHubData);
}

function setMcpCategoryFilter(cat) {
  _mcpToolCategoryFilter = cat;
  document.querySelectorAll('.mcp-cat-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.cat === cat);
  });
  if (_mcpHubData) renderMcpToolCatalog(_mcpHubData);
}

async function toggleMcpServerState(name, enabled) {
  try {
    const res = await fetch('/api/mcp/hub/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, enabled })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await loadMcpHub();
  } catch (err) {
    alert(`Failed to toggle MCP server: ${err.message}`);
    loadMcpHub();
  }
}

function openAddMcpServerModal() {
  const modal = document.getElementById('mcpServerModal');
  if (modal) modal.style.display = 'flex';
}

function closeAddMcpServerModal() {
  const modal = document.getElementById('mcpServerModal');
  if (modal) modal.style.display = 'none';
}

function applyMcpPreset(preset) {
  const nameInp = document.getElementById('mcpFormName');
  const transportSel = document.getElementById('mcpFormTransport');
  const cmdInp = document.getElementById('mcpFormCommand');
  const urlInp = document.getElementById('mcpFormUrl');

  if (preset === 'postgres') {
    if (nameInp) nameInp.value = 'postgres';
    if (transportSel) { transportSel.value = 'stdio'; onMcpTransportChange(); }
    if (cmdInp) cmdInp.value = 'npx -y @modelcontextprotocol/server-postgres postgresql://localhost/mydb';
  } else if (preset === 'sqlite') {
    if (nameInp) nameInp.value = 'sqlite';
    if (transportSel) { transportSel.value = 'stdio'; onMcpTransportChange(); }
    if (cmdInp) cmdInp.value = 'uvx mcp-server-sqlite --db-path /workspace/data.db';
  } else if (preset === 'git') {
    if (nameInp) nameInp.value = 'git';
    if (transportSel) { transportSel.value = 'stdio'; onMcpTransportChange(); }
    if (cmdInp) cmdInp.value = 'uvx mcp-server-git --repository /workspace';
  } else if (preset === 'remote') {
    if (nameInp) nameInp.value = 'cloud-mcp';
    if (transportSel) { transportSel.value = 'http'; onMcpTransportChange(); }
    if (urlInp) urlInp.value = 'https://mcp.internal.nutanix.com/sse';
  }
}

function onMcpTransportChange() {
  const transportSel = document.getElementById('mcpFormTransport');
  const stdioGroup = document.getElementById('mcpStdioGroup');
  const httpGroup = document.getElementById('mcpHttpGroup');
  const val = transportSel ? transportSel.value : 'stdio';

  if (stdioGroup) stdioGroup.style.display = val === 'stdio' ? 'block' : 'none';
  if (httpGroup) httpGroup.style.display = val === 'http' ? 'block' : 'none';
}

async function submitMcpServerForm() {
  const name = document.getElementById('mcpFormName')?.value.trim();
  const transport = document.getElementById('mcpFormTransport')?.value;
  const command = document.getElementById('mcpFormCommand')?.value.trim();
  const url = document.getElementById('mcpFormUrl')?.value.trim();
  const envText = document.getElementById('mcpFormEnv')?.value.trim();

  if (!name) {
    alert('Server name is required.');
    return;
  }

  let envObj = {};
  if (envText) {
    try {
      envObj = JSON.parse(envText);
    } catch (e) {
      alert('Environment variables must be valid JSON.');
      return;
    }
  }

  const payload = {
    name,
    transport,
    command: transport === 'stdio' ? command : undefined,
    url: transport === 'http' ? url : undefined,
    env: envObj,
    enabled: true
  };

  try {
    const res = await fetch('/api/mcp/hub/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    closeAddMcpServerModal();
    await loadMcpHub();
  } catch (err) {
    alert(`Failed to save MCP server: ${err.message}`);
  }
}

async function deleteSelectedMcpServer() {
  if (!_selectedMcpServer) return;
  if (!confirm(`Are you sure you want to remove MCP server "${_selectedMcpServer.name}"?`)) return;

  try {
    const res = await fetch('/api/mcp/hub/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: _selectedMcpServer.name })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _selectedMcpServer = null;
    await loadMcpHub();
  } catch (err) {
    alert(`Failed to delete MCP server: ${err.message}`);
  }
}

function escapeHtml(str) {
  if (typeof str !== 'string') return String(str || '');
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}
