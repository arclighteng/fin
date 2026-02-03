/**
 * fin UI helpers
 * Toast notifications, modals, confirmations
 */
const finUI = (function() {
    'use strict';

    /**
     * Show a simple toast notification
     */
    function toast(message, duration = 3000) {
        const toastEl = document.getElementById('toast');
        if (!toastEl) return;

        toastEl.textContent = message;
        toastEl.classList.remove('with-action');
        toastEl.classList.add('show');
        setTimeout(() => toastEl.classList.remove('show'), duration);
    }

    /**
     * Show toast with action button
     */
    function toastWithAction(message, actionLabel, actionCallback, timeout = 15000) {
        const toastEl = document.getElementById('toast');
        if (!toastEl) return;

        toastEl.innerHTML = `
            <span class="toast-message">${escapeHtml(message)}</span>
            <button class="toast-action" data-action="primary">${escapeHtml(actionLabel)}</button>
            <button class="toast-dismiss" data-action="dismiss">&times;</button>
        `;

        // Handle action button
        toastEl.querySelector('[data-action="primary"]').onclick = () => {
            toastEl.classList.remove('show', 'with-action');
            if (actionCallback) actionCallback();
        };

        // Handle dismiss
        toastEl.querySelector('[data-action="dismiss"]').onclick = () => {
            toastEl.classList.remove('show', 'with-action');
        };

        toastEl.classList.add('show', 'with-action');

        // Auto-dismiss after timeout
        setTimeout(() => {
            toastEl.classList.remove('show', 'with-action');
        }, timeout);
    }

    /**
     * Show confirmation dialog
     */
    function confirm(message) {
        return window.confirm(message);
    }

    /**
     * Open a modal by ID
     */
    function openModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.style.display = 'flex';
        }
    }

    /**
     * Close a modal by ID
     */
    function closeModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.style.display = 'none';
        }
    }

    /**
     * Escape HTML to prevent XSS
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Format cents as USD
     */
    function formatCents(cents) {
        const dollars = Math.abs(cents) / 100;
        const formatted = dollars.toLocaleString('en-US', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
        return (cents < 0 ? '-' : '') + '$' + formatted;
    }

    /**
     * Format date for display
     */
    function formatDate(dateStr) {
        const date = new Date(dateStr + 'T00:00:00');
        return date.toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric'
        });
    }

    // Public API
    return {
        toast,
        toastWithAction,
        confirm,
        openModal,
        closeModal,
        escapeHtml,
        formatCents,
        formatDate,
    };
})();

// Legacy compatibility - keep showToast available globally
function showToast(message, duration) {
    finUI.toast(message, duration);
}

function showToastWithAction(message, actionLabel, actionCallback) {
    finUI.toastWithAction(message, actionLabel, actionCallback);
}
