/**
 * Slash Command & Goal Workflow Palette for Antigravity (AGY) WebUI
 * Conforms to .gemini/rules/webui_design_system.md
 */

const AGY_SLASH_COMMANDS = [
  {
    cmd: '/deepmode',
    title: 'DeepMode Autonomous Execution',
    category: 'Workflows',
    desc: 'Toggle or execute tasks with Solar Pro 4 / Hermes autonomous discipline (zero fluff, proactive tool calling, high effort).',
    icon: 'zap',
    hasParams: true,
    paramPlaceholder: 'on | off | [task description]'
  },
  {
    cmd: '/goal',
    title: 'Autonomous Goal Execution',
    category: 'Workflows',
    desc: 'Launch a long-running goal where AGY works autonomously until the objective is completed.',
    icon: 'target',
    hasParams: true,
    paramPlaceholder: 'e.g. Refactor API routes and add comprehensive test suite'
  },
  {
    cmd: '/plan',
    title: 'Deep Step-by-Step Plan',
    category: 'Workflows',
    desc: 'Generate a structured architectural plan with verification milestones.',
    icon: 'clipboard-list',
    hasParams: true,
    paramPlaceholder: 'e.g. Migration plan to Nutanix NC2 cluster'
  },
  {
    cmd: '/grill-me',
    title: 'Interactive Requirements Interview',
    category: 'Workflows',
    desc: 'Engage in an interactive interview where AGY asks tough questions to refine your plan.',
    icon: 'help-circle',
    hasParams: false
  },
  {
    cmd: '/schedule',
    title: 'Schedule Timer or Cron Job',
    category: 'Automation',
    desc: 'Set a one-shot notification timer or recurring schedule in the background.',
    icon: 'clock',
    hasParams: true,
    paramPlaceholder: 'e.g. DurationSeconds=300 Prompt="Check build status"'
  },
  {
    cmd: '/browser',
    title: 'Autonomous Browser Agent',
    category: 'Web & Tools',
    desc: 'Delegate web exploration, documentation lookups, and visual testing.',
    icon: 'globe',
    hasParams: true,
    paramPlaceholder: 'e.g. https://portal.nutanix.com/page/documents/details?targetId=NC2'
  },
  {
    cmd: '/learn',
    title: 'Persist Rules & Guidelines',
    category: 'Memory',
    desc: 'Save corrections and design standards into .gemini/rules/ for future sessions.',
    icon: 'bookmark',
    hasParams: true,
    paramPlaceholder: 'e.g. Always use standard Workbench design tokens'
  },
  {
    cmd: '/memorize',
    title: 'Memorize to Knowledge Vault',
    category: 'Memory',
    desc: 'Extract and persist user preferences, conventions, or architectural decisions into the Knowledge Vault.',
    icon: 'bookmark',
    hasParams: true,
    paramPlaceholder: 'e.g. Always deploy Nutanix NC2 VPC subnets with /24 CIDRs'
  },
  {
    cmd: '/recall',
    title: 'Recall from Knowledge Vault',
    category: 'Memory',
    desc: 'Search the Knowledge Vault and summon relevant ADRs, architectural decisions, and note excerpts directly into chat.',
    icon: 'search',
    hasParams: true,
    paramPlaceholder: 'e.g. redis, etcd, fastapi, telemetry'
  },
  {
    cmd: '/digest',
    title: 'Atomic Note Synthesizer & Auto-Wikilinker',
    category: 'Memory',
    desc: 'Transform raw study notes, transcripts, or thoughts into atomic Obsidian notes with auto-woven wikilinks.',
    icon: 'file-text',
    hasParams: true,
    paramPlaceholder: 'e.g. Kubernetes Pod Disruption Budgets: PDBs specify minimum available pods...'
  },
  {
    cmd: '/gaps',
    title: 'Knowledge Gap & Stub Auditor',
    category: 'Memory',
    desc: 'Audit active space for missing note stubs, unresolved wikilinks, and orphan notes with 1-click creation.',
    icon: 'activity',
    hasParams: false
  },
  {
    cmd: '/quota',
    title: 'Rate Limits & Quota Summary',
    category: 'Diagnostics',
    desc: 'View remaining token allotments, RPM/TPM limits, and reset windows.',
    icon: 'activity',
    hasParams: false
  },
  {
    cmd: '/effort',
    title: 'Reasoning Effort Mode',
    category: 'Settings',
    desc: 'Configure thinking reasoning budget (low, medium, high).',
    icon: 'cpu',
    hasParams: true,
    paramPlaceholder: 'low | medium | high'
  },
  {
    cmd: '/swarm',
    title: 'Subagent Swarm Visualizer',
    category: 'Navigation',
    desc: 'Open the live multi-agent swarm hierarchy and execution inspector.',
    icon: 'share-2',
    action: () => switchPanel('subagents', { fromRailClick: true })
  },
  {
    cmd: '/mcp',
    title: 'Model Context Protocol Hub',
    category: 'Navigation',
    desc: 'Open the MCP server manager and tool catalog.',
    icon: 'server',
    action: () => switchPanel('mcp', { fromRailClick: true })
  },
  {
    cmd: '/skills',
    title: 'Skill & Rule Scaffolder',
    category: 'Navigation',
    desc: 'Open the custom domain skill and rule scaffolding wizard.',
    icon: 'layers',
    action: () => { switchPanel('skills', { fromRailClick: true }); if (typeof openSkillOrRuleWizard === 'function') openSkillOrRuleWizard(); }
  },
  {
    cmd: '/artifacts',
    title: 'Artifacts & File Explorer',
    category: 'Navigation',
    desc: 'Open the right-hand workspace and session artifact explorer.',
    icon: 'file-text',
    action: () => {
      if (typeof openWorkspacePanel === 'function') openWorkspacePanel('browse');
      if (typeof switchWorkspacePanelTab === 'function') switchWorkspacePanelTab('artifacts');
    }
  },
  {
    cmd: '/vault',
    title: 'Knowledge Vault & Graph',
    category: 'Navigation',
    desc: 'Open the Obsidian-style Knowledge Vault and 2D interactive force-directed graph.',
    icon: 'share-2',
    action: () => {
      if (typeof switchPanel === 'function') switchPanel('vault', { fromRailClick: true });
    }
  }
];

