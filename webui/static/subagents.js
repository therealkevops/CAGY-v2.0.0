/**
 * Subagent Swarm Visualizer for Antigravity (AGY) WebUI
 * Conforms to .gemini/rules/webui_design_system.md
 */

let _subagentsData = null;
let _selectedSubagent = null;
let _subagentPollTimer = null;

async function loadSubagents(force = false) {
  const listEl = document.getElementById('subagentList');
  if (!listEl) return;

  try {
    const sessionId = (typeof S !== 'undefined' && S && S.session && S.session.session_id) || '';
    const res = await fetch(`/api/subagents?session_id=${encodeURIComponent(sessionId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _subagentsData = data;
    renderSubagentsView(data);
    _syncSubagentsPolling();
  } catch (err) {
    if (listEl) {
      listEl.innerHTML = `<div style="padding:16px;color:var(--muted);font-size:12px">Unable to load subagents (${escapeHtml(err.message)}).</div>`;
    }
  }
}

function _syncSubagentsPolling() {
  if (_subagentPollTimer) {
    clearTimeout(_subagentPollTimer);
    _subagentPollTimer = null;
  }
  if (_currentPanel === 'subagents' && _subagentsData && _subagentsData.active_count > 0) {
    _subagentPollTimer = setTimeout(() => {
      loadSubagents();
    }, 3000);
  }
}

function renderSubagentsView(data) {
  const totalEl = document.getElementById('swarmMetricTotal');
  const activeEl = document.getElementById('swarmMetricActive');
  const toolsEl = document.getElementById('swarmMetricTools');
  const listEl = document.getElementById('subagentList');
  const graphEl = document.getElementById('swarmGraphContainer');

  const subs = data.subagents || [];
  let totalTools = 0;
  subs.forEach(s => { totalTools += (s.tool_count || 0); });

  if (totalEl) totalEl.textContent = String(subs.length);
  if (activeEl) activeEl.textContent = String(data.active_count || 0);
  if (toolsEl) toolsEl.textContent = String(totalTools);

  // Render Sidebar List
  if (listEl) {
    if (subs.length === 0) {
      listEl.innerHTML = `
        <div class="swarm-empty-list">
          <div style="font-weight:600;margin-bottom:4px;color:var(--text)">No Subagents Active</div>
          <div style="font-size:11.5px;color:var(--muted)">When AGY delegates tasks using <code>invoke_subagent</code>, spawned agents will appear here in real time.</div>
        </div>
      `;
    } else {
      listEl.innerHTML = subs.map((sub, idx) => {
        const isRunning = sub.status === 'running';
        const isSelected = _selectedSubagent && (_selectedSubagent.conversation_id === sub.conversation_id && _selectedSubagent.role === sub.role);
        return `
          <div class="swarm-list-item ${isSelected ? 'selected' : ''}" onclick="selectSubagentByIndex(${idx})">
            <div class="swarm-list-item-top">
              <span class="swarm-status-dot ${isRunning ? 'running' : 'done'}"></span>
              <span class="swarm-item-role">${escapeHtml(sub.role || 'Subagent')}</span>
              <span class="swarm-item-badge">${escapeHtml(sub.type_name || 'research')}</span>
            </div>
            <div class="swarm-item-prompt">${escapeHtml(sub.prompt ? sub.prompt.slice(0, 75) + (sub.prompt.length > 75 ? '...' : '') : 'No prompt provided')}</div>
            <div class="swarm-list-item-meta">
              <span>Model: <strong>${escapeHtml(sub.model || 'inherit')}</strong></span>
              <span>Tools: <strong>${sub.tool_count || 0}</strong></span>
            </div>
          </div>
        `;
      }).join('');
    }
  }

  // Render Swarm Topology Graph (DAG)
  renderSwarmTopology(data);
}

function renderSwarmTopology(data) {
  const graphEl = document.getElementById('swarmGraphContainer');
  if (!graphEl) return;

  const root = data.root || { role: 'Lead Architect (Parent)', model: 'Gemini 3.7 Flash/Pro (AGY)' };
  const subs = data.subagents || [];

  let html = `
    <div class="swarm-tree-root">
      <div class="swarm-node root-node">
        <div class="swarm-node-header">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          <span class="swarm-node-title">${escapeHtml(root.role || 'Parent Agent')}</span>
          <span class="swarm-node-pill pill-gold">${escapeHtml(root.model || 'Gemini 3.7 Pro')}</span>
        </div>
        <div class="swarm-node-body">
          <span class="swarm-node-sub">Root Orchestration Engine · Concurrency Hub</span>
        </div>
      </div>
      <div class="swarm-tree-connector ${subs.length > 0 ? 'active' : ''}"></div>
  `;

  if (subs.length === 0) {
    html += `
      <div class="swarm-no-children">
        <span>No subagents currently spawned in this session.</span>
      </div>
    `;
  } else {
    html += `<div class="swarm-children-row">`;
    subs.forEach((sub, idx) => {
      const isRunning = sub.status === 'running';
      const isSelected = _selectedSubagent && (_selectedSubagent.conversation_id === sub.conversation_id && _selectedSubagent.role === sub.role);
      html += `
        <div class="swarm-node child-node ${isSelected ? 'selected' : ''}" onclick="selectSubagentByIndex(${idx})">
          <div class="swarm-node-header">
            <span class="swarm-status-dot ${isRunning ? 'running' : 'done'}"></span>
            <span class="swarm-node-title">${escapeHtml(sub.role || 'Subagent')}</span>
          </div>
          <div class="swarm-node-tags">
            <span class="swarm-node-pill pill-blue">${escapeHtml(sub.type_name || 'research')}</span>
            <span class="swarm-node-pill pill-surface">${escapeHtml(sub.model || 'inherit')}</span>
            <span class="swarm-node-pill pill-surface">${escapeHtml(sub.workspace || 'inherit')}</span>
          </div>
          <div class="swarm-node-activity">
            <span>Activity: <strong>${escapeHtml(sub.last_activity || 'Invoked')}</strong></span>
            <span>Tools Run: <strong>${sub.tool_count || 0}</strong></span>
          </div>
        </div>
      `;
    });
    html += `</div>`;
  }

  html += `</div>`;
  graphEl.innerHTML = html;
}

function selectSubagentByIndex(idx) {
  if (!_subagentsData || !_subagentsData.subagents || !_subagentsData.subagents[idx]) return;
  const sub = _subagentsData.subagents[idx];
  _selectedSubagent = sub;
  renderSubagentsView(_subagentsData);
  inspectSubagent(sub);
}

async function inspectSubagent(sub) {
  const emptyEl = document.getElementById('swarmInspectorEmpty');
  const contentEl = document.getElementById('swarmInspectorContent');
  const roleEl = document.getElementById('inspectorRole');
  const typeEl = document.getElementById('inspectorTypeName');
  const modelEl = document.getElementById('inspectorModel');
  const wsEl = document.getElementById('inspectorWorkspace');
  const convIdEl = document.getElementById('inspectorConvId');
  const promptEl = document.getElementById('inspectorPrompt');
  const timelineEl = document.getElementById('inspectorTimeline');
  const stepCountEl = document.getElementById('inspectorStepCount');
  const statusBadge = document.getElementById('subagentStatusBadge');

  if (emptyEl) emptyEl.style.display = 'none';
  if (contentEl) contentEl.style.display = 'block';

  if (roleEl) roleEl.textContent = sub.role || 'Subagent';
  if (typeEl) typeEl.textContent = `Type: ${sub.type_name || 'research'}`;
  if (modelEl) modelEl.textContent = `Model: ${sub.model || 'inherit'}`;
  if (wsEl) wsEl.textContent = `Workspace: ${sub.workspace || 'inherit'}`;
  if (convIdEl) convIdEl.textContent = sub.conversation_id ? `ID: ${sub.conversation_id}` : (sub.parent_id ? `Parent: ${sub.parent_id.slice(0, 12)}...` : 'Session Scoped');
  if (promptEl) promptEl.textContent = sub.prompt || 'No initial prompt text recorded.';

  if (statusBadge) {
    const isRunning = sub.status === 'running';
    statusBadge.className = `swarm-status-badge ${isRunning ? 'running' : 'done'}`;
    statusBadge.textContent = isRunning ? '● Active Running' : '✓ Completed';
  }

  if (timelineEl) {
    timelineEl.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">Loading execution trace...</div>';
  }

  // Load Transcript if subagent conversation id exists
  if (sub.conversation_id) {
    try {
      const res = await fetch(`/api/subagents/detail?id=${encodeURIComponent(sub.conversation_id)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const tData = await res.json();
      renderSubagentTimeline(tData.steps || []);
      if (stepCountEl) stepCountEl.textContent = String(tData.total_steps || (tData.steps ? tData.steps.length : 0));
    } catch (err) {
      if (timelineEl) {
        timelineEl.innerHTML = `<div style="padding:12px;color:var(--muted);font-size:12px">Transcript unavailable (${escapeHtml(err.message)}).</div>`;
      }
    }
  } else {
    // Render brief timeline from parent tool invocation
    if (stepCountEl) stepCountEl.textContent = '1';
    if (timelineEl) {
      timelineEl.innerHTML = `
        <div class="timeline-step">
          <div class="timeline-step-head">
            <span class="timeline-step-idx">#1</span>
            <span class="timeline-step-type">invoke_subagent</span>
            <span class="timeline-step-time">${escapeHtml(sub.created_at || 'Recently')}</span>
          </div>
          <div class="timeline-step-body">
            Subagent dispatched with task instructions.
          </div>
        </div>
      `;
    }
  }
}

function renderSubagentTimeline(steps) {
  const timelineEl = document.getElementById('inspectorTimeline');
  if (!timelineEl) return;

  if (!steps || steps.length === 0) {
    timelineEl.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">No execution steps recorded yet.</div>';
    return;
  }

  timelineEl.innerHTML = steps.map((st, i) => {
    let toolHtml = '';
    if (Array.isArray(st.tool_calls) && st.tool_calls.length > 0) {
      toolHtml = `
        <div class="timeline-tools">
          ${st.tool_calls.map(tc => `
            <div class="timeline-tool-pill">
              <code>${escapeHtml(tc.name || 'tool')}</code>
              <span class="timeline-tool-args">${escapeHtml(JSON.stringify(tc.args || {}).slice(0, 80))}</span>
            </div>
          `).join('')}
        </div>
      `;
    }

    let contentHtml = '';
    if (st.content) {
      contentHtml = `<div class="timeline-step-content">${escapeHtml(st.content)}</div>`;
    }

    return `
      <div class="timeline-step">
        <div class="timeline-step-head">
          <span class="timeline-step-idx">#${st.step_index || (i + 1)}</span>
          <span class="timeline-step-source">${escapeHtml(st.source || 'AGENT')}</span>
          <span class="timeline-step-type">${escapeHtml(st.type || 'STEP')}</span>
          <span class="timeline-step-time">${escapeHtml(st.created_at ? st.created_at.slice(11, 19) : '')}</span>
        </div>
        ${contentHtml}
        ${toolHtml}
      </div>
    `;
  }).join('');
}

function copySubagentId() {
  if (!_selectedSubagent) return;
  const id = _selectedSubagent.conversation_id || _selectedSubagent.parent_id;
  if (id) {
    navigator.clipboard.writeText(id).then(() => {
      const btn = document.getElementById('btnCopySubagentId');
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = orig; }, 1500);
      }
    });
  }
}

function escapeHtml(str) {
  if (typeof str !== 'string') return String(str || '');
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}
