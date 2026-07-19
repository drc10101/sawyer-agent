/**
 * Sawyer Agent — Global Reactive State (Preact Signals)
 * 
 * Single source of truth for all UI state. Every component reads from
 * these signals. Changes propagate automatically to any subscribed component.
 * 
 * Import { state } from './state.js' and read .value — that's it.
 * 
 * Bridge: key functions are also assigned to window for legacy inline code.
 */

import { signal, computed, effect, batch } from 'https://esm.sh/@preact/signals@1.2.3';

// ============================================================
// Reactive State
// ============================================================

export const state = {
  // --- Session ---
  sessionId:          signal(''),
  startTime:          signal(Date.now()),

  // --- Navigation ---
  activePanel:        signal('chat'),
  sidebarCollapsed:   signal(false),
  theme:              signal(localStorage.getItem('sawyer-theme') || 'dark'),

  // --- Chat ---
  messages:           signal([]),
  isStreaming:        signal(false),

  // --- Models ---
  models:             signal([]),
  currentModel:       signal(''),
  modelHealth:        signal({}),

  // --- Skills ---
  skills:             signal([]),
  skillDetail:        signal(null),
  skillTab:           signal('browse'),

  // --- Skill Creator ---
  skillSessions:      signal([]),
  activeSkillSession: signal(null),

  // --- Goals ---
  goals:              signal([]),

  // --- Tools ---
  tools:              signal([]),
  auditLog:           signal([]),

  // --- Sessions ---
  sessions:           signal([]),
  sessionNotes:       signal(''),

  // --- Projects ---
  projects:           signal([]),
  currentProject:     signal(null),
  projectFiles:       signal([]),

  // --- Cron ---
  cronJobs:           signal([]),

  // --- Memory ---
  memoryEntries:      signal([]),

  // --- Context ---
  contextStats:       signal(null),
  contextModels:      signal([]),

  // --- Keys ---
  keys:               signal({}),
  keysVersion:        signal(0),
  keysTab:            signal('ssh'),

  // --- Rules ---
  rules:              signal([]),

  // --- Agents ---
  agentTemplates:     signal([]),

  // --- Orchestration ---
  orchestrations:     signal([]),
  activeOrchestration: signal(null),

  // --- Status ---
  status:             signal({}),

  // --- UI ---
  toasts:             signal([]),
  keysChangeNotified:  signal(false),
};

// ============================================================
// Computed / Derived State
// ============================================================

export const tokenUsagePct = computed(() => {
  const stats = state.contextStats.value;
  if (!stats?.budget) return 0;
  const total = stats.window_size || stats.budget?.total_window || 128000;
  const used = total - (stats.budget?.free_space || 0);
  return total > 0 ? Math.round((used / total) * 100) : 0;
});

export const pendingTasks = computed(() => {
  const goals = state.goals.value;
  let total = 0, done = 0;
  for (const g of goals) {
    if (g.subtasks) {
      total += g.subtasks.length;
      done += g.subtasks.filter(s => s.status === 'completed').length;
    }
  }
  return { total, done };
});

export const activeModelName = computed(() => {
  // Prefer the actual configured model name from context stats
  const configModel = state.contextStats.value?.model;
  if (configModel) return configModel;
  const current = state.currentModel.value;
  if (current) return current;
  const models = state.models.value;
  if (models.length > 0) return models[0].name || models[0].provider || '--';
  return '--';
});

export const modelHealthStatus = computed(() => {
  const health = state.modelHealth.value;
  const entries = Object.entries(health);
  if (entries.length === 0) return { status: 'unknown', latency: 0 };
  const model = state.currentModel.value || (state.models.value[0]?.name);
  if (model && health[model]) {
    return { status: health[model].status, latency: health[model].latency_ms };
  }
  const [name, info] = entries[0];
  return { status: info?.status || 'unknown', latency: info?.latency_ms || 0 };
});

