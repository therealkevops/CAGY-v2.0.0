/**
 * Antigravity (CAGY) Token Economics & Memory Efficiency Analytics Engine (Frontend)
 * Visualizes:
 * - Real-time session token consumption, throughput, and estimated spend
 * - Turn-by-turn context accumulation curves & Context Debt Thresholds
 * - Knowledge Vault compression leverage (MLR) and cumulative avoided-turn savings
 * - Multi-model pricing matrix comparisons and Context Diet recommendations
 */

let _analyticsData = null;
let _selectedAnalyticsSessionId = null;

/**
 * Main entry point: fetch efficiency metrics and render dashboard.
 */
async function loadAnalyticsPanel(force = false) {
  const container = document.getElementById('analyticsMainBody');
  if (!container) return;

  // Set loading skeleton if empty
  if (force || !container.children.length) {
    container.innerHTML = `
      <div class="analytics-loading">
        <div class="analytics-spinner"></div>
        <span>Calculating Token Economics & Memory ROI...</span>
      </div>
    `;
  }

  // Determine active session ID
  let targetSid = _selectedAnalyticsSessionId;
  if (!targetSid && typeof S !== 'undefined' && S && S.session && S.session.session_id) {
    targetSid = S.session.session_id;
  }

  try {
    const url = targetSid
      ? `/api/analytics/efficiency?session_id=${encodeURIComponent(targetSid)}`
      : '/api/analytics/efficiency';
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _analyticsData = data;

    renderAnalyticsSidebar(data);
    renderAnalyticsMain(data);
    populateAnalyticsSessionPicker();
  } catch (err) {
    console.error('Failed to load analytics:', err);
    if (container) {
      container.innerHTML = `
        <div class="analytics-error">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
          <div>
            <div style="font-weight:600;margin-bottom:4px">Failed to load analytics data</div>
            <div style="color:var(--muted);font-size:12px">${err.message || 'Unknown error'}</div>
            <button class="settings-btn" style="margin-top:12px" onclick="loadAnalyticsPanel(true)">Retry</button>
          </div>
        </div>
      `;
    }
  }
}

/**
 * Render Left Sidebar KPI strip and summary
 */
function renderAnalyticsSidebar(data) {
  const s = data.session || {};
  const v = data.vault || {};

  const sideTokens = document.getElementById('analyticsSideTokens');
  const sideCost = document.getElementById('analyticsSideCost');
  const sideMlr = document.getElementById('analyticsSideMlr');
  const sideSummary = document.getElementById('analyticsSidebarSummary');

  if (sideTokens) sideTokens.textContent = formatNumber(s.total_tokens || 0);
  if (sideCost) sideCost.textContent = `$${(s.estimated_cost_usd || 0).toFixed(4)}`;
  if (sideMlr) sideMlr.textContent = `${(v.memory_leverage_ratio || 1.0).toFixed(1)}x`;

  if (sideSummary) {
    sideSummary.innerHTML = `
      <div class="analytics-side-card">
        <div class="analytics-side-title">Memory Leverage (MLR)</div>
        <div class="analytics-side-desc">
          Your Knowledge Vault contains <strong>${formatNumber(v.total_words || 0)}</strong> words across <strong>${v.total_notes || 0}</strong> notes, distilled into <strong>${formatNumber(v.compiled_rule_tokens || 0)}</strong> prompt tokens at turn 0.
        </div>
      </div>
      <div class="analytics-side-card">
        <div class="analytics-side-title">Avoided Context Debt</div>
        <div class="analytics-side-desc">
          Estimated <strong>~${v.avoided_turns_estimate || 0}</strong> redundant context re-explanation turns saved across sessions.
        </div>
      </div>
    `;
  }
}

/**
 * Populate session picker dropdown
 */
