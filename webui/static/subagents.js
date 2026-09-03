/**
 * Subagent Swarm Visualizer for Antigravity (AGY) WebUI
 * Conforms to .gemini/rules/webui_design_system.md
 */

let _subagentsData = null;
let _selectedSubagent = null;
let _subagentPollTimer = null;
let _currentSubagentRawSteps = [];
let _subagentTimelineFilter = 'all';
let _subagentTimelineSearch = '';

async function loadSubagents(force = false) {
  const listEl = document.getElementById('subagentList');
  const btnSidebar = document.getElementById('subagentsRefreshBtn');
  const btnHeader = document.getElementById('btnRefreshSubagents');

  if (force) {
    if (btnSidebar) btnSidebar.classList.add('spinning');
    if (btnHeader) btnHeader.classList.add('spinning');
  }

  try {
    let sessionId = '';
    if (typeof S !== 'undefined' && S && S.session && S.session.session_id) {
      sessionId = S.session.session_id;
    } else if (typeof _currentSessionId !== 'undefined' && _currentSessionId) {
      sessionId = _currentSessionId;
    } else if (typeof localStorage !== 'undefined') {
      sessionId = localStorage.getItem('agy-webui-session') || '';
    }

    const res = await fetch(`/api/subagents?session_id=${encodeURIComponent(sessionId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _subagentsData = data;
    renderSubagentsView(data);

    // If a subagent is selected, re-inspect to refresh its timeline & steps
    if (_selectedSubagent) {
      const updated = (data.subagents || []).find(s => s.conversation_id === _selectedSubagent.conversation_id && s.role === _selectedSubagent.role) || _selectedSubagent;
      inspectSubagent(updated);
    }

    _syncSubagentsPolling();
  } catch (err) {
    if (listEl) {
      listEl.innerHTML = `<div style="padding:16px;color:var(--muted);font-size:12px">Unable to load subagents (${escapeHtml(err.message)}).</div>`;
    }
  } finally {
    if (force) {
      setTimeout(() => {
        if (btnSidebar) btnSidebar.classList.remove('spinning');
        if (btnHeader) btnHeader.classList.remove('spinning');
      }, 300);
    }
  }
}

function _syncSubagentsPolling() {
  if (_subagentPollTimer) {
    clearTimeout(_subagentPollTimer);
    _subagentPollTimer = null;
  }
  if (_currentPanel === 'subagents') {
    const isAnyActive = _subagentsData && _subagentsData.active_count > 0;
    const pollInterval = isAnyActive ? 2000 : 5000;
    _subagentPollTimer = setTimeout(() => {
      loadSubagents();
      // Auto-refresh timeline if an active subagent is selected
      if (_selectedSubagent && _selectedSubagent.status === 'running') {
        inspectSubagent(_selectedSubagent);
      }
    }, pollInterval);
  }
}

function renderSubagentsView(data) {
  const totalEl = document.getElementById('swarmMetricTotal');
  const activeEl = document.getElementById('swarmMetricActive');
  const toolsEl = document.getElementById('swarmMetricTools');
  const listEl = document.getElementById('subagentList');

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

  const root = data.tree || data.root || { role: 'Lead Architect (Parent)', model: 'Gemini 3.7 Flash/Pro (AGY)', children: data.subagents || [] };
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
          ${root.descendant_count ? `<span class="swarm-node-sub" style="margin-left:6px;font-weight:600;color:var(--text);">(${root.descendant_count} agents total)</span>` : ''}
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
    html += _renderSubagentBranch(root.children || subs);
  }

  html += `</div>`;
  graphEl.innerHTML = html;
}

function _renderSubagentBranch(children) {
  if (!children || children.length === 0) return '';
  let html = `<div class="swarm-children-row">`;
  children.forEach((sub) => {
    const isRunning = sub.status === 'running';
    const isSelected = _selectedSubagent && (_selectedSubagent.conversation_id === sub.conversation_id && _selectedSubagent.role === sub.role);
    const subChildren = sub.children || [];
    html += `
      <div class="swarm-branch-container" style="display:flex;flex-direction:column;align-items:center;gap:12px;">
        <div class="swarm-node child-node ${isSelected ? 'selected' : ''} ${isRunning ? 'is-running' : ''}" onclick="selectSubagentByConvId('${escapeHtml(sub.conversation_id || '')}', '${escapeHtml(sub.role || '')}')">
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
        ${subChildren.length > 0 ? `
          <div class="swarm-tree-connector active"></div>
          ${_renderSubagentBranch(subChildren)}
        ` : ''}
      </div>
    `;
  });
  html += `</div>`;
  return html;
}

function selectSubagentByIndex(idx) {
  if (!_subagentsData || !_subagentsData.subagents || !_subagentsData.subagents[idx]) return;
  const sub = _subagentsData.subagents[idx];
  _selectedSubagent = sub;
  renderSubagentsView(_subagentsData);
  inspectSubagent(sub);
}

function selectSubagentByConvId(convId, role) {
  if (!_subagentsData || !_subagentsData.subagents) return;
  const sub = _subagentsData.subagents.find(s => (convId && s.conversation_id === convId) || s.role === role);
  if (sub) {
    _selectedSubagent = sub;
    renderSubagentsView(_subagentsData);
    inspectSubagent(sub);
  }
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
    statusBadge.textContent = isRunning ? 'Active' : 'Completed';
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
      _currentSubagentRawSteps = tData.steps || [];
      renderFilteredTimeline();
      if (stepCountEl) stepCountEl.textContent = String(tData.total_steps || (_currentSubagentRawSteps.length));
    } catch (err) {
      if (timelineEl) {
        timelineEl.innerHTML = `<div style="padding:12px;color:var(--muted);font-size:12px">Transcript unavailable (${escapeHtml(err.message)}).</div>`;
      }
    }
  } else {
    // Render brief timeline from parent tool invocation
    if (stepCountEl) stepCountEl.textContent = '1';
    _currentSubagentRawSteps = [{
      type: 'USER_INPUT',
      content: sub.prompt || 'Dispatched task',
      created_at: sub.created_at || ''
    }];
    renderFilteredTimeline();
  }
}

function setTimelineFilter(filter) {
  _subagentTimelineFilter = filter;
  document.querySelectorAll('.timeline-filter-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.filter === filter);
  });
  renderFilteredTimeline();
}

function onTimelineSearch(val) {
  _subagentTimelineSearch = (val || '').toLowerCase().trim();
  renderFilteredTimeline();
}

function renderFilteredTimeline() {
  const steps = _currentSubagentRawSteps || [];
  const timelineEl = document.getElementById('inspectorTimeline');
  if (!timelineEl) return;

  if (steps.length === 0) {
    timelineEl.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">No execution steps recorded yet.</div>';
    _updateFilterCounts(0, 0, 0, 0);
    return;
  }

  // Unify steps: pair tool invocation with its corresponding result output
  const unified = [];
  let i = 0;
  while (i < steps.length) {
    const st = steps[i];
    const stType = st.type || '';
    const toolCalls = Array.isArray(st.tool_calls) ? st.tool_calls : [];

    if (stType === 'USER_INPUT') {
      unified.push({
        kind: 'prompt',
        title: 'Delegated Task Prompt',
        stepIndex: st.step_index ?? i,
        time: st.created_at || '',
        content: st.content || ''
      });
      i++;
    } else if (toolCalls.length > 0) {
      let outputContent = null;
      if (i + 1 < steps.length && steps[i + 1].type === 'GENERIC') {
        outputContent = steps[i + 1].content || '';
        i += 2;
      } else {
        i++;
      }
      toolCalls.forEach(tc => {
        unified.push({
          kind: 'tool',
          title: `Tool: ${tc.name || 'tool'}`,
          toolName: tc.name || 'tool',
          args: tc.args || {},
          output: outputContent,
          stepIndex: st.step_index ?? i,
          time: st.created_at || ''
        });
      });
    } else if (st.content && String(st.content).trim()) {
      unified.push({
        kind: 'response',
        title: 'Subagent Response',
        stepIndex: st.step_index ?? i,
        time: st.created_at || '',
        content: st.content
      });
      i++;
    } else {
      i++;
    }
  }

  const countAll = unified.length;
  const countTools = unified.filter(u => u.kind === 'tool').length;
  const countPrompts = unified.filter(u => u.kind === 'prompt').length;
  const countResponses = unified.filter(u => u.kind === 'response').length;
  _updateFilterCounts(countAll, countTools, countPrompts, countResponses);

  // Apply filters
  let filtered = unified;
  if (_subagentTimelineFilter !== 'all') {
    filtered = filtered.filter(u => u.kind === _subagentTimelineFilter);
  }

  if (_subagentTimelineSearch) {
    filtered = filtered.filter(u => {
      const titleMatch = u.title && u.title.toLowerCase().includes(_subagentTimelineSearch);
      const contentMatch = u.content && String(u.content).toLowerCase().includes(_subagentTimelineSearch);
      const toolMatch = u.toolName && u.toolName.toLowerCase().includes(_subagentTimelineSearch);
      const outMatch = u.output && String(u.output).toLowerCase().includes(_subagentTimelineSearch);
      return titleMatch || contentMatch || toolMatch || outMatch;
    });
  }

  if (filtered.length === 0) {
    timelineEl.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">No steps match the active filters.</div>';
    return;
  }

  timelineEl.innerHTML = filtered.map((entry, idx) => {
    let bodyHtml = '';
    if (entry.kind === 'tool') {
      let argsFormatted = '';
      if (typeof entry.args === 'string') {
        try {
          argsFormatted = JSON.stringify(JSON.parse(entry.args), null, 2);
        } catch(_) {
          argsFormatted = entry.args;
        }
      } else {
        argsFormatted = JSON.stringify(entry.args, null, 2);
      }

      let outHtml = '';
      if (entry.output) {
        outHtml = `
          <div class="timeline-tool-result">
            <div class="timeline-tool-result-header">Execution Result</div>
            <pre class="timeline-output-pre"><code>${escapeHtml(entry.output.slice(0, 1200) + (entry.output.length > 1200 ? '...' : ''))}</code></pre>
          </div>
        `;
      }
      bodyHtml = `
        <div class="timeline-tool-block">
          <div class="timeline-tool-call">
            <pre class="timeline-args-pre"><code>${escapeHtml(argsFormatted)}</code></pre>
          </div>
          ${outHtml}
        </div>
      `;
    } else if (entry.content) {
      bodyHtml = `<div class="timeline-step-content">${escapeHtml(entry.content)}</div>`;
    }

    return `
      <div class="timeline-step">
        <div class="timeline-step-head">
          <span class="timeline-step-idx">#${idx + 1}</span>
          <span class="timeline-step-type ${entry.kind}">${escapeHtml(entry.title)}</span>
          <span class="timeline-step-time">${escapeHtml(entry.time ? entry.time.slice(11, 19) : '')}</span>
        </div>
        ${bodyHtml}
      </div>
    `;
  }).join('');
}

function _updateFilterCounts(all, tools, prompts, responses) {
  const elAll = document.getElementById('countFilterAll');
  const elTools = document.getElementById('countFilterTools');
  const elPrompts = document.getElementById('countFilterPrompts');
  const elResponses = document.getElementById('countFilterResponses');
  if (elAll) elAll.textContent = String(all);
  if (elTools) elTools.textContent = String(tools);
  if (elPrompts) elPrompts.textContent = String(prompts);
  if (elResponses) elResponses.textContent = String(responses);
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

function copySubagentPrompt() {
  if (!_selectedSubagent) return;
  const prompt = _selectedSubagent.prompt || '';
  if (prompt) {
    navigator.clipboard.writeText(prompt).then(() => {
      const btn = document.getElementById('btnCopySubagentPrompt');
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = orig; }, 1500);
      }
    });
  }
}

function exportSubagentTranscript() {
  if (!_selectedSubagent || !_selectedSubagent.conversation_id) return;
  window.open(`/api/subagents/export?id=${encodeURIComponent(_selectedSubagent.conversation_id)}`, '_blank');
}

function escapeHtml(str) {
  if (typeof str !== 'string') return String(str || '');
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}
