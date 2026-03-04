// @ts-check
(function () {
    // @ts-ignore
    const vscode = acquireVsCodeApi();

    const state = vscode.getState() || { activeTab: 'semantic', results: null };

    const searchInput = document.getElementById('searchInput');
    const resultsDiv = document.getElementById('results');
    const saveInput = document.getElementById('saveInput');
    const saveBtn = document.getElementById('saveBtn');
    const preview = document.getElementById('preview');
    const previewText = document.getElementById('previewText');
    const previewFile = document.getElementById('previewFile');
    const previewClose = document.getElementById('previewClose');
    const statsBar = document.getElementById('statsBar');
    const tabs = document.querySelectorAll('.tab');

    let activeTab = state.activeTab || 'semantic';
    let currentResults = state.results || null;
    let searchTimeout = null;
    let tempMsgId = 0;

    // --- Tabs ---
    tabs.forEach(tab => {
        if (tab.dataset.tab === activeTab) {
            tab.classList.add('active');
            tab.setAttribute('aria-selected', 'true');
        } else {
            tab.classList.remove('active');
            tab.setAttribute('aria-selected', 'false');
        }

        tab.addEventListener('click', () => {
            activeTab = tab.dataset.tab;
            tabs.forEach(t => {
                t.classList.remove('active');
                t.setAttribute('aria-selected', 'false');
            });
            tab.classList.add('active');
            tab.setAttribute('aria-selected', 'true');
            if (currentResults) renderResults(currentResults);
            saveState();
        });
    });

    // --- Search ---
    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            doSearch(searchInput.value.trim());
        }
    });

    searchInput.addEventListener('input', () => {
        if (searchTimeout) clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            const q = searchInput.value.trim();
            if (q.length >= 3) doSearch(q);
        }, 500);
    });

    function doSearch(query) {
        if (!query) return;
        resultsDiv.innerHTML = '<div class="loading">Searching...</div>';
        vscode.postMessage({ type: 'search', query });
    }

    // --- Save ---
    saveBtn.addEventListener('click', () => {
        const text = saveInput.value.trim();
        if (!text) return;
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving...';
        vscode.postMessage({ type: 'save', text });
    });

    saveInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            saveBtn.click();
        }
    });

    // --- Preview ---
    previewClose.addEventListener('click', () => {
        preview.classList.add('hidden');
        saveInput.value = '';
    });

    // --- Messages from extension ---
    window.addEventListener('message', (event) => {
        const msg = event.data;
        if (!msg || typeof msg.type !== 'string') return;

        switch (msg.type) {
            case 'searchResults':
                currentResults = {
                    semantic: msg.semantic || [],
                    episodic: msg.episodic || [],
                    procedural: msg.procedural || [],
                };
                renderResults(currentResults);
                saveState();
                break;

            case 'saveSuccess':
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save to Mengram';
                saveInput.value = '';
                preview.classList.add('hidden');
                showTemp(resultsDiv, '<div class="empty">Saved successfully.</div>', 2000);
                break;

            case 'stats':
                statsBar.textContent = `${msg.entities} entities \u00b7 ${msg.facts} facts \u00b7 ${msg.knowledge} knowledge`;
                break;

            case 'selectedText':
                preview.classList.remove('hidden');
                previewText.textContent = msg.text;
                previewFile.textContent = msg.fileName;
                saveInput.value = msg.text;
                saveInput.focus();
                break;

            case 'focusSearch':
                searchInput.focus();
                searchInput.select();
                break;

            case 'searchWithQuery':
                searchInput.value = msg.query;
                searchInput.focus();
                doSearch(msg.query);
                break;

            case 'error':
                resultsDiv.innerHTML = `<div class="error-msg">${escapeHtml(msg.message)}</div>`;
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save to Mengram';
                break;
        }
    });

    // --- Render ---
    function renderResults(results) {
        const items = activeTab === 'semantic' ? results.semantic
            : activeTab === 'episodic' ? results.episodic
            : results.procedural;

        if (!items || items.length === 0) {
            const tabName = activeTab === 'semantic' ? 'memories'
                : activeTab === 'episodic' ? 'episodes' : 'procedures';
            resultsDiv.innerHTML = `<div class="empty">No ${tabName} found.</div>`;
            return;
        }

        if (activeTab === 'semantic') {
            resultsDiv.innerHTML = items.map(renderSemantic).join('');
        } else if (activeTab === 'episodic') {
            resultsDiv.innerHTML = items.map(renderEpisode).join('');
        } else {
            resultsDiv.innerHTML = items.map(renderProcedure).join('');
        }

        // Click to expand/collapse, insert button to insert
        resultsDiv.querySelectorAll('.result-card, .episode-card, .procedure-card').forEach((card, i) => {
            // Expand/collapse on card click
            card.addEventListener('click', (e) => {
                // Don't toggle if clicking the insert button
                if (e.target.closest('.insert-btn')) return;
                card.classList.toggle('expanded');
            });
            card.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    if (e.target.closest('.insert-btn')) return;
                    e.preventDefault();
                    card.classList.toggle('expanded');
                }
            });

            // Insert button
            const insertBtn = card.querySelector('.insert-btn');
            if (insertBtn) {
                insertBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const item = items[i];
                    const text = formatForInsert(item, activeTab);
                    vscode.postMessage({ type: 'insertToEditor', text });
                });
            }
        });
    }

    function renderSemantic(r) {
        const allFacts = r.facts || [];
        const previewFacts = allFacts.slice(0, 3).map(f =>
            `<li>${escapeHtml(f)}</li>`
        ).join('');
        const restFacts = allFacts.slice(3).map(f =>
            `<li>${escapeHtml(f)}</li>`
        ).join('');
        const hasMore = allFacts.length > 3;

        return `<div class="result-card clickable" tabindex="0" role="button"
                     aria-label="Expand ${escapeHtml(r.entity)}">
            <div class="result-header">
                <span class="result-entity">${escapeHtml(r.entity)}</span>
                <span class="result-type">${escapeHtml(r.type)}</span>
                <span class="result-score" title="Relevance score">${Math.round((r.score || 0) * 100)}%</span>
            </div>
            ${previewFacts ? `<ul class="result-facts">${previewFacts}</ul>` : ''}
            ${hasMore ? `<div class="expandable">
                <ul class="result-facts">${restFacts}</ul>
            </div>
            <div class="expand-hint">${allFacts.length - 3} more facts</div>` : ''}
            <div class="card-actions">
                <button class="insert-btn" title="Insert to editor">Insert</button>
            </div>
        </div>`;
    }

    function renderEpisode(ep) {
        const valenceClass = `valence-${ep.emotional_valence || 'neutral'}`;
        const participants = (ep.participants || []).join(', ');
        const date = ep.created_at ? new Date(ep.created_at).toLocaleDateString() : '';

        return `<div class="episode-card clickable" tabindex="0" role="button"
                     aria-label="Expand episode">
            <div class="episode-summary">${escapeHtml(ep.summary || '')}</div>
            <div class="expandable">
                ${ep.outcome ? `<div class="episode-detail"><strong>Outcome:</strong> ${escapeHtml(ep.outcome)}</div>` : ''}
                ${ep.context ? `<div class="episode-detail"><strong>Context:</strong> ${escapeHtml(ep.context)}</div>` : ''}
            </div>
            <div class="episode-meta">
                <span class="${valenceClass}">${ep.emotional_valence || 'neutral'}</span>
                ${participants ? `<span>${escapeHtml(participants)}</span>` : ''}
                ${date ? `<span>${date}</span>` : ''}
            </div>
            <div class="card-actions">
                <button class="insert-btn" title="Insert to editor">Insert</button>
            </div>
        </div>`;
    }

    function renderProcedure(pr) {
        const allSteps = pr.steps || [];
        const previewSteps = allSteps.slice(0, 3).map(s =>
            `<li><strong>${escapeHtml(s.action)}</strong> ${escapeHtml(s.detail || '')}</li>`
        ).join('');
        const restSteps = allSteps.slice(3).map(s =>
            `<li><strong>${escapeHtml(s.action)}</strong> ${escapeHtml(s.detail || '')}</li>`
        ).join('');
        const total = (pr.success_count || 0) + (pr.fail_count || 0);
        const hasMore = allSteps.length > 3;

        return `<div class="procedure-card clickable" tabindex="0" role="button"
                     aria-label="Expand ${escapeHtml(pr.name || 'procedure')}">
            <div class="procedure-name">${escapeHtml(pr.name || '')}</div>
            ${previewSteps ? `<ol class="procedure-steps">${previewSteps}</ol>` : ''}
            ${hasMore ? `<div class="expandable">
                <ol class="procedure-steps" start="4">${restSteps}</ol>
            </div>
            <div class="expand-hint">${allSteps.length - 3} more steps</div>` : ''}
            <div class="procedure-meta">
                ${total > 0 ? `${pr.success_count}/${total} success` : ''}
                ${pr.version > 1 ? ` \u00b7 v${pr.version}` : ''}
            </div>
            <div class="card-actions">
                <button class="insert-btn" title="Insert to editor">Insert</button>
            </div>
        </div>`;
    }

    function formatForInsert(item, tab) {
        if (tab === 'semantic') {
            const lines = [`## ${item.entity} (${item.type})`, ''];
            (item.facts || []).forEach(f => lines.push(`- ${f}`));
            lines.push('');
            return lines.join('\n');
        }
        if (tab === 'episodic') {
            return `**Episode:** ${item.summary || ''}${item.outcome ? '\n**Outcome:** ' + item.outcome : ''}\n`;
        }
        const lines = [`## ${item.name}`, ''];
        (item.steps || []).forEach(s => lines.push(`${s.step}. **${s.action}** \u2014 ${s.detail || ''}`));
        lines.push('');
        return lines.join('\n');
    }

    // --- Helpers ---
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function showTemp(el, html, ms) {
        const id = ++tempMsgId;
        el.innerHTML = html;
        setTimeout(() => { if (tempMsgId === id) el.innerHTML = ''; }, ms);
    }

    function saveState() {
        vscode.setState({ activeTab, results: currentResults });
    }

    // --- Init ---
    if (currentResults) renderResults(currentResults);
    vscode.postMessage({ type: 'getStats' });
})();
