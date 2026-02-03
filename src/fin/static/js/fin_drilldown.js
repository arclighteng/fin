/**
 * fin Drilldown Module
 * Unified drilldown system for dashboard numbers.
 */
const finDrilldown = (function() {
    'use strict';

    let currentData = null;
    let currentParams = null;

    function _getDateParams() {
        const urlParams = new URLSearchParams(window.location.search);
        let startDate = urlParams.get('start_date');
        let endDate = urlParams.get('end_date');

        if (!startDate || !endDate) {
            const today = new Date();
            const period = urlParams.get('period') || 'this_month';
            if (period === 'last_month') {
                const lm = today.getMonth() === 0 ? 11 : today.getMonth() - 1;
                const ly = today.getMonth() === 0 ? today.getFullYear() - 1 : today.getFullYear();
                startDate = new Date(ly, lm, 1).toISOString().split('T')[0];
                endDate = new Date(ly, lm + 1, 1).toISOString().split('T')[0];
            } else {
                startDate = new Date(today.getFullYear(), today.getMonth(), 1).toISOString().split('T')[0];
                endDate = new Date(today.getFullYear(), today.getMonth() + 1, 1).toISOString().split('T')[0];
            }
        }

        const accounts = urlParams.get('accounts');
        return { startDate, endDate, accounts };
    }

    function _buildUrl(base, scope, params) {
        let url = `${base}?scope=${encodeURIComponent(scope)}&start_date=${params.startDate}&end_date=${params.endDate}`;
        if (params.accounts) url += `&accounts=${encodeURIComponent(params.accounts)}`;
        return url;
    }

    async function open(scope) {
        const params = _getDateParams();
        currentParams = { scope, ...params };

        // Show loading state
        document.getElementById('drilldownTitle').textContent = 'Loading...';
        document.getElementById('drilldownSummary').innerHTML = '';
        document.getElementById('drilldownTable').innerHTML = '<div class="drilldown-loading">Loading...</div>';
        document.getElementById('drilldownFooter').innerHTML = '';
        document.getElementById('drilldownFilters').style.display = 'none';
        finUI.openModal('drilldownModal');

        try {
            const url = _buildUrl('/api/drilldown', scope, params);
            const resp = await finApi.get(url);
            const data = await resp.json();
            if (resp.ok) {
                currentData = data;
                render(data);
            } else {
                document.getElementById('drilldownTitle').textContent = 'Error';
                document.getElementById('drilldownTable').innerHTML =
                    `<div class="drilldown-empty">${finUI.escapeHtml(data.error || 'Unknown error')}</div>`;
            }
        } catch (err) {
            document.getElementById('drilldownTitle').textContent = 'Error';
            document.getElementById('drilldownTable').innerHTML =
                `<div class="drilldown-empty">${finUI.escapeHtml(err.message)}</div>`;
        }
    }

    function render(data) {
        // Title
        document.getElementById('drilldownTitle').textContent = data.scope_label;

        // Summary strip
        const period = `${data.start_date} to ${data.end_date}`;
        const pills = (data.inclusion_rules || []).map(r => `<span class="drilldown-rule-pill">${finUI.escapeHtml(r)}</span>`).join('');
        document.getElementById('drilldownSummary').innerHTML =
            `<span class="drilldown-period">${finUI.escapeHtml(period)}</span>` +
            pills +
            `<span class="drilldown-total">${finUI.formatCents(data.total_cents)}</span>`;

        // Filters bar (hidden by default, toggle with button)
        const accounts = data.accounts || {};
        const accountKeys = Object.keys(accounts);
        let filtersHtml = '<input type="text" id="drilldownSearch" placeholder="Search merchant..." oninput="finDrilldown.applyFilters()">';
        if (accountKeys.length > 1) {
            filtersHtml += '<select id="drilldownAccountFilter" onchange="finDrilldown.applyFilters()"><option value="">All accounts</option>';
            accountKeys.forEach(id => {
                filtersHtml += `<option value="${finUI.escapeHtml(id)}">${finUI.escapeHtml(accounts[id])}</option>`;
            });
            filtersHtml += '</select>';
        }
        document.getElementById('drilldownFilters').innerHTML = filtersHtml;

        // Render table
        _renderTable(data.transactions);

        // Footer
        _renderFooter(data);
    }

    function _renderTable(transactions) {
        if (!transactions || transactions.length === 0) {
            document.getElementById('drilldownTable').innerHTML = '<div class="drilldown-empty">No transactions</div>';
            return;
        }

        let html = '<table><thead><tr><th>Date</th><th>Merchant</th><th class="text-right">Amount</th><th>Account</th><th>Type</th></tr></thead><tbody>';
        transactions.forEach(t => {
            const amtClass = t.amount_cents > 0 ? 'positive' : '';
            html += `<tr>
                <td>${finUI.formatDate(t.date)}</td>
                <td>${finUI.escapeHtml(t.merchant)}</td>
                <td class="text-right ${amtClass}">${finUI.formatCents(t.amount_cents)}</td>
                <td class="muted">${finUI.escapeHtml(t.account_name || '')}</td>
                <td class="muted">${finUI.escapeHtml(t.type || '')}</td>
            </tr>`;
        });
        html += '</tbody></table>';
        document.getElementById('drilldownTable').innerHTML = html;
    }

    function _renderFooter(data) {
        let parts = [];

        // Show/hide filter toggle
        parts.push(`<button class="drilldown-filter-toggle" onclick="finDrilldown.toggleFilters()">Filter</button>`);
        parts.push(`<span class="drilldown-count">${data.transaction_count} transactions</span>`);

        // Excluded counts
        const excluded = data.excluded || {};
        if (excluded.transfers) {
            parts.push(`<span class="drilldown-excluded-link" onclick="finDrilldown.open('unmatched_transfers')">${excluded.transfers.count} transfers excluded</span>`);
        }
        if (excluded.credit_other) {
            parts.push(`<span class="drilldown-excluded-link" onclick="finDrilldown.open('credit_other')">${excluded.credit_other.count} unclassified credits excluded</span>`);
        }

        // Export button
        parts.push(`<button class="drilldown-export-btn" onclick="finDrilldown.exportCSV()">Export CSV</button>`);

        document.getElementById('drilldownFooter').innerHTML = parts.join('');
    }

    function toggleFilters() {
        const el = document.getElementById('drilldownFilters');
        el.style.display = el.style.display === 'none' ? 'flex' : 'none';
        if (el.style.display === 'flex') {
            const searchEl = document.getElementById('drilldownSearch');
            if (searchEl) searchEl.focus();
        }
    }

    function applyFilters() {
        if (!currentData) return;

        const searchEl = document.getElementById('drilldownSearch');
        const accountEl = document.getElementById('drilldownAccountFilter');
        const search = (searchEl ? searchEl.value : '').toLowerCase();
        const accountId = accountEl ? accountEl.value : '';

        const filtered = currentData.transactions.filter(t => {
            if (search && !(t.merchant || '').toLowerCase().includes(search)) return false;
            if (accountId && t.account_id !== accountId) return false;
            return true;
        });

        _renderTable(filtered);

        // Update count
        const countEl = document.querySelector('.drilldown-count');
        if (countEl) {
            countEl.textContent = `${filtered.length} of ${currentData.transaction_count} transactions`;
        }
    }

    function exportCSV() {
        if (!currentParams) return;
        const url = _buildUrl('/api/drilldown/export', currentParams.scope, currentParams);
        window.open(url, '_blank');
    }

    function close() {
        finUI.closeModal('drilldownModal');
        currentData = null;
        currentParams = null;
    }

    return {
        open,
        close,
        applyFilters,
        toggleFilters,
        exportCSV,
    };
})();