let _selectedPaletteIndex = 0;
let _filteredPaletteCommands = [];

function openSlashPalette(initialQuery = '') {
  const modal = document.getElementById('slashPaletteModal');
  const input = document.getElementById('slashPaletteInput');
  if (!modal || !input) return;

  modal.style.display = 'flex';
  input.value = initialQuery;
  _filterPalette(initialQuery);
  input.focus();
}

function closeSlashPalette() {
  const modal = document.getElementById('slashPaletteModal');
  if (modal) modal.style.display = 'none';
}

function _filterPalette(query) {
  const q = (query || '').trim().toLowerCase().replace(/^\//, '');
  _filteredPaletteCommands = AGY_SLASH_COMMANDS.filter(c => {
    if (!q) return true;
    return c.cmd.toLowerCase().includes(q) ||
           c.title.toLowerCase().includes(q) ||
           c.desc.toLowerCase().includes(q) ||
           c.category.toLowerCase().includes(q);
  });
  _selectedPaletteIndex = 0;
  _renderPaletteList();
}

function _renderPaletteList() {
  const listEl = document.getElementById('slashPaletteList');
  if (!listEl) return;

  if (_filteredPaletteCommands.length === 0) {
    listEl.innerHTML = `
      <div style="padding: 24px; text-align: center; color: var(--muted); font-size: 12.5px;">
        No matching slash commands found.
      </div>
    `;
    return;
  }

  listEl.innerHTML = _filteredPaletteCommands.map((item, idx) => {
    const isSelected = idx === _selectedPaletteIndex;
    return `
      <div class="slash-palette-item ${isSelected ? 'selected' : ''}" onclick="executePaletteItem(${idx})">
        <div class="slash-palette-item-left">
          <span class="slash-palette-cmd"><code>${escapeHtml(item.cmd)}</code></span>
          <div>
            <div class="slash-palette-title">${escapeHtml(item.title)}</div>
            <div class="slash-palette-desc">${escapeHtml(item.desc)}</div>
          </div>
        </div>
        <span class="slash-palette-category">${escapeHtml(item.category)}</span>
      </div>
    `;
  }).join('');
}

function handlePaletteKeydown(e) {
  if (e.key === 'Escape') {
    closeSlashPalette();
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (_filteredPaletteCommands.length > 0) {
      _selectedPaletteIndex = (_selectedPaletteIndex + 1) % _filteredPaletteCommands.length;
      _renderPaletteList();
      _scrollPaletteItemIntoView();
    }
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (_filteredPaletteCommands.length > 0) {
      _selectedPaletteIndex = (_selectedPaletteIndex - 1 + _filteredPaletteCommands.length) % _filteredPaletteCommands.length;
      _renderPaletteList();
      _scrollPaletteItemIntoView();
    }
  } else if (e.key === 'Enter') {
    e.preventDefault();
    executePaletteItem(_selectedPaletteIndex);
  }
}

function _scrollPaletteItemIntoView() {
  const items = document.querySelectorAll('.slash-palette-item');
  if (items[_selectedPaletteIndex]) {
    items[_selectedPaletteIndex].scrollIntoView({ block: 'nearest' });
  }
}

function executePaletteItem(idx) {
  const item = _filteredPaletteCommands[idx];
  if (!item) return;
  closeSlashPalette();

  if (typeof item.action === 'function') {
    item.action();
    return;
  }

  // Populate into Composer
  const msgInput = document.getElementById('msg');
  if (msgInput) {
    if (item.hasParams) {
      msgInput.value = `${item.cmd} `;
      msgInput.focus();
      msgInput.setSelectionRange(item.cmd.length + 1, item.cmd.length + 1);
    } else {
      msgInput.value = item.cmd;
      if (typeof sendMsg === 'function') {
        sendMsg();
      }
    }
  }
}

// Global keyboard shortcut: Cmd+K / Ctrl+K opens Slash Palette
if (typeof window !== 'undefined') {
  window.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      const modal = document.getElementById('slashPaletteModal');
      if (modal && modal.style.display === 'flex') {
        closeSlashPalette();
      } else {
        openSlashPalette();
      }
    }
  });
}
