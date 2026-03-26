/**
 * fin API helpers
 * Centralized API communication.
 */
const finApi = (function() {
    'use strict';

    function getHeaders(contentType = 'application/json') {
        const headers = {};
        if (contentType) {
            headers['Content-Type'] = contentType;
        }
        return headers;
    }

    async function get(url) {
        return fetch(url, { method: 'GET' });
    }

    async function postJSON(url, data) {
        return fetch(url, {
            method: 'POST',
            headers: getHeaders('application/json'),
            body: JSON.stringify(data),
        });
    }

    async function postForm(url, formData) {
        return fetch(url, { method: 'POST', body: formData });
    }

    async function deleteRequest(url) {
        return fetch(url, { method: 'DELETE' });
    }

    return { get, postJSON, postForm, delete: deleteRequest };
})();
