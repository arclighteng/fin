/**
 * fin API helpers
 * Centralized API communication with auth and CSRF handling
 */
const finApi = (function() {
    'use strict';

    // Auth token injected by template
    const token = typeof API_TOKEN !== 'undefined' ? API_TOKEN : '';
    const csrfToken = typeof CSRF_TOKEN !== 'undefined' ? CSRF_TOKEN : '';
    const readOnly = !token;

    /**
     * Get auth headers for API requests
     */
    function getHeaders(contentType = 'application/json') {
        const headers = {};
        if (contentType) {
            headers['Content-Type'] = contentType;
        }
        if (token) {
            headers['Authorization'] = 'Bearer ' + token;
        }
        return headers;
    }

    /**
     * Get mutation headers (auth + CSRF)
     */
    function getMutationHeaders(contentType = 'application/json') {
        const headers = getHeaders(contentType);
        if (csrfToken) {
            headers['X-CSRF-Token'] = csrfToken;
        }
        return headers;
    }

    /**
     * Check if in read-only mode
     */
    function isReadOnly() {
        return readOnly;
    }

    /**
     * GET request
     */
    async function get(url) {
        const response = await fetch(url, {
            method: 'GET',
            headers: getHeaders(null),
        });
        return response;
    }

    /**
     * POST JSON request
     */
    async function postJSON(url, data) {
        if (readOnly) {
            throw new Error('Read-only mode: Set FIN_AUTH_TOKEN to enable changes');
        }
        const response = await fetch(url, {
            method: 'POST',
            headers: getMutationHeaders('application/json'),
            body: JSON.stringify(data),
        });
        return response;
    }

    /**
     * POST form data
     */
    async function postForm(url, formData) {
        if (readOnly) {
            throw new Error('Read-only mode: Set FIN_AUTH_TOKEN to enable changes');
        }
        const response = await fetch(url, {
            method: 'POST',
            headers: getMutationHeaders(null),
            body: formData,
        });
        return response;
    }

    /**
     * DELETE request
     */
    async function deleteRequest(url) {
        if (readOnly) {
            throw new Error('Read-only mode: Set FIN_AUTH_TOKEN to enable changes');
        }
        const response = await fetch(url, {
            method: 'DELETE',
            headers: getMutationHeaders(null),
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
function getAuthHeaders() {
    return finApi.getMutationHeaders();
}
