/**
 * Skill & Rule Scaffolding Wizard + Token Velocity HUD for Antigravity WebUI
 * Conforms to .gemini/rules/webui_design_system.md
 */

function openSkillOrRuleWizard() {
  if (typeof switchPanel === 'function' && _currentPanel !== 'skills') {
    switchPanel('skills');
  }
  _renderSkillWizardForm();
  _closeMobileSidebarAfterPanelSelection();
}

function _renderSkillWizardForm() {
  const title = document.getElementById('skillDetailTitle');
  const body = document.getElementById('skillDetailBody');
  const empty = document.getElementById('skillDetailEmpty');
  if (!body || !title) return;

  title.textContent = 'Skill & Rule Scaffolding Wizard';
  
  body.innerHTML = `
    <div class="main-view-content">
      <div class="wizard-container">
        <div class="wizard-header">
          <div class="wizard-type-selector">
            <button type="button" class="wizard-type-btn active" id="wizardTypeSkill" onclick="setWizardType('skill')">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
              <div>
                <strong>AGY Domain Skill</strong>
                <div class="wizard-type-sub">Creates <code>skills/&lt;name&gt;/SKILL.md</code></div>
              </div>
            </button>
            <button type="button" class="wizard-type-btn" id="wizardTypeRule" onclick="setWizardType('rule')">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/></svg>
              <div>
                <strong>Workspace Rule</strong>
                <div class="wizard-type-sub">Creates <code>.gemini/rules/&lt;name&gt;.md</code></div>
              </div>
            </button>
          </div>
        </div>

        <div class="wizard-form-body">
          <div class="detail-form-row">
            <label for="wizardName">Identifier / Name</label>
            <input type="text" id="wizardName" placeholder="e.g. nutanix-cli or code-standards" oninput="updateWizardPreview()" autocomplete="off" required>
            <div class="detail-form-hint" id="wizardPathHint">Target path: <code>skills/&lt;name&gt;/SKILL.md</code></div>
          </div>

          <div class="detail-form-row">
            <label for="wizardDesc">Description & Intent</label>
            <input type="text" id="wizardDesc" placeholder="e.g. Guidance and scripts for Nutanix NC2 cluster administration" oninput="updateWizardPreview()" autocomplete="off">
          </div>

          <div class="detail-form-row" id="wizardPresetsRow">
            <label>Template Preset</label>
            <div class="wizard-presets-wrap">
              <button type="button" class="wizard-preset-chip active" onclick="applyWizardPreset('cli')">CLI & Tools</button>
              <button type="button" class="wizard-preset-chip" onclick="applyWizardPreset('expert')">Domain Knowledge</button>
              <button type="button" class="wizard-preset-chip" onclick="applyWizardPreset('swarm')">Swarm Coordinator</button>
              <button type="button" class="wizard-preset-chip" onclick="applyWizardPreset('rule')">Architecture Rule</button>
            </div>
          </div>

          <div class="detail-form-row" id="wizardDirsRow">
            <label>Auto-Scaffold Subdirectories</label>
            <div class="wizard-checkbox-group">
              <label class="wizard-check"><input type="checkbox" id="scaffoldScripts" checked onchange="updateWizardPreview()"> <code>scripts/</code> (Automation)</label>
              <label class="wizard-check"><input type="checkbox" id="scaffoldReferences" checked onchange="updateWizardPreview()"> <code>references/</code> (Docs)</label>
              <label class="wizard-check"><input type="checkbox" id="scaffoldExamples" onchange="updateWizardPreview()"> <code>examples/</code> (Samples)</label>
            </div>
          </div>

          <div class="detail-form-row">
            <label for="wizardContent">Initial Content (Frontmatter & Instructions)</label>
            <textarea id="wizardContent" rows="12" class="monospace-editor"></textarea>
          </div>

          <div id="wizardError" class="detail-form-error" style="display:none"></div>

          <div class="wizard-actions-bar">
            <button type="button" class="btn-mcp-action" onclick="cancelSkillForm()">Cancel</button>
            <button type="button" class="btn-mcp-action primary" onclick="submitSkillWizard()">Scaffold & Initialize</button>
          </div>
        </div>
      </div>
    </div>
  `;

  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _setSkillHeaderButtons('empty');
  applyWizardPreset('cli');
}

let _wizardCurrentType = 'skill';
let _wizardCurrentPreset = 'cli';

function setWizardType(kind) {
  _wizardCurrentType = kind;
  const btnSkill = document.getElementById('wizardTypeSkill');
  const btnRule = document.getElementById('wizardTypeRule');
  const dirsRow = document.getElementById('wizardDirsRow');
  const pathHint = document.getElementById('wizardPathHint');

  if (btnSkill) btnSkill.classList.toggle('active', kind === 'skill');
  if (btnRule) btnRule.classList.toggle('active', kind === 'rule');
  if (dirsRow) dirsRow.style.display = kind === 'skill' ? 'block' : 'none';

  const nameVal = document.getElementById('wizardName')?.value.trim() || '<name>';
  if (pathHint) {
    pathHint.innerHTML = kind === 'skill' ? `Target path: <code>skills/${escapeHtml(nameVal)}/SKILL.md</code>` : `Target path: <code>.gemini/rules/${escapeHtml(nameVal)}.md</code>`;
  }

  if (kind === 'rule') {
    applyWizardPreset('rule');
  } else {
    applyWizardPreset('cli');
  }
}

