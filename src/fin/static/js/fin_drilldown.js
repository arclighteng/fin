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

        // Show resolution controls if available
        if (data.resolution_context && data.resolution_context.allows_resolution) {
            _renderResolutionControls(data.resolution_context, data.transactions);
        } else {
            // Clear resolution controls if they exist
            const controlsEl = document.getElementById('drilldownResolutionControls');
            if (controlsEl) controlsEl.remove();
        }

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
        _renderTable(data.transactions, data.resolution_context, data.categories);

        // Footer
        _renderFooter(data);
    }

    // Scopes that span multiple categories — show a Category column
    const MULTI_CAT_SCOPES = new Set(['spend', 'income', 'net', 'recurring', 'discretionary']);

    function _renderTable(transactions, resolutionContext, categories) {
        if (!transactions || transactions.length === 0) {
            document.getElementById('drilldownTable').innerHTML = '<div class="drilldown-empty">No transactions</div>';
            return;
        }

        const showResolution = resolutionContext && resolutionContext.allows_resolution;
        const showCategory = !showResolution && currentData && MULTI_CAT_SCOPES.has(currentData.scope);

        let html = '<table><thead><tr><th>Date</th><th>Merchant</th><th class="text-right">Amount</th><th>Account</th>';
        if (showCategory) html += '<th>Category</th>';
        if (!showResolution) html += '<th>Type</th>';
        if (showResolution) html += '<th>Classify As</th>';
        html += '</tr></thead><tbody>';

        transactions.forEach(t => {
            const amtClass = t.amount_cents > 0 ? 'positive' : '';

            html += `<tr class="txn-row" data-fp="${finUI.escapeHtml(t.fingerprint)}">
                <td>${finUI.formatDate(t.date)}</td>
                <td>${finUI.escapeHtml(t.merchant)}</td>
                <td class="text-right ${amtClass}">${finUI.formatCents(t.amount_cents)}</td>
                <td class="muted">${finUI.escapeHtml(t.account_name || '')}</td>`;

            if (showCategory) {
                html += _renderCategoryCell(t, categories);
            }

            if (!showResolution) {
                html += `<td class="muted">${finUI.escapeHtml(t.type || '')}</td>`;
            }

            if (showResolution) {
                const existingOverride = t.override_type || '';
                html += '<td onclick="event.stopPropagation()"><select id="resolution-' + finUI.escapeHtml(t.fingerprint) + '" class="resolution-select" data-fp="' + finUI.escapeHtml(t.fingerprint) + '" onchange="finDrilldown.autoSaveClassification(this)">';
                html += '<option value="">- Select -</option>';
                resolutionContext.resolution_options.forEach(opt => {
                    const selected = opt.value === existingOverride ? 'selected' : '';
                    html += `<option value="${finUI.escapeHtml(opt.value)}" ${selected} title="${finUI.escapeHtml(opt.description)}">${finUI.escapeHtml(opt.label)}</option>`;
                });
                html += '</select></td>';
            }

            html += '</tr>';
        });
        html += '</tbody></table>';

        document.getElementById('drilldownTable').innerHTML = html;
    }

    function _renderCategoryCell(t, categories) {
        // Build label text
        const catLabel = t.category_icon && t.category
            ? `${finUI.escapeHtml(t.category_icon)} ${finUI.escapeHtml(t.category)}`
            : (t.category ? finUI.escapeHtml(t.category) : '<span class="muted">—</span>');

        const overrideBadge = t.category_is_override
            ? ' <span class="cat-override-badge" title="Category manually set">*</span>' : '';

        const merchantEsc = finUI.escapeHtml(t.merchant);
        const catIdEsc = finUI.escapeHtml(t.category_id || '');

        // Clickable label — clicking turns into a select dropdown
        return `<td class="cat-cell" data-merchant="${merchantEsc}" data-cat-id="${catIdEsc}" onclick="finDrilldown.openCategorySelect(this, event)">
            <span class="cat-label" title="Click to override category for all ${merchantEsc} transactions">${catLabel}${overrideBadge}</span>
        </td>`;
    }

    function openCategorySelect(tdEl, evt) {
        evt.stopPropagation();

        // If already open, do nothing
        if (tdEl.querySelector('select.cat-override-select')) return;

        const merchant = tdEl.dataset.merchant;
        const currentCatId = tdEl.dataset.catId;
        const categories = (currentData && currentData.categories) || [];

        let selectHtml = `<select class="cat-override-select" data-merchant="${finUI.escapeHtml(merchant)}"
            onchange="finDrilldown.saveCategoryOverride(this)"
            onblur="finDrilldown.closeCategorySelect(this.closest('td'))"
            onclick="event.stopPropagation()">`;
        selectHtml += `<option value="auto"${!currentCatId ? ' selected' : ''}>Auto (no override)</option>`;
        categories.forEach(cat => {
            const sel = cat.id === currentCatId ? ' selected' : '';
            selectHtml += `<option value="${finUI.escapeHtml(cat.id)}"${sel}>${finUI.escapeHtml(cat.icon || '')} ${finUI.escapeHtml(cat.name)}</option>`;
        });
        selectHtml += '</select>';

        // Hide label, show select
        const labelEl = tdEl.querySelector('.cat-label');
        if (labelEl) labelEl.style.display = 'none';
        tdEl.insertAdjacentHTML('beforeend', selectHtml);
        const selectEl = tdEl.querySelector('select.cat-override-select');
        if (selectEl) selectEl.focus();
    }

    function closeCategorySelect(tdEl) {
        if (!tdEl) return;
        const selectEl = tdEl.querySelector('select.cat-override-select');
        const labelEl = tdEl.querySelector('.cat-label');
        // Small delay so change event fires first
        setTimeout(() => {
            if (selectEl && tdEl.contains(selectEl)) selectEl.remove();
            if (labelEl) labelEl.style.display = '';
        }, 150);
    }

    async function saveCategoryOverride(selectEl) {
        const merchant = selectEl.dataset.merchant;
        const categoryId = selectEl.value;
        if (!merchant) return;

        selectEl.disabled = true;

        try {
            const resp = await finApi.postJSON('/api/category-override', {
                merchant: merchant,
                category_id: categoryId,
            });

            if (resp.ok) {
                const td = selectEl.closest('td');
                // Update data attr and label
                if (td) {
                    const newCatId = categoryId === 'auto' ? '' : categoryId;
                    td.dataset.catId = newCatId;

                    const categories = (currentData && currentData.categories) || [];
                    let newLabel = '<span class="muted">—</span>';
                    let isOverride = categoryId !== 'auto';
                    if (categoryId !== 'auto') {
                        const cat = categories.find(c => c.id === categoryId);
                        if (cat) newLabel = `${finUI.escapeHtml(cat.icon || '')} ${finUI.escapeHtml(cat.name)}`;
                    }
                    const overrideBadge = isOverride ? ' <span class="cat-override-badge" title="Category manually set">*</span>' : '';
                    const labelEl = td.querySelector('.cat-label');
                    if (labelEl) {
                        labelEl.innerHTML = newLabel + overrideBadge;
                        labelEl.style.display = '';
                    }
                    selectEl.remove();
                }

                // Show undo toast for 4 seconds
                const prevCatId = td ? td.dataset.catId : '';
                finUI.toastWithAction(
                    `Category override applied to all "${merchant}" transactions`,
                    'Undo',
                    async () => {
                        await finApi.postJSON('/api/category-override', { merchant, category_id: prevCatId || 'auto' });
                        // Refresh drilldown data
                        if (currentParams) await open(currentParams.scope);
                    },
                    4000
                );
            } else {
                const data = await resp.json();
                finUI.toast(`Error: ${data.error || 'Failed to save category'}`);
                selectEl.disabled = false;
            }
        } catch (err) {
            finUI.toast(`Error: ${err.message}`);
            selectEl.disabled = false;
        }
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

        _renderTable(filtered, currentData.resolution_context, currentData.categories);

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

    function _renderResolutionControls(context, transactions) {
        // Create or get container
        let container = document.getElementById('drilldownResolutionControls');
        if (!container) {
            const summaryEl = document.getElementById('drilldownSummary');
            container = document.createElement('div');
            container.id = 'drilldownResolutionControls';
            container.className = 'drilldown-resolution-controls';
            summaryEl.after(container);
        }

        // Bulk action bar (auto-saves each individually)
        let html = '<div class="resolution-bulk-bar">';
        html += '<span class="resolution-bulk-label">Classify all as:</span>';
        context.resolution_options.forEach(opt => {
            if (opt.value !== 'CREDIT_OTHER') {
                html += `<button class="btn-resolution-bulk" data-type="${finUI.escapeHtml(opt.value)}">${finUI.escapeHtml(opt.label)}</button>`;
            }
        });
        html += '</div>';

        container.innerHTML = html;

        // Bind bulk action buttons - set all selects then auto-save each
        document.querySelectorAll('.btn-resolution-bulk').forEach(btn => {
            btn.onclick = async () => {
                const targetType = btn.getAttribute('data-type');
                btn.disabled = true;
                btn.textContent = 'Saving...';

                const overrides = [];
                transactions.forEach(txn => {
                    const selectEl = document.getElementById(`resolution-${txn.fingerprint}`);
                    if (selectEl) {
                        selectEl.value = targetType;
                        overrides.push({
                            fingerprint: txn.fingerprint,
                            target_type: targetType,
                            reason: `Bulk: ${currentData.scope_label}`
                        });
                    }
                });

                try {
                    const resp = await finApi.postJSON('/api/txn-type-override/bulk', {
                        overrides: overrides,
                        reason: `Bulk resolution from dashboard`
                    });
                    if (resp.ok) {
                        // Flash all selects green
                        transactions.forEach(txn => {
                            const selectEl = document.getElementById(`resolution-${txn.fingerprint}`);
                            if (selectEl) _flashSaved(selectEl);
                        });
                        finUI.toast(`${overrides.length} transactions classified`);
                    }
                } catch (err) {
                    finUI.toast(`Error: ${err.message}`, 5000);
                }
                btn.disabled = false;
                btn.textContent = btn.getAttribute('data-type').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
            };
        });
    }

    async function autoSaveClassification(selectEl) {
        const fingerprint = selectEl.dataset.fp;
        const targetType = selectEl.value;

        if (!targetType || !fingerprint) return;

        selectEl.disabled = true;

        try {
            const resp = await finApi.postJSON('/api/txn-type-override', {
                fingerprint: fingerprint,
                target_type: targetType,
                reason: `Classified from ${currentData ? currentData.scope_label : 'drilldown'}`
            });

            if (resp.ok) {
                _flashSaved(selectEl);
            } else {
                const data = await resp.json();
                finUI.toast(`Error: ${data.error || 'Failed'}`, 3000);
                selectEl.style.borderColor = 'var(--accent-red)';
            }
        } catch (err) {
            finUI.toast(`Error: ${err.message}`, 3000);
            selectEl.style.borderColor = 'var(--accent-red)';
        }

        selectEl.disabled = false;
    }

    function _flashSaved(el) {
        el.style.transition = 'background 300ms ease, border-color 300ms ease';
        el.style.background = 'var(--accent-green-dim)';
        el.style.borderColor = 'var(--accent-green)';
        setTimeout(() => {
            el.style.background = '';
            el.style.borderColor = '';
        }, 1200);
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
        autoSaveClassification,
        openCategorySelect,
        closeCategorySelect,
        saveCategoryOverride,
    };
})();