async function populateAnalyticsSessionPicker() {
  const select = document.getElementById('analyticsSessionSelect');
  if (!select) return;

  let sessions = [];
  if (typeof S !== 'undefined' && S && Array.isArray(S.sessions) && S.sessions.length > 0) {
    sessions = S.sessions;
  } else {
    try {
      const res = await fetch('/api/sessions');
      if (res.ok) {
        const d = await res.json();
        sessions = d.sessions || d || [];
      }
    } catch (_) {}
  }

  const currentVal = _selectedAnalyticsSessionId || (S && S.session && S.session.session_id) || '';
  
  select.innerHTML = '<option value="">-- Active / Most Recent Session --</option>';
  sessions.forEach(sess => {
    const opt = document.createElement('option');
    opt.value = sess.session_id || sess.id;
    const title = (sess.title || 'Untitled Session').slice(0, 36);
    opt.textContent = `${title} (${opt.value.slice(0, 8)})`;
    if (opt.value === currentVal) opt.selected = true;
    select.appendChild(opt);
  });
}

function onAnalyticsSessionChange(val) {
  _selectedAnalyticsSessionId = val || null;
  loadAnalyticsPanel(true);
}

/**
 * Main dashboard view rendering
 */
function renderAnalyticsMain(data) {
  const container = document.getElementById('analyticsMainBody');
  if (!container) return;

  const s = data.session || {};
  const v = data.vault || {};
  const diet = data.diet || { status: 'optimal', message: 'Context is optimal.' };
  const pricing = data.pricing_comparison || {};

  const healthColor = s.context_health === 'optimal'
    ? 'var(--emerald, #10b981)'
    : s.context_health === 'growing'
      ? 'var(--amber, #f59e0b)'
      : 'var(--rose, #f43f5e)';

  const healthBadge = s.context_health === 'optimal'
    ? 'Optimal (<15k)'
    : s.context_health === 'growing'
      ? 'Growing (15k-30k)'
      : 'Bloated (>30k)';

  // Build Context Diet Banner
  let dietActionBtn = '';
  if (diet.action === '/new') {
    dietActionBtn = `<button class="analytics-diet-btn" onclick="triggerDietAction('/new')">Start Clean Session (/new)</button>`;
  } else if (diet.action === '/memorize') {
    dietActionBtn = `<button class="analytics-diet-btn" onclick="triggerDietAction('/memorize')">Memorize Key Takeaways (/memorize)</button>`;
  }

  const dietClass = diet.status === 'warning'
    ? 'diet-warning'
    : diet.status === 'notice'
      ? 'diet-notice'
      : 'diet-optimal';

  const dietIcon = diet.status === 'warning'
    ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`
    : diet.status === 'notice'
      ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`
      : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>`;

  container.innerHTML = `
    <div class="analytics-dashboard-scroll">
      <!-- 1. Hero KPI Grid -->
      <div class="analytics-hero-grid">
        <div class="analytics-kpi-card">
          <div class="analytics-kpi-label">
            <span>Context Health</span>
            <span class="analytics-health-badge" style="background:${healthColor}20;color:${healthColor};border:1px solid ${healthColor}50">${healthBadge}</span>
          </div>
          <div class="analytics-kpi-value">${formatNumber(s.current_context_tokens || 0)} <span class="analytics-kpi-unit">tok</span></div>
          <div class="analytics-kpi-sub">Prompt size in active session</div>
        </div>

        <div class="analytics-kpi-card">
          <div class="analytics-kpi-label">
            <span>Estimated Spend</span>
            <span class="analytics-tag">${s.model || 'Gemini 3.8 Flash'}</span>
          </div>
          <div class="analytics-kpi-value">$${(s.estimated_cost_usd || 0).toFixed(4)}</div>
          <div class="analytics-kpi-sub">${formatNumber(s.total_input_tokens || 0)} in · ${formatNumber(s.total_output_tokens || 0)} out</div>
        </div>

        <div class="analytics-kpi-card">
          <div class="analytics-kpi-label">
            <span>Memory Leverage (MLR)</span>
            <span class="analytics-tag">Vault Leverage</span>
          </div>
          <div class="analytics-kpi-value" style="color:var(--accent,#0288a8)">${(v.memory_leverage_ratio || 1.0).toFixed(1)}x</div>
          <div class="analytics-kpi-sub">${formatNumber(v.total_words || 0)} vault words / ${formatNumber(v.compiled_rule_tokens || 0)} rule tok</div>
        </div>

        <div class="analytics-kpi-card">
          <div class="analytics-kpi-label">
            <span>Generation Throughput</span>
            <span class="analytics-tag">${s.total_turns || 0} turns</span>
          </div>
          <div class="analytics-kpi-value">${s.avg_tps > 0 ? s.avg_tps.toFixed(1) : '--'} <span class="analytics-kpi-unit">tok/s</span></div>
          <div class="analytics-kpi-sub">Average model generation speed</div>
        </div>
      </div>

      <!-- 2. Context Diet Advisor Banner -->
      <div class="analytics-diet-banner ${dietClass}">
        <div class="analytics-diet-icon">${dietIcon}</div>
        <div class="analytics-diet-content">
          <div class="analytics-diet-title">Context Diet & Memory Preservation Advisor</div>
          <div class="analytics-diet-message">${diet.message}</div>
        </div>
        ${dietActionBtn}
      </div>

      <!-- 3. Growth Curve Chart -->
      <div class="analytics-section-card">
        <div class="analytics-section-header">
          <div>
            <h3 class="analytics-section-title">Context Accumulation Curve</h3>
            <div class="analytics-section-desc">Prompt token growth per conversation turn vs. Lean Context Threshold</div>
          </div>
          <div class="analytics-chart-legend">
            <span class="legend-item"><span class="legend-dot" style="background:#3b82f6"></span> Cumulative Prompt</span>
            <span class="legend-item"><span class="legend-dot" style="background:#10b981"></span> Turn Output</span>
            <span class="legend-item"><span class="legend-line" style="border-top:2px dashed #f43f5e"></span> Debt Limit (25k)</span>
          </div>
        </div>
        <div class="analytics-chart-container" id="analyticsChartWrap">
          ${renderGrowthCurveSvg(s.growth_curve || [])}
        </div>
      </div>

      <!-- 4. Two-Column Scorecard & Pricing Matrix -->
      <div class="analytics-two-col">
        <!-- Vault Memory ROI -->
        <div class="analytics-section-card">
          <div class="analytics-section-header">
            <div>
              <h3 class="analytics-section-title">Knowledge Vault ROI</h3>
              <div class="analytics-section-desc">Quantified leverage of persistent 2D second-brain memory</div>
            </div>
            <button class="settings-btn" onclick="switchPanel('vault',{fromRailClick:true})">Open Vault</button>
          </div>
          <div class="analytics-roi-grid">
            <div class="roi-item">
              <div class="roi-val">${v.total_notes || 0}</div>
              <div class="roi-lbl">Knowledge Notes</div>
            </div>
            <div class="roi-item">
              <div class="roi-val">${v.total_edges || 0}</div>
              <div class="roi-lbl">Graph Interlinks</div>
            </div>
            <div class="roi-item">
              <div class="roi-val">${formatNumber(v.compiled_rule_tokens || 0)}</div>
              <div class="roi-lbl">Rule Footprint (Tok)</div>
            </div>
            <div class="roi-item">
              <div class="roi-val" style="color:var(--emerald,#10b981)">~${v.avoided_turns_estimate || 0}</div>
              <div class="roi-lbl">Avoided Turns</div>
            </div>
            <div class="roi-item">
              <div class="roi-val" style="color:var(--emerald,#10b981)">~${formatNumber(v.estimated_tokens_saved || 0)}</div>
              <div class="roi-lbl">Est. Tokens Saved</div>
            </div>
            <div class="roi-item">
              <div class="roi-val" style="color:var(--emerald,#10b981)">+$${(v.estimated_usd_saved || 0).toFixed(4)}</div>
              <div class="roi-lbl">Est. Value Created</div>
            </div>
          </div>
          <div class="analytics-vault-pitch">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
            <span>Compiled vault rules give turn-0 recall without wasting prompt tokens re-explaining context every session.</span>
          </div>
        </div>

        <!-- Model Pricing Comparison -->
        <div class="analytics-section-card">
          <div class="analytics-section-header">
            <div>
              <h3 class="analytics-section-title">Model Economics Comparison</h3>
              <div class="analytics-section-desc">What this conversation would cost across tier-1 foundation models</div>
            </div>
          </div>
          <table class="analytics-pricing-table">
            <thead>
              <tr>
                <th>Model</th>
                <th>In / Out per 1M</th>
                <th>This Session</th>
                <th>Efficiency</th>
              </tr>
            </thead>
            <tbody>
              <tr class="active-row">
                <td>
                  <div style="font-weight:600">Gemini 3.8 Flash (agy)</div>
                  <div style="font-size:10px;color:var(--muted)">Active Default</div>
                </td>
                <td class="num-cell">zsh.075 / zsh.30</td>
                <td class="num-cell highlight-green">$${(pricing.gemini_3_8_flash || 0).toFixed(4)}</td>
                <td><span class="analytics-badge-pill green">Baseline</span></td>
              </tr>
              <tr>
                <td>
                  <div style="font-weight:600">Gemini 1.5 Pro</div>
                  <div style="font-size:10px;color:var(--muted)">Deep Reasoning</div>
                </td>
                <td class="num-cell">.25 / .00</td>
                <td class="num-cell">$${(pricing.gemini_1_5_pro || 0).toFixed(4)}</td>
                <td><span class="analytics-badge-pill">16.6x cost</span></td>
              </tr>
              <tr>
                <td>
                  <div style="font-weight:600">Claude 3.5 Sonnet</div>
                  <div style="font-size:10px;color:var(--muted)">Frontier Model</div>
                </td>
                <td class="num-cell">.00 / .00</td>
                <td class="num-cell">$${(pricing.claude_3_5_sonnet || 0).toFixed(4)}</td>
                <td><span class="analytics-badge-pill rose">40x cost</span></td>
              </tr>
            </tbody>
          </table>
          <div class="analytics-vault-pitch">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
            <span>Antigravity container caching + Gemini Flash delivers sub-second inference at fractions of a cent per turn.</span>
          </div>
        </div>
      </div>
    </div>
  `;
}

/**
 * Generate lightweight, zero-dependency SVG growth curve chart
 */
function renderGrowthCurveSvg(curve) {
  if (!curve || curve.length === 0) {
    return `
      <div class="analytics-chart-empty">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
        </svg>
        <span>No conversation turns recorded yet in this session. Start chatting to trace context accumulation.</span>
      </div>
    `;
  }

  const W = 680;
  const H = 220;
  const padLeft = 60;
  const padRight = 30;
  const padTop = 25;
  const padBottom = 35;
  const chartW = W - padLeft - padRight;
  const chartH = H - padTop - padBottom;

  // Max value calculation
  const maxTok = Math.max(
    30000,
    ...curve.map(c => c.prompt_tokens || 0),
    ...curve.map(c => c.total_tokens || 0)
  );

  const turnsCount = Math.max(curve.length, 1);
  const xStep = turnsCount > 1 ? chartW / (turnsCount - 1) : chartW / 2;

  const getY = (val) => padTop + chartH - ((val / maxTok) * chartH);
  const getX = (idx) => turnsCount > 1 ? padLeft + (idx * xStep) : padLeft + chartW / 2;

  // Generate points for prompt tokens
  const promptPoints = curve.map((c, idx) => ({
    x: getX(idx),
    y: getY(c.prompt_tokens || 0),
    turn: c.turn || idx + 1,
    prompt: c.prompt_tokens || 0,
    output: c.output_tokens || 0,
    tps: c.tps || 0
  }));

  // Build SVG path
  let promptPath = '';
  let promptArea = '';
  if (promptPoints.length === 1) {
    const p = promptPoints[0];
    promptPath = `M ${padLeft} ${p.y} L ${padLeft + chartW} ${p.y}`;
    promptArea = `M ${padLeft} ${p.y} L ${padLeft + chartW} ${p.y} L ${padLeft + chartW} ${padTop + chartH} L ${padLeft} ${padTop + chartH} Z`;
  } else {
    promptPath = promptPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    promptArea = `${promptPath} L ${promptPoints[promptPoints.length - 1].x.toFixed(1)} ${(padTop + chartH).toFixed(1)} L ${promptPoints[0].x.toFixed(1)} ${(padTop + chartH).toFixed(1)} Z`;
  }

  // Debt threshold line (25k)
  const debtY = getY(25000);

  // Y-axis grid lines (0, 10k, 20k, 30k, max)
  const yTicks = [0, 10000, 20000, 30000];
  if (maxTok > 35000) yTicks.push(Math.round(maxTok / 10000) * 10000);

  const gridLines = yTicks.map(t => {
    const y = getY(t);
    return `
      <line x1="${padLeft}" y1="${y}" x2="${W - padRight}" y2="${y}" stroke="var(--border2, rgba(255,255,255,0.08))" stroke-width="1" />
      <text x="${padLeft - 10}" y="${y + 4}" text-anchor="end" fill="var(--muted, #888)" font-size="10" font-family="var(--font-mono, monospace)">${t >= 1000 ? (t / 1000) + 'k' : t}</text>
    `;
  }).join('');

  // X-axis markers
  const xLabels = curve.map((c, idx) => {
    const x = getX(idx);
    return `
      <text x="${x}" y="${H - 10}" text-anchor="middle" fill="var(--muted, #888)" font-size="10" font-family="var(--font-mono, monospace)">T${c.turn || idx + 1}</text>
    `;
  }).join('');

  // Circles and hover hotspots
  const dots = promptPoints.map((p) => `
    <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4.5" fill="#3b82f6" stroke="var(--surface, #1e1e2e)" stroke-width="2" />
    <title>Turn ${p.turn}: ${formatNumber(p.prompt)} prompt tok · ${formatNumber(p.output)} out tok · ${p.tps} tok/s</title>
  `).join('');

  return `
    <svg class="analytics-chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
      <!-- Grid -->
      ${gridLines}

      <!-- Context Debt Line -->
      <line x1="${padLeft}" y1="${debtY}" x2="${W - padRight}" y2="${debtY}" stroke="#f43f5e" stroke-width="1.5" stroke-dasharray="4 4" opacity="0.8" />
      <text x="${W - padRight}" y="${debtY - 6}" text-anchor="end" fill="#f43f5e" font-size="9" font-weight="600" font-family="var(--font-mono, monospace)">DEBT THRESHOLD (25k)</text>

      <!-- Prompt Area & Line -->
      <path d="${promptArea}" fill="rgba(59, 130, 246, 0.12)" />
      <path d="${promptPath}" fill="none" stroke="#3b82f6" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />

      <!-- Dots with Tooltips -->
      ${dots}

      <!-- X-axis -->
      <line x1="${padLeft}" y1="${padTop + chartH}" x2="${W - padRight}" y2="${padTop + chartH}" stroke="var(--border, rgba(255,255,255,0.15))" stroke-width="1" />
      ${xLabels}
    </svg>
  `;
}

/**
 * Handle quick diet action from banner
 */
async function triggerDietAction(action) {
  if (action === '/new') {
    if (typeof switchPanel === 'function') switchPanel('chat');
    if (typeof cmdNew === 'function') cmdNew();
  } else if (action === '/memorize') {
    if (typeof switchPanel === 'function') switchPanel('chat');
    const composer = document.getElementById('composerInput');
    if (composer) {
      composer.value = '/memorize ';
      composer.focus();
    }
  }
}

/**
 * Number formatter helper
 */
function formatNumber(num) {
  return Number(num || 0).toLocaleString();
}
