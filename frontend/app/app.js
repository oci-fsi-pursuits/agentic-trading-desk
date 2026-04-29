const STAGES = [
  'gather',
  'quantify',
  'debate',
  'synthesize',
  'risk_review',
  'pm_review',
  'trade_finalize',
  'monitor',
];

const PHASE_AUDIT_ID = 'phase_audit';

const MACRO_PHASES = [
  {
    id: 'phase_1',
    label: '1. Information Gathering',
    stages: ['gather', 'quantify'],
    purpose: 'Collect analyst inputs and run quant validation before debate.',
  },
  {
    id: 'phase_2',
    label: '2. Research, Debate & Synthesis',
    stages: ['debate', 'synthesize'],
    purpose: 'Run structured debate, then synthesize a detailed research-manager handoff package.',
  },
  {
    id: 'phase_3',
    label: '3. Risk & Decision',
    stages: ['risk_review', 'pm_review'],
    purpose: 'Risk Manager and PM set approval, size, and guardrails.',
  },
  {
    id: 'phase_4',
    label: '4. Execution',
    stages: ['trade_finalize'],
    purpose: 'Trader simulates realistic execution and final ticket.',
  },
  {
    id: 'phase_5',
    label: '5. Monitoring',
    stages: ['monitor'],
    purpose: 'Continuous monitoring loop with reroute on breaking news.',
  },
];

const ROLE_WEIGHTS = {
  market_analyst: 0.12,
  news_analyst: 0.1,
  fundamentals_analyst: 0.12,
  bull_researcher: 0.14,
  bear_researcher: 0.14,
  aggressive_analyst: 0.08,
  conservative_analyst: 0.08,
  neutral_analyst: 0.08,
  quant_analyst: 0.1,
  risk_manager: 0.04,
};

const GATHER_ANALYST_SEATS = [
  'market_analyst',
  'news_analyst',
  'fundamentals_analyst',
  'social_analyst',
  'macro_economist',
  'geopolitical_analyst',
];

const PHASE_DEBATE_STAGES = new Set(['debate', 'synthesize', 'risk_review', 'pm_review']);
const STOCK_TIMEFRAMES = new Set(['1d', '5d', '30d', '180d', '1y']);
const BUSINESS_PHASE_LABELS = {
  phase_1: 'Research',
  phase_2: 'Debate',
  phase_3: 'Decision',
  phase_4: 'Execution',
  phase_5: 'Monitoring',
};

const state = {
  scenarios: [],
  scenarioPlan: {
    requiredSeatIds: [],
    optionalSeatIds: [],
    preferredOptionalSeatIds: [],
    suppressedSeatIds: [],
  },
  runRosterSeatIds: [],
  currentRunId: '',
  activeBackendRun: false,
  runRuntime: '',
  runTicker: '',
  runStartedAt: '',
  runCompletedAt: '',
  liveRunId: '',
  livePollHandle: null,
  queuedEvents: [],
  awaitingNextStage: false,
  pausedAfterStage: '',
  pendingFinalizeRunId: '',
  finalizingRunId: '',
  seenEventIds: new Set(),
  lastEventCount: 0,
  handoffs: [],
  debateTurns: [],
  stageMeta: {},
  stageActivity: {},
  activeStages: new Set(),
  completedStages: new Set(),
  events: [],
  consensusTimeline: [],
  llm: null,
  riskGate: { status: 'pending', note: 'Constraints not evaluated.' },
  finalPackage: { decision: null, ticket: null },
  phaseExecutionNote: '',
  phaseExecutionEmittedAt: '',
  phaseExecutionConfidence: null,
  phaseMonitoringNote: '',
  phaseMonitoringEmittedAt: '',
  phaseMonitoringConfidence: null,
  phaseQuantNote: '',
  phaseQuantEmittedAt: '',
  phasePmConfidence: null,
  roleLean: {},
  roleSnapshots: {},
  lastScenarioInstrument: '',
  stockChart: {
    ticker: '',
    peerTicker: '',
    timeframe: '30d',
    points: [],
    peerPoints: [],
    currency: 'USD',
    exchange: '',
    source: '',
    peerSource: '',
    regularMarketPrice: null,
    previousClose: null,
    loading: false,
    error: '',
    pairError: '',
    requestId: 0,
    loadedKey: '',
  },
  objects: {
    source: {},
    evidence: {},
    claim: {},
    metric: {},
    decision: {},
    trade_ticket: {},
    constraint: {},
    artifact: {},
  },
  uiActivePhaseId: 'phase_1',
  uiManualPhaseSelection: false,
};

function resetState() {
  stopLivePolling();
  state.currentRunId = '';
  state.activeBackendRun = false;
  state.runRuntime = '';
  state.runTicker = '';
  state.runStartedAt = '';
  state.runCompletedAt = '';
  state.liveRunId = '';
  state.queuedEvents = [];
  state.awaitingNextStage = false;
  state.pausedAfterStage = '';
  state.pendingFinalizeRunId = '';
  state.finalizingRunId = '';
  state.seenEventIds = new Set();
  state.lastEventCount = 0;
  state.handoffs = [];
  state.debateTurns = [];
  state.stageMeta = {};
  state.stageActivity = {};
  state.runRosterSeatIds = [];
  state.scenarioPlan = {
    requiredSeatIds: [],
    optionalSeatIds: [],
    preferredOptionalSeatIds: [],
    suppressedSeatIds: [],
  };
  state.activeStages.clear();
  state.completedStages.clear();
  state.events = [];
  state.consensusTimeline = [];
  state.llm = null;
  state.riskGate = { status: 'pending', note: 'Constraints not evaluated.' };
  state.finalPackage = { decision: null, ticket: null };
  state.phaseExecutionNote = '';
  state.phaseExecutionEmittedAt = '';
  state.phaseExecutionConfidence = null;
  state.phaseMonitoringNote = '';
  state.phaseMonitoringEmittedAt = '';
  state.phaseMonitoringConfidence = null;
  state.phaseQuantNote = '';
  state.phaseQuantEmittedAt = '';
  state.phasePmConfidence = null;
  state.roleLean = {};
  state.roleSnapshots = {};
  state.objects = {
    source: {},
    evidence: {},
    claim: {},
    metric: {},
    decision: {},
    trade_ticket: {},
    constraint: {},
    artifact: {},
  };
  state.uiActivePhaseId = 'phase_1';
  state.uiManualPhaseSelection = false;
  refreshPanels();
  updateStageControls();
}

function stopLivePolling() {
  if (state.livePollHandle) {
    clearInterval(state.livePollHandle);
    state.livePollHandle = null;
  }
}

function setStatus(message, level = 'info') {
  const text = String(message || '');
  const statusNode = document.getElementById('status');
  statusNode.textContent = text;
  statusNode.dataset.level = level === 'error' ? 'error' : 'info';
  if (level === 'error') {
    console.error(`[UI] ${text}`);
    return;
  }
  console.log(`[UI] ${text}`);
}

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function formatRole(roleId) {
  return String(roleId || '')
    .split('_')
    .map((token) => token.charAt(0).toUpperCase() + token.slice(1))
    .join(' ');
}

function formatStage(stageId) {
  return String(stageId || '').replaceAll('_', ' ');
}

function formatTime(value) {
  if (!value) return '--:--:--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--:--:--';
  return date.toLocaleTimeString([], { hour12: false });
}

function shortError(value) {
  const raw = String(value || '').replace(/\s+/g, ' ').trim();
  if (raw.length <= 180) return raw;
  return `${raw.slice(0, 177)}...`;
}

function timeValue(value) {
  if (!value) return 0;
  const ts = Date.parse(value);
  return Number.isFinite(ts) ? ts : 0;
}

function newestOf(items) {
  return items.slice().sort((a, b) => timeValue(b.emittedAt) - timeValue(a.emittedAt))[0] || null;
}

function parseTickerTokens(raw, maxSymbols = 4) {
  const input = String(raw || '').trim().toUpperCase();
  const matches = input.match(/[A-Z][A-Z0-9.-]{0,9}/g) || [];
  const unique = [];
  for (const token of matches) {
    if (!unique.includes(token)) unique.push(token);
    if (unique.length >= maxSymbols) break;
  }
  return unique;
}

function parseTickerRequest() {
  const tokens = parseTickerTokens(document.getElementById('ticker')?.value || '', 4);
  if (!tokens.length) return 'NVDA';
  return tokens.join(',');
}

function parseTicker() {
  const request = parseTickerRequest();
  const primary = String(request).split(',')[0]?.trim();
  return primary || 'NVDA';
}

