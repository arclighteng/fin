/**
 * fin API helpers
 * Centralized API communication with CSRF handling.
 *
 * Authentication is now via HttpOnly fin_session cookie (set by /auth/session).
 * The browser sends the cookie automatically with every same-origin request.
 * No token is stored in JavaScript — const API_TOKEN has been removed.
 */
const finApi = (function() {
    'use strict';

    // CSRF token injected by template (still needed for mutation endpoints)
    const csrfToken = typeof CSRF_TOKEN !== 'undefined' ? CSRF_TOKEN : '';
    // FIN_AUTH_ENABLED is injected by template (true = auth active, false = auth disabled)
    const authEnabled = typeof FIN_AUTH_ENABLED !== 'undefined' ? FIN_AUTH_ENABLED : true;
    const readOnly = !authEnabled;

    /**
     * Get standard headers for GET requests.
     * No Authorization header — cookie is sent automatically by the browser.
     */
    function getHeaders(contentType = 'application/json') {
        const headers = {};
        if (contentType) {
            headers['Content-Type'] = contentType;
        }
        return headers;
    }

    /**
     * Get mutation headers (CSRF only — cookie handles auth).
     */
    function getMutationHeaders(contentType = 'application/json') {
        const headers = getHeaders(contentType);
        if (csrfToken) {
            headers['X-CSRF-Token'] = csrfToken;
        }
        return headers;
    }

    /**
     * Check if in read-only mode (auth disabled server-side).
     */
    function isReadOnly() {
        return readOnly;
    }

    /**
     * GET request — cookie sent automatically by browser.
     */
    async function get(url) {
        const response = await fetch(url, {
            method: 'GET',
            credentials: 'same-origin',
        });
        return response;
    }

    /**
     * POST JSON request
     */
    async function postJSON(url, data) {
        if (readOnly) {
            throw new Error('Read-only mode: auth is disabled server-side');
        }
        const response = await fetch(url, {
            method: 'POST',
            headers: getMutationHeaders('application/json'),
            body: JSON.stringify(data),
            credentials: 'same-origin',
        });
        return response;
    }

    /**
     * POST form data
     */
    async function postForm(url, formData) {
        if (readOnly) {
            throw new Error('Read-only mode: auth is disabled server-side');
        }
        const response = await fetch(url, {
            method: 'POST',
            headers: getMutationHeaders(null),
            body: formData,
            credentials: 'same-origin',
        });
        return response;
    }

    /**
     * DELETE request
     */
    async function deleteRequest(url) {
        if (readOnly) {
            throw new Error('Read-only mode: auth is disabled server-side');
        }
        const response = await fetch(url, {
            method: 'DELETE',
            headers: getMutationHeaders(null),
            credentials: 'same-origin',
        });
        return response;
    }

    // Public API
    return {
        getHeaders,
        getMutationHeaders,
        isReadOnly,
        get,
        postJSON,
        postForm,
        delete: deleteRequest,
    };
})();

// Legacy compatibility - keep getAuthHeaders available globally
// Now returns only CSRF headers (no Authorization: Bearer — auth is via cookie)
function getAuthHeaders() {
    return finApi.getMutationHeaders();
}
