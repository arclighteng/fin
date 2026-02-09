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
        _renderTable(data.transactions, data.resolution_context);

        // Footer
        _renderFooter(data);
    }

    // Suggested tags for autocomplete
    const SUGGESTED_TAGS = ['reimbursable', 'tax-deductible', 'split', 'gift', 'business'];

    function _renderTable(transactions, resolutionContext) {
        if (!transactions || transactions.length === 0) {
            document.getElementById('drilldownTable').innerHTML = '<div class="drilldown-empty">No transactions</div>';
            return;
        }

        const showResolution = resolutionContext && resolutionContext.allows_resolution;
        const colCount = showResolution ? 5 : 5;

        let html = '<table><thead><tr><th>Date</th><th>Merchant</th><th class="text-right">Amount</th><th>Account</th>';
        if (!showResolution) html += '<th>Type</th>';
        if (showResolution) html += '<th>Classify As</th>';
        html += '</tr></thead><tbody>';

        transactions.forEach(t => {
            const amtClass = t.amount_cents > 0 ? 'positive' : '';
            const hasNote = t.note ? ' has-note' : '';
            const hasTags = (t.tags && t.tags.length > 0) ? ' has-tags' : '';
            const annotationIndicator = (t.note || (t.tags && t.tags.length > 0))
                ? '<span class="annotation-dot" title="Has notes/tags"></span>' : '';

            html += `<tr class="txn-row${hasNote}${hasTags}" data-fp="${finUI.escapeHtml(t.fingerprint)}" onclick="finDrilldown.toggleAnnotation(this)">
                <td>${finUI.formatDate(t.date)}</td>
                <td>${annotationIndicator}${finUI.escapeHtml(t.merchant)}</td>
                <td class="text-right ${amtClass}">${finUI.formatCents(t.amount_cents)}</td>
                <td class="muted">${finUI.escapeHtml(t.account_name || '')}</td>`;

            if (!showResolution) {
                html += `<td class="muted">${finUI.escapeHtml(t.type || '')}</td>`;
            }

            if (showResolution) {
                const existingOverride = t.override_type || '';
                html += '<td onclick="event.stopPropagation()"><select id="resolution-' + finUI.escapeHtml(t.fingerprint) + '" class="resolution-select" onchange="finDrilldown.markChanged()">';
                html += '<option value="">- Select -</option>';
                resolutionContext.resolution_options.forEach(opt => {
                    const selected = opt.value === existingOverride ? 'selected' : '';
                    html += `<option value="${finUI.escapeHtml(opt.value)}" ${selected} title="${finUI.escapeHtml(opt.description)}">${finUI.escapeHtml(opt.label)}</option>`;
                });
                html += '</select></td>';
            }

            html += '</tr>';

            // Annotation row (hidden by default)
            const tagsHtml = (t.tags || []).map(tag =>
                `<span class="txn-tag">${finUI.escapeHtml(tag)}<button class="tag-remove" onclick="event.stopPropagation(); finDrilldown.removeTag('${finUI.escapeHtml(t.fingerprint)}', '${finUI.escapeHtml(tag)}')">&times;</button></span>`
            ).join('');

            html += `<tr class="annotation-row" id="ann-${finUI.escapeHtml(t.fingerprint)}" style="display:none" onclick="event.stopPropagation()">
                <td colspan="${colCount}">
                    <div class="annotation-panel">
                        <div class="annotation-note">
                            <textarea class="note-input" id="note-${finUI.escapeHtml(t.fingerprint)}"
                                placeholder="Add a note..." onblur="finDrilldown.saveNote('${finUI.escapeHtml(t.fingerprint)}')">${finUI.escapeHtml(t.note || '')}</textarea>
                        </div>
                        <div class="annotation-tags">
                            <div class="tags-list" id="tags-${finUI.escapeHtml(t.fingerprint)}">${tagsHtml}</div>
                            <div class="tag-add">
                                <input type="text" class="tag-input" id="tag-input-${finUI.escapeHtml(t.fingerprint)}"
                                    placeholder="Add tag..." onkeydown="if(event.key==='Enter'){event.preventDefault(); finDrilldown.addTag('${finUI.escapeHtml(t.fingerprint)}')}" list="tag-suggestions">
                                <button class="btn-tag-add" onclick="finDrilldown.addTag('${finUI.escapeHtml(t.fingerprint)}')">+</button>
                            </div>
                        </div>
                    </div>
                </td>
            </tr>`;
        });
        html += '</tbody></table>';

        // Tag suggestions datalist
        html += '<datalist id="tag-suggestions">';
        SUGGESTED_TAGS.forEach(tag => { html += `<option value="${finUI.escapeHtml(tag)}">`; });
        html += '</datalist>';

        document.getElementById('drilldownTable').innerHTML = html;
    }

    function toggleAnnotation(row) {
        const fp = row.dataset.fp;
        const annRow = document.getElementById('ann-' + fp);
        if (!annRow) return;
        const isVisible = annRow.style.display !== 'none';
        // Close all others
        document.querySelectorAll('.annotation-row').forEach(r => r.style.display = 'none');
        document.querySelectorAll('.txn-row').forEach(r => r.classList.remove('expanded'));
        if (!isVisible) {
            annRow.style.display = 'table-row';
            row.classList.add('expanded');
        }
    }

    async function saveNote(fingerprint) {
        const textarea = document.getElementById('note-' + fingerprint);
        if (!textarea) return;
        const note = textarea.value.trim();
        try {
            if (note) {
                await finApi.postJSON('/api/transaction/' + encodeURIComponent(fingerprint) + '/note', { note });
            } else {
                await finApi.delete('/api/transaction/' + encodeURIComponent(fingerprint) + '/note');
            }
            // Update dot indicator
            const row = document.querySelector(`tr[data-fp="${fingerprint}"]`);
            if (row) {
                if (note) {
                    row.classList.add('has-note');
                    if (!row.querySelector('.annotation-dot')) {
                        const merchantTd = row.querySelectorAll('td')[1];
                        merchantTd.insertAdjacentHTML('afterbegin', '<span class="annotation-dot" title="Has notes/tags"></span>');
                    }
                } else {
                    row.classList.remove('has-note');
                    const hasTags = row.classList.contains('has-tags');
                    if (!hasTags) {
                        const dot = row.querySelector('.annotation-dot');
                        if (dot) dot.remove();
                    }
                }
            }
        } catch (err) {
            // Silently handle read-only mode
        }
    }

    async function addTag(fingerprint) {
        const input = document.getElementById('tag-input-' + fingerprint);
        if (!input) return;
        const tag = input.value.trim().toLowerCase();
        if (!tag) return;

        try {
            const resp = await finApi.postJSON('/api/transaction/' + encodeURIComponent(fingerprint) + '/tag', { tag });
            if (!resp.ok) {
                const err = await resp.json();
                finUI.toast(err.error || 'Invalid tag');
                return;
            }
            input.value = '';

            // Add tag chip to UI
            const tagsList = document.getElementById('tags-' + fingerprint);
            const chip = document.createElement('span');
            chip.className = 'txn-tag';
            chip.innerHTML = `${finUI.escapeHtml(tag)}<button class="tag-remove" onclick="event.stopPropagation(); finDrilldown.removeTag('${finUI.escapeHtml(fingerprint)}', '${finUI.escapeHtml(tag)}')">&times;</button>`;
            tagsList.appendChild(chip);

            // Update row indicator
            const row = document.querySelector(`tr[data-fp="${fingerprint}"]`);
            if (row) {
                row.classList.add('has-tags');
                if (!row.querySelector('.annotation-dot')) {
                    const merchantTd = row.querySelectorAll('td')[1];
                    merchantTd.insertAdjacentHTML('afterbegin', '<span class="annotation-dot" title="Has notes/tags"></span>');
                }
            }
        } catch (err) {
            // Read-only mode
        }
    }

    async function removeTag(fingerprint, tag) {
        try {
            await finApi.delete('/api/transaction/' + encodeURIComponent(fingerprint) + '/tag/' + encodeURIComponent(tag));

            // Remove chip from UI
            const tagsList = document.getElementById('tags-' + fingerprint);
            if (tagsList) {
                tagsList.querySelectorAll('.txn-tag').forEach(chip => {
                    if (chip.textContent.replace('\u00d7', '').trim() === tag) chip.remove();
                });
            }

            // Update indicator if no tags left
            const remaining = tagsList ? tagsList.querySelectorAll('.txn-tag').length : 0;
            if (remaining === 0) {
                const row = document.querySelector(`tr[data-fp="${fingerprint}"]`);
                if (row) {
                    row.classList.remove('has-tags');
                    if (!row.classList.contains('has-note')) {
                        const dot = row.querySelector('.annotation-dot');
                        if (dot) dot.remove();
                    }
                }
            }
        } catch (err) {
            // Read-only mode
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

        _renderTable(filtered, currentData.resolution_context);

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

        // Bulk action bar
        let html = '<div class="resolution-bulk-bar">';
        html += '<span class="resolution-bulk-label">Classify all as:</span>';
        context.resolution_options.forEach(opt => {
            if (opt.value !== 'CREDIT_OTHER') {  // Don't show "Keep Unclassified" for bulk
                html += `<button class="btn-resolution-bulk" data-type="${finUI.escapeHtml(opt.value)}">${finUI.escapeHtml(opt.label)}</button>`;
            }
        });
        html += '</div>';

        // Save button (initially hidden)
        html += '<button id="resolutionSaveBtn" class="btn btn-primary" style="display:none;" onclick="finDrilldown.saveResolutions()">Save Changes</button>';

        container.innerHTML = html;

        // Bind bulk action buttons
        document.querySelectorAll('.btn-resolution-bulk').forEach(btn => {
            btn.onclick = () => {
                const targetType = btn.getAttribute('data-type');
                transactions.forEach(txn => {
                    const selectEl = document.getElementById(`resolution-${txn.fingerprint}`);
                    if (selectEl) selectEl.value = targetType;
                });
                // Show save button
                document.getElementById('resolutionSaveBtn').style.display = 'block';
            };
        });
    }

    function markChanged() {
        const saveBtn = document.getElementById('resolutionSaveBtn');
        if (saveBtn) saveBtn.style.display = 'block';
    }

    async function saveResolutions() {
        if (!currentData || !currentData.transactions) return;

        const overrides = [];

        currentData.transactions.forEach(txn => {
            const selectEl = document.getElementById(`resolution-${txn.fingerprint}`);
            if (selectEl && selectEl.value && selectEl.value !== '') {
                overrides.push({
                    fingerprint: txn.fingerprint,
                    target_type: selectEl.value,
                    reason: `Resolved from ${currentData.scope_label} (${currentParams.startDate} to ${currentParams.endDate})`
                });
            }
        });

        if (overrides.length === 0) {
            finUI.toast('No changes to save');
            return;
        }

        // Show loading state
        const saveBtn = document.getElementById('resolutionSaveBtn');
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving...';

        try {
            const resp = await finApi.postJSON('/api/txn-type-override/bulk', {
                overrides: overrides,
                reason: `Bulk resolution from dashboard (${new Date().toISOString()})`
            });

            const data = await resp.json();

            if (resp.ok) {
                finUI.toast(`${data.overrides_applied} transactions reclassified. Integrity score: ${data.integrity_percent}%`, 3000);
                close();  // Close modal

                // Refresh dashboard to show updated integrity score
                setTimeout(() => window.location.reload(), 1000);
            } else {
                finUI.toast(`Error: ${data.error || 'Unknown error'}`, 5000);
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save Changes';
            }
        } catch (err) {
            finUI.toast(`Error: ${err.message}`, 5000);
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save Changes';
        }
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
        markChanged,
        saveResolutions,
        toggleAnnotation,
        saveNote,
        addTag,
        removeTag,
    };
})();
