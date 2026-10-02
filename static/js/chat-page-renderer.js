/**
 * JSON-driven shell for /chat: loads chat-page.{variant}.json and mounts DOM
 * with stable ids/classes expected by MostyChat (same structure as legacy chat.html).
 */
(function (global) {
  'use strict';

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function getSafeOrigin() {
    if (typeof location === 'undefined') return '';
    try {
      if (location.protocol === 'http:' || location.protocol === 'https:') {
        return location.origin;
      }
    } catch (_) {
      /* invalid location in special document contexts */
    }
    return '';
  }

  /**
   * @param {string} origin
   * @returns {Promise<object>}
   */
  async function fetchBootstrap(origin) {
    var fallback = {
      success: false,
      env: origin
        ? {
            origin: origin,
            apiBaseUrl: origin + '/api/v1/',
            staticBaseUrl: origin + '/static/',
          }
        : null,
      features: {},
      limits: { maxInputChars: 8000 },
    };
    if (!origin) {
      if (typeof global !== 'undefined') global.__MOSTY_BOOTSTRAP__ = fallback;
      return fallback;
    }
    try {
      var url = new URL('/api/v1/chat/bootstrap', origin).href;
      var r = await fetch(url);
      if (!r.ok) throw new Error('bootstrap ' + r.status);
      var data = await r.json();
      var merged = {
        success: true,
        env: data.env || fallback.env,
        features: data.features || {},
        limits: data.limits || fallback.limits,
      };
      if (typeof global !== 'undefined') global.__MOSTY_BOOTSTRAP__ = merged;
      return merged;
    } catch (_) {
      if (typeof global !== 'undefined') global.__MOSTY_BOOTSTRAP__ = fallback;
      return fallback;
    }
  }

  function applyTheme(schema) {
    const vars = schema && schema.theme && schema.theme.cssVariables;
    if (!vars || typeof vars !== 'object') return;
    const root = document.documentElement;
    Object.keys(vars).forEach(function (k) {
      root.style.setProperty(k, vars[k]);
    });
  }

  function logoMarkHtml() {
    const g = typeof global !== 'undefined' ? global : {};
    if (typeof g.MOSTY_LOGO_MARK_SVG === 'string' && g.MOSTY_LOGO_MARK_SVG.length) {
      return g.MOSTY_LOGO_MARK_SVG;
    }
    return '';
  }

  function langOptionsHtml(languages) {
    if (!Array.isArray(languages)) return '';
    return languages
      .map(function (L) {
        if (typeof L === 'string' && L.indexOf(':') !== -1) {
          const parts = L.split(':');
          return '<option value="' + esc(parts[0]) + '">' + esc(parts.slice(1).join(':')) + '</option>';
        }
        if (L && typeof L === 'object' && L.value != null) {
          return '<option value="' + esc(L.value) + '">' + esc(L.label || L.value) + '</option>';
        }
        return '';
      })
      .join('');
  }

  function renderShell(root, schema) {
    const p = schema.page || {};
    const sb = schema.sidebar || {};
    const hdr = schema.header || {};
    const comp = schema.composer || {};
    const set = schema.settings || {};

    root.innerHTML =
      '<div class="app">' +
      '<div class="sidebar-backdrop" id="sidebarBackdrop"></div>' +
      '<aside class="sidebar" id="sidebar">' +
      '<div class="sidebar-head">' +
      '<div class="brand">' +
      '<div class="brand-mark">' +
      logoMarkHtml() +
      '</div>' +
      '<span class="brand-name">' +
      esc(p.brandName || p.title || 'Chat') +
      '</span>' +
      '</div>' +
      '<button class="new-chat-btn" id="newChatBtn">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>' +
      esc(sb.newChat || 'New') +
      '</button>' +
      '</div>' +
      '<div class="sidebar-list" id="sidebarList"></div>' +
      '<div class="sidebar-foot">' +
      '<button class="settings-btn" id="settingsBtn">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>' +
      esc(sb.settings || 'Settings') +
      '</button>' +
      '<div style="display:flex;display:none;flex-direction:column;align-items:flex-start;gap:8px;width:100%">' +
      '<a class="settings-btn" href="/outfit-builder" style="text-decoration:none;display:inline-flex;width:100%;justify-content:center">' +
      'Outfit Builder' +
      '</a>' +
      '<span class="version-tag">' +
      esc(p.version || '') +
      '</span>' +
      '</div>' +
      '</div>' +
      '</aside>' +
      '<main class="chat-main">' +
      '<header class="chat-header">' +
      '<button class="sidebar-toggle" id="sidebarToggle" title="' +
      esc(hdr.sidebarToggleTitle || 'Toggle sidebar') +
      '">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/></svg>' +
      '</button>' +
      '<div class="header-center">' +
      '<h1>' +
      esc(p.title || 'Chat') +
      '</h1>' +
      '<span class="model-tag">' +
      esc(p.modelTag || '') +
      '</span>' +
      '</div>' +
      '<div class="header-actions">' +
      '<button class="icon-btn" id="exportChatBtn" type="button" title="' +
      esc(hdr.exportTitle || 'Export conversation (Markdown)') +
      '" aria-label="' +
      esc(hdr.exportTitle || 'Export conversation') +
      '">' +
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>' +
      '</button>' +
      '<button class="icon-btn" id="clearChatBtn" title="' +
      esc(hdr.clearTitle || 'Clear chat') +
      '">' +
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>' +
      '</button>' +
      '</div>' +
      '</header>' +
      '<div class="filter-bar" id="filterBar" hidden></div>' +
      '<div class="chat-scroll" id="chatScroll">' +
      '<div class="chat-inner" id="chatInner"></div>' +
      '</div>' +
      '<div class="chat-input-area">' +
      '<div class="input-wrapper">' +
      '<div class="input-glow"></div>' +
      '<div class="input-box">' +
      '<input type="file" id="attachmentInput" accept="image/*,application/pdf" multiple style="display:none" />' +
      '<button type="button" class="attach-btn" id="attachBtn" title="Attach file" aria-label="Attach file">' +
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>' +
      '</button>' +
      '<textarea id="chatInput" rows="1" aria-label="' +
      esc(comp.ariaLabel || 'Message') +
      '" placeholder="' +
      esc(comp.placeholder || '') +
      '"></textarea>' +
      '<button type="button" class="send-btn stop-btn" id="stopBtn" title="Stop" aria-label="Stop generating" style="display:none">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>' +
      '</button>' +
      '<button class="send-btn" id="sendBtn" title="Send">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>' +
      '</button>' +
      '</div>' +
      '<p class="input-hint">' +
      esc(comp.hint || '') +
      '</p>' +
      '</div>' +
      '</div>' +
      '</main>' +
      '</div>' +
      '<div class="modal-overlay" id="settingsModal">' +
      '<div class="modal-card">' +
      '<div class="modal-head">' +
      '<h3>' +
      esc(set.title || 'Settings') +
      '</h3>' +
      '<button class="modal-close" id="settingsClose">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>' +
      '</button>' +
      '</div>' +
      '<div class="field" id="apiKeyFieldWrap">' +
      '<label class="field-label">' +
      esc(set.apiKeyLabel || 'API Key') +
      '</label>' +
      '<input class="field-input" type="password" id="apiKeyInput" placeholder="' +
      esc(set.apiKeyPlaceholder || '') +
      '">' +
      '<p class="field-hint">' +
      esc(set.apiKeyHint || '') +
      '</p>' +
      '</div>' +
      '<div class="field">' +
      '<label class="field-label">' +
      esc(set.languageLabel || 'Language') +
      '</label>' +
      '<select class="field-select" id="langSelect">' +
      langOptionsHtml(set.languages) +
      '</select>' +
      '</div>' +
      '<button class="btn-save" id="saveSettingsBtn">' +
      esc(set.saveButton || 'Save') +
      '</button>' +
      '</div>' +
      '</div>' +
      '<div class="modal-overlay" id="shareModal">' +
      '<div class="modal-card share-modal-card">' +
      '<div class="modal-head share-modal-head">' +
      '<div>' +
      '<h3>Share conversation</h3>' +
      '<p class="share-head-sub">Create and manage a public link for this thread.</p>' +
      '</div>' +
      '<button class="modal-close" id="shareClose">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>' +
      '</button>' +
      '</div>' +
      '<div class="share-state-card" id="shareStateCard">' +
      '<span class="share-state-pill" id="shareStatePill">Not shared</span>' +
      '<div class="share-state-title" id="shareStateTitle">This conversation is private</div>' +
      '<div class="share-state-desc" id="shareStatusText">Create a public link to let others open this conversation.</div>' +
      '</div>' +
      '<div class="field share-url-field">' +
      '<label class="field-label">Share link</label>' +
      '<div class="share-url-row">' +
      '<input class="field-input share-url-input" type="text" id="shareUrlInput" readonly placeholder="No active link yet">' +
      '<button class="settings-btn share-inline-copy" id="copyShareBtn" type="button">Copy</button>' +
      '</div>' +
      '<p class="field-hint" id="shareReplaceHint">If you create a new link, the previous one stops working.</p>' +
      '<p class="field-hint share-error-text" id="shareErrorText" style="display:none"></p>' +
      '</div>' +
      '<div class="share-actions-primary">' +
      '<button class="btn-save" id="createShareBtn">Create link</button>' +
      '<button class="settings-btn" id="shareCloseSecondaryBtn" type="button">Close</button>' +
      '</div>' +
      '<div class="share-danger-zone">' +
      '<div class="share-danger-copy">Turn off public access for the current link.</div>' +
      '<button class="settings-btn share-danger-btn" id="revokeShareBtn" type="button">Disable sharing</button>' +
      '</div>' +
      '</div>' +
      '</div>' +
      '<div class="modal-overlay" id="onboardingModal" role="presentation">' +
      '<div class="modal-card onboarding-card" role="dialog" aria-modal="true" aria-labelledby="onboardingTitle" tabindex="-1">' +
      '<div class="modal-head">' +
      '<h3 id="onboardingTitle">' +
      esc((schema.onboarding && schema.onboarding.title) || '') +
      '</h3>' +
      '<button type="button" class="modal-close" id="onboardingClose" aria-label="Close">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>' +
      '</button>' +
      '</div>' +
      '<div class="onboarding-body" id="onboardingBody"></div>' +
      '<button class="btn-save" id="onboardingDismiss" type="button">' +
      esc((schema.onboarding && schema.onboarding.dismissButton) || 'OK') +
      '</button>' +
      '</div>' +
      '</div>' +
      '<div class="toast" id="toast"></div>';
  }

  /**
   * @param {HTMLElement} root
   * @param {{ schema?: object, schemaUrl?: string, variant?: string }} [options]
   * @returns {Promise<object>}
   */
  async function renderChatPage(root, options) {
    options = options || {};
    const origin = getSafeOrigin();
    await fetchBootstrap(origin);
    let schema = options.schema;
    if (!schema) {
      const variant =
        options.variant ||
        (typeof location !== 'undefined' && new URLSearchParams(location.search).get('variant')) ||
        'production';
      const url =
        options.schemaUrl ||
        (origin
          ? new URL('/static/schemas/chat-page.' + variant + '.json', origin).href
          : '/static/schemas/chat-page.' + variant + '.json');
      const r = await fetch(url);
      if (!r.ok) throw new Error('Chat page schema failed: ' + r.status);
      schema = await r.json();
    }
    applyTheme(schema);
    renderShell(root, schema);
    if (schema.page && schema.page.documentTitle && typeof document !== 'undefined') {
      document.title = schema.page.documentTitle;
    }
    return schema;
  }

  global.renderChatPage = renderChatPage;
})(typeof window !== 'undefined' ? window : globalThis);