export const uptimeStr = computed(() => {
  const elapsed = Math.floor((Date.now() - state.startTime.value) / 1000);
  const h = Math.floor(elapsed / 3600);
  const m = Math.floor((elapsed % 3600) / 60);
  const s = elapsed % 60;
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`;
});

export const pendingKeyCount = computed(() => {
  const keys = state.keys.value;
  let count = 0;
  for (const cat of Object.values(keys)) {
    if (Array.isArray(cat)) count += cat.length;
  }
  return count;
});

// ============================================================
// API base URL (same-origin)
// ============================================================

const API = '';

// ============================================================
// State Mutations
// ============================================================

/** Switch active panel */
export function setActivePanel(panel) {
  state.activePanel.value = panel;
  const titles = {
    chat: 'Chat', goals: 'Goal Loops', 'skill-creator': 'Skills',
    tools: 'Tools', files: 'Files', models: 'Models',
    sessions: 'Sessions', projects: 'Projects', cron: 'Cron',
    context: 'Memory', keys: 'Keys', rules: 'Rules', agents: 'Sub-Agents',
  };
  const titleEl = document.getElementById('panel-title');
  if (titleEl) titleEl.textContent = titles[panel] || panel;
  
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const panelEl = document.getElementById('panel-' + panel);
  if (panelEl) panelEl.classList.add('active');
  
  document.querySelectorAll('.sidebar-nav-item').forEach(item => {
    item.classList.toggle('active', item.dataset.panel === panel);
  });
  
  const sb = document.getElementById('sidebar');
  const overlay = document.getElementById('mobile-overlay');
  if (sb) sb.classList.remove('mobile-open');
  if (overlay) overlay.classList.remove('active');
  
  loadPanelData(panel);
}

function loadPanelData(panel) {
  switch (panel) {
    case 'models': loadModels(); break;
    case 'sessions': if (typeof window.loadSessions === 'function') window.loadSessions(); else loadSessions(); break;
    case 'skill-creator': if (typeof window.loadSkills === 'function') window.loadSkills(); else loadSkills(); if (typeof window.loadSkillSessions === 'function') window.loadSkillSessions(); else loadSkillSessions(); break;
    case 'tools': if (typeof window.loadTools === 'function') window.loadTools(); else loadTools(); break;
    case 'goals': if (typeof window.loadGoals === 'function') window.loadGoals(); else loadGoals(); break;
    case 'context': loadContextStats(); break;
    case 'projects': if (typeof window.loadProjects === 'function') window.loadProjects(); else loadProjects(); break;
    case 'cron': if (typeof window.loadCron === 'function') window.loadCron(); else loadCronJobs(); break;
    case 'keys': if (typeof window.loadKeys === 'function') window.loadKeys(); else loadKeys(); break;
    case 'rules': if (typeof window.loadRules === 'function') window.loadRules(); else loadRulesState(); break;
    case 'agents': if (typeof window.loadAgents === 'function') window.loadAgents(); else loadAgentsState(); break;
    case 'files': loadProjectFiles(); break;
  }
}

/** Toggle sidebar collapsed state */
export function toggleSidebar() {
  state.sidebarCollapsed.value = !state.sidebarCollapsed.value;
  const sb = document.getElementById('sidebar');
  if (sb) sb.classList.toggle('collapsed', state.sidebarCollapsed.value);
}

/** Toggle theme */
export function toggleTheme() {
  const next = state.theme.value === 'dark' ? 'light' : 'dark';
  state.theme.value = next;
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('sawyer-theme', next);
  const btn = document.getElementById('theme-btn');
  if (btn) btn.innerHTML = `<i data-lucide="${next === 'dark' ? 'moon' : 'sun'}"></i>`;
  if (window.lucide) window.lucide.createIcons();
}

/** Add a toast notification */
export function addToast(msg, type = 'info') {
  const id = Date.now();
  batch(() => {
    state.toasts.value = [...state.toasts.value, { id, msg, type, timestamp: Date.now() }];
  });
  setTimeout(() => {
    state.toasts.value = state.toasts.value.filter(t => t.id !== id);
  }, 3000);
}

/** Add a chat message to state */
export function addMessage(role, content) {
  state.messages.value = [...state.messages.value, { role, content, timestamp: Date.now() }];
}

// ============================================================
// API Loaders (fetch data → update signals)
// ============================================================

export async function loadModels() {
  try {
    const res = await fetch(`${API}/api/models`);
    const data = await res.json();
    // API returns { providers: { name: {...}, ... } } — convert to array
    if (data.providers && typeof data.providers === 'object' && !Array.isArray(data.providers)) {
      state.models.value = Object.entries(data.providers).map(([name, info]) => ({ name, ...info }));
    } else {
      state.models.value = Array.isArray(data.providers) ? data.providers : (Array.isArray(data) ? data : []);
    }
    if (data.current) state.currentModel.value = data.current;
  } catch (e) { console.error('loadModels:', e); }
}

export async function loadContextStats() {
  try {
    const sid = state.sessionId.value;
    const res = await fetch(`${API}/api/context/stats${sid ? '?session_id=' + sid : ''}`);
    const stats = await res.json();
    state.contextStats.value = stats;
    updateContextBars(stats);
  } catch (e) { console.error('loadContextStats:', e); }
}

export async function loadSkills() {
  try {
    const res = await fetch(`${API}/api/skills`);
    const data = await res.json();
    // API returns { skills: [...] } or just array
    state.skills.value = Array.isArray(data) ? data : (data.skills || []);
  } catch (e) { console.error('loadSkills:', e); }
}

export async function loadSkillSessions() {
  try {
    const res = await fetch(`${API}/api/skill-creator/sessions`);
    const data = await res.json();
    state.skillSessions.value = Array.isArray(data) ? data : (data.sessions || []);
  } catch (e) { console.error('loadSkillSessions:', e); }
}

export async function loadGoals() {
  try {
    const res = await fetch(`${API}/api/goals`);
    const data = await res.json();
    // API returns { goals: [...] }
    state.goals.value = Array.isArray(data) ? data : (data.goals || []);
  } catch (e) { console.error('loadGoals:', e); }
}

export async function loadTools() {
  try {
    const [toolsRes, auditRes] = await Promise.all([
      fetch(`${API}/api/tools`),
      fetch(`${API}/api/tools/audit?limit=50`),
    ]);
    state.tools.value = (await toolsRes.json()).tools || [];
    state.auditLog.value = (await auditRes.json()).entries || [];
  } catch (e) { console.error('loadTools:', e); }
}

export async function loadSessions() {
  try {
    const res = await fetch(`${API}/api/sessions`);
    const data = await res.json();
    state.sessions.value = Array.isArray(data) ? data : (data.sessions || []);
  } catch (e) { console.error('loadSessions:', e); }
}

export async function loadProjects() {
  try {
    const res = await fetch(`${API}/api/projects`);
    const data = await res.json();
    state.projects.value = Array.isArray(data) ? data : (data.projects || []);
  } catch (e) { console.error('loadProjects:', e); }
}

export async function loadProjectFiles() {
  try {
    const res = await fetch(`${API}/api/projects/current/files`);
    const data = await res.json();
    state.projectFiles.value = data.files || [];
  } catch (e) { console.error('loadProjectFiles:', e); }
}

export async function loadCronJobs() {
  try {
    const res = await fetch(`${API}/api/cron`);
    const data = await res.json();
    state.cronJobs.value = Array.isArray(data) ? data : (data.jobs || []);
  } catch (e) { console.error('loadCronJobs:', e); }
}

export async function loadMemory() {
  try {
    const res = await fetch(`${API}/api/memory`);
    const data = await res.json();
    state.memoryEntries.value = Array.isArray(data) ? data : (data.entries || []);
  } catch (e) { console.error('loadMemory:', e); }
}

export async function loadKeys(category) {
  const cat = category || state.keysTab.value;
  try {
    const res = await fetch(`${API}/api/keys?category=${cat}`);
    const data = await res.json();
    const current = { ...state.keys.value };
    current[cat] = data[cat] || [];
    state.keys.value = current;
  } catch (e) { console.error('loadKeys:', e); }
}

export async function loadStatus() {
  try {
    const res = await fetch(`${API}/api/status`);
    const data = await res.json();
    state.status.value = data;
    if (data.pid) {
      const pidEl = document.getElementById('status-pid');
      if (pidEl) pidEl.textContent = 'PID ' + data.pid;
    }
  } catch (e) { console.error('loadStatus:', e); }
}

export async function pollKeysVersion() {
  try {
    const res = await fetch(`${API}/api/keys?category=ssh`);
    const data = await res.json();
    const newVersion = data.version || 0;
    if (newVersion > state.keysVersion.value) {
      state.keysVersion.value = newVersion;
      if (!state.keysChangeNotified.value) {
        state.keysChangeNotified.value = true;
        showKeysChangedNotification();
      }
    }
  } catch (e) { /* silent */ }
}

export async function checkModelHealth() {
  try {
    const providers = state.models.value;
    const health = { ...state.modelHealth.value };
    for (const p of providers) {
      const name = p.name || p.provider;
      try {
        const res = await fetch(`${API}/api/models/${encodeURIComponent(name)}/health`);
        const data = await res.json();
        health[name] = { status: data.healthy ? 'healthy' : 'down', latency_ms: data.latency_ms || 0 };
      } catch {
        health[name] = { status: 'down', latency_ms: 0 };
      }
    }
    state.modelHealth.value = health;
  } catch (e) { /* silent */ }
}

// ============================================================
// DOM Update Helpers (bridge: signals → legacy DOM)
// As panels migrate to Preact components, these become unnecessary.
// ============================================================

function updateContextBars(stats) {
  if (!stats?.budget) return;
  const b = stats.budget;
  const total = stats.window_size || b.total_window || 128000;

  // Real measured token counts (not budget allocations)
  const system_tokens = b.system_prompt || 0;
  const memory_tokens = b.memory || 0;
  const skills_tokens = b.skills || 0;
  const session_tokens = b.session_notes || 0;
  const message_tokens = b.messages || 0;

  // Used = sum of all real token usage
  const used_tokens = system_tokens + memory_tokens + skills_tokens + session_tokens + message_tokens;
  // Available = what's left in the window
  const available_tokens = total - used_tokens;

  // Update budget table in Context panel (real measurements only)
  setBudgetRow('system', system_tokens, total);
  setBudgetRow('memory', memory_tokens, total);
  setBudgetRow('skills', skills_tokens, total);
  setBudgetRow('session', session_tokens, total);
  setBudgetRow('recent', message_tokens, total);
  setBudgetRow('used', used_tokens, total);
  setBudgetRow('free', available_tokens, total);

  const modelEl = document.getElementById('ctx-model-name');
  if (modelEl) modelEl.textContent = stats.model || state.currentModel.value || '--';
  const windowEl = document.getElementById('ctx-window-size');
  if (windowEl) windowEl.textContent = total.toLocaleString();

  // Update header context meter
  updateHeaderMetrics();
}

function setBudgetRow(name, tokens, total) {
  const el = document.getElementById('ctx-budget-' + name);
  const pctEl = document.getElementById('ctx-budget-' + name + '-pct');
  if (el) el.textContent = (tokens || 0).toLocaleString();
  if (pctEl) pctEl.textContent = total > 0 ? ((tokens || 0) / total * 100).toFixed(1) + '%' : '--';
}

function showKeysChangedNotification() {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  const existing = document.getElementById('keys-change-notification');
  if (existing) existing.remove();
  
  const msg = document.createElement('div');
  msg.id = 'keys-change-notification';
  msg.style.cssText = 'background:rgba(18,199,239,0.08);border:1px solid var(--brand);border-radius:8px;padding:12px 16px;margin:8px 0;display:flex;align-items:center;gap:12px;flex-wrap:wrap';
  msg.innerHTML = '<span style="color:var(--brand);font-size:14px;font-weight:600">Access keys have changed</span>' +
    '<span style="color:var(--text-2);font-size:13px">Your key storage was updated. Changes take effect in a new session.</span>' +
    '<button class="btn btn--primary btn-sm" onclick="saveAndNewSession()" style="margin-left:auto">Save & New Session</button>' +
    '<button class="btn btn--subtle btn-sm" onclick="dismissKeysNotification()">Dismiss</button>';
  container.appendChild(msg);
  container.scrollTop = container.scrollHeight;
  setActivePanel('chat');
}

// ============================================================
// Initialization
// ============================================================

export async function loadRulesState() {
  try {
    const res = await fetch(`${API}/api/rules`);
    const data = await res.json();
    state.rules.value = data.rules || [];
  } catch (e) { console.error('loadRulesState:', e); }
}

export async function loadAgentsState() {
  try {
    const res = await fetch(`${API}/api/agents`);
    const data = await res.json();
    state.agentTemplates.value = data.templates || [];
  } catch (e) { console.error('loadAgentsState:', e); }
}

export async function initApp() {
  console.log('[Sawyer] initApp starting...');
  // Apply saved theme
  document.documentElement.setAttribute('data-theme', state.theme.value);
  
  // Load initial data in parallel
  console.log('[Sawyer] Loading initial data...');
  try {
    await Promise.all([
      loadStatus(),
      loadModels(),
      loadContextStats(),
      loadSkills(),
      loadGoals(),
      loadSessions(),
      loadKeys(),
    ]);
    console.log('[Sawyer] Initial data loaded. models:', state.models.value.length);
  } catch(e) {
    console.error('[Sawyer] initApp data loading error:', e);
  }
  
  // Update header metrics
  updateHeaderMetrics();
  
  // Periodic refresh
  setInterval(() => { loadStatus(); updateUptimeDisplay(); }, 30000);
  setInterval(pollKeysVersion, 60000);
  setInterval(loadContextStats, 15000);
}

function updateHeaderMetrics() {
  const modelEl = document.getElementById('metric-model');
  if (modelEl) modelEl.textContent = activeModelName.value;
  // Context meter: show total used tokens / window size.
  // This includes system overhead + memory + skills + session + messages.
  const stats = state.contextStats.value;
  const total = stats?.window_size || stats?.budget?.total_window || 128000;
  const budget = stats?.budget || {};
  const used = (budget.system_prompt || 0) + (budget.memory || 0) +
               (budget.skills || 0) + (budget.session_notes || 0) +
               (budget.messages || 0);
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  const tokenEl = document.getElementById('metric-tokens');
  if (tokenEl) {
    const fmtK = n => n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K' : n.toLocaleString();
    tokenEl.textContent = fmtK(used) + ' / ' + fmtK(total);
  }
  const fillEl = document.getElementById('ctx-meter-fill');
  if (fillEl) {
    fillEl.style.width = pct + '%';
    if (pct > 90) fillEl.style.background = 'var(--danger)';
    else if (pct > 70) fillEl.style.background = 'var(--warning)';
    else fillEl.style.background = 'var(--brand)';
  }
  // Turn New Session button red when context pressure is high
  const newBtn = document.getElementById('new-session-btn');
  if (newBtn) {
    const needsCompression = stats?.needs_compression || pct >= 80;
    newBtn.classList.toggle('ctx-warning', needsCompression);
  }
  const taskEl = document.getElementById('metric-tasks');
  if (taskEl) {
    const t = pendingTasks.value;
    taskEl.textContent = `${t.done}/${t.total}`;
  }
}

function updateUptimeDisplay() {
  const el = document.getElementById('status-uptime');
  if (el) el.textContent = uptimeStr.value;
}

// Bridge: expose key functions on window for legacy inline code
// Do NOT override window.switchPanel -- the inline switchPanel in index.html
// handles DOM rendering for panels. setActivePanel only updates Preact signals.
// Do NOT override inline load* functions that render DOM (loadTools, loadGoals, etc.)
// Only expose functions that have NO inline equivalent in index.html.
window.toggleSidebar = toggleSidebar;
window.toggleTheme = toggleTheme;
window.showToast = function(msg, type) { addToast(msg, type); };
window.loadContextStats = loadContextStats;
window.loadMemory = loadMemory;