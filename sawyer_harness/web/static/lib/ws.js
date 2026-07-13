/**
 * Sawyer Agent — WebSocket Client with Auto-Reconnect
 * 
 * Handles streaming chat responses and real-time state updates.
 * Reconnects automatically on disconnect with exponential backoff.
 * Dispatches events to Preact Signals in state.js.
 * 
 * Falls back gracefully when WebSocket is not available.
 */

const WS_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/chat`;

class SawyerWS {
  constructor() {
    this.ws = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 50;
    this.baseDelay = 1000;
    this.maxDelay = 30000;
    this.shouldReconnect = true;
    this.messageQueue = [];
    this.handlers = new Map();
  }

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      return;
    }

    try {
      this.ws = new WebSocket(WS_URL);
      
      this.ws.onopen = () => {
        console.log('[WS] Connected');
        this.reconnectAttempts = 0;
        // Flush queued messages
        while (this.messageQueue.length > 0) {
          const msg = this.messageQueue.shift();
          this.ws.send(JSON.stringify(msg));
        }
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this._dispatch(data);
        } catch (e) {
          console.error('[WS] Parse error:', e);
        }
      };

      this.ws.onclose = (event) => {
        console.log(`[WS] Disconnected (code: ${event.code})`);
        if (this.shouldReconnect) {
          this._scheduleReconnect();
        }
      };

      this.ws.onerror = (event) => {
        console.error('[WS] Error:', event);
      };
    } catch (e) {
      console.error('[WS] Connection failed:', e);
      this._scheduleReconnect();
    }
  }

  send(message, sessionId) {
    const payload = {
      type: 'chat',
      message: message,
      session_id: sessionId || '',
    };
    
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    } else {
      this.messageQueue.push(payload);
      this.connect();
    }
  }

  disconnect() {
    this.shouldReconnect = false;
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
  }

  on(type, callback) {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, []);
    }
    this.handlers.get(type).push(callback);
  }

  off(type, callback) {
    const handlers = this.handlers.get(type);
    if (handlers) {
      const idx = handlers.indexOf(callback);
      if (idx !== -1) handlers.splice(idx, 1);
    }
  }

  _dispatch(data) {
    const type = data.type || 'unknown';
    
    // Delegate to state.js functions if available
    // (lazy import to avoid circular dependency)
    
    switch (type) {
      case 'chat_chunk':
        this._handleChatChunk(data);
        break;
      case 'chat_done':
        this._callHandlers('chat_done', data);
        // Refresh context stats after response
        import('./state.js').then(m => m.loadContextStats()).catch(() => {});
        break;
      case 'goal_update':
        import('./state.js').then(m => m.loadGoals()).catch(() => {});
        this._callHandlers('goal_update', data);
        break;
      case 'context_update':
        // Update context stats from push
        import('./state.js').then(m => {
          m.state.contextStats.value = data.stats;
        }).catch(() => {});
        this._callHandlers('context_update', data);
        break;
      case 'approval_request':
        import('./state.js').then(m => m.addToast(`Approval needed: ${data.tool} (${data.risk})`, 'warning')).catch(() => {});
        this._callHandlers('approval_request', data);
        break;
      case 'notification':
        import('./state.js').then(m => m.addToast(data.message || 'Notification', data.level || 'info')).catch(() => {});
        this._callHandlers('notification', data);
        break;
      case 'file_change':
        this._callHandlers('file_change', data);
        break;
      case 'error':
        import('./state.js').then(m => m.addToast(data.message || 'Unknown error', 'danger')).catch(() => {});
        this._callHandlers('error', data);
        break;
      default:
        this._callHandlers(type, data);
    }
  }

  _handleChatChunk(data) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    let streamMsg = document.getElementById('streaming-msg');
    if (!streamMsg) {
      streamMsg = document.createElement('div');
      streamMsg.id = 'streaming-msg';
      streamMsg.className = 'msg assistant';
      container.appendChild(streamMsg);
    }

    if (data.content) {
      streamMsg.innerHTML += _formatResponse(data.content);
    }

    container.scrollTop = container.scrollHeight;
  }

  _callHandlers(type, data) {
    const handlers = this.handlers.get(type) || [];
    for (const cb of handlers) {
      try { cb(data); } catch (e) { console.error(`[WS] Handler error for ${type}:`, e); }
    }
  }

  _scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('[WS] Max reconnect attempts reached');
      import('./state.js').then(m => m.addToast('Connection lost. Please refresh.', 'danger')).catch(() => {});
      return;
    }

    const delay = Math.min(
      this.baseDelay * Math.pow(2, this.reconnectAttempts),
      this.maxDelay
    );
    this.reconnectAttempts++;

    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
    
    setTimeout(() => {
      if (this.shouldReconnect) {
        this.connect();
      }
    }, delay);
  }
}

function _formatResponse(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="lang-$1">$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
}

// Singleton instance
export const ws = new SawyerWS();

// Auto-connect when module loads (non-blocking)
ws.connect();