function escapeRegExp(text) {
  return String(text || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function replaceTickerWord(text, fromTicker, toTicker) {
  const source = String(text || '');
  const from = String(fromTicker || '').trim().toUpperCase();
  const to = String(toTicker || '').trim().toUpperCase();
  if (!source || !from || !to || from === to) return source;
  const pattern = new RegExp(`\\b${escapeRegExp(from)}\\b`, 'g');
  return source.replace(pattern, to);
}

function applyScenarioTickerOverrides(text, scenario, requestedTickers = []) {
  let output = String(text || '');
  if (!output) return output;
  const tickers = Array.isArray(requestedTickers) ? requestedTickers : [];
  const primaryRequested = String(tickers[0] || '').trim().toUpperCase();
  const peerRequested = String(tickers[1] || '').trim().toUpperCase();
  const scenarioPrimary = String(scenario?.instrument || '').trim().toUpperCase();
  const scenarioPeer = String(
    scenario?.pair_peer
      || (Array.isArray(scenario?.instrument_universe) ? scenario.instrument_universe[1] : '')
      || '',
  ).trim().toUpperCase();

  if (primaryRequested && scenarioPrimary) {
    output = replaceTickerWord(output, scenarioPrimary, primaryRequested);
  }
  if (peerRequested && scenarioPeer) {
    output = replaceTickerWord(output, scenarioPeer, peerRequested);
  }

  const scenarioLabel = String(scenario?.instrument_label || '').trim();
  if (scenarioLabel && primaryRequested) {
    const replacement = peerRequested ? `${primaryRequested}/${peerRequested}` : primaryRequested;
    output = output.replaceAll(scenarioLabel, replacement);
    output = output.replaceAll(scenarioLabel.replace('/', ' / '), replacement.replace('/', ' / '));
  }
  return output;
}

function normalizeBreakingNewsMode(value) {
  const raw = String(value || '').trim().toLowerCase();
  if (raw === 'manual' || raw === 'manual_now' || raw === 'manual-now') return 'manual';
  if (raw === 'auto_after_gather' || raw === 'auto-after-gather' || raw === 'auto' || raw === 'timer' || raw === 'timed') {
    return 'auto_after_gather';
  }
  return 'off';
}

function breakingNewsModeLabel(value) {
  const mode = normalizeBreakingNewsMode(value);
  if (mode === 'manual') return 'manual trigger';
  if (mode === 'auto_after_gather') return 'timed trigger after gather';
  return 'off';
}

function scenarioForcesBreakingNews(scenario = selectedScenario()) {
  if (!scenario || typeof scenario !== 'object') return false;
  return Boolean(
    scenario.demo_mode?.force_breaking_news
    || scenario.branch_conditions?.force_breaking_news,
  );
}

function applyBreakingNewsControlForScenario(scenario = selectedScenario(), preferredMode = 'off') {
  const node = document.getElementById('breaking-news');
  const normalizedPreferredMode = normalizeBreakingNewsMode(preferredMode);
  if (!node) return normalizedPreferredMode;
  const forceBreakingNews = scenarioForcesBreakingNews(scenario);
  const offOption = node.querySelector('option[value="off"]');
  if (offOption) {
    offOption.disabled = forceBreakingNews;
    offOption.hidden = forceBreakingNews;
  }
  const resolvedMode = forceBreakingNews && normalizedPreferredMode === 'off'
    ? 'auto_after_gather'
    : normalizedPreferredMode;
  node.value = normalizeBreakingNewsMode(resolvedMode);
  if (forceBreakingNews && normalizeBreakingNewsMode(node.value) === 'off') {
    node.value = 'auto_after_gather';
  }
  return normalizeBreakingNewsMode(node.value);
}

function formatCurrency(value, currency = 'USD') {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return '--';
  const code = String(currency || 'USD').toUpperCase();
  const useSymbol = ['USD', 'CAD', 'AUD', 'EUR', 'GBP', 'JPY'].includes(code);
  const options = {
    style: 'currency',
    currency: code,
    minimumFractionDigits: amount >= 100 ? 2 : 3,
    maximumFractionDigits: amount >= 100 ? 2 : 3,
  };
  try {
    const locale = useSymbol ? undefined : 'en-US';
    return new Intl.NumberFormat(locale, options).format(amount);
  } catch {
    return `$${amount.toFixed(2)}`;
  }
}

function formatSignedPercent(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return '--';
  const prefix = amount > 0 ? '+' : '';
  return `${prefix}${amount.toFixed(2)}%`;
}

function formatCompactNumber(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return '--';
  try {
    return new Intl.NumberFormat('en-US', {
      notation: 'compact',
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return String(Math.round(amount));
  }
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function stanceBucket(stance) {
  const normalized = String(stance || '').trim().toLowerCase();
  if (normalized === 'bull' || normalized === 'long') return 'long';
  if (normalized === 'bear' || normalized === 'dissent' || normalized === 'short') return 'short';
  return 'neutral';
}

function stanceDisplayLabel(stance) {
  const bucket = stanceBucket(stance);
  if (bucket === 'long') return 'Long';
  if (bucket === 'short') return 'Short';
  return 'Neutral';
}

function stanceToNumeric(stance) {
  const bucket = stanceBucket(stance);
  if (bucket === 'long') return 1;
  if (bucket === 'short') return -1;
  return 0;
}

function scoreToBias(score) {
  if (score > 0.15) return 'Long Bias';
  if (score < -0.15) return 'Short Bias';
  return 'Neutral Bias';
}

function waitingMessageForSeat(seatId) {
  const map = {
    market_analyst: 'Awaiting gather-phase market assignment.',
    news_analyst: 'Awaiting gather-phase news intake.',
    fundamentals_analyst: 'Awaiting gather-phase fundamentals intake.',
    social_analyst: 'Awaiting gather-phase sentiment intake.',
    macro_economist: 'Awaiting gather-phase macro context request.',
    geopolitical_analyst: 'Awaiting gather-phase geopolitical request.',
    bull_researcher: 'Waiting for gather outputs before debate.',
    bear_researcher: 'Waiting for gather outputs before debate.',
    aggressive_analyst: 'Waiting for debate kickoff.',
    conservative_analyst: 'Waiting for debate kickoff.',
    neutral_analyst: 'Waiting for debate kickoff.',
    quant_analyst: 'Waiting for gather outputs before phase-1 quant validation.',
    research_manager: 'Coordinating phase transitions and handoffs.',
    risk_manager: 'Waiting for synthesis handoff.',
    portfolio_manager: 'Waiting for risk package.',
    trader: 'Waiting for PM decision package.',
  };
  return map[seatId] || 'Awaiting assignment.';
}

function normalizeSeatIds(value) {
  if (!Array.isArray(value)) return [];
  const unique = [];
  const seen = new Set();
  value.forEach((seatId) => {
    const normalized = String(seatId || '').trim();
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    unique.push(normalized);
  });
  return unique;
}

function scenarioSeatPlan(scenario = selectedScenario()) {
  if (!scenario) {
    return {
      requiredSeatIds: [],
      optionalSeatIds: [],
      preferredOptionalSeatIds: [],
      suppressedSeatIds: [],
    };
  }
  const requiredSeatIds = normalizeSeatIds(scenario.required_seat_ids || []);
  const optionalSeatIds = normalizeSeatIds(scenario.optional_seat_ids || []);
  const seatOverrides = scenario.seat_plan?.scenario_overrides || {};
  const preferredRaw = normalizeSeatIds(
    seatOverrides.prefer_enabled
    || scenario.branch_conditions?.prefer_enabled
    || [],
  );
  const suppressedSeatIds = normalizeSeatIds(
    seatOverrides.suppress
    || scenario.branch_conditions?.suppress_seats
    || [],
  );
  const preferredOptionalSeatIds = preferredRaw
    .filter((seatId) => optionalSeatIds.includes(seatId))
    .filter((seatId) => !suppressedSeatIds.includes(seatId));
  return {
    requiredSeatIds,
    optionalSeatIds,
    preferredOptionalSeatIds,
    suppressedSeatIds,
  };
}

function scenarioSelectableOptionalSeatIds(plan = state.scenarioPlan) {
  return normalizeSeatIds((plan?.optionalSeatIds || []).filter((seatId) => !(plan?.suppressedSeatIds || []).includes(seatId)));
}

function selectedOptionalSeatIds(plan = state.scenarioPlan) {
  const selectable = scenarioSelectableOptionalSeatIds(plan);
  const checked = Array.from(document.querySelectorAll('#optional-seats input:checked'))
    .map((input) => String(input.dataset.seatId || '').trim())
    .filter(Boolean);
  return normalizeSeatIds(checked.filter((seatId) => selectable.includes(seatId)));
}

function normalizeConfidence(value) {
  if (typeof value !== 'number') return 50;
  if (value <= 1) return clamp(value * 100, 0, 100);
  return clamp(value, 0, 100);
}

function eventConfidenceValue(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return normalizeConfidence(numeric);
}

function selectedSeatIds() {
  const scenarioId = document.getElementById('scenario').value;
  const scenario = state.scenarios.find((item) => item.scenario_id === scenarioId);
  const plan = scenarioSeatPlan(scenario);
  return normalizeSeatIds([...plan.requiredSeatIds, ...selectedOptionalSeatIds(plan)]);
}

function selectedScenario() {
  const scenarioId = document.getElementById('scenario')?.value || '';
  return state.scenarios.find((item) => item.scenario_id === scenarioId) || state.scenarios[0] || null;
}

function nextStageAfter(stageId) {
  const index = STAGES.indexOf(stageId);
  if (index < 0 || index + 1 >= STAGES.length) return '';
  return STAGES[index + 1];
}

function computePhaseProgress() {
  const runStarted = state.events.some((item) => item.eventType === 'run.started') || Boolean(state.currentRunId);
  const runComplete = state.events.some((item) => item.eventType === 'run.completed');
  const activePhaseIndex = MACRO_PHASES.findIndex((phase) => phase.stages.some((stageId) => state.activeStages.has(stageId)));

  let furthestTouchedPhaseIndex = -1;
  MACRO_PHASES.forEach((phase, idx) => {
    if (phase.stages.some((stageId) => state.completedStages.has(stageId) || state.stageMeta[stageId]?.startedAt)) {
      furthestTouchedPhaseIndex = idx;
    }
  });

  const phaseCursor = activePhaseIndex >= 0
    ? activePhaseIndex
    : Math.min(Math.max(furthestTouchedPhaseIndex + 1, 0), MACRO_PHASES.length - 1);

  return {
    runStarted,
    runComplete,
    activePhaseIndex,
    furthestTouchedPhaseIndex,
    phaseCursor,
  };
}

function getActionablePhaseIndex(progress = computePhaseProgress()) {
  if (!progress.runStarted) return 0;
  if (progress.runComplete) return -1;

  if (state.awaitingNextStage) {
    const nextStage = nextStageAfter(state.pausedAfterStage);
    if (!nextStage) return -1;
    const idx = MACRO_PHASES.findIndex((phase) => phase.stages.includes(nextStage));
    return idx >= 0 ? idx : progress.phaseCursor;
  }

  if (progress.activePhaseIndex >= 0) return progress.activePhaseIndex;
  return progress.phaseCursor;
}

function phaseLabelForStage(stageId) {
  const phase = MACRO_PHASES.find((item) => item.stages.includes(stageId));
  return phase ? phase.label : formatStage(stageId);
}

function phaseForStage(stageId) {
  return MACRO_PHASES.find((item) => item.stages.includes(stageId)) || null;
}

function businessPhaseLabel(phaseOrId) {
  const phaseId = typeof phaseOrId === 'string' ? phaseOrId : phaseOrId?.id;
  if (BUSINESS_PHASE_LABELS[phaseId]) return BUSINESS_PHASE_LABELS[phaseId];
  if (typeof phaseOrId === 'object' && phaseOrId?.label) return phaseOrId.label;
  return String(phaseId || '').trim() || 'Phase';
}

function phaseIndexById(phaseId) {
  return MACRO_PHASES.findIndex((phase) => phase.id === phaseId);
}

function phaseIdForIndex(index) {
  if (!Number.isFinite(index) || index < 0 || index >= MACRO_PHASES.length) return MACRO_PHASES[0].id;
  return MACRO_PHASES[index].id;
}

function deriveLivePhaseIndex(progress = computePhaseProgress()) {
  if (!progress.runStarted) return 0;
  if (progress.activePhaseIndex >= 0) return progress.activePhaseIndex;
  if (progress.runComplete) {
    if (progress.furthestTouchedPhaseIndex >= 0) return progress.furthestTouchedPhaseIndex;
    return MACRO_PHASES.length - 1;
  }
  const actionable = getActionablePhaseIndex(progress);
  if (actionable >= 0) return actionable;
  return clamp(progress.phaseCursor, 0, MACRO_PHASES.length - 1);
}

function deriveLivePhaseId(progress = computePhaseProgress()) {
  return phaseIdForIndex(deriveLivePhaseIndex(progress));
}

function deriveAutoWorkspacePhaseId(progress = computePhaseProgress()) {
  if (state.awaitingNextStage && state.pausedAfterStage) {
    const pausedPhase = phaseForStage(state.pausedAfterStage);
    if (pausedPhase?.id) return pausedPhase.id;
  }
  return deriveLivePhaseId(progress);
}

function normalizeWorkspacePhaseId(phaseId, fallbackId = MACRO_PHASES[0].id) {
  if (phaseId === PHASE_AUDIT_ID) return PHASE_AUDIT_ID;
  const idx = phaseIndexById(phaseId);
  if (idx >= 0) return MACRO_PHASES[idx].id;
  return fallbackId;
}

function workspacePhaseId(progress = computePhaseProgress()) {
  const livePhaseId = deriveAutoWorkspacePhaseId(progress);
  if (!state.uiManualPhaseSelection) {
    state.uiActivePhaseId = livePhaseId;
    return livePhaseId;
  }
  state.uiActivePhaseId = normalizeWorkspacePhaseId(state.uiActivePhaseId, livePhaseId);
  return state.uiActivePhaseId;
}

function phaseStatusForIndex(index, progress = computePhaseProgress()) {
  const phase = MACRO_PHASES[index];
  const activeStage = phase.stages.find((stageId) => state.activeStages.has(stageId));
  const doneCount = phase.stages.filter((stageId) => state.completedStages.has(stageId)).length;
  const allDone = doneCount === phase.stages.length;
  const partial = !activeStage && doneCount > 0 && !allDone;

  let stateKey = 'queued';
  if (allDone) {
    stateKey = 'done';
  } else if (activeStage) {
    stateKey = 'running';
  } else if (!progress.runStarted) {
    stateKey = index === 0 ? 'queued' : 'locked';
  } else if (index > progress.phaseCursor) {
    stateKey = 'locked';
  } else if (partial) {
    stateKey = 'partial';
  } else if (index < progress.phaseCursor) {
    stateKey = 'done';
  }

  return { phase, activeStage, doneCount, allDone, partial, stateKey };
}

function phaseStateLabel(status) {
  if (status.stateKey === 'running') return 'Live';
  if (status.stateKey === 'done') return 'Complete';
  if (status.stateKey === 'partial') return 'In Progress';
  if (status.stateKey === 'locked') return 'Locked';
  return 'Ready';
}

function applyPhaseWorkspaceFilter(progress = computePhaseProgress()) {
  let activePhaseId = workspacePhaseId(progress);
  if (activePhaseId === PHASE_AUDIT_ID) {
    activePhaseId = deriveLivePhaseId(progress);
    state.uiActivePhaseId = activePhaseId;
    state.uiManualPhaseSelection = false;
  }
  document.querySelectorAll('[data-phase-panel]').forEach((panel) => {
    const phaseId = panel.getAttribute('data-phase-panel');
    panel.style.display = phaseId === activePhaseId ? '' : 'none';
  });
}

function runStarted() {
  return state.events.some((item) => item.eventType === 'run.started') || Boolean(state.currentRunId);
}

function ensureStageMeta(stageId) {
  if (!state.stageMeta[stageId]) {
    state.stageMeta[stageId] = {};
  }
  return state.stageMeta[stageId];
}

function ensureStageActivity(stageId) {
  if (!state.stageActivity[stageId]) {
    state.stageActivity[stageId] = {
      instances: [],
      activeSeatIdsUnion: [],
    };
  }
  return state.stageActivity[stageId];
}

function latestStageInstance(stageId) {
  const activity = state.stageActivity[stageId];
  if (!activity || !Array.isArray(activity.instances) || !activity.instances.length) return null;
  return activity.instances[activity.instances.length - 1];
}

function stageActiveSeatIds(stageId) {
  const instance = latestStageInstance(stageId);
  return normalizeSeatIds(instance?.activeSeatIds || []);
}

function recordStageStart(stageId, event, payload) {
  const activity = ensureStageActivity(stageId);
  const instance = {
    startedAt: event.emitted_at || '',
    completedAt: '',
    objective: payload.objective || '',
    dependsOn: Array.isArray(payload.depends_on) ? payload.depends_on : [],
    activeSeatIds: normalizeSeatIds(payload.active_seat_ids || []),
    reason: payload.reason || '',
    outputSummary: '',
  };
  activity.instances.push(instance);
  if (instance.activeSeatIds.length) {
    activity.activeSeatIdsUnion = normalizeSeatIds([...activity.activeSeatIdsUnion, ...instance.activeSeatIds]);
  }
  const meta = ensureStageMeta(stageId);
  if (!meta.startedAt) {
    meta.startedAt = instance.startedAt;
  }
  meta.latestStartedAt = instance.startedAt;
  meta.objective = instance.objective || meta.objective;
  if (instance.dependsOn.length) meta.dependsOn = instance.dependsOn;
  meta.reason = instance.reason || meta.reason;
  meta.activeSeatIds = instance.activeSeatIds;
  meta.activeSeatIdsUnion = activity.activeSeatIdsUnion;
  return instance;
}

function recordStageCompleted(stageId, event, payload) {
  const activity = ensureStageActivity(stageId);
  let instance = [...activity.instances].reverse().find((item) => !item.completedAt) || null;
  if (!instance) {
    instance = {
      startedAt: '',
      completedAt: '',
      objective: '',
      dependsOn: [],
      activeSeatIds: [],
      reason: '',
      outputSummary: '',
    };
    activity.instances.push(instance);
  }
  instance.completedAt = event.emitted_at || instance.completedAt;
  if (payload.output_summary) {
    instance.outputSummary = payload.output_summary;
  }
  const meta = ensureStageMeta(stageId);
  if (!meta.startedAt) {
    meta.startedAt = instance.startedAt;
  }
  meta.completedAt = instance.completedAt;
  if (payload.output_summary) {
    meta.outputSummary = payload.output_summary;
  }
  meta.activeSeatIds = normalizeSeatIds(instance.activeSeatIds || meta.activeSeatIds || []);
  meta.activeSeatIdsUnion = normalizeSeatIds(activity.activeSeatIdsUnion || meta.activeSeatIdsUnion || []);
  return instance;
}

function seedRunRoster(seatIds) {
  const incoming = normalizeSeatIds(seatIds);
  if (!incoming.length) return;
  state.runRosterSeatIds = normalizeSeatIds([...state.runRosterSeatIds, ...incoming]);
}

function syncSeatEditorState() {
  const locked = runStarted();
  const modeNode = document.getElementById('seat-editor-mode');
  if (modeNode) {
    modeNode.textContent = locked
      ? 'Seat editor is locked for the active run. Reset or start a new simulation to change seats.'
      : 'Operator seat selection lives here.';
  }
  document.querySelectorAll('#optional-seats input[type="checkbox"]').forEach((input) => {
    input.disabled = locked;
  });
}

function renderRunRosterSummary() {
  const node = document.getElementById('run-roster-summary');
  if (!node) return;
  const scenario = selectedScenario();
  if (!scenario) {
    node.textContent = 'Run roster unavailable until a scenario is selected.';
    return;
  }
  const plan = scenarioSeatPlan(scenario);
  const selectableOptionalCount = scenarioSelectableOptionalSeatIds(plan).length;
  if (runStarted() && state.runRosterSeatIds.length) {
    node.textContent = `Run roster (${state.runRosterSeatIds.length} seats): ${state.runRosterSeatIds.map((seatId) => formatRole(seatId)).join(', ')}.`;
    return;
  }
  const configured = selectedSeatIds();
  const suppressedCount = normalizeSeatIds(plan.suppressedSeatIds).length;
  const suffix = suppressedCount ? ` ${suppressedCount} seat${suppressedCount === 1 ? '' : 's'} suppressed by scenario.` : '';
  node.textContent = `Next run configuration: ${configured.length} seats selected (${plan.requiredSeatIds.length} required + ${selectableOptionalCount} selectable optional).${suffix}`;
}

function runCompleted() {
  return state.events.some((item) => item.eventType === 'run.completed');
}

function bucketCount(bucketName) {
  return Object.keys(state.objects[bucketName] || {}).length;
}

function setPanelVisibleByChildId(childId, visible) {
  const child = document.getElementById(childId);
  const panel = child?.closest('.panel');
  if (!panel) return;
  panel.style.display = visible ? '' : 'none';
}

function updatePanelVisibility() {
  setPanelVisibleByChildId('analyst-perspectives', true);
  setPanelVisibleByChildId('quant-analyst-perspective', true);
  setPanelVisibleByChildId('handoffs', true);
  setPanelVisibleByChildId('risk-status', true);
  setPanelVisibleByChildId('execution-phase-note', true);
  setPanelVisibleByChildId('monitoring-phase-note', true);
  setPanelVisibleByChildId('outcome-banner', true);
  setPanelVisibleByChildId('decision-package', true);
  setPanelVisibleByChildId('evidence-support', true);
  setPanelVisibleByChildId('quant-metrics', true);
  setPanelVisibleByChildId('exposure-preview', true);
  setPanelVisibleByChildId('events', true);
  setPanelVisibleByChildId('audit', true);
}

function toSentenceCase(value) {
  const cleaned = String(value || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!cleaned) return '';
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

function renderSimpleList(items, emptyMessage) {
  const rows = (Array.isArray(items) ? items : [])
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .slice(0, 5);
  if (!rows.length) {
    return `<div class="phase-note-v">${escapeHtml(emptyMessage)}</div>`;
  }
  return `<ul class="phase-note-list">${rows.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
}

function renderScenarioBrief(scenario = selectedScenario()) {
  const node = document.getElementById('scenario-brief');
  if (!node) return;
  if (!scenario) {
    node.innerHTML = '<p class="scenario-summary">No scenario selected yet.</p>';
    return;
  }

  const constraints = Array.isArray(scenario.constraints) ? scenario.constraints : [];
  const blockingCount = constraints.filter((item) => String(item?.severity || '').toLowerCase() === 'blocking').length;
  const warningCount = constraints.filter((item) => String(item?.severity || '').toLowerCase() === 'warning').length;
  const plan = scenarioSeatPlan(scenario);
  const optionalSelected = selectedOptionalSeatIds(plan).length;
  const requiredCount = plan.requiredSeatIds.length;
  const optionalCount = scenarioSelectableOptionalSeatIds(plan).length;
  const configuredSeatCount = requiredCount + optionalSelected;
  const runRosterCount = state.runRosterSeatIds.length;
  const activeSeats = runStarted() && runRosterCount ? runRosterCount : configuredSeatCount;
  const tickerMatches = parseTickerTokens(document.getElementById('ticker')?.value || '', 4);
  const typedTickerLabel = tickerMatches.length
    ? tickerMatches.join(',')
    : '';
  const scenarioUniverse = Array.isArray(scenario.instrument_universe) && scenario.instrument_universe.length
    ? scenario.instrument_universe.join(' / ')
    : (scenario.instrument_label || scenario.instrument || 'N/A');
  const instrumentUniverse = typedTickerLabel || scenarioUniverse;
  const forceBreakingNews = scenarioForcesBreakingNews(scenario);
  const currentBreakingMode = normalizeBreakingNewsMode(
    document.getElementById('breaking-news')?.value
    || (forceBreakingNews ? 'auto_after_gather' : 'off'),
  );
  const breakingNewsSummary = forceBreakingNews
    ? `${breakingNewsModeLabel(currentBreakingMode)} (required)`
    : breakingNewsModeLabel(currentBreakingMode);

  const pmDefault = scenario.demo_mode?.scripted_pm_default || null;
  const pmPosture = pmDefault
    ? `${formatOutcome(pmDefault.outcome || 'needs_follow_up')} · ${pmDefault.position_size_bps || 'N/A'} bps`
    : 'No scripted PM default';
  const pmLabel = scenario.pm_decision_policy ? 'PM Range' : 'PM Default';
  const scenarioSummaryText = applyScenarioTickerOverrides(
    scenario.summary || 'No scenario summary provided.',
    scenario,
    tickerMatches,
  );
  const scenarioThesisText = applyScenarioTickerOverrides(
    scenario.thesis_prompt || 'No thesis prompt provided.',
    scenario,
    tickerMatches,
  );

  node.innerHTML = `
    <div class="scenario-brief-head">
      <div class="scenario-name">${escapeHtml(scenario.name || scenario.scenario_id || 'Scenario')}</div>
      <span class="scenario-runtime">${escapeHtml((scenario.primary_runtime || 'wayflow').toUpperCase())}</span>
    </div>
    <p class="scenario-summary">${escapeHtml(scenarioSummaryText)}</p>
    <div class="scenario-metrics">
      <div class="scenario-metric">
        <div class="k">Universe</div>
        <div class="v">${escapeHtml(instrumentUniverse)}</div>
      </div>
      <div class="scenario-metric">
        <div class="k">Active Seats</div>
        <div class="v">${escapeHtml(`${activeSeats} / ${requiredCount + optionalCount}`)}</div>
      </div>
      <div class="scenario-metric">
        <div class="k">Constraints</div>
        <div class="v">${escapeHtml(String(constraints.length))}</div>
      </div>
      <div class="scenario-metric">
        <div class="k">${escapeHtml(pmLabel)}</div>
        <div class="v">${escapeHtml(pmPosture)}</div>
      </div>
    </div>
    <div class="scenario-pill-row">
      <span class="scenario-pill">${escapeHtml(`${requiredCount} required seats`)}</span>
      <span class="scenario-pill">${escapeHtml(`${optionalSelected}/${optionalCount} optional active`)}</span>
      <span class="scenario-pill">${escapeHtml(`Breaking trigger: ${breakingNewsSummary}`)}</span>
      <span class="scenario-pill warning">${escapeHtml(`${warningCount} warning constraints`)}</span>
      <span class="scenario-pill blocking">${escapeHtml(`${blockingCount} blocking constraints`)}</span>
    </div>
    <p class="scenario-thesis">${escapeHtml(scenarioThesisText)}</p>
    <ul class="scenario-constraints">
      ${
        constraints.length
          ? constraints.slice(0, 4).map((constraint) => `
            <li>${escapeHtml(constraint.label || constraint.constraint_id || 'Constraint')} (${escapeHtml(toSentenceCase(constraint.severity || 'info'))})</li>
          `).join('')
          : '<li>No constraints configured.</li>'
      }
    </ul>
  `;
}

function buildPricePath(points, minPrice, maxPrice, options = {}) {
  if (!Array.isArray(points) || points.length < 2) return 'M0 98 L320 98';
  const width = Number.isFinite(Number(options.width)) ? Number(options.width) : 320;
  const top = Number.isFinite(Number(options.top)) ? Number(options.top) : 10;
  const bottom = Number.isFinite(Number(options.bottom)) ? Number(options.bottom) : 98;
  const span = Math.max(maxPrice - minPrice, 1e-9);
  return points.map((point, idx) => {
    const x = points.length === 1 ? 0 : (idx / (points.length - 1)) * width;
    const y = top + ((maxPrice - point.close) / span) * (bottom - top);
    return `${idx === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(' ');
}

function buildTrendPath(points, minPrice, maxPrice, startIndex = 0, options = {}) {
  if (!Array.isArray(points) || points.length - startIndex < 2) return '';
  const segment = points.slice(startIndex);
  const count = segment.length;
  const width = Number.isFinite(Number(options.width)) ? Number(options.width) : 320;
  const top = Number.isFinite(Number(options.top)) ? Number(options.top) : 10;
  const bottom = Number.isFinite(Number(options.bottom)) ? Number(options.bottom) : 98;
  const span = Math.max(maxPrice - minPrice, 1e-9);

  let xSum = 0;
  let ySum = 0;
  let xySum = 0;
  let x2Sum = 0;
  for (let idx = 0; idx < count; idx += 1) {
    const x = idx;
    const y = segment[idx].close;
    xSum += x;
    ySum += y;
    xySum += x * y;
    x2Sum += x * x;
  }
  const denominator = (count * x2Sum) - (xSum * xSum);
  const slope = denominator === 0 ? 0 : ((count * xySum) - (xSum * ySum)) / denominator;
  const intercept = (ySum - (slope * xSum)) / count;

  const firstY = intercept;
  const lastY = intercept + slope * (count - 1);
  const x0 = points.length === 1 ? 0 : (startIndex / (points.length - 1)) * width;
  const x1 = points.length === 1 ? width : ((points.length - 1) / (points.length - 1)) * width;
  const y0 = top + ((maxPrice - firstY) / span) * (bottom - top);
  const y1 = top + ((maxPrice - lastY) / span) * (bottom - top);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} L ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

function buildHorizontalLinePath(value, minPrice, maxPrice, options = {}) {
  if (!Number.isFinite(value)) return '';
  const width = Number.isFinite(Number(options.width)) ? Number(options.width) : 320;
  const top = Number.isFinite(Number(options.top)) ? Number(options.top) : 10;
  const bottom = Number.isFinite(Number(options.bottom)) ? Number(options.bottom) : 98;
  const span = Math.max(maxPrice - minPrice, 1e-9);
  const y = top + ((maxPrice - value) / span) * (bottom - top);
  return `M 0 ${y.toFixed(2)} L ${width.toFixed(2)} ${y.toFixed(2)}`;
}

function mean(values) {
  if (!Array.isArray(values) || !values.length) return null;
  const cleaned = values.map((item) => Number(item)).filter((item) => Number.isFinite(item));
  if (!cleaned.length) return null;
  const total = cleaned.reduce((sum, item) => sum + item, 0);
  return total / cleaned.length;
}

function stdev(values) {
  if (!Array.isArray(values) || values.length < 2) return null;
  const avg = mean(values);
  if (!Number.isFinite(avg)) return null;
  const variance = values
    .map((item) => Number(item))
    .filter((item) => Number.isFinite(item))
    .reduce((sum, item, _idx, arr) => sum + ((item - avg) ** 2), 0) / values.length;
  return Number.isFinite(variance) ? Math.sqrt(variance) : null;
}

function activeChartTickers() {
  const runTokens = parseTickerTokens(state.runTicker || '', 2);
  const inputTokens = parseTickerTokens(document.getElementById('ticker')?.value || '', 2);
  if (runTokens.length > 1) return runTokens.slice(0, 2);
  if (inputTokens.length > 1) return inputTokens.slice(0, 2);
  if (runTokens.length) return runTokens.slice(0, 1);
  if (inputTokens.length) return inputTokens.slice(0, 1);
  return ['NVDA'];
}

function buildVolumeProfile(points, marketPrice, binCount = 10) {
  const samples = (Array.isArray(points) ? points : [])
    .map((item) => ({
      close: Number(item.close),
      volume: Number(item.volume),
    }))
    .filter((item) => Number.isFinite(item.close) && Number.isFinite(item.volume) && item.volume > 0);
  if (samples.length < 4) return null;
  const prices = samples.map((item) => item.close);
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const normalizedBins = Math.max(6, Math.min(16, Number(binCount) || 10));
  const span = Math.max(maxPrice - minPrice, 1e-9);
  const bins = Array.from({ length: normalizedBins }, (_item, idx) => {
    const low = minPrice + (idx * span) / normalizedBins;
    const high = minPrice + ((idx + 1) * span) / normalizedBins;
    return { low, high, volume: 0, trades: 0 };
  });

  for (const sample of samples) {
    const ratio = (sample.close - minPrice) / span;
    const idx = clamp(Math.floor(ratio * normalizedBins), 0, normalizedBins - 1);
    bins[idx].volume += sample.volume;
    bins[idx].trades += 1;
  }

  let currentIndex = -1;
  if (Number.isFinite(Number(marketPrice))) {
    const ratio = (Number(marketPrice) - minPrice) / span;
    currentIndex = clamp(Math.floor(ratio * normalizedBins), 0, normalizedBins - 1);
  }

  let peakIndex = 0;
  for (let idx = 1; idx < bins.length; idx += 1) {
    if (bins[idx].volume > bins[peakIndex].volume) peakIndex = idx;
  }
  return { bins, currentIndex, peakIndex };
}

function alignPairSeries(primaryPoints, peerPoints) {
  const left = Array.isArray(primaryPoints) ? primaryPoints : [];
  const right = Array.isArray(peerPoints) ? peerPoints : [];
  if (!left.length || !right.length) return [];

  const rightByTs = new Map();
  for (const point of right) {
    const ts = Number(point.ts);
    const close = Number(point.close);
    if (Number.isFinite(ts) && Number.isFinite(close) && close > 0) {
      rightByTs.set(ts, close);
    }
  }

  const aligned = [];
  for (const point of left) {
    const ts = Number(point.ts);
    const close = Number(point.close);
    const peerClose = rightByTs.get(ts);
    if (Number.isFinite(ts) && Number.isFinite(close) && Number.isFinite(peerClose) && close > 0 && peerClose > 0) {
      aligned.push({ ts, close: close / peerClose });
    }
  }
  if (aligned.length >= 6) return aligned;

  const count = Math.min(left.length, right.length);
  if (count < 6) return aligned;
  const fallback = [];
  const leftOffset = left.length - count;
  const rightOffset = right.length - count;
  for (let idx = 0; idx < count; idx += 1) {
    const l = left[leftOffset + idx];
    const r = right[rightOffset + idx];
    const lClose = Number(l?.close);
    const rClose = Number(r?.close);
    const ts = Number(l?.ts);
    if (!Number.isFinite(lClose) || !Number.isFinite(rClose) || lClose <= 0 || rClose <= 0 || !Number.isFinite(ts)) continue;
    fallback.push({ ts, close: lClose / rClose });
  }
  return fallback;
}

function renderStockPriceChart() {
  const priceNode = document.getElementById('stock-price-value');
  const changeNode = document.getElementById('stock-price-change');
  const metaNode = document.getElementById('stock-price-meta');
  const statusNode = document.getElementById('stock-chart-status');
  const lineNode = document.getElementById('stock-price-line');
  const longNode = document.getElementById('stock-trend-long');
  const shortNode = document.getElementById('stock-trend-short');
  const timeframeSelect = document.getElementById('stock-timeframe-select');
  const volumeBarsNode = document.getElementById('stock-volume-bars');
  const volumeStatusNode = document.getElementById('stock-volume-status');
  const profileBarsNode = document.getElementById('volume-profile-bars');
  const profileStatusNode = document.getElementById('volume-profile-status');
  const pairWrapNode = document.getElementById('pair-spread-wrap');
  const pairMetaNode = document.getElementById('pair-spread-meta');
  const pairStatusNode = document.getElementById('pair-spread-status');
  const pairLineNode = document.getElementById('pair-spread-line');
  const pairMeanNode = document.getElementById('pair-spread-mean');
  const pairUpperNode = document.getElementById('pair-spread-band-upper');
  const pairLowerNode = document.getElementById('pair-spread-band-lower');
  if (!priceNode || !changeNode || !metaNode || !statusNode || !lineNode || !longNode || !shortNode || !timeframeSelect) return;

  const chart = state.stockChart;
  if (STOCK_TIMEFRAMES.has(chart.timeframe) && timeframeSelect.value !== chart.timeframe) {
    timeframeSelect.value = chart.timeframe;
  }

  const points = Array.isArray(chart.points)
    ? chart.points
      .map((item) => ({
        ts: Number(item.ts),
        close: Number(item.close),
        volume: Number(item.volume),
      }))
      .filter((item) => Number.isFinite(item.ts) && Number.isFinite(item.close))
    : [];
  const peerPoints = Array.isArray(chart.peerPoints)
    ? chart.peerPoints
      .map((item) => ({
        ts: Number(item.ts),
        close: Number(item.close),
      }))
      .filter((item) => Number.isFinite(item.ts) && Number.isFinite(item.close))
    : [];

  const latestPoint = points.at(-1) || null;
  const marketPrice = Number.isFinite(chart.regularMarketPrice) ? chart.regularMarketPrice : latestPoint?.close;
  const previousClose = Number.isFinite(chart.previousClose)
    ? chart.previousClose
    : (points.length > 1 ? points[points.length - 2].close : null);
  const pctChange = Number.isFinite(marketPrice) && Number.isFinite(previousClose) && previousClose !== 0
    ? ((marketPrice - previousClose) / previousClose) * 100
    : null;

  priceNode.textContent = formatCurrency(marketPrice, chart.currency || 'USD');
  changeNode.textContent = formatSignedPercent(pctChange);
  changeNode.classList.remove('up', 'down');
  if (Number.isFinite(pctChange)) {
    if (pctChange > 0.01) changeNode.classList.add('up');
    if (pctChange < -0.01) changeNode.classList.add('down');
  }

  const ticker = chart.ticker || activeChartTickers()[0] || parseTicker();
  const peerTicker = String(chart.peerTicker || '').trim().toUpperCase();
  const sourceLabel = chart.exchange || chart.source || 'Market Data';
  metaNode.textContent = peerTicker
    ? `${ticker} vs ${peerTicker} · ${String(chart.timeframe || '30d').toUpperCase()} · ${sourceLabel}`
    : `${ticker} · ${String(chart.timeframe || '30d').toUpperCase()} · ${sourceLabel}`;

  if (points.length >= 2) {
    const prices = points.map((item) => item.close);
    const minPrice = Math.min(...prices);
    const maxPrice = Math.max(...prices);
    lineNode.setAttribute('d', buildPricePath(points, minPrice, maxPrice));
    longNode.setAttribute('d', buildTrendPath(points, minPrice, maxPrice, 0));
    const shortStart = Math.max(0, points.length - Math.max(8, Math.round(points.length * 0.3)));
    shortNode.setAttribute('d', buildTrendPath(points, minPrice, maxPrice, shortStart));
  } else {
    lineNode.setAttribute('d', 'M0 98 L320 98');
    longNode.setAttribute('d', '');
    shortNode.setAttribute('d', '');
  }

  if (chart.loading) {
    statusNode.textContent = 'Loading price data...';
  } else if (chart.error) {
    statusNode.textContent = chart.error;
  } else if (points.length >= 2) {
    const firstTs = points[0].ts * 1000;
    const lastTs = points[points.length - 1].ts * 1000;
    statusNode.textContent = `Trendlines: long (green), short-term (orange) · ${new Date(firstTs).toLocaleDateString()} to ${new Date(lastTs).toLocaleDateString()}`;
  } else {
    statusNode.textContent = 'Waiting for price data.';
  }

  if (volumeBarsNode && volumeStatusNode) {
    const volumes = points.map((point) => (Number.isFinite(point.volume) && point.volume > 0 ? point.volume : 0));
    const maxVolume = volumes.length ? Math.max(...volumes) : 0;
    if (points.length >= 2 && maxVolume > 0) {
      const width = 320;
      const bottom = 60;
      const maxHeight = 52;
      const barWidth = Math.max(1.5, width / Math.max(points.length, 1));
      volumeBarsNode.innerHTML = points.map((point, idx) => {
        const volume = Number.isFinite(point.volume) && point.volume > 0 ? point.volume : 0;
        const ratio = volume / maxVolume;
        const height = Math.max(1, ratio * maxHeight);
        const x = idx * barWidth;
        const y = bottom - height;
        const className = idx === points.length - 1 ? 'chart-volume-bar current' : 'chart-volume-bar';
        const widthPx = Math.max(1, barWidth - 0.6);
        return `<rect class="${className}" x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${widthPx.toFixed(2)}" height="${height.toFixed(2)}"></rect>`;
      }).join('');
      const latestVolume = volumes[volumes.length - 1];
      const avgWindow = volumes.slice(-20).filter((item) => item > 0);
      const avgVolume = avgWindow.length ? avgWindow.reduce((sum, item) => sum + item, 0) / avgWindow.length : null;
      volumeStatusNode.textContent = Number.isFinite(avgVolume)
        ? `Volume: latest ${formatCompactNumber(latestVolume)} vs 20-bar avg ${formatCompactNumber(avgVolume)} shares.`
        : `Volume: latest ${formatCompactNumber(latestVolume)} shares.`;
    } else {
      volumeBarsNode.innerHTML = '';
      volumeStatusNode.textContent = chart.loading ? 'Loading volume data...' : 'Volume data unavailable for this range.';
    }
  }

  if (profileBarsNode && profileStatusNode) {
    const profile = buildVolumeProfile(points, marketPrice, 10);
    if (profile && profile.bins.length) {
      const maxBinVolume = Math.max(...profile.bins.map((bin) => bin.volume), 1);
      const rows = profile.bins.map((bin, idx) => ({ ...bin, idx })).reverse();
      profileBarsNode.innerHTML = rows.map((bin) => {
        const widthPct = clamp((bin.volume / maxBinVolume) * 100, 0, 100);
        const isCurrent = bin.idx === profile.currentIndex;
        return `
          <div class="vp-row ${isCurrent ? 'current' : ''}">
            <span>${bin.low.toFixed(2)}-${bin.high.toFixed(2)}</span>
            <div class="vp-track"><div class="vp-fill" style="width:${widthPct.toFixed(1)}%;"></div></div>
            <span>${formatCompactNumber(bin.volume)}</span>
          </div>
        `;
      }).join('');
      const currentBucket = profile.currentIndex >= 0 ? profile.bins[profile.currentIndex] : null;
      const peakBucket = profile.bins[profile.peakIndex];
      if (currentBucket) {
        profileStatusNode.textContent = `Current ${formatCurrency(marketPrice, chart.currency || 'USD')} sits in ${currentBucket.low.toFixed(2)}-${currentBucket.high.toFixed(2)} with ${formatCompactNumber(currentBucket.volume)} shares. Peak bucket ${peakBucket.low.toFixed(2)}-${peakBucket.high.toFixed(2)} (${formatCompactNumber(peakBucket.volume)}).`;
      } else {
        profileStatusNode.textContent = `Peak liquidity bucket ${peakBucket.low.toFixed(2)}-${peakBucket.high.toFixed(2)} with ${formatCompactNumber(peakBucket.volume)} shares.`;
      }
    } else {
      profileBarsNode.innerHTML = '<div class="exec-meta">Insufficient volume data to build a profile.</div>';
      profileStatusNode.textContent = chart.loading ? 'Loading price-volume profile...' : 'Price-volume profile unavailable.';
    }
  }

  if (pairWrapNode && pairMetaNode && pairStatusNode && pairLineNode && pairMeanNode && pairUpperNode && pairLowerNode) {
    if (!peerTicker) {
      pairWrapNode.hidden = true;
      pairLineNode.setAttribute('d', '');
      pairMeanNode.setAttribute('d', '');
      pairUpperNode.setAttribute('d', '');
      pairLowerNode.setAttribute('d', '');
    } else {
      pairWrapNode.hidden = false;
      const spreadPoints = alignPairSeries(points, peerPoints);
      if (spreadPoints.length >= 6) {
        const values = spreadPoints.map((item) => item.close);
        const avg = mean(values);
        const sigma = stdev(values) || 0;
        const latest = values[values.length - 1];
        const zScore = sigma > 1e-9 ? (latest - avg) / sigma : 0;
        const upper = Number.isFinite(avg) ? avg + (2 * sigma) : null;
        const lower = Number.isFinite(avg) ? avg - (2 * sigma) : null;
        const minSpread = Math.min(...values, Number.isFinite(lower) ? lower : values[0]);
        const maxSpread = Math.max(...values, Number.isFinite(upper) ? upper : values[0]);
        const options = { width: 320, top: 8, bottom: 92 };
        pairLineNode.setAttribute('d', buildPricePath(spreadPoints, minSpread, maxSpread, options));
        pairMeanNode.setAttribute('d', Number.isFinite(avg) ? buildHorizontalLinePath(avg, minSpread, maxSpread, options) : '');
        pairUpperNode.setAttribute('d', Number.isFinite(upper) ? buildHorizontalLinePath(upper, minSpread, maxSpread, options) : '');
        pairLowerNode.setAttribute('d', Number.isFinite(lower) ? buildHorizontalLinePath(lower, minSpread, maxSpread, options) : '');
        pairMetaNode.textContent = `${ticker}/${peerTicker} ratio · latest ${latest.toFixed(4)} · window ${String(chart.timeframe || '30d').toUpperCase()}`;
        pairStatusNode.textContent = `Spread z-score ${zScore.toFixed(2)} (mean ${Number(avg).toFixed(4)}, ±2σ ${Number(lower).toFixed(4)} to ${Number(upper).toFixed(4)}).`;
      } else if (chart.pairError) {
        pairLineNode.setAttribute('d', '');
        pairMeanNode.setAttribute('d', '');
        pairUpperNode.setAttribute('d', '');
        pairLowerNode.setAttribute('d', '');
        pairMetaNode.textContent = `${ticker}/${peerTicker} ratio`;
        pairStatusNode.textContent = chart.pairError;
      } else {
        pairLineNode.setAttribute('d', '');
        pairMeanNode.setAttribute('d', '');
        pairUpperNode.setAttribute('d', '');
        pairLowerNode.setAttribute('d', '');
        pairMetaNode.textContent = `${ticker}/${peerTicker} ratio`;
        pairStatusNode.textContent = chart.loading ? 'Loading peer chart for spread...' : 'Not enough overlapping points to compute spread.';
      }
    }
  }
}

async function fetchChartSnapshotByTicker(ticker, timeframe) {
  const response = await fetch(`/api/market/chart?ticker=${encodeURIComponent(ticker)}&range=${encodeURIComponent(timeframe)}`);
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.message || payload.error || `Market data request failed (${response.status})`);
  }
  return payload;
}

async function loadStockPriceChart(options = {}) {
  const force = Boolean(options.force);
  const tickers = activeChartTickers();
  const ticker = tickers[0] || parseTicker();
  const peerTicker = tickers.length > 1 ? tickers[1] : '';
  const timeframe = STOCK_TIMEFRAMES.has(state.stockChart.timeframe) ? state.stockChart.timeframe : '30d';
  const cacheKey = `${ticker}:${peerTicker}:${timeframe}`;
  if (!force && state.stockChart.loadedKey === cacheKey && state.stockChart.points.length && !state.stockChart.error) {
    return;
  }

  const requestId = state.stockChart.requestId + 1;
  state.stockChart.requestId = requestId;
  state.stockChart.loading = true;
  state.stockChart.error = '';
  state.stockChart.pairError = '';
  state.stockChart.ticker = ticker;
  state.stockChart.peerTicker = peerTicker;
  state.stockChart.peerPoints = [];
  state.stockChart.peerSource = '';
  state.stockChart.timeframe = timeframe;
  renderStockPriceChart();

  try {
    const payload = await fetchChartSnapshotByTicker(ticker, timeframe);
    if (requestId !== state.stockChart.requestId) return;
    const points = Array.isArray(payload.points) ? payload.points : [];
    if (points.length < 2) {
      throw new Error('The selected provider returned insufficient chart points for this range.');
    }
    state.stockChart.points = points;
    state.stockChart.currency = payload.currency || 'USD';
    state.stockChart.exchange = payload.exchange || '';
    state.stockChart.source = payload.source || '';
    state.stockChart.regularMarketPrice = Number(payload.regular_market_price);
    state.stockChart.previousClose = Number(payload.previous_close);
    state.stockChart.error = '';

    if (peerTicker) {
      try {
        const peerPayload = await fetchChartSnapshotByTicker(peerTicker, timeframe);
        if (requestId !== state.stockChart.requestId) return;
        const peerPoints = Array.isArray(peerPayload.points) ? peerPayload.points : [];
        if (peerPoints.length < 2) {
          throw new Error('Insufficient peer chart points for spread.');
        }
        state.stockChart.peerPoints = peerPoints;
        state.stockChart.peerSource = peerPayload.source || '';
        state.stockChart.pairError = '';
      } catch (peerError) {
        if (requestId !== state.stockChart.requestId) return;
        state.stockChart.peerPoints = [];
        state.stockChart.peerSource = '';
        state.stockChart.pairError = `Pair spread unavailable: ${shortError(peerError?.message || peerError)}`;
      }
    } else {
      state.stockChart.peerPoints = [];
      state.stockChart.peerSource = '';
      state.stockChart.pairError = '';
    }
    state.stockChart.loadedKey = cacheKey;
  } catch (error) {
    if (requestId !== state.stockChart.requestId) return;
    state.stockChart.points = [];
    state.stockChart.peerPoints = [];
    state.stockChart.loadedKey = '';
    state.stockChart.source = '';
    state.stockChart.peerSource = '';
    state.stockChart.pairError = '';
    state.stockChart.error = `Market data unavailable: ${shortError(error?.message || error)}`;
  } finally {
    if (requestId === state.stockChart.requestId) {
      state.stockChart.loading = false;
      renderStockPriceChart();
    }
  }
}

function isPhaseBoundaryStage(stageId) {
  const phase = phaseForStage(stageId);
  if (!phase || !phase.stages.length) return true;
  return phase.stages[phase.stages.length - 1] === stageId;
}

function updateStageControls() {
  const statusNode = document.getElementById('stage-gate-status');
  if (!statusNode) return;

  const progress = computePhaseProgress();
  const loading = Boolean(state.liveRunId) || progress.activePhaseIndex >= 0;
  if (!progress.runStarted) {
    statusNode.textContent = 'No run is active. Open Demo Controls to start the committee review.';
    return;
  }

  if (state.awaitingNextStage) {
    const nextStage = nextStageAfter(state.pausedAfterStage);
    const nextPhaseLabel = nextStage ? phaseLabelForStage(nextStage) : 'finalize';
    statusNode.textContent = `Run paused after ${formatStage(state.pausedAfterStage)}. Continue from Demo Controls when you want the next phase.`;
    return;
  }

  if (progress.runComplete) {
    statusNode.textContent = 'Run complete. Diagnostics remain available here for replay and audit.';
    return;
  }

  statusNode.textContent = loading
    ? 'Live run in progress. Diagnostics update as new events arrive.'
    : 'Run is between phase boundaries.';
}

function deriveConsensusScore() {
  let weightedSum = 0;
  let totalWeight = 0;
  for (const [role, item] of Object.entries(state.roleLean)) {
    const score = stanceToNumeric(item.stance);
    const confidence = normalizeConfidence(item.confidence) / 100;
    const weight = ROLE_WEIGHTS[role] || 0.06;
    weightedSum += score * confidence * weight;
    totalWeight += weight;
  }
  if (totalWeight === 0) return 0;
  return weightedSum / totalWeight;
}

function pushConsensusPoint(event, contextLabel) {
  const score = deriveConsensusScore();
  const votes = deriveVoteBreakdown();
  state.consensusTimeline.push({
    emittedAt: event.emitted_at || '',
    stage: event.stage_id || '',
    label: contextLabel,
    score,
    longCount: votes.longCount,
    neutralCount: votes.neutralCount,
    shortCount: votes.shortCount,
    total: votes.total,
  });
  state.consensusTimeline = state.consensusTimeline.slice(-160);
}

function updateRoleSnapshot(role, stage, text, stance, confidence, emittedAt) {
  const nextStance = stance ? stanceBucket(stance) : (state.roleSnapshots[role]?.stance || 'neutral');
  const next = {
    stage: stage || '',
    text: String(text || '').trim() || 'No material update yet.',
    stance: nextStance,
    confidence: Number.isFinite(confidence) ? normalizeConfidence(confidence) : (state.roleSnapshots[role]?.confidence || 50),
    emittedAt: emittedAt || '',
  };
  state.roleSnapshots[role] = next;
  if (stance) {
    state.roleLean[role] = {
      stance: nextStance,
      confidence: next.confidence,
    };
  }
}

function appendTranscript(event, text) {
  state.events.unshift({
    eventType: event.event_type,
    stage: event.stage_id,
    producer: event.producer,
    emittedAt: event.emitted_at,
    text,
  });
  state.events = state.events.slice(0, 80);
}

function addHandoff(event, fromRole, toRole, text) {
  state.handoffs.unshift({
    emittedAt: event.emitted_at || '',
    stage: event.stage_id || '',
    fromRole: fromRole || event.producer || '',
    toRole: toRole || '',
    text: String(text || '').trim(),
  });
  state.handoffs = state.handoffs.slice(0, 80);
}

function ticketLegs(ticket) {
  return Array.isArray(ticket?.legs)
    ? ticket.legs.filter((leg) => leg && typeof leg === 'object')
    : [];
}

function ticketPrimaryLeg(ticket) {
  const legs = ticketLegs(ticket);
  return legs.find((leg) => String(leg.role || '').toLowerCase() === 'primary') || legs[0] || null;
}

function ticketDisplayInstrument(ticket) {
  const explicit = String(ticket?.display_instrument || '').trim();
  if (explicit) return explicit;
  const inferred = ticketLegs(ticket)
    .map((leg) => String(leg.instrument || '').trim().toUpperCase())
    .filter(Boolean);
  return inferred.join(' / ');
}

function ticketPrimarySide(ticket) {
  return String(ticketPrimaryLeg(ticket)?.side || 'HOLD').toUpperCase();
}

function ticketPrimarySizeBps(ticket) {
  const value = Number(ticketPrimaryLeg(ticket)?.size_bps);
  return Number.isFinite(value) ? value : 0;
}

function ticketGrossExposureBps(ticket) {
  const explicit = Number(ticket?.exposure?.gross_bps);
  if (Number.isFinite(explicit)) return explicit;
  return ticketLegs(ticket).reduce((total, leg) => {
    const side = String(leg.side || '').toUpperCase();
    const size = Number(leg.size_bps);
    if (!Number.isFinite(size) || size <= 0) return total;
    return side === 'BUY' || side === 'SELL' ? total + size : total;
  }, 0);
}

function ticketNetExposureBps(ticket) {
  const explicit = Number(ticket?.exposure?.net_bps);
  if (Number.isFinite(explicit)) return explicit;
  return ticketLegs(ticket).reduce((net, leg) => {
    const side = String(leg.side || '').toUpperCase();
    const size = Number(leg.size_bps);
    if (!Number.isFinite(size) || size <= 0) return net;
    if (side === 'BUY') return net + size;
    if (side === 'SELL') return net - size;
    return net;
  }, 0);
}

function ticketEntryConditions(ticket) {
  return Array.isArray(ticket?.entry_conditions) ? ticket.entry_conditions : [];
}

function ticketExitConditions(ticket) {
  return Array.isArray(ticket?.exit_conditions) ? ticket.exit_conditions : [];
}

function ticketLegSummary(ticket) {
  const legs = ticketLegs(ticket);
  if (!legs.length) return 'No leg data';
  return legs.map((leg) => {
    const side = String(leg.side || 'HOLD').toUpperCase();
    const instrument = String(leg.instrument || '').toUpperCase() || 'UNKNOWN';
    const size = Number(leg.size_bps);
    const sizeText = Number.isFinite(size) ? `${size} bps` : '0 bps';
    return `${side} ${instrument} ${sizeText}`;
  }).join(' | ');
}

function summarizeObject(objectType, obj) {
  if (!obj || typeof obj !== 'object') return '';
  if (objectType === 'source') return obj.content || obj.title || '';
  if (objectType === 'evidence') return obj.summary || obj.title || '';
  if (objectType === 'claim') return obj.statement || '';
  if (objectType === 'metric') return `${obj.name || 'metric'}: ${obj.value}${obj.unit ? ` ${obj.unit}` : ''}`;
  if (objectType === 'artifact') return obj.label || `${obj.artifact_type || 'artifact'} published`;
  if (objectType === 'constraint') return `${obj.label || obj.constraint_id}: ${JSON.stringify(obj.value)}`;
  if (objectType === 'decision') {
    const outcome = formatOutcome(obj.outcome || 'needs_follow_up');
    const size = obj.position_size_bps ? `, ${obj.position_size_bps} bps` : '';
    return `${obj.decision_type || 'decision'} -> ${outcome}${size}`;
  }
  if (objectType === 'trade_ticket') {
    const display = ticketDisplayInstrument(obj) || 'instrument';
    const gross = ticketGrossExposureBps(obj);
    return `${display} · ${ticketLegSummary(obj)} · ${gross} bps gross`;
  }
  return '';
}

function upsertObjectBucket(objectType, objectId, value) {
  if (!objectType || !objectId || !value || typeof value !== 'object') return;
  if (!state.objects[objectType]) {
    state.objects[objectType] = {};
  }
  state.objects[objectType][objectId] = value;
}

function hydrateRoleSnapshotsFromObjects() {
  const records = [];
  const pushRecord = (role, stage, text, stance, confidence, emittedAt) => {
    if (!role || !text) return;
    records.push({ role, stage, text, stance, confidence, emittedAt });
  };

  const buckets = [
    ['source', state.objects.source || {}],
    ['evidence', state.objects.evidence || {}],
    ['claim', state.objects.claim || {}],
    ['metric', state.objects.metric || {}],
    ['artifact', state.objects.artifact || {}],
    ['constraint', state.objects.constraint || {}],
    ['decision', state.objects.decision || {}],
    ['trade_ticket', state.objects.trade_ticket || {}],
  ];

  buckets.forEach(([objectType, bucket]) => {
    Object.values(bucket).forEach((obj) => {
      const provenance = obj.provenance || {};
      const role = provenance.producer_role;
      const stage = provenance.stage_id || '';
      const emittedAt = provenance.emitted_at || '';
      const text = summarizeObject(objectType, obj);
      let stance = null;
      if (objectType === 'claim') stance = obj.stance || null;
      let confidence = null;
      if (typeof obj.confidence === 'number') confidence = normalizeConfidence(obj.confidence);
      pushRecord(role, stage, text, stance, confidence, emittedAt);
    });
  });

  const latestByRole = {};
  records.forEach((record) => {
    const current = latestByRole[record.role];
    if (!current || timeValue(record.emittedAt) > timeValue(current.emittedAt)) {
      latestByRole[record.role] = record;
    }
  });

  Object.values(latestByRole).forEach((record) => {
    const existing = state.roleSnapshots[record.role];
    if (existing && timeValue(existing.emittedAt) > timeValue(record.emittedAt)) {
      return;
    }
    updateRoleSnapshot(
      record.role,
      record.stage,
      record.text,
      record.stance,
      Number.isFinite(record.confidence) ? record.confidence : null,
      record.emittedAt,
    );
  });
}

function processEvent(event) {
  const payload = event.payload || {};

  if (event.event_type === 'run.started') {
    state.currentRunId = event.run_id;
    state.runRuntime = payload.runtime || state.runRuntime;
    state.runTicker = payload.ticker || state.runTicker;
    const hasRequestedBreakingMode = typeof payload.breaking_news_mode_requested === 'string' && payload.breaking_news_mode_requested.trim() !== '';
    const breakingNewsModeRequested = hasRequestedBreakingMode
      ? normalizeBreakingNewsMode(payload.breaking_news_mode_requested)
      : '';
    const breakingNewsModeEffective = normalizeBreakingNewsMode(payload.breaking_news_mode);
    const breakingNewsMode = applyBreakingNewsControlForScenario(selectedScenario(), breakingNewsModeEffective);
    if (Array.isArray(payload.active_seat_ids)) {
      state.runRosterSeatIds = normalizeSeatIds(payload.active_seat_ids);
    }
    if (Number.isFinite(Number(payload.debate_depth))) {
      document.getElementById('debate-depth').value = String(payload.debate_depth);
    }
    state.runStartedAt = event.emitted_at || state.runStartedAt;
    const modeOverrideNote = (
      hasRequestedBreakingMode
      && breakingNewsModeRequested !== breakingNewsModeEffective
    )
      ? ` requested ${breakingNewsModeLabel(breakingNewsModeRequested)}, effective ${breakingNewsModeLabel(breakingNewsModeEffective)}`
      : '';
    const triggerDelay = Number(payload.breaking_news_delay_s);
    const timerNote = payload.breaking_news_trigger === 'timer' && Number.isFinite(triggerDelay) && triggerDelay > 0
      ? ` timer delay ${triggerDelay.toFixed(1)}s`
      : '';
    appendTranscript(
      event,
      `Run started for ${payload.ticker || 'N/A'} on ${payload.runtime || 'unknown runtime'} (${breakingNewsModeLabel(breakingNewsMode)}${modeOverrideNote}${timerNote}).`,
    );
    updateRoleSnapshot(event.producer, event.stage_id, `Kicked off run for ${payload.ticker || 'N/A'} on ${payload.runtime || 'runtime'}.`, null, 65, event.emitted_at);
    pushConsensusPoint(event, 'Run started');
    renderScenarioBrief();
    return;
  }

  if (event.event_type === 'seat.activated') {
    seedRunRoster([payload.seat_id || event.producer]);
    updateRoleSnapshot(event.producer, event.stage_id, 'Seat activated, waiting for assignment.', null, 50, event.emitted_at);
    appendTranscript(event, `${formatRole(event.producer)} seat activated.`);
    return;
  }

  if (event.event_type === 'stage.started') {
    state.activeStages.add(event.stage_id);
    recordStageStart(event.stage_id, event, payload);
    updateRoleSnapshot(
      event.producer,
      event.stage_id,
      payload.objective || `Working ${formatStage(event.stage_id)} phase.`,
      null,
      60,
      event.emitted_at,
    );
    addHandoff(
      event,
      event.producer,
      'desk',
      payload.objective || `Started ${formatStage(event.stage_id)} phase.`,
    );
    appendTranscript(event, `${formatStage(event.stage_id)} stage started${payload.reason ? ` (${payload.reason})` : ''}.`);
    pushConsensusPoint(event, `${formatStage(event.stage_id)} started`);
    return;
  }

  if (event.event_type === 'stage.completed') {
    state.activeStages.delete(event.stage_id);
    state.completedStages.add(event.stage_id);
    recordStageCompleted(event.stage_id, event, payload);
    const completionText = payload.output_summary || `Completed ${formatStage(event.stage_id)} handoff.`;
    updateRoleSnapshot(event.producer, event.stage_id, completionText, null, 64, event.emitted_at);
    addHandoff(event, event.producer, 'desk', completionText);
    appendTranscript(event, `${formatStage(event.stage_id)} stage completed${payload.status ? ` (${payload.status})` : ''}.`);
    pushConsensusPoint(event, `${formatStage(event.stage_id)} completed`);
    return;
  }

  if (event.event_type === 'source.ingested') {
    if (payload.source && payload.source_id) {
      upsertObjectBucket('source', payload.source_id, payload.source);
    }
    const source = state.objects.source[payload.source_id];
    if (source) {
      updateRoleSnapshot(event.producer, event.stage_id, source.content, payload.stance || null, eventConfidenceValue(payload.confidence), event.emitted_at);
      appendTranscript(event, `${formatRole(event.producer)} provided fresh source context.`);
      addHandoff(event, event.producer, 'research_manager', source.title || 'New source context');
    }
    return;
  }

  if (event.event_type === 'evidence.upserted') {
    if (payload.object && payload.object_id) {
      upsertObjectBucket('evidence', payload.object_id, payload.object);
    }
    const evidence = state.objects.evidence[payload.object_id];
    if (evidence) {
      updateRoleSnapshot(event.producer, event.stage_id, evidence.summary, payload.stance || null, evidence.confidence, event.emitted_at);
      appendTranscript(event, `${formatRole(event.producer)} added evidence: ${evidence.title}.`);
      addHandoff(event, event.producer, 'research_manager', evidence.title || 'Evidence update');
    }
    return;
  }

  if (event.event_type === 'claim.upserted') {
    if (payload.object && payload.object_id) {
      upsertObjectBucket('claim', payload.object_id, payload.object);
    }
    const claim = state.objects.claim[payload.object_id];
    if (claim) {
      updateRoleSnapshot(event.producer, event.stage_id, claim.statement, claim.stance, claim.confidence, event.emitted_at);
      pushConsensusPoint(event, `${formatRole(event.producer)} ${stanceDisplayLabel(claim.stance)}`);
      const replyToClaimId = payload.reply_to_claim_id || claim.reply_to_claim_id || '';
      const replyToClaim = replyToClaimId ? state.objects.claim[replyToClaimId] : null;
      const replyToRole = replyToClaim?.provenance?.producer_role || '';
      state.debateTurns.push({
        claimId: claim.claim_id,
        stage: event.stage_id || claim.provenance?.stage_id || 'debate',
        roundIndex: Number(payload.round_index || claim.round_index || 0),
        turnIndex: Number(payload.turn_index || claim.turn_index || 0),
        role: event.producer,
        stance: stanceBucket(claim.stance || payload.stance || 'neutral'),
        statement: claim.statement || '',
        replyToClaimId,
        replyToRole,
        emittedAt: event.emitted_at || '',
      });
      state.debateTurns = state.debateTurns.slice(-120);
      const stanceLabel = stanceDisplayLabel(claim.stance).toLowerCase();
      appendTranscript(
        event,
        `${formatRole(event.producer)} (${stanceLabel}) ${replyToRole ? `responded to ${formatRole(replyToRole)}` : 'opened the debate'}.`,
      );
      addHandoff(event, event.producer, 'research_manager', `${stanceDisplayLabel(claim.stance)} debate claim submitted`);
    }
    return;
  }

  if (event.event_type === 'metric.upserted') {
    if (payload.object && payload.object_id) {
      upsertObjectBucket('metric', payload.object_id, payload.object);
    }
    const metric = state.objects.metric[payload.object_id];
    if (metric) {
      updateRoleSnapshot(event.producer, event.stage_id, `${metric.name}: ${metric.value} ${metric.unit}`.trim(), payload.stance || null, metric.confidence, event.emitted_at);
      appendTranscript(event, `${formatRole(event.producer)} published quant metric ${metric.name}.`);
      addHandoff(event, event.producer, 'research_manager', `Quant metric ${metric.name}`);
    }
    return;
  }

  if (event.event_type === 'artifact.created') {
    if (payload.artifact && payload.artifact_id) {
      upsertObjectBucket('artifact', payload.artifact_id, payload.artifact);
    }
    const artifact = state.objects.artifact[payload.artifact_id];
    const payloadConfidence = eventConfidenceValue(payload.confidence);
    const executionNote = event.stage_id === 'trade_finalize' ? cleanAgentNarrative(payload.execution_note || '') : '';
    const monitoringNote = event.stage_id === 'monitor' ? cleanAgentNarrative(payload.monitoring_note || '') : '';
    const quantNote = event.stage_id === 'quantify' ? cleanAgentNarrative(payload.quant_note || '') : '';
    if (executionNote) {
      state.phaseExecutionNote = executionNote;
      state.phaseExecutionEmittedAt = event.emitted_at || state.phaseExecutionEmittedAt;
      state.phaseExecutionConfidence = Number.isFinite(payloadConfidence)
        ? payloadConfidence
        : state.phaseExecutionConfidence;
    }
    if (monitoringNote) {
      state.phaseMonitoringNote = monitoringNote;
      state.phaseMonitoringEmittedAt = event.emitted_at || state.phaseMonitoringEmittedAt;
      state.phaseMonitoringConfidence = Number.isFinite(payloadConfidence)
        ? payloadConfidence
        : state.phaseMonitoringConfidence;
    }
    if (quantNote) {
      state.phaseQuantNote = quantNote;
      state.phaseQuantEmittedAt = event.emitted_at || state.phaseQuantEmittedAt;
    }
    const message = quantNote || monitoringNote || executionNote || artifact?.label || `${payload.artifact_type || 'artifact'} published`;
    updateRoleSnapshot(event.producer, event.stage_id, message, payload.stance || null, payloadConfidence, event.emitted_at);
    appendTranscript(event, `${formatRole(event.producer)} published ${payload.artifact_type || 'artifact'}.`);
    addHandoff(event, event.producer, 'desk', message);
    return;
  }

  if (event.event_type === 'approval.requested') {
    state.riskGate.note = 'PM approval checkpoint is active.';
    updateRoleSnapshot(event.producer, event.stage_id, 'Requested PM checkpoint and editable trade fields.', payload.stance || null, 66, event.emitted_at);
    addHandoff(event, event.producer, 'risk_manager', 'Requested final risk-aware approval review.');
    appendTranscript(event, 'Portfolio Manager checkpoint requested for final sizing.' );
    return;
  }

  if (event.event_type === 'approval.resolved') {
    if (payload.decision && payload.decision.decision_id) {
      upsertObjectBucket('decision', payload.decision.decision_id, payload.decision);
    }
    const outcome = payload.outcome || 'unknown';
    const payloadConfidence = eventConfidenceValue(payload.confidence);
    state.riskGate.status = outcome === 'rejected' ? 'blocked' : 'passed';
    state.riskGate.note = `PM outcome: ${outcome}.`;
    const stance = payload.stance || (outcome === 'rejected' ? 'short' : 'long');
    state.phasePmConfidence = Number.isFinite(payloadConfidence)
      ? payloadConfidence
      : state.phasePmConfidence;
    updateRoleSnapshot(
      event.producer,
      event.stage_id,
      payload.note || `Resolved PM decision: ${formatOutcome(outcome)}.`,
      stance,
      payloadConfidence,
      event.emitted_at,
    );
    addHandoff(event, event.producer, 'trader', `PM verdict: ${formatOutcome(outcome)}.`);
    appendTranscript(event, `PM decision resolved: ${outcome}.`);
    pushConsensusPoint(event, `PM decision ${formatOutcome(outcome)}`);
    return;
  }

  if (event.event_type === 'risk.rechecked') {
    if (Array.isArray(payload.constraints)) {
      payload.constraints.forEach((constraint) => {
        if (constraint && constraint.constraint_id) {
          upsertObjectBucket('constraint', constraint.constraint_id, constraint);
        }
      });
    }
    const status = payload.status || 'passed';
    state.riskGate.status = status === 'blocked' ? 'blocked' : (status === 'adjusted' ? 'warning' : 'passed');
    state.riskGate.note = `Risk recheck status: ${status}.`;
    updateRoleSnapshot(event.producer, event.stage_id, `Risk recheck returned ${status}.`, payload.stance || null, 72, event.emitted_at);
    addHandoff(event, event.producer, 'portfolio_manager', `Risk gate ${status}.`);
    appendTranscript(event, `Risk recheck returned ${status}.`);
    return;
  }

  if (event.event_type === 'ticket.updated') {
    if (payload.ticket && payload.ticket.ticket_id) {
      upsertObjectBucket('trade_ticket', payload.ticket.ticket_id, payload.ticket);
    }
    const payloadConfidence = eventConfidenceValue(payload.confidence);
    const executionNote = cleanAgentNarrative(payload.execution_note || '');
    if (executionNote) {
      state.phaseExecutionNote = executionNote;
      state.phaseExecutionEmittedAt = event.emitted_at || state.phaseExecutionEmittedAt;
      state.phaseExecutionConfidence = Number.isFinite(payloadConfidence)
        ? payloadConfidence
        : state.phaseExecutionConfidence;
    }
    const updateText = executionNote || `Ticket status is now ${payload.status || 'unknown'}.`;
    updateRoleSnapshot(event.producer, event.stage_id, updateText, payload.stance || null, payloadConfidence, event.emitted_at);
    addHandoff(event, event.producer, 'desk', executionNote || `Execution ticket moved to ${payload.status || 'unknown'}.`);
    appendTranscript(event, `Trade ticket moved to ${payload.status || 'unknown'} status.`);
    return;
  }

  if (event.event_type === 'run.completed') {
    state.runCompletedAt = event.emitted_at || state.runCompletedAt;
    updateRoleSnapshot(event.producer, event.stage_id, 'Run complete. Final decision package delivered to desk.', null, 78, event.emitted_at);
    addHandoff(event, event.producer, 'desk', 'Delivered final decision package.');
    appendTranscript(event, 'Run completed. Decision package finalized.');
  }
}

function collectEvidenceLists() {
  const evidence = Object.values(state.objects.evidence || {});
  const sorted = evidence
    .slice()
    .sort((a, b) => normalizeConfidence(b.confidence) - normalizeConfidence(a.confidence));

  const support = sorted
    .filter((item) => !Array.isArray(item.tags) || !item.tags.includes('risk'))
    .slice(0, 4);

  const risk = sorted
    .filter((item) => Array.isArray(item.tags) && item.tags.some((tag) => ['risk', 'event', 'valuation', 'liquidity', 'geopolitics'].includes(tag)))
    .slice(0, 4);

  return { support, risk };
}

function deriveFinalPackage() {
  const decisions = Object.values(state.objects.decision || {});
  const pm = decisions
    .filter((item) => item.decision_type === 'pm_approval')
    .sort((a, b) => String(b.provenance?.emitted_at || '').localeCompare(String(a.provenance?.emitted_at || '')))[0] || null;

  const ticket = Object.values(state.objects.trade_ticket || {})[0] || null;
  state.finalPackage = { decision: pm, ticket };
}

function linkedDecisionClaims(decision) {
  const claimBucket = state.objects.claim || {};
  const supporting = (decision?.linked_claim_ids || [])
    .map((id) => claimBucket[id])
    .filter(Boolean);
  const dissenting = (decision?.dissent_claim_ids || [])
    .map((id) => claimBucket[id])
    .filter(Boolean);
  const seen = new Set();
  const combined = [];
  for (const claim of [...supporting, ...dissenting]) {
    const id = String(claim?.claim_id || '');
    if (!id || seen.has(id)) continue;
    seen.add(id);
    combined.push(claim);
  }
  return { supporting, dissenting, combined };
}

function deriveVoteBreakdown() {
  const debateClaims = Object.values(state.objects.claim || {})
    .filter((claim) => claim?.provenance?.stage_id === 'debate');
  if (debateClaims.length) {
    const latestBySeat = new Map();
    for (const claim of debateClaims) {
      const seatId = String(claim?.provenance?.producer_role || '').trim().toLowerCase();
      if (!seatId) continue;
      const turnIndex = Number.isFinite(Number(claim?.turn_index)) ? Number(claim.turn_index) : 0;
      const emittedAt = timeValue(claim?.provenance?.emitted_at);
      const previous = latestBySeat.get(seatId);
      if (!previous || turnIndex > previous.turnIndex || (turnIndex === previous.turnIndex && emittedAt >= previous.emittedAt)) {
        latestBySeat.set(seatId, { stance: stanceBucket(claim?.stance || 'neutral'), turnIndex, emittedAt });
      }
    }

    let longCount = 0;
    let shortCount = 0;
    let neutralCount = 0;
    const votes = latestBySeat.size ? [...latestBySeat.values()] : debateClaims.map((claim) => ({ stance: stanceBucket(claim?.stance || 'neutral') }));
    for (const vote of votes) {
      const stance = vote.stance;
      if (stance === 'long') {
        longCount += 1;
      } else if (stance === 'short') {
        shortCount += 1;
      } else {
        neutralCount += 1;
      }
    }
    return { longCount, neutralCount, shortCount, total: votes.length, basis: 'seats' };
  }

  const seatIds = selectedSeatIds();
  let longCount = 0;
  let shortCount = 0;
  let neutralCount = 0;
  for (const seatId of seatIds) {
    const stance = stanceBucket(state.roleSnapshots[seatId]?.stance || 'neutral');
    if (stance === 'long') {
      longCount += 1;
    } else if (stance === 'short') {
      shortCount += 1;
    } else {
      neutralCount += 1;
    }
  }
  return { longCount, neutralCount, shortCount, total: seatIds.length, basis: 'seats' };
}

function formatOutcome(outcome) {
  const raw = String(outcome || 'needs_follow_up').replaceAll('_', ' ');
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

function decisionMeaning(decision) {
  if (!decision) return '';
  const action = String(decision.position_action || '').toLowerCase();
  if (action === 'defer') {
    return 'Desk stays neutral for now and waits for better confirmation before putting risk on.';
  }
  if (action === 'exit') {
    return 'Desk exits the existing position and requires fresh work before any re-entry.';
  }
  if (action === 'trim') {
    return 'Desk keeps some exposure but reduces risk materially while the thesis is reassessed.';
  }
  if (action === 'hold') {
    return 'Desk maintains the current position but under tighter monitoring and without adding risk.';
  }
  if (decision.outcome === 'approved_with_changes') {
    return 'Trade is approved only if PM/risk modifications are applied before execution.';
  }
  if (decision.outcome === 'approved') {
    return 'Trade is approved as proposed, within portfolio and risk limits.';
  }
  if (decision.outcome === 'rejected') {
    return 'Trade is blocked. Desk must revise thesis, sizing, or timing before re-submission.';
  }
  return 'Decision requires additional follow-up before execution.';
}

function deriveRequiredChanges(decision, ticket) {
  if (!decision || decision.outcome !== 'approved_with_changes') return [];
  const changes = [];
  if (decision.position_action === 'defer') {
    changes.push('Wait for stronger confirmation before initiating any new position.');
  }
  if (decision.position_action === 'trim') {
    changes.push('Reduce existing exposure and reassess after stabilization.');
  }
  const sizeBps = decision.position_size_bps || ticketPrimarySizeBps(ticket);
  if (sizeBps) {
    changes.push(`Cap initial position at ${sizeBps} bps until post-event confirmation.`);
  }
  const linkedConstraints = (decision.linked_constraint_ids || [])
    .map((constraintId) => state.objects.constraint[constraintId])
    .filter(Boolean)
    .map((constraint) => `${constraint.label || constraint.constraint_id} (${constraint.severity || 'n/a'})`);
  linkedConstraints.slice(0, 3).forEach((item) => {
    changes.push(`Must satisfy: ${item}.`);
  });
  const entryRules = ticketEntryConditions(ticket);
  if (entryRules.length) {
    changes.push(`Execution gating: ${entryRules.slice(0, 3).join(', ')}.`);
  }
  return changes.slice(0, 4);
}

function bpsToPercentString(bps) {
  const value = Number(bps);
  if (!Number.isFinite(value)) return 'N/A';
  return `${(value / 100).toFixed(2)}%`;
}

function deriveDecisionDirection(decision, ticket, consensusScore) {
  const normalizedStance = String(decision?.stance || '').toUpperCase();
  if (normalizedStance === 'LONG') return 'LONG';
  if (normalizedStance === 'SHORT') return 'SHORT';
  if (normalizedStance === 'NEUTRAL') return 'NEUTRAL';
  if (!decision || decision.outcome === 'rejected') return 'NEUTRAL';
  if (ticketPrimarySide(ticket) === 'BUY') return 'LONG';
  if (ticketPrimarySide(ticket) === 'SELL') return 'SHORT';
  if (consensusScore > 0.12) return 'LONG';
  if (consensusScore < -0.12) return 'SHORT';
  return 'NEUTRAL';
}

function deriveConvictionLabel(consensusScore, decision) {
  if (!decision || decision.outcome === 'rejected') return 'Weak';
  const absolute = Math.abs(consensusScore);
  if (absolute >= 0.45) return 'Strong';
  if (absolute >= 0.2) return 'Moderate';
  return 'Weak';
}

function parseTargetStop(ticket) {
  const conditions = [
    ...ticketEntryConditions(ticket),
    ...ticketExitConditions(ticket),
  ].map((item) => String(item || ''));
  let target = '';
  let stop = '';
  for (const condition of conditions) {
    if (!target) {
      const targetMatch = condition.match(/(?:target|take[_-]?profit|tp)[_-]?(\d+(?:\.\d+)?)pct/i);
      if (targetMatch) target = `+${targetMatch[1]}%`;
    }
    if (!stop) {
      const stopMatch = condition.match(/(?:stop(?:[_-]?loss)?|sl)[_-]?(\d+(?:\.\d+)?)pct/i);
      if (stopMatch) stop = `-${stopMatch[1]}%`;
    }
  }
  return { target: target || 'Optional', stop: stop || 'Optional' };
}

function inferSector(ticker) {
  const normalized = String(ticker || '').toUpperCase().replace(/\s+/g, '');
  if (normalized.includes('NVDA/AMD')) return 'Semis Pair';
  const map = {
    NVDA: 'Semiconductors',
    ORCL: 'Software',
    AAPL: 'Consumer Tech',
    MSFT: 'Software',
    AMZN: 'Internet',
    META: 'Internet',
    JPM: 'Financials',
  };
  return map[normalized] || 'Cross-Sector';
}

function renderExposurePreview() {
  const node = document.getElementById('exposure-preview');
  if (!node) return;
  const decision = state.finalPackage.decision;
  const ticket = state.finalPackage.ticket;
  if (!decision || !ticket) {
    node.innerHTML = '<div class="section-subtitle">Exposure preview appears after PM decision and ticket finalization.</div>';
    return;
  }
  const pairTicket = ticket?.ticket_type === 'pair_trade';
  const pairNetBps = ticketNetExposureBps(ticket);
  const sizeBps = pairTicket
    ? Math.abs(pairNetBps)
    : Number(decision.position_size_bps || ticketPrimarySizeBps(ticket) || 0);
  const consensus = deriveConsensusScore();
  const direction = deriveDecisionDirection(decision, ticket, consensus);
  const sign = direction === 'SHORT' ? -1 : direction === 'LONG' ? 1 : 0;
  const directionalSign = pairTicket ? Math.sign(pairNetBps) : sign;
  const deltaPct = pairTicket ? (pairNetBps / 100) : ((sizeBps / 100) * sign);
  const sector = inferSector(ticketDisplayInstrument(ticket) || state.runTicker);
  const corrConstraint = Object.values(state.objects.constraint || {}).find((item) => item.constraint_id === 'ai_basket_correlation');
  const factorBase = corrConstraint?.value === 'elevated_watch' ? 0.18 : 0.06;
  const factorDelta = factorBase * directionalSign;
  const formatSigned = (value, suffix = '%') => {
    const n = Number(value || 0);
    const prefix = n > 0 ? '+' : '';
    return `${prefix}${n.toFixed(2)}${suffix}`;
  };
  node.innerHTML = `
    <div class="exposure-grid">
      <div class="exposure-item">
        <div class="k">Portfolio Delta</div>
        <div class="v">${escapeHtml(formatSigned(deltaPct))}</div>
      </div>
      <div class="exposure-item">
        <div class="k">Sector Tilt</div>
        <div class="v">${escapeHtml(formatSigned(deltaPct))} ${escapeHtml(sector)}</div>
      </div>
      <div class="exposure-item">
        <div class="k">Factor Tilt</div>
        <div class="v">${escapeHtml(formatSigned(factorDelta, ' beta'))}</div>
      </div>
    </div>
  `;
}

function runNextTickerAction() {
  resetState();
  const tickerInput = document.getElementById('ticker');
  tickerInput.focus();
  tickerInput.select();
  setStatus('Ready for next ticker. Enter a symbol, then open Demo Controls to start the run.');
}

function startNewSimulationAction() {
  resetState();
  setStatus('New simulation ready. Adjust the demo settings you want, then start from Demo Controls.');
}

function exportDecisionPacketAction() {
  deriveFinalPackage();
  const decision = state.finalPackage.decision;
  const ticket = state.finalPackage.ticket;
  if (!decision || !ticket) {
    setStatus('Decision packet not available yet.', 'error');
    return;
  }
  const linkedClaims = linkedDecisionClaims(decision);
  const payload = {
    generated_at: new Date().toISOString(),
    run_id: state.currentRunId,
    ticker: state.runTicker || ticketDisplayInstrument(ticket),
    decision,
    ticket,
    consensus_score: deriveConsensusScore(),
    committee_vote: deriveVoteBreakdown(),
    risk_gate: state.riskGate,
    linked_constraints: (decision.linked_constraint_ids || [])
      .map((id) => state.objects.constraint[id])
      .filter(Boolean),
    top_claims: linkedClaims.combined.slice(0, 8),
    supporting_claims: linkedClaims.supporting.slice(0, 8),
    dissenting_claims: linkedClaims.dissenting.slice(0, 8),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `decision-packet-${state.currentRunId || 'run'}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 500);
  setStatus('Decision packet exported.');
}

function renderFlow() {
  const navNode = document.getElementById('phase-nav');
  const progressNode = document.getElementById('phase-progress-line');
  const followLiveButton = document.getElementById('phase-follow-live-btn');
  if (!navNode) return;

  deriveFinalPackage();
  const progress = computePhaseProgress();
  const actionablePhaseIndex = getActionablePhaseIndex(progress);
  const livePhaseIndex = deriveLivePhaseIndex(progress);
  const livePhaseId = deriveLivePhaseId(progress);
  const selectedPhaseId = workspacePhaseId(progress);
  const totalPhases = MACRO_PHASES.length;

  if (progressNode && !progress.runStarted) {
    progressNode.textContent = `Phase 0 of ${totalPhases} · Waiting to run.`;
  } else if (progressNode && progress.runComplete) {
    progressNode.textContent = `Phase ${totalPhases} of ${totalPhases} · Run complete.`;
  } else if (progressNode && progress.activePhaseIndex >= 0) {
    progressNode.textContent = `${businessPhaseLabel(MACRO_PHASES[progress.activePhaseIndex])} is live.`;
  } else if (progressNode) {
    progressNode.textContent = `${businessPhaseLabel(MACRO_PHASES[progress.phaseCursor])} is ready for review.`;
  }

  navNode.innerHTML = MACRO_PHASES.map((phase, idx) => {
    const status = phaseStatusForIndex(idx, progress);
    const classes = ['phase-tab'];
    if (status.stateKey !== 'queued' && status.stateKey !== 'partial') classes.push(status.stateKey);
    if (selectedPhaseId === phase.id) classes.push('active');
    const stateLabel = phaseStateLabel(status);
    const canStartHere = !progress.runComplete
      && idx === actionablePhaseIndex
      && (!progress.runStarted || state.awaitingNextStage || progress.activePhaseIndex === idx);
    const isRunningTab = progress.runStarted && !state.awaitingNextStage && progress.activePhaseIndex === idx;
    const tabAction = canStartHere
      ? `<button type="button" class="phase-tab-cta" data-phase-action="start" data-phase-index="${idx}" ${isRunningTab ? 'disabled' : ''}>${escapeHtml(isRunningTab ? 'Running' : 'Start')}</button>`
      : '';
    return `
      <div class="${classes.join(' ')}" data-phase-id="${phase.id}">
        <button type="button" class="phase-tab-select" data-phase-id="${phase.id}">
        <span class="name">${escapeHtml(businessPhaseLabel(phase))}</span>
        <span class="state">${escapeHtml(stateLabel)}</span>
        </button>
        ${tabAction}
      </div>
    `;
  }).join('');

  if (followLiveButton) {
    const shouldShowFollowLive = state.uiManualPhaseSelection && selectedPhaseId !== livePhaseId;
    followLiveButton.style.display = shouldShowFollowLive ? '' : 'none';
    followLiveButton.textContent = `Follow Live Phase (${businessPhaseLabel(MACRO_PHASES[livePhaseIndex])})`;
  }
}

function cleanAgentNarrative(value) {
  const raw = String(value || '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/\b(?:about|around|roughly|approximately|~)?\s*\d+\s*words?\b/gi, '')
    .replace(/\(\s*~?\s*\d+\s*words?\s*\)/gi, '')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/gi, '$1')
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*{1,3}/g, '')
    .replace(/_{1,3}/g, '');
  const normalized = raw
    .split('\n')
    .map((line) => {
      const trimmed = line.trim();
      if (!trimmed) return '';
      if (/^\|?[\s:-]+\|[\s|:-]*$/.test(trimmed)) return '';
      if (trimmed.includes('|') && (trimmed.match(/\|/g) || []).length >= 2) {
        const cells = trimmed
          .split('|')
          .map((cell) => cell.trim())
          .filter(Boolean);
        return cells.join(' · ');
      }
      if (/^[-*]\s+/.test(trimmed)) {
        return `• ${trimmed.replace(/^[-*]\s+/, '')}`;
      }
      return trimmed;
    })
    .join('\n')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return normalized;
}

function formatAgentTextHtml(value) {
  const text = cleanAgentNarrative(value);
  if (!text) return '';
  return text
    .split('\n')
    .map((line) => {
      const trimmed = String(line || '').trim();
      if (!trimmed) return '';
      const breakingMatch = trimmed.match(/^breaking news\s*-\s*(.*)$/i);
      if (breakingMatch) {
        const headline = String(breakingMatch[1] || '').trim();
        return `<span class="breaking-news-prefix">BREAKING NEWS -</span>${headline ? ` ${escapeHtml(headline)}` : ''}`;
      }
      return escapeHtml(trimmed);
    })
    .join('<br>');
}

function latestRoleObject(bucket, roleId, stageId = '') {
  return Object.values(bucket || {})
    .filter((item) => {
      if (item?.provenance?.producer_role !== roleId) return false;
      if (stageId && item?.provenance?.stage_id !== stageId) return false;
      return true;
    })
    .sort((a, b) => timeValue(a.provenance?.emitted_at) - timeValue(b.provenance?.emitted_at))
    .at(-1) || null;
}

function buildAgentPerspectiveText(seatId, snapshot) {
  const latestGatherSource = latestRoleObject(state.objects.source, seatId, 'gather');
  if (
    seatId === 'news_analyst'
    && latestGatherSource?.content
    && /breaking news\s*-/i.test(String(latestGatherSource.content))
  ) {
    return cleanAgentNarrative(latestGatherSource.content);
  }
  const latestGatherEvidence = latestRoleObject(state.objects.evidence, seatId, 'gather');
  if (latestGatherEvidence?.summary) {
    return cleanAgentNarrative(latestGatherEvidence.summary);
  }
  if (latestGatherSource?.content) {
    return cleanAgentNarrative(latestGatherSource.content);
  }
  if (snapshot?.stage === 'gather' && snapshot?.text) {
    return cleanAgentNarrative(snapshot.text);
  }
  return waitingMessageForSeat(seatId);
}

function gatherAnalystSeatIds() {
  const roster = runStarted() && state.runRosterSeatIds.length
    ? state.runRosterSeatIds
    : selectedSeatIds();
  const gatherRoster = roster.filter((seatId) => GATHER_ANALYST_SEATS.includes(seatId));
  if (gatherRoster.length) return gatherRoster;
  const fallback = selectedSeatIds().filter((seatId) => GATHER_ANALYST_SEATS.includes(seatId));
  return fallback.length ? fallback : selectedSeatIds().slice(0, 4);
}

function renderCommittee() {
  const panel = document.getElementById('analyst-perspectives');
  if (!panel) return;
  const seatIds = gatherAnalystSeatIds();
  const gatherActiveSeats = stageActiveSeatIds('gather');
  const runIsStarted = runStarted();
  const gatherLive = state.activeStages.has('gather');
  const gatherDone = state.completedStages.has('gather');

  panel.innerHTML = seatIds.map((seatId) => {
    const snapshot = state.roleSnapshots[seatId] || {};
    const leanClass = stanceBucket(snapshot.stance || 'neutral');
    const leanLabel = stanceDisplayLabel(snapshot.stance || 'neutral');
    const confidence = Number.isFinite(snapshot.confidence) ? `${snapshot.confidence.toFixed(0)}%` : '--';
    const stage = snapshot.stage ? formatStage(snapshot.stage) : 'No stage';
    const text = buildAgentPerspectiveText(seatId, snapshot);
    const activity = gatherLive
        ? (gatherActiveSeats.includes(seatId) ? 'active now (Information Gathering)' : 'waiting for information-gathering update')
      : gatherDone
        ? ''
        : runIsStarted
          ? 'queued for information gathering'
          : 'idle';
    return `
      <article class="agent-card">
        <div class="agent-head">
          <div class="agent-role">${escapeHtml(formatRole(seatId))}</div>
          <span class="lean ${leanClass}">${leanLabel}</span>
        </div>
        <div class="agent-meta">
          <span>${escapeHtml(stage)}</span>
          <span>${activity ? `${escapeHtml(activity)} · ` : ''}Confidence ${escapeHtml(confidence)}</span>
        </div>
        <div class="agent-text">${formatAgentTextHtml(text)}</div>
      </article>
    `;
  }).join('') || '<div class="section-subtitle">Research summaries will appear here once the desk starts gathering inputs.</div>';
}

function buildQuantAnalystText(snapshot) {
  if (state.phaseQuantNote) {
    return cleanAgentNarrative(state.phaseQuantNote);
  }
  const latestQuantArtifact = latestRoleObject(state.objects.artifact, 'quant_analyst', 'quantify');
  if (latestQuantArtifact?.label) {
    return cleanAgentNarrative(latestQuantArtifact.label);
  }
  if (snapshot?.stage === 'quantify' && snapshot?.text) {
    return cleanAgentNarrative(snapshot.text);
  }
  return waitingMessageForSeat('quant_analyst');
}

function renderQuantAnalystPanel() {
  const panel = document.getElementById('quant-analyst-perspective');
  if (!panel) return;
  const snapshot = state.roleSnapshots.quant_analyst || {};
  const leanClass = stanceBucket(snapshot.stance || 'neutral');
  const leanLabel = stanceDisplayLabel(snapshot.stance || 'neutral');
  const confidence = Number.isFinite(snapshot.confidence) ? `${snapshot.confidence.toFixed(0)}%` : '--';
  const stage = snapshot.stage ? formatStage(snapshot.stage) : 'No stage';
  const quantLive = state.activeStages.has('quantify');
  const quantDone = state.completedStages.has('quantify');
  const runLive = runStarted();
  const activity = quantLive
    ? 'active now (Quant Validation)'
    : quantDone
      ? 'phase 1 quant output locked for downstream phases'
      : runLive
        ? 'queued for quant validation'
        : 'idle';
  const text = buildQuantAnalystText(snapshot);
  panel.innerHTML = `
    <article class="agent-card">
      <div class="agent-head">
        <div class="agent-role">${escapeHtml(formatRole('quant_analyst'))}</div>
        <span class="lean ${leanClass}">${leanLabel}</span>
      </div>
      <div class="agent-meta">
        <span>${escapeHtml(stage)}</span>
        <span>${escapeHtml(activity)} · Confidence ${escapeHtml(confidence)}</span>
      </div>
      <div class="agent-text">${formatAgentTextHtml(text)}</div>
    </article>
  `;
}

function topDebateClaim(claims, stances) {
  const allowed = stances instanceof Set ? stances : new Set(stances || []);
  return claims
    .filter((claim) => allowed.has(stanceBucket(claim.stance || 'neutral')))
    .sort((a, b) => {
      const confidenceDelta = normalizeConfidence(b.confidence) - normalizeConfidence(a.confidence);
      if (confidenceDelta !== 0) return confidenceDelta;
      return timeValue(b.provenance?.emitted_at) - timeValue(a.provenance?.emitted_at);
    })[0] || null;
}

function synthesisClaimSnippet(claim) {
  if (!claim?.statement) return '';
  return cleanAgentNarrative(claim.statement);
}

function buildSynthesisNarrative() {
  const debateClaims = Object.values(state.objects.claim || {})
    .filter((claim) => claim?.provenance?.stage_id === 'debate' && claim?.statement);
  const metrics = Object.values(state.objects.metric || {});
  const longCount = debateClaims.filter((claim) => stanceBucket(claim.stance) === 'long').length;
  const shortCount = debateClaims.filter((claim) => stanceBucket(claim.stance) === 'short').length;
  const neutralCount = Math.max(0, debateClaims.length - longCount - shortCount);
  const topLong = topDebateClaim(debateClaims, new Set(['long']));
  const topShort = topDebateClaim(debateClaims, new Set(['short']));

  const synthDecision = Object.values(state.objects.decision || {})
    .filter((decision) => decision?.provenance?.stage_id === 'synthesize')
    .sort((a, b) => timeValue(b.provenance?.emitted_at) - timeValue(a.provenance?.emitted_at))[0] || null;
  const synthLinkedClaims = linkedDecisionClaims(synthDecision);
  const linkedClaims = synthLinkedClaims.combined;
  const linkedLong = synthLinkedClaims.supporting.length;
  const linkedShort = synthLinkedClaims.dissenting.length;

  const stageSummaryRaw = cleanAgentNarrative(state.stageMeta.synthesize?.outputSummary || '');
  const stageSummary = /desk recommendation package prepared for risk review/i.test(stageSummaryRaw) ? '' : stageSummaryRaw;
  const synthArtifactLabel = Object.values(state.objects.artifact || {})
    .filter((artifact) => artifact?.provenance?.stage_id === 'synthesize')
    .map((artifact) => cleanAgentNarrative(artifact.label || ''))
    .find((label) => label && !/^research manager summary$/i.test(label) && !/^research manager synthesis$/i.test(label));

  const parts = [];
  if (debateClaims.length || metrics.length) {
    const neutralDetail = neutralCount > 0 ? `, ${neutralCount} neutral` : '';
    parts.push(`Synthesis reviewed ${debateClaims.length} debate claims (${longCount} long, ${shortCount} short${neutralDetail}) and ${metrics.length} quant metrics.`);
  }
  if (topLong) {
    parts.push(`Lead long thesis: ${synthesisClaimSnippet(topLong)}.`);
  }
  if (topShort) {
    parts.push(`Primary counter-thesis: ${synthesisClaimSnippet(topShort)}.`);
  }
  if (linkedClaims.length) {
    parts.push(`Risk handoff anchors on ${linkedClaims.length} selected claim${linkedClaims.length === 1 ? '' : 's'} (${linkedLong} supportive, ${linkedShort} dissenting).`);
  }
  if (synthDecision?.outcome) {
    parts.push(`Pre-risk recommendation: ${formatOutcome(synthDecision.outcome)}.`);
  }
  if (stageSummary) {
    parts.push(stageSummary.endsWith('.') ? stageSummary : `${stageSummary}.`);
  }
  if (synthArtifactLabel) {
    parts.push(`Memo highlight: ${synthArtifactLabel}.`);
  }
  const merged = parts.join(' ').replace(/\s+/g, ' ').trim();
  if (merged) return merged;
  if (stageSummaryRaw) return stageSummaryRaw;
  return '';
}

function collectDebateInteractions() {
  const debateTurns = state.debateTurns.map((item) => ({
    stage: item.stage || 'debate',
    role: item.role,
    stance: stanceBucket(item.stance || 'neutral'),
    text: cleanAgentNarrative(item.statement || ''),
    roundIndex: Number(item.roundIndex || 0),
    turnIndex: Number(item.turnIndex || 0),
    replyToRole: item.replyToRole || '',
    emittedAt: item.emittedAt || '',
  }))
    .filter((item) => item.role && item.text)
    .sort((a, b) => {
      const timeDelta = timeValue(b.emittedAt) - timeValue(a.emittedAt);
      if (timeDelta !== 0) return timeDelta;
      if (b.roundIndex !== a.roundIndex) return b.roundIndex - a.roundIndex;
      return b.turnIndex - a.turnIndex;
    });

  const synthesizeEntries = [];
  const synthMeta = state.stageMeta.synthesize || {};
  const synthesisNarrative = buildSynthesisNarrative();
  if (synthesisNarrative) {
    synthesizeEntries.push({
      stage: 'synthesize',
      role: 'research_manager',
      stance: stanceBucket(state.roleSnapshots.research_manager?.stance || 'neutral'),
      text: synthesisNarrative,
      roundIndex: 0,
      turnIndex: 0,
      replyToRole: '',
      emittedAt: synthMeta.completedAt || synthMeta.startedAt || '',
    });
  }

  const merged = [...debateTurns, ...synthesizeEntries];
  return merged
    .filter((item) => item.role && item.text)
    .sort((a, b) => {
      const timeDelta = timeValue(b.emittedAt) - timeValue(a.emittedAt);
      if (timeDelta !== 0) return timeDelta;
      if (b.roundIndex !== a.roundIndex) return b.roundIndex - a.roundIndex;
      return (b.turnIndex || 0) - (a.turnIndex || 0);
    });
}

function renderAgentDebate() {
  const node = document.getElementById('agent-debate');
  if (!node) return;

  const debateOrSynthesisTouched = Boolean(
    state.stageMeta.debate?.startedAt
    || state.stageMeta.synthesize?.startedAt
    || state.completedStages.has('debate')
    || state.completedStages.has('synthesize')
    || state.debateTurns.length,
  );
  const showDebate = runStarted() && (state.completedStages.has('gather') || debateOrSynthesisTouched);
  if (!showDebate) {
    node.innerHTML = '<div class="section-subtitle">Debate highlights will appear here after research is complete.</div>';
    return;
  }

  const stream = collectDebateInteractions();
  if (!stream.length) {
    node.innerHTML = '<div class="section-subtitle">The desk has entered debate, but the strongest arguments are still forming.</div>';
    return;
  }

  const recent = stream.slice(0, 16);
  const isLiveDebateWindow = PHASE_DEBATE_STAGES.has(STAGES.find((stageId) => state.activeStages.has(stageId)) || '');
  const totalTurns = stream.length;

  node.innerHTML = recent.map((turn, index) => {
    const leanClass = stanceBucket(turn.stance || 'neutral');
    const leanLabel = stanceDisplayLabel(turn.stance || 'neutral');
    const liveBadge = isLiveDebateWindow && index === 0 ? '<span class="live-chip">Live</span>' : '';
    const responseTo = turn.replyToRole ? `Responding to ${formatRole(turn.replyToRole)}` : 'Opening position';
    const turnNumber = totalTurns - index;
    return `
      <article class="dialogue-exchange">
        <div class="exchange-meta">${liveBadge}Turn ${escapeHtml(String(turnNumber))} · ${escapeHtml(phaseLabelForStage(turn.stage))} · ${escapeHtml(formatTime(turn.emittedAt))}</div>
        <div class="speech-block">
          <div class="speech-head">
            <span class="speaker-pill">${escapeHtml(formatRole(turn.role))}</span>
            <span class="lean ${leanClass}">${escapeHtml(leanLabel)}</span>
          </div>
          <div class="speech-arrow">${escapeHtml(responseTo)}</div>
          <div>${escapeHtml(turn.text)}</div>
        </div>
      </article>
    `;
  }).join('');
}

function renderConsensus() {
  const pillsNode = document.getElementById('vote-trend-pills');
  if (!pillsNode) return;

  const votes = deriveVoteBreakdown();
  pillsNode.innerHTML = `
    <div class="vote-pill long">
      <div class="label">Long</div>
      <div class="count">${votes.longCount}</div>
    </div>
    <div class="vote-pill neutral">
      <div class="label">Neutral</div>
      <div class="count">${votes.neutralCount}</div>
    </div>
    <div class="vote-pill short">
      <div class="label">Short</div>
      <div class="count">${votes.shortCount}</div>
    </div>
  `;
}

function renderRiskGate() {
  const statusNode = document.getElementById('risk-status');
  const constraintsNode = document.getElementById('constraints');
  const status = state.riskGate.status || 'pending';
  const display = status === 'blocked' ? 'Blocked' : status === 'warning' ? 'Warning' : status === 'passed' ? 'Passed' : 'Pending';
  statusNode.textContent = `${display} · ${state.riskGate.note || ''}`;

  const constraints = Object.values(state.objects.constraint || {});
  if (!constraints.length) {
    constraintsNode.innerHTML = '<div class="section-subtitle">Risk constraints appear in risk review phase.</div>';
    return;
  }

  constraintsNode.innerHTML = constraints.map((item) => {
    return `
      <div class="constraint-item">
        <strong>${escapeHtml(item.label || item.constraint_id)}</strong>
        <div class="meta">${escapeHtml(item.constraint_type || 'constraint')} · ${escapeHtml(item.severity || 'n/a')}</div>
        <div>${escapeHtml(JSON.stringify(item.value))}</div>
      </div>
    `;
  }).join('');
}

function latestTradeTicket() {
  return Object.values(state.objects.trade_ticket || {})
    .slice()
    .sort((a, b) => timeValue(b.provenance?.emitted_at) - timeValue(a.provenance?.emitted_at))[0] || null;
}

function latestArtifactForStage(stageId, artifactType = '') {
  return Object.values(state.objects.artifact || {})
    .filter((item) => item?.provenance?.stage_id === stageId && (!artifactType || item?.artifact_type === artifactType))
    .slice()
    .sort((a, b) => timeValue(b.provenance?.emitted_at) - timeValue(a.provenance?.emitted_at))[0] || null;
}

function renderExecutionAndMonitoring() {
  const executionNode = document.getElementById('execution-phase-note');
  const executionMetaNode = document.getElementById('execution-phase-meta');
  const monitoringNode = document.getElementById('monitoring-phase-note');
  const monitoringMetaNode = document.getElementById('monitoring-phase-meta');
  if (!executionNode || !executionMetaNode || !monitoringNode || !monitoringMetaNode) return;

  const executionMeta = state.stageMeta.trade_finalize || {};
  const monitoringMeta = state.stageMeta.monitor || {};
  const executionStatus = state.activeStages.has('trade_finalize')
    ? `Running · ${formatTime(executionMeta.startedAt)}`
    : executionMeta.completedAt
      ? `Completed · ${formatTime(executionMeta.completedAt)}`
      : executionMeta.startedAt
        ? `Started · ${formatTime(executionMeta.startedAt)}`
        : 'Pending';
  const monitoringStatus = state.activeStages.has('monitor')
    ? `Running · ${formatTime(monitoringMeta.startedAt)}`
    : monitoringMeta.completedAt
      ? `Completed · ${formatTime(monitoringMeta.completedAt)}`
      : monitoringMeta.startedAt
        ? `Started · ${formatTime(monitoringMeta.startedAt)}`
        : 'Pending';
  const executionConfidenceValue = Number.isFinite(state.phaseExecutionConfidence)
    ? state.phaseExecutionConfidence
    : (Number.isFinite(state.roleSnapshots.trader?.confidence) && state.roleSnapshots.trader?.stage === 'trade_finalize'
      ? normalizeConfidence(state.roleSnapshots.trader.confidence)
      : null);
  const monitoringConfidenceValue = Number.isFinite(state.phaseMonitoringConfidence)
    ? state.phaseMonitoringConfidence
    : (Number.isFinite(state.roleSnapshots.trader?.confidence) && state.roleSnapshots.trader?.stage === 'monitor'
      ? normalizeConfidence(state.roleSnapshots.trader.confidence)
      : null);
  const executionConfidence = Number.isFinite(executionConfidenceValue) ? `${executionConfidenceValue.toFixed(0)}%` : '--';
  const monitoringConfidence = Number.isFinite(monitoringConfidenceValue) ? `${monitoringConfidenceValue.toFixed(0)}%` : '--';
  executionMetaNode.textContent = `${executionStatus} · Confidence ${executionConfidence}`;
  monitoringMetaNode.textContent = `${monitoringStatus} · Confidence ${monitoringConfidence}`;

  const ticket = latestTradeTicket();
  const constraints = Object.values(state.objects.constraint || {});
  const blockingConstraints = constraints
    .filter((item) => String(item?.severity || '').toLowerCase() === 'blocking')
    .map((item) => item.label || item.constraint_id || 'Blocking constraint');
  const warningConstraints = constraints
    .filter((item) => String(item?.severity || '').toLowerCase() === 'warning')
    .map((item) => item.label || item.constraint_id || 'Warning constraint');
  const entryConditions = ticketEntryConditions(ticket);
  const exitConditions = ticketExitConditions(ticket);

  const executionFallback = ticket
    ? `Ticket: ${ticketDisplayInstrument(ticket)} · ${ticketLegSummary(ticket)} · Entry: ${entryConditions.join(', ') || 'n/a'} · Exit: ${exitConditions.join(', ') || 'n/a'}`
    : (latestArtifactForStage('trade_finalize', 'trade_ticket')?.label || '');
  const monitoringFallback = latestArtifactForStage('monitor', 'monitoring_plan')?.label || '';

  const executionText = cleanAgentNarrative(state.phaseExecutionNote || executionFallback);
  const monitoringText = cleanAgentNarrative(state.phaseMonitoringNote || monitoringFallback);
  const executionAction = executionText || 'Awaiting trade-finalize output. Ticket conditions will appear when the trader publishes the package.';
  const monitoringAction = monitoringText || 'Awaiting monitoring-plan output. Triggers and review cadence will appear in phase 5.';

  const executionContext = ticket
    ? `${ticketDisplayInstrument(ticket) || 'instrument'} · ${ticketLegSummary(ticket)} · ${ticket.time_horizon || 'event_tactical'}`
    : 'No ticket context yet.';
  const executionCheckpoint = executionMeta.completedAt
    ? `Execution finalized at ${formatTime(executionMeta.completedAt)}.`
    : state.activeStages.has('trade_finalize')
      ? 'Execution stage is live, watch for ticket updates.'
      : 'Execution stage is queued behind PM decision.';

  const monitoringContext = ticket
    ? `${ticketDisplayInstrument(ticket) || 'instrument'} monitoring after ${ticketPrimarySide(ticket)} primary-leg setup.`
    : 'Monitoring context unlocks after trade finalize.';
  const monitoringCheckpoint = monitoringMeta.completedAt
    ? `Monitoring plan completed at ${formatTime(monitoringMeta.completedAt)}.`
    : state.activeStages.has('monitor')
      ? 'Monitoring stage is live, watching trigger updates.'
      : 'Monitoring stage is queued.';

  executionNode.innerHTML = `
    <div class="phase-note-block">
      <div class="phase-note-k">Action Now</div>
      <div class="phase-note-v">${escapeHtml(executionAction)}</div>
    </div>
    <div class="phase-note-columns">
      <div class="phase-note-block">
        <div class="phase-note-k">Ticket Context</div>
        <div class="phase-note-v">${escapeHtml(executionContext)}</div>
      </div>
      <div class="phase-note-block">
        <div class="phase-note-k">Checkpoint</div>
        <div class="phase-note-v">${escapeHtml(executionCheckpoint)}</div>
      </div>
      <div class="phase-note-block">
        <div class="phase-note-k">Execution Confidence</div>
        <div class="phase-note-v">${escapeHtml(executionConfidence)}</div>
      </div>
    </div>
    <div class="phase-note-columns">
      <div class="phase-note-block">
        <div class="phase-note-k">Entry Conditions</div>
        ${renderSimpleList(entryConditions.map(toSentenceCase), 'No entry conditions yet.')}
      </div>
      <div class="phase-note-block">
        <div class="phase-note-k">Exit Conditions</div>
        ${renderSimpleList(exitConditions.map(toSentenceCase), 'No exit conditions yet.')}
      </div>
    </div>
  `;

  monitoringNode.innerHTML = `
    <div class="phase-note-block">
      <div class="phase-note-k">Action Now</div>
      <div class="phase-note-v">${escapeHtml(monitoringAction)}</div>
    </div>
    <div class="phase-note-columns">
      <div class="phase-note-block">
        <div class="phase-note-k">Monitoring Context</div>
        <div class="phase-note-v">${escapeHtml(monitoringContext)}</div>
      </div>
      <div class="phase-note-block">
        <div class="phase-note-k">Checkpoint</div>
        <div class="phase-note-v">${escapeHtml(monitoringCheckpoint)}</div>
      </div>
      <div class="phase-note-block">
        <div class="phase-note-k">Monitoring Confidence</div>
        <div class="phase-note-v">${escapeHtml(monitoringConfidence)}</div>
      </div>
    </div>
    <div class="phase-note-columns">
      <div class="phase-note-block">
        <div class="phase-note-k">Blocking Risk Watch</div>
        ${renderSimpleList(blockingConstraints, 'No blocking constraints active.')}
      </div>
      <div class="phase-note-block">
        <div class="phase-note-k">Warning Risk Watch</div>
        ${renderSimpleList(warningConstraints, 'No warning constraints active.')}
      </div>
    </div>
  `;
}

function renderEvidence() {
  const { support, risk } = collectEvidenceLists();
  document.getElementById('evidence-support').innerHTML = support.length
    ? support.map((item) => `<li>${escapeHtml(item.title)} (${normalizeConfidence(item.confidence).toFixed(0)}%)</li>`).join('')
    : '<li>No supporting evidence yet.</li>';
  document.getElementById('evidence-risk').innerHTML = risk.length
    ? risk.map((item) => `<li>${escapeHtml(item.title)} (${normalizeConfidence(item.confidence).toFixed(0)}%)</li>`).join('')
    : '<li>No risk evidence yet.</li>';
}

function renderQuantMetrics() {
  const list = document.getElementById('quant-metrics');
  const metrics = Object.values(state.objects.metric || {});
  list.innerHTML = metrics.length
    ? metrics.map((item) => `<li>${escapeHtml(item.name)}: ${escapeHtml(String(item.value))} ${escapeHtml(item.unit || '')}</li>`).join('')
    : '<li>No quant metrics yet.</li>';
}

function renderHandoffs() {
  const node = document.getElementById('handoffs');
  const rows = state.handoffs.slice(0, 24);
  node.innerHTML = rows.length
    ? rows.map((item) => `
      <div class="handoff-item">
        <div><strong>${escapeHtml(formatRole(item.fromRole))}</strong> -> <strong>${escapeHtml(formatRole(item.toRole || 'desk'))}</strong></div>
        <div>${escapeHtml(item.text || '')}</div>
        <div class="meta">${escapeHtml(formatStage(item.stage))} · ${escapeHtml(formatTime(item.emittedAt))}</div>
      </div>
    `).join('')
    : '<div class="section-subtitle">Agent handoffs will appear here once the run begins producing artifacts.</div>';
}

function renderDecisionPacket() {
  deriveFinalPackage();
  const container = document.getElementById('decision-package');
  const decision = state.finalPackage.decision;
  const ticket = state.finalPackage.ticket;

  if (!decision) {
    container.innerHTML = '<div class="section-subtitle">Final decision package appears after PM review and trade finalize stages.</div>';
    return;
  }

  const decisionClaims = linkedDecisionClaims(decision);
  const supportingTitles = decisionClaims.supporting
    .map((claim) => claim.statement)
    .filter(Boolean)
    .slice(0, 3);
  const dissentingTitles = decisionClaims.dissenting
    .map((claim) => claim.statement)
    .filter(Boolean)
    .slice(0, 3);

  const riskItems = (decision.linked_constraint_ids || [])
    .map((constraintId) => state.objects.constraint[constraintId])
    .filter(Boolean)
    .map((item) => item.label || item.constraint_id)
    .slice(0, 3);

  const outcomeLabel = formatOutcome(decision.outcome);
  const meaning = decisionMeaning(decision);
  const requiredChanges = deriveRequiredChanges(decision, ticket);
  const consensusScore = deriveConsensusScore();
  const direction = deriveDecisionDirection(decision, ticket, consensusScore);
  const conviction = deriveConvictionLabel(consensusScore, decision);
  const pmConfidenceValue = Number.isFinite(state.phasePmConfidence)
    ? state.phasePmConfidence
    : (Number.isFinite(state.roleSnapshots.portfolio_manager?.confidence) ? normalizeConfidence(state.roleSnapshots.portfolio_manager.confidence) : null);
  const pmConfidence = Number.isFinite(pmConfidenceValue) ? `${pmConfidenceValue.toFixed(0)}%` : '--';
  const weightPct = bpsToPercentString(decision.position_size_bps || ticketPrimarySizeBps(ticket));
  const targetStop = parseTargetStop(ticket);

  container.innerHTML = `
    <div class="decision-head">
      <div class="decision-title">${escapeHtml(outcomeLabel)}</div>
      <span class="decision-pill">${escapeHtml(decision.decision_type || 'decision')}</span>
    </div>
    <div class="section-subtitle">${escapeHtml(meaning)}</div>
    <div class="decision-grid">
      <div class="decision-metric"><div class="k">Direction</div><div class="v">${escapeHtml(direction)}</div></div>
      <div class="decision-metric"><div class="k">Conviction</div><div class="v">${escapeHtml(conviction)}</div></div>
      <div class="decision-metric"><div class="k">PM Confidence</div><div class="v">${escapeHtml(pmConfidence)}</div></div>
      <div class="decision-metric"><div class="k">Suggested Weight</div><div class="v">${escapeHtml(weightPct)}</div></div>
      <div class="decision-metric"><div class="k">Target (Optional)</div><div class="v">${escapeHtml(targetStop.target)}</div></div>
      <div class="decision-metric"><div class="k">Stop (Optional)</div><div class="v">${escapeHtml(targetStop.stop)}</div></div>
      <div class="decision-metric"><div class="k">Instrument / Horizon</div><div class="v">${escapeHtml(`${ticketDisplayInstrument(ticket) || 'N/A'} · ${ticket?.time_horizon || 'event_tactical'}`)}</div></div>
    </div>
    <div class="columns-2">
      <div class="ticket-box">
        <div class="section-title" style="margin-bottom:6px;">Top Supporting Claims</div>
        <ul>${supportingTitles.length ? supportingTitles.map((item) => `<li>${escapeHtml(item)}</li>`).join('') : '<li>No supporting claim linkage available.</li>'}</ul>
      </div>
      <div class="ticket-box">
        <div class="section-title" style="margin-bottom:6px;">Top Counter Claims</div>
        <ul>${dissentingTitles.length ? dissentingTitles.map((item) => `<li>${escapeHtml(item)}</li>`).join('') : '<li>No dissenting claim linkage available.</li>'}</ul>
      </div>
    </div>
    <div class="ticket-box">
      <div class="section-title" style="margin-bottom:6px;">Top Risk Constraints</div>
      <ul>${riskItems.length ? riskItems.map((item) => `<li>${escapeHtml(item)}</li>`).join('') : '<li>No constraint linkage available.</li>'}</ul>
    </div>
    <div class="ticket-box">
      <div class="section-title" style="margin-bottom:6px;">What Changed</div>
      <ul>${requiredChanges.length ? requiredChanges.map((item) => `<li>${escapeHtml(item)}</li>`).join('') : '<li>No mandatory modifications attached.</li>'}</ul>
    </div>
    <div class="ticket-box">
      <div class="section-title" style="margin-bottom:6px;">Next Action</div>
      <div class="action-grid">
        <button type="button" class="action-btn" id="action-run-next">Run Next Ticker</button>
        <button type="button" class="action-btn" id="action-export-packet">Export Decision Packet</button>
        <button type="button" class="action-btn" id="action-start-new">Start New Simulation</button>
      </div>
    </div>
  `;
  const runNextButton = document.getElementById('action-run-next');
  const exportButton = document.getElementById('action-export-packet');
  const startNewButton = document.getElementById('action-start-new');
  if (runNextButton) runNextButton.addEventListener('click', runNextTickerAction);
  if (exportButton) exportButton.addEventListener('click', exportDecisionPacketAction);
  if (startNewButton) startNewButton.addEventListener('click', startNewSimulationAction);
}

function renderConclusion() {
  const node = document.getElementById('outcome-banner');
  const decision = state.finalPackage.decision;
  const ticket = state.finalPackage.ticket;
  const runComplete = state.events.some((item) => item.eventType === 'run.completed');
  const votes = deriveVoteBreakdown();

  if (!decision && !runComplete) {
    node.className = 'outcome-banner';
    node.innerHTML = `
      <div class="outcome-title">Run in progress</div>
      <div class="outcome-meta">Committee outputs are updating in real time. Final package appears after PM review.</div>
      <div class="outcome-meta">Votes: ${votes.longCount} long · ${votes.neutralCount} neutral · ${votes.shortCount} short</div>
    `;
    return;
  }

  if (!decision && runComplete) {
    node.className = 'outcome-banner warning';
    node.innerHTML = `
      <div class="outcome-title">Run complete with no final PM decision</div>
      <div class="outcome-meta">Escalate to Research Manager to close missing handoff artifacts.</div>
    `;
    return;
  }

  const outcome = formatOutcome(decision.outcome);
  const bannerClass = decision.outcome === 'rejected'
    ? 'outcome-banner blocked'
    : decision.outcome === 'approved_with_changes'
      ? 'outcome-banner warning'
      : 'outcome-banner good';
  const ticker = state.runTicker || ticketDisplayInstrument(ticket) || 'N/A';
  const size = decision.position_size_bps || (ticket?.ticket_type === 'pair_trade' ? ticketGrossExposureBps(ticket) : ticketPrimarySizeBps(ticket)) || 'N/A';
  const sizeLabel = ticket?.ticket_type === 'pair_trade' ? `${size} gross bps` : `${size} bps`;
  const horizon = ticket?.time_horizon || 'event_tactical';
  const confidenceDetail = businessConfidenceDetail();
  const confidenceText = Number.isFinite(confidenceDetail.percent)
    ? `${normalizeConfidence(confidenceDetail.percent).toFixed(0)}% (${confidenceDetail.source})`
    : confidenceDetail.summary;

  node.className = bannerClass;
  node.innerHTML = `
    <div class="outcome-title">Desk Verdict: ${escapeHtml(outcome)}</div>
    <div class="outcome-meta">${escapeHtml(String(ticker))} · Size ${escapeHtml(String(sizeLabel))} · Horizon ${escapeHtml(horizon)}</div>
    <div class="outcome-meta">Committee vote: ${votes.longCount} long · ${votes.neutralCount} neutral · ${votes.shortCount} short</div>
    <div class="outcome-meta">Desk confidence: ${escapeHtml(confidenceText)}</div>
    <div>${escapeHtml(decisionMeaning(decision))}</div>
  `;
}

function businessStatusLine(progress = computePhaseProgress()) {
  const livePhase = MACRO_PHASES[deriveLivePhaseIndex(progress)];
  if (!progress.runStarted) {
    return 'Choose a scenario and start a run when you want a fresh committee view.';
  }
  if (progress.runComplete) {
    return 'Committee review complete. The final decision and supporting evidence are ready.';
  }
  if (state.awaitingNextStage) {
    return `${businessPhaseLabel(livePhase)} is ready for review. Continue the run from Demo Controls when you want more output.`;
  }
  return `${businessPhaseLabel(livePhase)} is in progress. Business summaries update here as the desk works.`;
}

function businessRiskSummary() {
  const status = state.riskGate.status || 'pending';
  if (status === 'blocked') {
    return {
      value: 'Blocked',
      meta: state.riskGate.note || 'Risk constraints currently block the trade.',
    };
  }
  if (status === 'warning') {
    return {
      value: 'Warning',
      meta: state.riskGate.note || 'Risk review requires changes before clean approval.',
    };
  }
  if (status === 'passed') {
    return {
      value: 'Passed',
      meta: state.riskGate.note || 'Risk constraints are inside approved guardrails.',
    };
  }
  return {
    value: 'Pending',
    meta: 'Risk review has not completed yet.',
  };
}

function businessRecommendationSummary() {
  deriveFinalPackage();
  const decision = state.finalPackage.decision;
  const ticket = state.finalPackage.ticket;
  const score = deriveConsensusScore();
  if (decision) {
    return {
      value: formatOutcome(decision.outcome),
      meta: decisionMeaning(decision),
    };
  }
  if (!runStarted()) {
    return {
      value: 'Decision Pending',
      meta: 'No committee output yet.',
    };
  }
  const direction = deriveDecisionDirection(decision, ticket, score);
  return {
    value: direction === 'NEUTRAL' ? scoreToBias(score) : `${direction} Lean`,
    meta: `Committee score ${score.toFixed(2)} from ${Object.keys(state.roleLean).length} contributing seats.`,
  };
}

function businessConfidenceDetail() {
  const seatCount = Object.keys(state.roleLean).length;
  if (!runStarted()) {
    return {
      percent: null,
      source: 'Awaiting run',
      summary: 'Conviction will update once enough analyst output is on the board.',
    };
  }
  if (Number.isFinite(state.phasePmConfidence)) {
    return {
      percent: normalizeConfidence(state.phasePmConfidence),
      source: 'PM decision',
      summary: 'Final decision confidence from portfolio manager handoff.',
    };
  }
  if (Number.isFinite(state.phaseMonitoringConfidence)) {
    return {
      percent: normalizeConfidence(state.phaseMonitoringConfidence),
      source: 'Monitoring',
      summary: 'Trader monitoring confidence from latest phase-5 guidance.',
    };
  }
  if (Number.isFinite(state.phaseExecutionConfidence)) {
    return {
      percent: normalizeConfidence(state.phaseExecutionConfidence),
      source: 'Execution',
      summary: 'Trader execution confidence from latest phase-4 guidance.',
    };
  }
  if (Number.isFinite(state.roleSnapshots.quant_analyst?.confidence)) {
    return {
      percent: normalizeConfidence(state.roleSnapshots.quant_analyst.confidence),
      source: 'Quant analyst',
      summary: 'Quant analyst confidence from phase-1 validation output.',
    };
  }
  if (!seatCount) {
    return {
      percent: null,
      source: 'Awaiting analyst output',
      summary: 'Confidence will update once analyst output is available.',
    };
  }
  const score = Math.abs(deriveConsensusScore());
  const derived = clamp(Math.round(45 + score * 40), 45, 85);
  return {
    percent: derived,
    source: 'Committee consensus',
    summary: `${seatCount} seat${seatCount === 1 ? '' : 's'} contributing to current view.`,
  };
}

function businessConfidenceSummary() {
  const detail = businessConfidenceDetail();
  if (!Number.isFinite(detail.percent)) {
    return {
      value: 'Low',
      meta: detail.summary,
    };
  }
  const percent = normalizeConfidence(detail.percent);
  const value = `${percent.toFixed(0)}%`;
  const band = percent >= 75 ? 'High' : percent >= 60 ? 'Moderate' : 'Low';
  return {
    value,
    meta: `${band} confidence · ${detail.source}. ${detail.summary}`,
  };
}

function renderExecutiveStrip() {
  const progress = computePhaseProgress();
  const livePhaseIndex = deriveLivePhaseIndex(progress);
  const livePhase = MACRO_PHASES[livePhaseIndex];
  const statusLineNode = document.getElementById('business-status-line');
  const recommendationNode = document.getElementById('exec-consensus');
  const recommendationMetaNode = document.getElementById('exec-consensus-meta');
  const riskNode = document.getElementById('business-risk');
  const riskMetaNode = document.getElementById('business-risk-meta');
  const confidenceNode = document.getElementById('business-confidence');
  const confidenceMetaNode = document.getElementById('business-confidence-meta');
  const phaseNode = document.getElementById('phase-current');
  const runtimeNode = document.getElementById('phase-runtime');
  const runIdNode = document.getElementById('phase-run-id');
  const recommendation = businessRecommendationSummary();
  const risk = businessRiskSummary();
  const confidence = businessConfidenceSummary();

  if (statusLineNode) statusLineNode.textContent = businessStatusLine(progress);
  if (recommendationNode) recommendationNode.textContent = recommendation.value;
  if (recommendationMetaNode) recommendationMetaNode.textContent = recommendation.meta;
  if (riskNode) riskNode.textContent = risk.value;
  if (riskMetaNode) riskMetaNode.textContent = risk.meta;
  if (confidenceNode) confidenceNode.textContent = confidence.value;
  if (confidenceMetaNode) confidenceMetaNode.textContent = confidence.meta;
  if (phaseNode) {
    if (!progress.runStarted) {
      phaseNode.textContent = 'Waiting to run';
    } else if (progress.runComplete) {
      phaseNode.textContent = 'Run complete';
    } else if (progress.activePhaseIndex >= 0) {
      phaseNode.textContent = `${businessPhaseLabel(livePhase)} · Running`;
    } else if (state.awaitingNextStage) {
      phaseNode.textContent = `${businessPhaseLabel(livePhase)} · Ready to start`;
    } else {
      phaseNode.textContent = `${businessPhaseLabel(livePhase)} · Transitioning`;
    }
  }
  if (runtimeNode) {
    const runtime = state.runRuntime || document.getElementById('runtime')?.value || 'N/A';
    runtimeNode.textContent = String(runtime).toUpperCase();
  }
  if (runIdNode) {
    runIdNode.textContent = state.currentRunId || state.liveRunId || 'Not started';
  }

  const llmStatus = document.getElementById('llm-status');
  if (llmStatus) llmStatus.textContent = llmStatusLine();
}

function renderTranscript() {
  const node = document.getElementById('events');
  node.innerHTML = state.events.map((item) => {
    return `
      <div class="event">
        <strong>${escapeHtml(item.text)}</strong>
        <div class="meta">${escapeHtml(formatStage(item.stage))} · ${escapeHtml(formatRole(item.producer))} · ${escapeHtml(formatTime(item.emittedAt))}</div>
      </div>
    `;
  }).join('') || '<div class="section-subtitle">Run events appear here.</div>';
}

function refreshPanels() {
  syncSeatEditorState();
  renderScenarioBrief();
  renderRunRosterSummary();
  hydrateRoleSnapshotsFromObjects();
  renderStockPriceChart();
  renderFlow();
  renderAgentDebate();
  renderCommittee();
  renderQuantAnalystPanel();
  renderHandoffs();
  renderConsensus();
  renderRiskGate();
  renderExecutionAndMonitoring();
  renderEvidence();
  renderQuantMetrics();
  renderExposurePreview();
  renderDecisionPacket();
  renderConclusion();
  renderExecutiveStrip();
  renderTranscript();
  updatePanelVisibility();
  applyPhaseWorkspaceFilter();
  updateStageControls();
}

function llmStatusLine() {
  if (!state.llm || typeof state.llm !== 'object') {
    return 'LLM diagnostics unavailable.';
  }
  const live = Number.isFinite(state.llm.live_count) ? state.llm.live_count : 0;
  const fallback = Number.isFinite(state.llm.fallback_count) ? state.llm.fallback_count : 0;
  const mode = state.llm.last_mode || 'unknown';
  let line = `LLM mode=${mode} live=${live} fallback=${fallback}`;
  if (state.llm.auth_mode) line += ` auth=${state.llm.auth_mode}`;
  if (state.llm.last_error) line += ` error=${shortError(state.llm.last_error)}`;
  return line;
}

function mergeObjectBuckets(input) {
  if (!input || typeof input !== 'object') return false;
  let changed = false;
  for (const [type, values] of Object.entries(input)) {
    if (!values || typeof values !== 'object') continue;
    if (!(type in state.objects)) {
      state.objects[type] = {};
    }
    const current = state.objects[type];
    const currentCount = Object.keys(current).length;
    const nextCount = Object.keys(values).length;
    if (currentCount !== nextCount) {
      changed = true;
    }
    state.objects[type] = values;
  }
  return changed;
}

function renderSeats(scenario) {
  const plan = scenarioSeatPlan(scenario);
  state.scenarioPlan = plan;
  const suppressed = new Set(plan.suppressedSeatIds);
  const preferred = new Set(plan.preferredOptionalSeatIds);

  document.getElementById('required-seats').innerHTML = plan.requiredSeatIds.map((seatId) => {
    return `<div class="seat-chip"><span>${escapeHtml(formatRole(seatId))}</span><span>Required</span></div>`;
  }).join('');

  document.getElementById('optional-seats').innerHTML = plan.optionalSeatIds.map((seatId) => {
    if (suppressed.has(seatId)) {
      return `
        <label class="seat-chip seat-chip-disabled">
          <span>${escapeHtml(formatRole(seatId))}</span>
          <span>Suppressed</span>
        </label>
      `;
    }
    const checked = preferred.has(seatId) ? 'checked' : '';
    return `
      <label class="seat-chip">
        <span>${escapeHtml(formatRole(seatId))}</span>
        <input type="checkbox" data-seat-id="${seatId}" ${checked} />
      </label>
    `;
  }).join('');

  const scenarioTicker = String(
    scenario.instrument
    || (Array.isArray(scenario.instrument_universe) && scenario.instrument_universe.length ? scenario.instrument_universe[0] : '')
    || '',
  ).trim().toUpperCase();
  const tickerInput = document.getElementById('ticker');
  if (scenarioTicker) tickerInput.value = scenarioTicker;
  state.lastScenarioInstrument = scenarioTicker;
  const defaultBreakingMode = scenarioForcesBreakingNews(scenario) ? 'auto_after_gather' : 'off';
  applyBreakingNewsControlForScenario(scenario, defaultBreakingMode);
  syncSeatEditorState();
  renderScenarioBrief(scenario);
  renderRunRosterSummary();
  void loadStockPriceChart({ force: true });
}

function ingestSummary(payload, options = {}) {
  if (!payload || typeof payload !== 'object') return;

  const summary = payload.summary && typeof payload.summary === 'object' ? payload.summary : payload;
  const seedStageState = Boolean(options.seedStageState);
  const includeObjects = options.includeObjects !== false;
  if (summary.llm && typeof summary.llm === 'object') {
    state.llm = summary.llm;
  }
  if (summary.runtime) state.runRuntime = summary.runtime;
  if (summary.ticker) state.runTicker = summary.ticker;
  if (summary.run_id) state.currentRunId = summary.run_id;
  if (Array.isArray(summary.active_seat_ids)) {
    state.runRosterSeatIds = normalizeSeatIds(summary.active_seat_ids);
  }
  if (Number.isFinite(Number(summary.debate_depth))) {
    document.getElementById('debate-depth').value = String(summary.debate_depth);
  }

  let objectBuckets = null;
  if (payload.objects && typeof payload.objects === 'object') {
    objectBuckets = payload.objects.objects && typeof payload.objects.objects === 'object'
      ? payload.objects.objects
      : payload.objects;
  }
  if (includeObjects) {
    mergeObjectBuckets(objectBuckets || {});
  }

  const stageSequence = summary.stage_sequence || [];
  if (seedStageState && Array.isArray(stageSequence)) {
    stageSequence.forEach((stage) => {
      if (stage !== 'completed') {
        state.completedStages.add(stage);
      }
    });
  }
}

function queueEvents(events) {
  let queued = 0;
  for (const event of events) {
    if (!event || !event.event_id || state.seenEventIds.has(event.event_id)) continue;
    state.seenEventIds.add(event.event_id);
    state.queuedEvents.push(event);
    queued += 1;
  }
  return queued;
}

async function drainQueuedEvents() {
  let applied = 0;
  while (state.queuedEvents.length > 0) {
    if (state.awaitingNextStage) break;
    const event = state.queuedEvents.shift();
    processEvent(event);
    applied += 1;
    if (event.event_type === 'stage.completed' && isPhaseBoundaryStage(event.stage_id || '')) {
      state.awaitingNextStage = true;
      state.pausedAfterStage = event.stage_id || '';
      const nextStage = nextStageAfter(state.pausedAfterStage);
      setStatus(
        nextStage
          ? `Paused after ${formatStage(state.pausedAfterStage)}. Continue from Demo Controls when you want ${phaseLabelForStage(nextStage)}.`
          : 'Paused at end of staged flow. All outputs are available.',
      );
      break;
    }
  }
  if (applied > 0) {
    state.lastEventCount += applied;
    refreshPanels();
  } else {
    updateStageControls();
  }

  if (
    state.pendingFinalizeRunId &&
    !state.awaitingNextStage &&
    state.queuedEvents.length === 0 &&
    state.events.some((item) => item.eventType === 'run.completed') &&
    state.finalizingRunId !== state.pendingFinalizeRunId
  ) {
    const runId = state.pendingFinalizeRunId;
    state.pendingFinalizeRunId = '';
    state.finalizingRunId = runId;
    await finalizeLiveRun(runId);
    state.finalizingRunId = '';
  }
  return applied;
}

async function fetchAndApplyLiveEvents(runId) {
  const response = await fetch(`/var/runs/${runId}/event-log.jsonl?ts=${Date.now()}`);
  if (!response.ok) {
    return 0;
  }
  const raw = await response.text();
  const lines = raw.split('\n').filter((line) => line.trim());
  const events = [];
  for (const line of lines) {
    let event = null;
    try {
      event = JSON.parse(line);
    } catch {
      continue;
    }
    if (event) events.push(event);
  }
  const queued = queueEvents(events);
  if (queued > 0) {
    await drainQueuedEvents();
  }
  return queued;
}

async function fetchRunSummary(runId) {
  const response = await fetch(`/api/runs?run_id=${encodeURIComponent(runId)}`);
  if (!response.ok) {
    return null;
  }
  return response.json();
}

async function finalizeLiveRun(runId) {
  await fetchAndApplyLiveEvents(runId);
  const payload = await fetchRunSummary(runId);
  if (payload) {
    ingestSummary(payload, { seedStageState: true, includeObjects: true });
    refreshPanels();
    await loadRecentRuns();
    await loadAudit();
    setStatus(`Completed ${state.runRuntime || 'run'} ${runId} (${state.lastEventCount} events).`);
  } else {
    setStatus(`Run ${runId} completed, but summary is not yet available.`, 'error');
  }
}

function beginLivePolling(runId) {
  state.liveRunId = runId;
  stopLivePolling();
  void fetchAndApplyLiveEvents(runId);
  let tick = 0;
  state.livePollHandle = setInterval(async () => {
    if (!state.liveRunId || state.liveRunId !== runId) {
      stopLivePolling();
      return;
    }
    tick += 1;
    try {
      await fetchAndApplyLiveEvents(runId);
      if (tick % 6 === 0) {
        const statusResponse = await fetch(`/api/run/status?run_id=${encodeURIComponent(runId)}`);
        if (!statusResponse.ok) return;
        const statusPayload = await statusResponse.json();
        if (statusPayload.status === 'failed') {
          stopLivePolling();
          state.liveRunId = '';
          state.activeBackendRun = false;
          setStatus(`Run failed: ${statusPayload.error || 'unknown error'}`, 'error');
          await loadAudit();
          return;
        }
        if (statusPayload.status === 'cancelled') {
          stopLivePolling();
          state.liveRunId = '';
          state.activeBackendRun = false;
          setStatus(`Run ${runId} cancelled.`);
          await loadAudit();
          return;
        }
        if (statusPayload.status === 'paused') {
          return;
        }
        if (statusPayload.status === 'completed') {
          state.pendingFinalizeRunId = runId;
          await fetchAndApplyLiveEvents(runId);
          stopLivePolling();
          state.liveRunId = '';
          state.activeBackendRun = false;
          if (state.awaitingNextStage) {
            const nextStage = nextStageAfter(state.pausedAfterStage);
            const phaseLabel = nextStage ? phaseLabelForStage(nextStage) : 'the next phase';
            setStatus(`Run ${runId} finished backend execution. Continue from Demo Controls to reveal ${phaseLabel}.`);
          } else {
            await drainQueuedEvents();
          }
          return;
        }
      }
    } catch (error) {
      setStatus(`Live update warning: ${shortError(error?.message || error)}`, 'error');
    }
  }, 120);
}

async function startNextStage() {
  if (!state.awaitingNextStage) {
    return;
  }
  const paused = state.pausedAfterStage || 'stage';
  if (state.currentRunId) {
    const continueResponse = await fetch(`/api/run/continue?run_id=${encodeURIComponent(state.currentRunId)}`);
    if (continueResponse.ok) {
      state.awaitingNextStage = false;
      state.pausedAfterStage = '';
      setStatus(`Continuing after ${formatStage(paused)}...`);
      updateStageControls();
      return;
    }
  }
  state.awaitingNextStage = false;
  state.pausedAfterStage = '';
  setStatus(`Continuing after ${formatStage(paused)}...`);
  updateStageControls();
  await drainQueuedEvents();
}

async function runCommittee() {
  try {
    const runtime = document.getElementById('runtime').value;
    const scenarioId = document.getElementById('scenario').value;
    const scenario = state.scenarios.find((item) => item.scenario_id === scenarioId);
    if (!scenario) {
      setStatus('Select a scenario first.', 'error');
      return;
    }

    const seatIds = selectedSeatIds();
    const ticker = parseTickerRequest();
    const breakingNewsMode = applyBreakingNewsControlForScenario(scenario, document.getElementById('breaking-news').value);
    const debateDepth = Number(document.getElementById('debate-depth').value || '1');

    resetState();
    state.runRosterSeatIds = normalizeSeatIds(seatIds);
    void loadStockPriceChart({ force: true });
    setStatus(
      `Starting ${runtime} run for ${ticker} with ${seatIds.length} seats, debate depth ${debateDepth}, breaking-news ${breakingNewsModeLabel(breakingNewsMode)}...`,
    );

    const params = new URLSearchParams({ runtime, scenario: scenarioId, ticker });
    params.append('breaking_news_mode', breakingNewsMode);
    if (breakingNewsMode === 'manual') params.append('breaking_news', '1');
    params.append('debate_depth', String(debateDepth));
    seatIds.forEach((seatId) => params.append('seat', seatId));
    const startResponse = await fetch(`/api/run/start?${params.toString()}`);
    if (startResponse.ok) {
      const startPayload = await startResponse.json();
      const runId = startPayload.run_id;
      state.currentRunId = runId;
      state.activeBackendRun = true;
      state.runRuntime = runtime;
      state.runTicker = ticker;
      if (Number.isFinite(Number(startPayload.debate_depth))) {
        document.getElementById('debate-depth').value = String(startPayload.debate_depth);
      }
      setStatus(`Run ${runId} started. Stage gate is active; run pauses after each stage.`);
      beginLivePolling(runId);
      return;
    }

    // Backward-compatible fallback if async endpoint is unavailable.
    state.activeBackendRun = false;
    setStatus('Async start unavailable, running in legacy mode...');
    const response = await fetch(`/api/run?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok || !Array.isArray(payload.events)) {
      setStatus(`Run failed: ${payload.message || payload.error || response.status}`, 'error');
      return;
    }
    ingestSummary(payload, { includeObjects: false });
    queueEvents(payload.events);
    await drainQueuedEvents();
    setStatus(`Loaded ${runtime} run ${payload.summary.run_id}. Stage gate is active for step-through.`);
    await loadRecentRuns();
    await loadAudit();
  } catch (error) {
    setStatus(`Run failed: ${shortError(error?.message || error)}`, 'error');
  }
}

async function loadReplay() {
  try {
    const runId = document.getElementById('replay-run').value;
    if (!runId) {
      setStatus('Select a run to replay.', 'error');
      return;
    }

    resetState();
    setStatus(`Loading replay ${runId}...`);

    const response = await fetch(`/api/runs?run_id=${encodeURIComponent(runId)}`);
    const payload = await response.json();
    if (!response.ok || !payload.event_log_url) {
      setStatus(`Replay load failed: ${payload.message || payload.error || response.status}`, 'error');
      return;
    }

    ingestSummary(payload, { includeObjects: false });
    if (payload.summary?.scenario_id) {
      const replayScenario = state.scenarios.find((item) => item.scenario_id === payload.summary.scenario_id);
      if (replayScenario) {
        document.getElementById('scenario').value = replayScenario.scenario_id;
        renderSeats(replayScenario);
      }
    }

    if (payload.summary?.ticker) {
      document.getElementById('ticker').value = payload.summary.ticker;
    } else if (payload.summary?.tickers?.length) {
      document.getElementById('ticker').value = payload.summary.tickers[0];
    }
    if (payload.summary?.breaking_news_mode) {
      document.getElementById('breaking-news').value = normalizeBreakingNewsMode(payload.summary.breaking_news_mode);
    } else if (typeof payload.summary?.breaking_news_reroute === 'boolean') {
      document.getElementById('breaking-news').value = payload.summary.breaking_news_reroute ? 'auto_after_gather' : 'off';
    }
    applyBreakingNewsControlForScenario(selectedScenario(), document.getElementById('breaking-news').value);
    if (Number.isFinite(Number(payload.summary?.debate_depth))) {
      document.getElementById('debate-depth').value = String(payload.summary.debate_depth);
    }
    void loadStockPriceChart({ force: true });

    const logResponse = await fetch(payload.event_log_url);
    if (!logResponse.ok) {
      ingestSummary(payload, { seedStageState: true, includeObjects: true });
      setStatus(`Replay loaded without event log (${logResponse.status}).`, 'error');
      refreshPanels();
      return;
    }

    const rawLog = await logResponse.text();
    const lines = rawLog.trim().split('\n').filter(Boolean);
    const events = lines
      .map((line) => {
        try {
          return JSON.parse(line);
        } catch {
          return null;
        }
      })
      .filter(Boolean);
    if (!events.length) {
      ingestSummary(payload, { seedStageState: true, includeObjects: true });
      refreshPanels();
      setStatus(`Loaded replay ${runId} (event log empty).`);
      return;
    }

    queueEvents(events);
    await drainQueuedEvents();
    const nextStage = nextStageAfter(state.pausedAfterStage);
    const phaseLabel = nextStage ? phaseLabelForStage(nextStage) : 'the next phase';
    setStatus(`Loaded replay ${runId}. Use Demo Controls when you want to continue into ${phaseLabel}.`);
    await loadAudit();
  } catch (error) {
    setStatus(`Replay failed: ${shortError(error?.message || error)}`, 'error');
  }
}

async function loadScenarios() {
  try {
    const response = await fetch('/api/scenarios');
    const scenarios = await response.json();
    state.scenarios = scenarios;
    const node = document.getElementById('scenario');
    node.innerHTML = scenarios.map((item) => {
      return `<option value="${item.scenario_id}">${escapeHtml(item.name)}</option>`;
    }).join('');
    if (scenarios.length) {
      renderSeats(scenarios[0]);
    }
  } catch (error) {
    setStatus(`Scenario load failed: ${shortError(error?.message || error)}`, 'error');
  }
}

async function loadRecentRuns() {
  try {
    const response = await fetch('/api/runs?run_id=recent&limit=20');
    const runs = await response.json();
    const replay = document.getElementById('replay-run');
    replay.innerHTML = '<option value="">Recent runs...</option>' + runs.map((item) => {
      const ticker = item.ticker || (item.tickers && item.tickers[0]) || 'N/A';
      return `<option value="${item.run_id}">${item.run_id} · ${item.runtime} · ${ticker}</option>`;
    }).join('');
  } catch (error) {
    setStatus(`Recent runs load failed: ${shortError(error?.message || error)}`, 'error');
  }
}

async function loadAudit() {
  try {
    const response = await fetch('/api/audit?limit=40');
    const rows = await response.json();
    const node = document.getElementById('audit');
    node.innerHTML = rows.map((row) => {
      return `
        <div class="event">
          <strong>${escapeHtml(row.event_type)}</strong>
          <div class="meta">${escapeHtml(formatTime(row.emitted_at))} · ${escapeHtml(formatStage(row.stage_id))} · ${escapeHtml(formatRole(row.producer))}</div>
          <div class="mono">${escapeHtml(JSON.stringify(row.payload || {}, null, 2))}</div>
        </div>
      `;
    }).join('') || '<div class="section-subtitle">No audit events yet.</div>';
  } catch (error) {
    setStatus(`Audit load failed: ${shortError(error?.message || error)}`, 'error');
  }
}

async function startCurrentPhaseFromRail(phaseIndex) {
  const progress = computePhaseProgress();
  const actionablePhaseIndex = getActionablePhaseIndex(progress);
  if (phaseIndex !== actionablePhaseIndex) {
    setStatus('Phase control updated. Use the active Start button in Demo Controls.');
    return;
  }
  if (!progress.runStarted) {
    await runCommittee();
    return;
  }
  if (state.awaitingNextStage) {
    await startNextStage();
    return;
  }
  setStatus('Current phase is still running. Wait for completion before starting the next phase.');
}

async function resetFlowFromDemoControls() {
  const runId = state.currentRunId;
  const shouldResetBackend = Boolean(state.activeBackendRun && runId);
  stopLivePolling();

  let backendMessage = '';
  let backendLevel = 'info';
  if (shouldResetBackend) {
    try {
      const response = await fetch(`/api/run/reset?run_id=${encodeURIComponent(runId)}`);
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      if (!response.ok) {
        const reason = payload.message || payload.error || response.status;
        backendMessage = `Backend reset warning for ${runId}: ${reason}.`;
        backendLevel = 'error';
      } else if (payload.thread_alive) {
        backendMessage = `Run ${runId} cancellation requested. Backend thread is still winding down.`;
      } else {
        backendMessage = `Run ${runId} cancelled.`;
      }
    } catch (error) {
      backendMessage = `Backend reset warning for ${runId}: ${shortError(error?.message || error)}.`;
      backendLevel = 'error';
    }
  }

  resetState();
  if (backendMessage) {
    setStatus(`${backendMessage} Flow reset. Start phase 1 from Demo Controls when ready.`, backendLevel);
    return;
  }
  setStatus('Flow reset. Start phase 1 from Demo Controls when ready.');
}

function setUtilityDrawer(target = '') {
  const normalized = target === 'demo' || target === 'diagnostics' ? target : '';
  const demoDrawer = document.getElementById('demo-controls-drawer');
  const diagnosticsDrawer = document.getElementById('diagnostics-drawer');
  const demoButton = document.getElementById('demo-controls-toggle');
  const diagnosticsButton = document.getElementById('diagnostics-toggle');

  if (demoDrawer) demoDrawer.hidden = normalized !== 'demo';
  if (diagnosticsDrawer) diagnosticsDrawer.hidden = normalized !== 'diagnostics';
  if (demoButton) demoButton.setAttribute('aria-expanded', normalized === 'demo' ? 'true' : 'false');
  if (diagnosticsButton) diagnosticsButton.setAttribute('aria-expanded', normalized === 'diagnostics' ? 'true' : 'false');
}

const demoControlsToggle = document.getElementById('demo-controls-toggle');
if (demoControlsToggle) {
  demoControlsToggle.addEventListener('click', () => {
    const expanded = demoControlsToggle.getAttribute('aria-expanded') === 'true';
    setUtilityDrawer(expanded ? '' : 'demo');
  });
}

const diagnosticsToggle = document.getElementById('diagnostics-toggle');
if (diagnosticsToggle) {
  diagnosticsToggle.addEventListener('click', () => {
    const expanded = diagnosticsToggle.getAttribute('aria-expanded') === 'true';
    setUtilityDrawer(expanded ? '' : 'diagnostics');
  });
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    setUtilityDrawer('');
  }
});

const phaseNavNode = document.getElementById('phase-nav');
if (phaseNavNode) {
  phaseNavNode.addEventListener('click', (event) => {
    if (!(event.target instanceof Element)) return;
    const startButton = event.target.closest('[data-phase-action="start"]');
    if (startButton) {
      const phaseIndex = Number(startButton.dataset.phaseIndex || '-1');
      if (!Number.isFinite(phaseIndex) || phaseIndex < 0) return;
      state.uiActivePhaseId = phaseIdForIndex(phaseIndex);
      state.uiManualPhaseSelection = true;
      refreshPanels();
      void startCurrentPhaseFromRail(phaseIndex);
      return;
    }
    const button = event.target.closest('.phase-tab-select[data-phase-id]');
    if (!button) return;
    const selectedId = normalizeWorkspacePhaseId(String(button.dataset.phaseId || ''), deriveLivePhaseId());
    const livePhaseId = deriveLivePhaseId();
    state.uiActivePhaseId = selectedId;
    state.uiManualPhaseSelection = selectedId !== livePhaseId;
    refreshPanels();
  });
}

const phaseFollowLiveButton = document.getElementById('phase-follow-live-btn');
if (phaseFollowLiveButton) {
  phaseFollowLiveButton.addEventListener('click', () => {
    state.uiManualPhaseSelection = false;
    state.uiActivePhaseId = deriveLivePhaseId();
    refreshPanels();
  });
}

document.getElementById('load-run-btn').addEventListener('click', loadReplay);
document.getElementById('reset-flow-btn').addEventListener('click', resetFlowFromDemoControls);
document.getElementById('refresh-audit-btn').addEventListener('click', loadAudit);
document.getElementById('ticker').addEventListener('change', () => {
  renderScenarioBrief();
  void loadStockPriceChart({ force: true });
});
document.getElementById('ticker').addEventListener('input', () => {
  renderScenarioBrief();
});
document.getElementById('ticker').addEventListener('keyup', (event) => {
  if (event.key !== 'Enter') return;
  renderScenarioBrief();
  void loadStockPriceChart({ force: true });
});
document.getElementById('stock-timeframe-select').addEventListener('change', (event) => {
  const range = String(event.target.value || '').toLowerCase();
  if (!STOCK_TIMEFRAMES.has(range)) return;
  if (state.stockChart.timeframe !== range) {
    state.stockChart.timeframe = range;
  }
  void loadStockPriceChart({ force: true });
});
document.getElementById('scenario').addEventListener('change', (event) => {
  const selected = state.scenarios.find((item) => item.scenario_id === event.target.value);
  if (selected) {
    renderSeats(selected);
  }
});
document.getElementById('breaking-news').addEventListener('change', () => {
  applyBreakingNewsControlForScenario(selectedScenario(), document.getElementById('breaking-news').value);
  renderScenarioBrief();
});
document.getElementById('optional-seats').addEventListener('change', () => {
  renderScenarioBrief();
  renderRunRosterSummary();
});

loadScenarios().then(async () => {
  refreshPanels();
  await loadStockPriceChart({ force: true });
  await loadRecentRuns();
  await loadAudit();
  setStatus('Ready. Choose a scenario, then open Demo Controls when you want to run the desk.');
});
