/**
 * Sawyer Agent — Preact App Entry Point
 * 
 * Migration bridge: Preact components render into existing DOM containers.
 * Legacy inline JS continues to work for panels not yet migrated.
 * As panels migrate, their legacy code is removed from index.html.
 * 
 * Load order: Preact Signals CDN (head) → state.js (module) → app.js (module)
 * 
 * Strategy: Preact renders initial structure, but real-time updates go through
 * state.js DOM manipulation (updateHeaderMetrics, updateContextBars) which
 * already works and is called by initApp's data loaders. This avoids Preact
 * signal reactivity issues while keeping the Preact component structure for
 * future migration.
 */

import { html, render, Component } from 'https://esm.sh/htm@3.1.1/preact/standalone';
import { signal, computed, effect, batch } from 'https://esm.sh/@preact/signals@1.2.3';
import {
  state, tokenUsagePct, pendingTasks, activeModelName, modelHealthStatus,
  uptimeStr, addToast, setActivePanel, loadContextStats, loadStatus, initApp
} from './state.js';
import { ws } from './ws.js';

// ============================================================
// Reactive Header Metrics
// ============================================================

function HeaderMetrics() {
  const health = modelHealthStatus.value;
  const healthColor = health.status === 'healthy' ? 'var(--success)' : 
                      health.status === 'degraded' ? 'var(--warning)' : 'var(--danger)';
  
  // Context meter: used / total with bar fill
  const stats = state.contextStats.value;
  const total = stats?.window_size || stats?.budget?.total_window || 128000;
  // Use actual tokens used (messages + system + memory + skills), not budget allocation
  // A fresh session with no messages should show 0 used, not the reserve percentages.
  const budget = stats?.budget || {};
  const used = (budget.messages || 0) + (budget.system_prompt || 0) + (budget.memory || 0) + (budget.skills || 0) + (budget.session_notes || 0);
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  const fmtK = n => n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K' : n.toLocaleString();
  const ctxLabel = fmtK(used) + ' / ' + fmtK(total);
  let barColor = 'var(--brand)';
  if (pct > 90) barColor = 'var(--danger)';
  else if (pct > 70) barColor = 'var(--warning)';
  
  // New session button turns red when context is getting tight
  const needsCompress = stats?.needs_compression || false;
  const sessionBtnStyle = needsCompress ? 'color:var(--danger);border-color:var(--danger)' : '';
  
  const tasks = pendingTasks.value;
  
  return html`
    <>
      <div class="metric-pill">
        <span class="pulse" style="background:${healthColor}"></span>
        <span>Model</span>
        <span class="metric-value" id="metric-model">${activeModelName.value}</span>
      </div>
      <div class="metric-pill metric-pill--ctx" id="ctx-meter-header" title="Context window usage">
        <span style="font-size:10px;color:var(--text-2)">CTX</span>
        <span class="metric-value" id="metric-tokens" style="font-variant-numeric:tabular-nums">${ctxLabel}</span>
        <div class="ctx-meter-bar"><div class="ctx-meter-fill" id="ctx-meter-fill" style="width:${pct}%;background:${barColor}"></div></div>
      </div>
      <div class="metric-pill">
        <span>Tasks</span>
        <span class="metric-value" id="metric-tasks">${tasks.done}/${tasks.total}</span>
      </div>
      <button class="btn btn--subtle btn-sm" style="${sessionBtnStyle}" onclick=${() => { if (window.newSession) window.newSession(); }} title="New session">+ NEW SESSION</button>
      </>
      `;
      }

// ============================================================
// Toast Container
// ============================================================

function ToastContainer() {
  const toasts = state.toasts.value;
  if (toasts.length === 0) return null;
  
  return html`
    <div class="toast-container" id="toast-container">
      ${toasts.map(t => html`
        <div class="toast toast--${t.type}" key=${t.id}>
          ${t.msg}
        </div>
      `)}
    </div>
  `;
}

// ============================================================
// Status Bar (reactive)
// ============================================================

function StatusBar() {
  const health = modelHealthStatus.value;
  const latencyStr = health.latency > 0 ? `${health.latency}ms` : '--';
  const pid = state.status.value?.pid || '';
  
  // Context usage for status bar -- use actual tokens, not budget reserves
  const stats = state.contextStats.value;
  const total = stats?.window_size || stats?.budget?.total_window || 128000;
  const budget = stats?.budget || {};
  const used = (budget.messages || 0) + (budget.system_prompt || 0) + (budget.memory || 0) + (budget.skills || 0) + (budget.session_notes || 0);
  const pct = total > 0 ? Math.round((used / total) * 100) : 0;
  const fmtK = n => n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K' : n.toLocaleString();
  const ctxStr = fmtK(used) + '/' + fmtK(total) + ' (' + pct + '%)';
  
  return html`
    <div class="status-info">
      <span class="status-item">Model: <strong>${activeModelName.value}</strong></span>
      <span class="status-item">Latency: <strong>${latencyStr}</strong></span>
      <span class="status-item">CTX: <strong>${ctxStr}</strong></span>
      <span class="status-item">Session: <strong>${state.sessionId.value || '--'}</strong></span>
      <span class="status-item">Uptime: <strong>${uptimeStr.value}</strong></span>
    </div>
    <div class="status-actions">
      <button class="btn btn--subtle btn-sm" onclick=${() => { if (window.newSession) window.newSession(); }} title="Start a new session">
        <i data-lucide="plus" style="width:14px;height:14px"></i> New Session
      </button>
      ${pid ? html`<span id="status-pid" style="color:var(--brand);font-size:11px;font-weight:600" title="Process ID">PID ${pid}</span>` : ''}
    </div>
  `;
}

// ============================================================
// Mount — render Preact components into existing DOM
// ============================================================

let mounted = false;

function mountComponents() {
  if (mounted) return;
  mounted = true;
  
  console.log('[Sawyer] Mounting Preact components...');
  
  // Header metrics — render into existing .header-right
  const headerRight = document.querySelector('.header-right');
  if (headerRight) {
    try {
      render(html`<${HeaderMetrics} />`, headerRight);
    } catch(e) { console.error('[Sawyer] HeaderMetrics render error:', e); }
  }
  
  // Toast container — append to body
  let toastMount = document.getElementById('toast-container-preact');
  if (!toastMount) {
    toastMount = document.createElement('div');
    toastMount.id = 'toast-container-preact';
    document.body.appendChild(toastMount);
  }
  try {
    render(html`<${ToastContainer} />`, toastMount);
  } catch(e) { console.error('[Sawyer] ToastContainer render error:', e); }
  
  // Status bar removed — header now shows model, CTX, tasks, and new-session button
  
  console.log('[Sawyer] Preact components mounted');
}

// ============================================================
// Bootstrap
// ============================================================

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mountComponents);
} else {
  mountComponents();
}

// Initialize state data — same module instance as static import
initApp().then(() => {
  console.log('[Sawyer] initApp completed. models:', state.models.value.length, 'activeModelName:', activeModelName.value);
  // Re-render Preact components now that data is loaded
  mountComponents();
  // Also update DOM directly via state.js helper
  const headerRight = document.querySelector('.header-right');
  if (headerRight) {
    try {
      render(html`<${HeaderMetrics} />`, headerRight);
    } catch(e) { /* silent */ }
  }
}).catch(e => {
  console.error('[Sawyer] initApp failed:', e);
});

console.log('[Sawyer] Preact app loaded');