function applyWizardPreset(preset) {
  _wizardCurrentPreset = preset;
  document.querySelectorAll('.wizard-preset-chip').forEach(c => {
    c.classList.toggle('active', c.textContent.toLowerCase().includes(preset));
  });

  const name = document.getElementById('wizardName')?.value.trim() || 'my-feature';
  const desc = document.getElementById('wizardDesc')?.value.trim() || 'Specialized instructions and guidance';
  const contentArea = document.getElementById('wizardContent');
  if (!contentArea) return;

  if (_wizardCurrentType === 'rule' || preset === 'rule') {
    contentArea.value = `# ${name.replace(/-/g, ' ').toUpperCase()} Rule

## Overview
${desc}

## Requirements & Constraints
- **Scope**: Apply strictly across all task operations.
- **Verification**: Always execute automated tests or inspection commands before finishing.
`;
  } else if (preset === 'cli') {
    contentArea.value = `---
name: ${name}
description: ${desc}
---

# ${name.replace(/-/g, ' ').toUpperCase()}

## Overview
${desc}

## Helper Scripts
- \`scripts/example.sh\`: Quick execution helper.

## Instructions
1. Inspect inputs.
2. Run script helpers where appropriate.
3. Verify exit codes and report clean markdown.
`;
  } else if (preset === 'expert') {
    contentArea.value = `---
name: ${name}
description: ${desc}
---

# ${name.replace(/-/g, ' ').toUpperCase()} Domain Reference

## Overview
${desc}

## Knowledge Bases & References
- Inspect \`references/reference.md\` for complete API contracts and schemas.
`;
  } else if (preset === 'swarm') {
    contentArea.value = `---
name: ${name}
description: ${desc}
---

# ${name.replace(/-/g, ' ').toUpperCase()} Swarm Coordinator

## Multi-Agent Workflows
When executing this workflow, spawn child agents using \`invoke_subagent\`:
- **Role**: Codebase Researcher
- **Role**: Test Verifier
`;
  }
}

function updateWizardPreview() {
  const name = document.getElementById('wizardName')?.value.trim() || 'my-feature';
  const pathHint = document.getElementById('wizardPathHint');
  if (pathHint) {
    pathHint.innerHTML = _wizardCurrentType === 'skill' ? `Target path: <code>skills/${escapeHtml(name)}/SKILL.md</code>` : `Target path: <code>.gemini/rules/${escapeHtml(name)}.md</code>`;
  }
}

async function submitSkillWizard() {
  const name = document.getElementById('wizardName')?.value.trim();
  const desc = document.getElementById('wizardDesc')?.value.trim();
  const content = document.getElementById('wizardContent')?.value;
  const errEl = document.getElementById('wizardError');

  if (!name) {
    if (errEl) { errEl.textContent = 'Name is required.'; errEl.style.display = 'block'; }
    return;
  }

  const includeDirs = [];
  if (document.getElementById('scaffoldScripts')?.checked) includeDirs.push('scripts');
  if (document.getElementById('scaffoldReferences')?.checked) includeDirs.push('references');
  if (document.getElementById('scaffoldExamples')?.checked) includeDirs.push('examples');

  const payload = {
    kind: _wizardCurrentType,
    name,
    description: desc,
    preset: _wizardCurrentPreset,
    include_dirs: includeDirs,
    content
  };

  try {
    const res = await fetch('/api/skills/scaffold', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Scaffolding failed');

    showToast(`✓ Created ${data.kind === 'rule' ? 'rule' : 'skill'}: ${data.name}`);
    _skillsData = null;
    await loadSkills();
  } catch (err) {
    if (errEl) { errEl.textContent = err.message; errEl.style.display = 'block'; }
  }
}

// Global hook to override openSkillCreate
window.openSkillCreate = openSkillOrRuleWizard;

/* ── Live Token & Thinking Velocity HUD Controller ── */
let _hudTimer = null;
let _hudStartTime = null;
let _hudFadeTimeout = null;

function updateVelocityHud(text, isDone = false, metrics = null) {
  const pill = document.getElementById('hudVelocityPill');
  const label = document.getElementById('hudVelocityText');
  if (!pill || !label) return;

  if (_hudFadeTimeout) {
    clearTimeout(_hudFadeTimeout);
    _hudFadeTimeout = null;
  }

  pill.style.display = 'inline-flex';
  pill.classList.toggle('done', Boolean(isDone));

  if (!isDone) {
    if (!_hudStartTime) _hudStartTime = Date.now();
    label.textContent = text || 'Thinking...';
  } else {
    _hudStartTime = null;
    if (metrics && metrics.tps) {
      label.textContent = `${metrics.tps} tps · ${metrics.output_tokens || 0} tok (${metrics.duration_sec || 0}s)`;
    } else {
      label.textContent = text || 'Complete';
    }
    _hudFadeTimeout = setTimeout(() => {
      pill.style.display = 'none';
    }, 10000);
  }
}

// Hook into stream status if available
if (typeof window !== 'undefined') {
  window.updateVelocityHud = updateVelocityHud;
}
