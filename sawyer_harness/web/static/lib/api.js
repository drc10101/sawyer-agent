/**
 * Sawyer Agent — API Client
 * 
 * Thin fetch wrappers for all Sawyer Agent REST endpoints.
 * Returns parsed JSON. Throws on non-2xx responses.
 * All mutations update Preact Signals in state.js.
 */

import { state, addToast, loadKeys, loadContextStats, loadGoals, loadSkillSessions, loadSessions } from './state.js';

const API = '';  // Same-origin — all requests go to the same host

/** Generic fetch wrapper */
async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || err.message || `HTTP ${res.status}`);
  }
  return res.json();
}

// ============================================================
// Chat
// ============================================================

/** Send a chat message and get the full response */
export async function sendChat(message, sessionId) {
  const sid = sessionId || state.sessionId.value;
  const data = await api('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ message, session_id: sid }),
  });
  if (data.session_id) state.sessionId.value = data.session_id;
  return data;
}

/** Clear a session */
export async function clearSession(sessionId) {
  const sid = sessionId || state.sessionId.value;
  await api(`/api/session/${sid}/clear`, { method: 'POST' });
  state.messages.value = [];
}

/** Generate session notes */
export async function generateNotes(sessionId) {
  const sid = sessionId || state.sessionId.value;
  const data = await api(`/api/session/${sid}/notes`, { method: 'POST' });
  state.sessionNotes.value = data;
  return data;
}

// ============================================================
// Models
// ============================================================

/** Check provider health */
export async function checkHealth(providerName) {
  return api(`/api/models/${encodeURIComponent(providerName)}/health`, { method: 'POST' });
}

/** Get/set routing config */
export async function getRoutingConfig() {
  return api('/api/models/routing');
}

export async function setRoutingPreference(taskType, providers) {
  return api('/api/models/routing', {
    method: 'POST',
    body: JSON.stringify({ task_type: taskType, providers }),
  });
}

/** Update LLM config at runtime */
export async function updateConfig(update) {
  return api('/api/config', {
    method: 'POST',
    body: JSON.stringify(update),
  });
}

// ============================================================
// Context
// ============================================================

/** Set context window / model */
export async function setContextWindow(model) {
  return api('/api/context/window', {
    method: 'POST',
    body: JSON.stringify({ model }),
  });
}

/** Compress current session context */
export async function compressContext(sessionId) {
  const sid = sessionId || state.sessionId.value;
  const data = await api(`/api/context/compress/${sid}`, { method: 'POST' });
  // Refresh stats after compression
  await loadContextStats();
  return data;
}

// ============================================================
// Skills
// ============================================================

/** Get a specific skill */
export async function getSkill(name) {
  return api(`/api/skills/${encodeURIComponent(name)}`);
}

/** Create a new skill */
export async function createSkill(skillData) {
  const data = await api('/api/skills', {
    method: 'POST',
    body: JSON.stringify(skillData),
  });
  await import('./state.js').then(m => m.loadSkills());
  return data;
}

/** Patch a skill */
export async function patchSkill(name, oldContent, newContent) {
  const data = await api(`/api/skills/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    body: JSON.stringify({ old_content: oldContent, new_content: newContent }),
  });
  await import('./state.js').then(m => m.loadSkills());
  return data;
}

/** Delete a skill */
export async function deleteSkill(name) {
  await api(`/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' });
  await import('./state.js').then(m => m.loadSkills());
}

/** Reload skills from disk */
export async function reloadSkills() {
  return api('/api/skills/reload', { method: 'POST' });
}

// ============================================================
// Goals
// ============================================================

/** Create a new goal */
export async function createGoal(goal, context = '') {
  const data = await api('/api/goals', {
    method: 'POST',
    body: JSON.stringify({ goal, context }),
  });
  await loadGoals();
  return data;
}

/** Get a specific goal */
export async function getGoal(goalId) {
  return api(`/api/goals/${goalId}`);
}

/** Complete a subtask */
export async function completeSubtask(goalId, subtaskId) {
  const data = await api(`/api/goals/${goalId}/subtask/${subtaskId}/complete`, { method: 'POST' });
  await loadGoals();
  return data;
}

// ============================================================
// Tools
// ============================================================

/** Toggle a tool on/off */
export async function toggleTool(toolName, enabled) {
  const data = await api('/api/tools/toggle', {
    method: 'POST',
    body: JSON.stringify({ tool_name: toolName, enabled }),
  });
  await import('./state.js').then(m => m.loadTools());
  return data;
}

// ============================================================
// Memory
// ============================================================

/** Add a memory entry */
export async function addMemory(key, content, category = 'general') {
  return api('/api/memory', {
    method: 'POST',
    body: JSON.stringify({ key, content, category }),
  });
}

/** Search memory entries */
export async function searchMemory(query, limit = 20) {
  const res = await api(`/api/memory/search?q=${encodeURIComponent(query)}&limit=${limit}`);
  return res.results || [];
}

/** Delete a memory entry */
export async function deleteMemory(key) {
  await api(`/api/memory/${encodeURIComponent(key)}`, { method: 'DELETE' });
  await import('./state.js').then(m => m.loadMemory());
}

// ============================================================
// Sessions
// ============================================================

/** Create a new session */
export async function createSession() {
  const data = await api('/api/sessions', { method: 'POST' });
  state.sessionId.value = data.session_id || data.id;
  return data;
}

/** Get session details and messages */
export async function getSession(sessionId) {
  return api(`/api/sessions/${sessionId}`);
}

/** Delete a session */
export async function deleteSession(sessionId) {
  return api(`/api/sessions/${sessionId}`, { method: 'DELETE' });
}

/** Get messages for a session */
export async function getSessionMessages(sessionId, limit = 1000) {
  return api(`/api/sessions/${sessionId}/messages?limit=${limit}`);
}

/** Resume a session (load messages back into active agent) */
export async function resumeSession(sessionId) {
  return api(`/api/sessions/${sessionId}/resume`, { method: 'POST' });
}

/** Export a session as Markdown */
export async function exportSession(sessionId) {
  const res = await fetch(`${API}/api/sessions/${sessionId}/export`);
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  return res.text();
}

/** Save session notes */
export async function saveNotes(sessionId) {
  const sid = sessionId || state.sessionId.value;
  return api(`/api/sessions/${sid}/notes/save`, { method: 'POST' });
}

/** Get session suggestions */
export async function getSessionSuggestions() {
  return api('/api/sessions/suggestions');
}

// ============================================================
// Projects
// ============================================================

/** Create a project with standard layout */
export async function createProject(name, template = 'default') {
  const data = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({ name, template }),
  });
  await import('./state.js').then(m => m.loadProjects());
  return data;
}

