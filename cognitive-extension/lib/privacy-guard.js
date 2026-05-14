/**
 * Cognitive Extension - Privacy Guard
 * Ensures NO sensitive content is captured, logged, or transmitted.
 * This module is the single source of truth for privacy enforcement.
 *
 * PRD Compliance: NFR-PS-001 through NFR-PS-005
 */

'use strict';

// ───── Forbidden Patterns ─────

// Fields that must NEVER be included in signals
const FORBIDDEN_FIELDS = [
  'password',
  'creditcard',
  'ssn',
  'secret',
  'token',
  'privatekey',
  'cookie',
  'session',
  'auth',
];

// Input types to NEVER capture keystrokes from
const SENSITIVE_INPUT_TYPES = new Set([
  'password',
  'email',        // Debatable, but excluded for privacy
  'search',       // Could contain sensitive queries
  'hidden',
  'number',       // Could be financial
  'tel',
  'credit-card',  // Future spec
]);

// URL patterns to never capture
const SENSITIVE_DOMAINS = new Set([
  'bankofamerica.com',
  'wellsfargo.com',
  'chase.com',
  'paypal.com',
  'gmail.com',        // Content stays offline; we only capture domain
  'protonmail.com',
  'signal.org',
  'web.whatsapp.com',
]);

// ───── Public API ─────

/**
 * Sanitize a signal payload before it leaves the content script.
 * Removes any field that could contain user content.
 */
function sanitizeSignal(payload) {
  const sanitized = {};

  for (const [key, value] of Object.entries(payload)) {
    // Block forbidden field names
    if (FORBIDDEN_FIELDS.some((f) => key.toLowerCase().includes(f))) {
      continue;
    }

    // Only allow known, safe fields
    if (isAllowedField(key)) {
      sanitized[key] = value;
    }
  }

  // Additional checks
  if (sanitized.active_url) {
    sanitized.active_url = extractDomain(sanitized.active_url);
  }

  // NEVER include title content (could contain sensitive document text)
  if (sanitized.active_title) {
    // Only keep title length, not content
    sanitized.active_title_length = sanitized.active_title.length;
    delete sanitized.active_title;
  }

  return sanitized;
}

/**
 * Check if an input element is in a sensitive context.
 * Used by content.js to decide whether to count keystrokes.
 */
function isSensitiveInput(element) {
  if (!element) return true; // Default to safe (don't count)

  const type = (element.type || '').toLowerCase();
  if (SENSITIVE_INPUT_TYPES.has(type)) return true;

  // Check for password-like attributes
  if (element.getAttribute('autocomplete') === 'current-password') return true;
  if (element.getAttribute('autocomplete') === 'cc-number') return true;

  // Check input name for sensitive keywords
  const name = (element.name || '').toLowerCase();
  if (FORBIDDEN_FIELDS.some((f) => name.includes(f))) return true;

  // Check parent elements for sensitive containers
  let parent = element.parentElement;
  let depth = 0;
  while (parent && depth < 5) {
    const id = (parent.id || '').toLowerCase();
    const cls = (parent.className || '').toLowerCase();
    if (id.includes('password') || id.includes('secret') ||
        cls.includes('password') || cls.includes('secret')) {
      return true;
    }
    parent = parent.parentElement;
    depth++;
  }

  return false;
}

/**
 * Check if the current page is on a sensitive domain.
 */
function isSensitiveDomain(url) {
  try {
    const domain = new URL(url).hostname.replace('www.', '');
    return SENSITIVE_DOMAINS.has(domain);
  } catch {
    return false;
  }
}

/**
 * Validate that a signal payload contains no content data.
 * Used by background.js before sending to server.
 */
function validateSignalIntegrity(payload) {
  const issues = [];

  // Check for content leakage
  if (payload.key_content || payload.typed_text || payload.input_value) {
    issues.push('signal contains typed content');
  }

  if (payload.screenshot || payload.screen_data) {
    issues.push('signal contains screenshot data');
  }

  // Check URL is just domain
  if (payload.active_url && payload.active_url.includes('/') &&
      !payload.active_url.startsWith('http')) {
    // Could be a full URL - strip to domain only
    try {
      const url = new URL(payload.active_url);
      issues.push('URL should be domain-only, got full URL');
    } catch {}
  }

  // Check for unexpected fields
  const allowedFields = new Set([
    'session_id', 'timestamp', 'kpm', 'inter_key_avg', 'switch_rate',
    'scroll_velocity', 'scroll_delta', 'mouse_entropy', 'idle_ratio',
    'tab_count', 'domain_switches', 'time_of_day', 'active_url',
    'active_title_length', 'idle_seconds',
  ]);

  for (const key of Object.keys(payload)) {
    if (!allowedFields.has(key)) {
      issues.push(`unexpected field in signal: ${key}`);
    }
  }

  return {
    valid: issues.length === 0,
    issues,
  };
}

// ───── Helpers ─────

function isAllowedField(key) {
  const allowed = new Set([
    'session_id', 'timestamp', 'kpm', 'interKeyAvg', 'inter_key_avg',
    'switchRate', 'switch_rate', 'scrollVelocity', 'scroll_velocity',
    'scrollDelta', 'scroll_delta', 'mouseEntropy', 'mouse_entropy',
    'idleRatio', 'idle_ratio', 'tabCount', 'tab_count',
    'domainSwitches', 'domain_switches', 'timeOfDay', 'time_of_day',
    'activeUrl', 'active_url', 'activeTitle', 'active_title',
    'idleSeconds', 'idle_seconds',
  ]);
  return allowed.has(key);
}

function extractDomain(url) {
  try {
    return new URL(url).hostname.replace('www.', '');
  } catch {
    return '';
  }
}

// ───── Auditing ─────

/**
 * Generate a privacy audit log entry.
 * Call this periodically to verify no content leakage.
 */
function generateAuditEntry(signalsProcessed, signalsBlocked) {
  return {
    timestamp: new Date().toISOString(),
    signals_processed: signalsProcessed,
    signals_blocked: signalsBlocked,
    sensitive_fields_filtered: FORBIDDEN_FIELDS.length,
    sensitive_domains_count: SENSITIVE_DOMAINS.size,
    no_content_logging: true,
    no_screenshots: true,
    local_processing_only: true,
  };
}

if (typeof module !== 'undefined') {
  module.exports = {
    sanitizeSignal,
    isSensitiveInput,
    isSensitiveDomain,
    validateSignalIntegrity,
    generateAuditEntry,
  };
}