/** Open an existing project */
export async function openProject(name) {
  const data = await api(`/api/projects/${encodeURIComponent(name)}/open`, { method: 'POST' });
  await import('./state.js').then(m => m.loadProjects());
  return data;
}

// ============================================================
// Files
// ============================================================

/** Upload a file to the drop zone */
export async function uploadFile(file, projectName) {
  const formData = new FormData();
  formData.append('file', file);
  if (projectName) formData.append('project', projectName);

  const res = await fetch(`${API}/api/files/upload`, {
    method: 'POST',
    body: formData,  // No Content-Type header — browser sets multipart boundary
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Upload failed: HTTP ${res.status}`);
  }
  return res.json();
}

/** Get file content */
export async function getFileContent(fileId) {
  return api(`/api/files/${fileId}/content`, { method: 'POST' });
}

// ============================================================
// Cron
// ============================================================

/** Create a cron job */
export async function createCronJob(job) {
  const data = await api('/api/cron', {
    method: 'POST',
    body: JSON.stringify(job),
  });
  await import('./state.js').then(m => m.loadCronJobs());
  return data;
}

/** Delete a cron job */
export async function deleteCronJob(jobId) {
  await api(`/api/cron/${jobId}`, { method: 'DELETE' });
  await import('./state.js').then(m => m.loadCronJobs());
}

/** Toggle a cron job */
export async function toggleCronJob(jobId) {
  const data = await api(`/api/cron/${jobId}/toggle`, { method: 'POST' });
  await import('./state.js').then(m => m.loadCronJobs());
  return data;
}

// ============================================================
// Keys
// ============================================================

/** Add a key entry */
export async function addKey(category, entry) {
  const data = await api(`/api/keys/${category}`, {
    method: 'POST',
    body: JSON.stringify(entry),
  });
  await loadKeys(category);
  return data;
}

/** Update a key entry */
export async function updateKey(category, name, updates) {
  const data = await api(`/api/keys/${category}/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify(updates),
  });
  await loadKeys(category);
  return data;
}

/** Delete a key entry */
export async function deleteKey(category, name) {
  await api(`/api/keys/${category}/${encodeURIComponent(name)}`, { method: 'DELETE' });
  await loadKeys(category);
}

/** Get key categories, presets, permissions */
export async function getKeyCategories() {
  return api('/api/keys/categories');
}

// ============================================================
// Git
// ============================================================

/** Push to git */
export async function gitPush() {
  return api('/api/git/push', { method: 'POST' });
}

// ============================================================
// Skill Creator
// ============================================================

/** Start a new skill creation session */
export async function startSkillSession() {
  const data = await api('/api/skill-creator/sessions', { method: 'POST' });
  await loadSkillSessions();
  return data;
}

/** Observe a message for skill signals */
export async function observeMessage(sessionId, message, role = 'user') {
  return api(`/api/skill-creator/sessions/${sessionId}/observe`, {
    method: 'POST',
    body: JSON.stringify({ message, role }),
  });
}

/** Theorize a skill from observation */
export async function theorizeSkill(sessionId, task = '', context = '') {
  return api(`/api/skill-creator/sessions/${sessionId}/theorize`, {
    method: 'POST',
    body: JSON.stringify({ task, context }),
  });
}

/** Refine a skill spec */
export async function refineSkill(sessionId, changes = {}) {
  return api(`/api/skill-creator/sessions/${sessionId}/refine`, {
    method: 'POST',
    body: JSON.stringify(changes),
  });
}

/** Approve a skill */
export async function approveSkill(sessionId) {
  const data = await api(`/api/skill-creator/sessions/${sessionId}/approve`, { method: 'POST' });
  await loadSkillSessions();
  return data;
}

/** Reject a skill */
export async function rejectSkill(sessionId) {
  await api(`/api/skill-creator/sessions/${sessionId}/reject`, { method: 'POST' });
  await loadSkillSessions();
}

/** Suggest skill creation */
export async function suggestSkillCreation(messages = []) {
  return api('/api/skill-creator/suggest', {
    method: 'POST',
    body: JSON.stringify({ messages }),
  });
}