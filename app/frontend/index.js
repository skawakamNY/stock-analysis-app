document.addEventListener('DOMContentLoaded', () => {
    // Tab switching
    const tabButtons = document.querySelectorAll('.tab-btn');
    const tabPanes = document.querySelectorAll('.tab-pane');
    
    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetTab = btn.getAttribute('data-tab');
            
            tabButtons.forEach(b => b.classList.remove('active'));
            tabPanes.forEach(p => p.classList.remove('active'));
            
            btn.classList.add('active');
            document.getElementById(targetTab).classList.add('active');
        });
    });

    // Elements
    const queryInput = document.getElementById('query-input');
    const btnSearch = document.getElementById('btn-search');
    const resultsGrid = document.getElementById('results-grid');
    const resultsCount = document.getElementById('results-count');
    const filterTicker = document.getElementById('filter-ticker');
    const filterForm = document.getElementById('filter-form');
    const topKSlider = document.getElementById('top-k-slider');
    const topKVal = document.getElementById('top-k-val');
    
    const tickerInput = document.getElementById('ticker-select');
    const btnIngest = document.getElementById('btn-ingest');
    const ingestSpinner = document.getElementById('ingest-spinner');
    const ingestStatus = document.getElementById('ingest-status');
    const registryTableBody = document.querySelector('#registry-table tbody');
    
    const evalFormSelect = document.getElementById('eval-form-select');
    const btnRunEval = document.getElementById('btn-run-eval');
    const evalSpinner = document.getElementById('eval-spinner');
    const mrrValDisplay = document.getElementById('mrr-val-display');
    const mrrProgressBar = document.getElementById('mrr-progress-bar');
    const evalDetailsTableBody = document.querySelector('#eval-details-table tbody');
    
    const parentModal = document.getElementById('parent-modal');
    const closeModal = document.querySelector('.close-modal');
    
    // Slider display update
    topKSlider.addEventListener('input', (e) => {
        topKVal.textContent = e.target.value;
    });
    
    // Fetch and load registry status on startup
    async function loadRegistry() {
        try {
            const res = await fetch('/api/registry');
            const data = await res.json();
            
            // Re-render registry table
            registryTableBody.innerHTML = '';
            
            // Collect unique tickers
            const tickers = new Set();
            let rowCount = 0;
            
            for (const [ticker, forms] of Object.entries(data)) {
                tickers.add(ticker);
                for (const [formType, meta] of Object.entries(forms)) {
                    rowCount++;
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td><strong>${ticker}</strong></td>
                        <td><span class="badge badge-form">${formType}</span></td>
                        <td class="code-font">${meta.filing_date || 'N/A'}</td>
                    `;
                    registryTableBody.appendChild(tr);
                }
            }
            
            if (rowCount === 0) {
                registryTableBody.innerHTML = `
                    <tr><td colspan="3" class="empty-msg">No data ingested yet</td></tr>
                `;
            }
            
            // Dynamic populate filter choices
            const prevTickerVal = filterTicker.value;
            filterTicker.innerHTML = '<option value="">No Filter</option>';
            
            const deleteTickerSelect = document.getElementById('delete-ticker-select');
            const prevDelVal = deleteTickerSelect ? deleteTickerSelect.value : '';
            if (deleteTickerSelect) {
                deleteTickerSelect.innerHTML = '<option value="">-- Choose Ticker --</option>';
            }
            
            tickers.forEach(t => {
                const opt = document.createElement('option');
                opt.value = t;
                opt.textContent = t;
                filterTicker.appendChild(opt);
                
                if (deleteTickerSelect) {
                    const optDel = document.createElement('option');
                    optDel.value = t;
                    optDel.textContent = t;
                    deleteTickerSelect.appendChild(optDel);
                }
            });
            filterTicker.value = prevTickerVal;
            if (deleteTickerSelect && prevDelVal) {
                deleteTickerSelect.value = prevDelVal;
            }
            
        } catch (err) {
            console.error('Failed to load registry:', err);
        }
    }
    
    // Ingestion execution trigger
    btnIngest.addEventListener('click', async () => {
        const ticker = tickerInput.value.trim().toUpperCase();
        if (!ticker) {
            alert('Please specify a ticker symbol.');
            return;
        }
        
        btnIngest.disabled = true;
        ingestSpinner.style.display = 'inline-block';
        ingestStatus.className = 'status-msg';
        ingestStatus.textContent = `Running concurrent SEC download and parsing pipeline for ${ticker}...`;
        
        try {
            const res = await fetch('/api/ingest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker })
            });
            const data = await res.json();
            
            if (res.ok) {
                ingestStatus.className = 'status-msg success-msg';
                ingestStatus.textContent = data.message || 'Ingestion completed successfully.';
                await loadRegistry();
            } else {
                ingestStatus.className = 'status-msg error-msg';
                ingestStatus.textContent = data.error || 'Ingestion failed.';
            }
        } catch (err) {
            ingestStatus.className = 'status-msg error-msg';
            ingestStatus.textContent = 'Network or server error occurred.';
            console.error(err);
        } finally {
            btnIngest.disabled = false;
            ingestSpinner.style.display = 'none';
        }
    });
    
    // Search execution trigger
    btnSearch.addEventListener('click', async () => {
        const query = queryInput.value.trim();
        if (!query) {
            alert('Please enter a query.');
            return;
        }
        
        resultsGrid.innerHTML = `
            <div class="empty-state">
                <span class="spinner"></span>
                <p style="margin-top: 1rem;">Executing hybrid search and pipeline rankings...</p>
            </div>
        `;
        
        const filterT = filterTicker.value;
        const filterF = filterForm.value;
        const topK = parseInt(topKSlider.value);
        
        try {
            const res = await fetch('/api/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query, ticker: filterT, form_type: filterF, top_k: topK })
            });
            const data = await res.json();
            
            if (!res.ok) {
                throw new Error(data.error || 'Query failed');
            }
            
            resultsGrid.innerHTML = '';
            const candidates = data.candidates || [];
            resultsCount.textContent = `${candidates.length} chunks found`;
            
            if (candidates.length === 0) {
                resultsGrid.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon">🔍</div>
                        <p>No results matched the query. Try ingestion or adjusting filters.</p>
                    </div>
                `;
                return;
            }
            
            candidates.forEach((cand, idx) => {
                const meta = cand.metadata || {};
                const card = document.createElement('div');
                card.className = 'card';
                
                card.innerHTML = `
                    <div class="card-header">
                        <div class="card-tags">
                            <span class="badge badge-ticker">${meta.ticker || 'UNKNOWN'}</span>
                            <span class="badge badge-form">${meta.form_type || 'UNKNOWN'}</span>
                            <span class="badge badge-item">${meta.item_name || 'Item'}</span>
                        </div>
                        <div class="score-badge">Rank #${idx + 1}</div>
                    </div>
                    <div class="card-body">
                        ${cand.text}
                    </div>
                    <div class="card-footer">
                        <span>Filing Date: ${meta.filing_date || 'N/A'}</span>
                        <span>Length: ${meta.char_count || cand.text.length} chars</span>
                    </div>
                    <div class="card-footer" style="border-top: 1px dashed var(--border-color); padding-top: 0.5rem; justify-content: flex-start; gap: 0.5rem; word-break: break-all;">
                        <strong>URL:</strong> <a href="${meta.url || '#'}" target="_blank" style="color: var(--primary); text-decoration: underline; font-size: 0.7rem;" onclick="event.stopPropagation();">${meta.url || 'N/A'}</a>
                    </div>
                `;
                
                // Show modal on click
                card.addEventListener('click', () => {
                    document.getElementById('modal-meta-ticker').textContent = meta.ticker || 'UNKNOWN';
                    document.getElementById('modal-meta-form').textContent = meta.form_type || 'UNKNOWN';
                    document.getElementById('modal-meta-item').textContent = meta.item_name || 'Item';
                    document.getElementById('modal-chunk-text').textContent = cand.text;
                    
                    // Populate Left Pane Metadata grid
                    document.getElementById('modal-chunk-ticker').textContent = meta.ticker || 'N/A';
                    document.getElementById('modal-chunk-form').textContent = meta.form_type || 'N/A';
                    document.getElementById('modal-chunk-section').textContent = meta.item_name || 'N/A';
                    document.getElementById('modal-chunk-date').textContent = meta.filing_date || 'N/A';
                    document.getElementById('modal-chunk-chars').textContent = meta.char_count || cand.text.length;
                    document.getElementById('modal-chunk-words').textContent = meta.word_count || cand.text.split(/\s+/).length;
                    
                    const urlLink = document.getElementById('modal-chunk-url');
                    if (meta.url) {
                        urlLink.textContent = meta.url;
                        urlLink.href = meta.url;
                    } else {
                        urlLink.textContent = 'N/A';
                        urlLink.href = '#';
                    }
                    
                    // Display parent document status
                    document.getElementById('modal-parent-text').textContent = cand.parent_text || 'No parent document text resolved.';
                    parentModal.style.display = 'block';
                });
                
                resultsGrid.appendChild(card);
            });
            
        } catch (err) {
            resultsGrid.innerHTML = `
                <div class="empty-state">
                    <p style="color: var(--danger);">Error: ${err.message}</p>
                </div>
            `;
        }
    });
    
    // Evaluation Dashboard runner
    btnRunEval.addEventListener('click', async () => {
        const formType = evalFormSelect.value;
        
        btnRunEval.disabled = true;
        evalSpinner.style.display = 'inline-block';
        mrrValDisplay.textContent = '...';
        mrrProgressBar.style.width = '0%';
        evalDetailsTableBody.innerHTML = `
            <tr><td colspan="4" class="empty-msg">Running retrieval evaluations...</td></tr>
        `;
        
        try {
            const res = await fetch('/api/eval', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ form_type: formType })
            });
            const data = await res.json();
            
            if (res.ok) {
                const mrr = data.mrr || 0;
                mrrValDisplay.textContent = mrr.toFixed(4);
                mrrProgressBar.style.width = `${mrr * 100}%`;
                
                evalDetailsTableBody.innerHTML = '';
                const details = data.details || [];
                
                details.forEach(d => {
                    const tr = document.createElement('tr');
                    const rrVal = d.reciprocal_rank.toFixed(4);
                    tr.innerHTML = `
                        <td><strong>${d.query}</strong></td>
                        <td><span class="badge badge-item">${d.expected}</span></td>
                        <td class="code-font" style="color: ${d.rank <= 5 ? 'var(--success)' : 'var(--danger)'}">
                            Rank ${d.rank === 999 ? 'N/A' : d.rank}
                        </td>
                        <td class="code-font font-bold">${rrVal}</td>
                    `;
                    evalDetailsTableBody.appendChild(tr);
                });
                
                if (details.length === 0) {
                    evalDetailsTableBody.innerHTML = `
                        <tr><td colspan="4" class="empty-msg">No details returned</td></tr>
                    `;
                }
            } else {
                mrrValDisplay.textContent = 'ERR';
                evalDetailsTableBody.innerHTML = `
                    <tr><td colspan="4" class="empty-msg" style="color: var(--danger);">${data.error || 'Evaluation failed.'}</td></tr>
                `;
            }
        } catch (err) {
            mrrValDisplay.textContent = 'ERR';
            evalDetailsTableBody.innerHTML = `
                <tr><td colspan="4" class="empty-msg" style="color: var(--danger);">Network or server error occurred.</td></tr>
            `;
            console.error(err);
        } finally {
            btnRunEval.disabled = false;
            evalSpinner.style.display = 'none';
        }
    });
    
    // Database Inspector logic
    const btnRefreshInspect = document.getElementById('btn-refresh-inspect');
    const inspectTableBody = document.querySelector('#inspect-table tbody');
    
    // Dropdown filters
    const filterInsTicker = document.getElementById('inspect-filter-ticker');
    const filterInsForm = document.getElementById('inspect-filter-form');
    const filterInsItem = document.getElementById('inspect-filter-item');
    
    // Pagination Controls
    const btnPagePrev = document.getElementById('btn-page-prev');
    const btnPageNext = document.getElementById('btn-page-next');
    const paginationInfo = document.getElementById('pagination-info');
    
    let allInspectItems = [];
    let currentPage = 1;
    const itemsPerPage = 15;
    
    async function loadInspectData() {
        currentPage = 1;
        inspectTableBody.innerHTML = `
            <tr><td colspan="5" class="empty-msg"><span class="spinner"></span> Querying collection...</td></tr>
        `;
        try {
            const res = await fetch('/api/inspect');
            const data = await res.json();
            allInspectItems = data.items || [];
            
            // Populate tickers dropdown initially
            populateTickerDropdown();
            
            // Apply filtering (initially empty filters)
            renderFilteredInspectItems();
        } catch (err) {
            console.error('Failed to load inspect data:', err);
            inspectTableBody.innerHTML = `
                <tr><td colspan="5" class="empty-msg" style="color: var(--danger);">Error querying database</td></tr>
            `;
        }
    }
    
    function populateTickerDropdown() {
        const tickers = new Set();
        allInspectItems.forEach(item => {
            const t = (item.metadata && item.metadata.ticker) || '';
            if (t) tickers.add(t);
        });
        
        filterInsTicker.innerHTML = '<option value="">All Tickers</option>';
        tickers.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t;
            opt.textContent = t;
            filterInsTicker.appendChild(opt);
        });
        
        // Reset form and item dropdowns
        filterInsForm.innerHTML = '<option value="">All Forms</option>';
        filterInsForm.disabled = true;
        filterInsItem.innerHTML = '<option value="">All Sections</option>';
        filterInsItem.disabled = true;
    }
    
    // Listen to Ticker selection change
    filterInsTicker.addEventListener('change', () => {
        const ticker = filterInsTicker.value;
        
        if (!ticker) {
            filterInsForm.innerHTML = '<option value="">All Forms</option>';
            filterInsForm.disabled = true;
            filterInsItem.innerHTML = '<option value="">All Sections</option>';
            filterInsItem.disabled = true;
        } else {
            // Find unique form types for selected ticker
            const forms = new Set();
            allInspectItems.forEach(item => {
                const meta = item.metadata || {};
                if (meta.ticker === ticker && meta.form_type) {
                    forms.add(meta.form_type);
                }
            });
            
            filterInsForm.innerHTML = '<option value="">All Forms</option>';
            forms.forEach(f => {
                const opt = document.createElement('option');
                opt.value = f;
                opt.textContent = f;
                filterInsForm.appendChild(opt);
            });
            filterInsForm.disabled = false;
            
            // Reset item dropdown
            filterInsItem.innerHTML = '<option value="">All Sections</option>';
            filterInsItem.disabled = true;
        }
        
        currentPage = 1;
        renderFilteredInspectItems();
    });
    
    // Listen to Form selection change
    filterInsForm.addEventListener('change', () => {
        const ticker = filterInsTicker.value;
        const form = filterInsForm.value;
        
        if (!form) {
            filterInsItem.innerHTML = '<option value="">All Sections</option>';
            filterInsItem.disabled = true;
        } else {
            // Find unique section/items for selected ticker and form
            const items = new Set();
            allInspectItems.forEach(item => {
                const meta = item.metadata || {};
                if (meta.ticker === ticker && meta.form_type === form && meta.item_name) {
                    items.add(meta.item_name);
                }
            });
            
            filterInsItem.innerHTML = '<option value="">All Sections</option>';
            items.forEach(i => {
                const opt = document.createElement('option');
                opt.value = i;
                opt.textContent = i;
                filterInsItem.appendChild(opt);
            });
            filterInsItem.disabled = false;
        }
        
        currentPage = 1;
        renderFilteredInspectItems();
    });
    
    // Listen to Item selection change
    filterInsItem.addEventListener('change', () => {
        currentPage = 1;
        renderFilteredInspectItems();
    });
    
    function renderFilteredInspectItems() {
        const selectTicker = filterInsTicker.value;
        const selectForm = filterInsForm.value;
        const selectItem = filterInsItem.value;
        
        // Filter elements
        const filtered = allInspectItems.filter(item => {
            const meta = item.metadata || {};
            if (selectTicker && meta.ticker !== selectTicker) return false;
            if (selectForm && meta.form_type !== selectForm) return false;
            if (selectItem && meta.item_name !== selectItem) return false;
            return true;
        });
        
        const totalItems = filtered.length;
        const totalPages = Math.ceil(totalItems / itemsPerPage) || 1;
        if (currentPage > totalPages) currentPage = totalPages;
        if (currentPage < 1) currentPage = 1;
        
        btnPagePrev.disabled = (currentPage === 1);
        btnPageNext.disabled = (currentPage === totalPages);
        
        const startIdx = (currentPage - 1) * itemsPerPage;
        const endIdx = Math.min(currentPage * itemsPerPage, totalItems);
        
        if (totalItems === 0) {
            paginationInfo.textContent = "Showing 0-0 of 0 entries";
            inspectTableBody.innerHTML = `
                <tr><td colspan="5" class="empty-msg">No chunks match the selected filters</td></tr>
            `;
            return;
        }
        
        paginationInfo.textContent = `Showing ${startIdx + 1}-${endIdx} of ${totalItems} entries (Page ${currentPage} of ${totalPages})`;
        
        const pageItems = filtered.slice(startIdx, endIdx);
        
        inspectTableBody.innerHTML = '';
        pageItems.forEach(item => {
            const meta = item.metadata || {};
            const tr = document.createElement('tr');
            tr.style.cursor = 'pointer';
            
            const textSnippet = item.text.length > 120 ? item.text.substring(0, 120) + '...' : item.text;
            
            tr.innerHTML = `
                <td class="code-font" style="font-size: 0.65rem; color: var(--text-muted);">${item.id}</td>
                <td><span class="badge badge-ticker">${meta.ticker || 'N/A'}</span></td>
                <td><span class="badge badge-form">${meta.form_type || 'N/A'}</span></td>
                <td><span class="badge badge-item">${meta.item_name || 'N/A'}</span></td>
                <td class="card-body" style="font-size: 0.75rem;">${textSnippet}</td>
            `;
            
            tr.addEventListener('click', () => {
                document.getElementById('modal-meta-ticker').textContent = meta.ticker || 'N/A';
                document.getElementById('modal-meta-form').textContent = meta.form_type || 'N/A';
                document.getElementById('modal-meta-item').textContent = meta.item_name || 'N/A';
                document.getElementById('modal-chunk-text').textContent = item.text;
                
                // Populate Left Pane Metadata grid
                document.getElementById('modal-chunk-ticker').textContent = meta.ticker || 'N/A';
                document.getElementById('modal-chunk-form').textContent = meta.form_type || 'N/A';
                document.getElementById('modal-chunk-section').textContent = meta.item_name || 'N/A';
                document.getElementById('modal-chunk-date').textContent = meta.filing_date || 'N/A';
                document.getElementById('modal-chunk-chars').textContent = meta.char_count || item.text.length;
                document.getElementById('modal-chunk-words').textContent = meta.word_count || item.text.split(/\s+/).length;
                
                const urlLink = document.getElementById('modal-chunk-url');
                if (meta.url) {
                    urlLink.textContent = meta.url;
                    urlLink.href = meta.url;
                } else {
                    urlLink.textContent = 'N/A';
                    urlLink.href = '#';
                }
                
                const p_text = meta.ticker && meta.form_type && meta.item_name ? 
                               `Parent document text is resolved dynamically in the RAG pipeline view.` :
                               'No parent metadata associated.';
                document.getElementById('modal-parent-text').textContent = p_text;
                parentModal.style.display = 'block';
            });
            
            inspectTableBody.appendChild(tr);
        });
    }
    
    btnPagePrev.addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            renderFilteredInspectItems();
        }
    });
    
    btnPageNext.addEventListener('click', () => {
        const totalItems = allInspectItems.filter(item => {
            const meta = item.metadata || {};
            const selectTicker = filterInsTicker.value;
            const selectForm = filterInsForm.value;
            const selectItem = filterInsItem.value;
            if (selectTicker && meta.ticker !== selectTicker) return false;
            if (selectForm && meta.form_type !== selectForm) return false;
            if (selectItem && meta.item_name !== selectItem) return false;
            return true;
        }).length;
        const totalPages = Math.ceil(totalItems / itemsPerPage) || 1;
        if (currentPage < totalPages) {
            currentPage++;
            renderFilteredInspectItems();
        }
    });
    
    btnRefreshInspect.addEventListener('click', loadInspectData);
    document.getElementById('btn-tab-inspect').addEventListener('click', loadInspectData);

    // Modal close controls
    closeModal.addEventListener('click', () => {
        parentModal.style.display = 'none';
    });
    
    window.addEventListener('click', (e) => {
        if (e.target === parentModal) {
            parentModal.style.display = 'none';
        }
    });
    
    // Delete Ticker vector and registry contents
    const btnDeleteTicker = document.getElementById('btn-delete-ticker');
    const deleteSpinner = document.getElementById('delete-spinner');
    const deleteStatus = document.getElementById('delete-status');
    const deleteTickerSelectEl = document.getElementById('delete-ticker-select');
    
    if (btnDeleteTicker) {
        btnDeleteTicker.addEventListener('click', async () => {
            const ticker = deleteTickerSelectEl.value;
            if (!ticker) {
                alert('Please select a ticker to delete.');
                return;
            }
            
            if (!confirm(`Are you sure you want to delete all vectors, chunks, and cached ingestion records for ticker ${ticker}?`)) {
                return;
            }
            
            btnDeleteTicker.disabled = true;
            deleteSpinner.style.display = 'inline-block';
            deleteStatus.className = 'status-msg';
            deleteStatus.textContent = `Deleting vector chunks and cache registry for ${ticker}...`;
            
            try {
                const res = await fetch('/api/delete-ticker', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ticker })
                });
                const data = await res.json();
                
                if (res.ok) {
                    deleteStatus.className = 'status-msg success-msg';
                    deleteStatus.textContent = data.message || `Deleted data for ${ticker} successfully.`;
                    deleteTickerSelectEl.value = '';
                    await loadRegistry();
                } else {
                    deleteStatus.className = 'status-msg error-msg';
                    deleteStatus.textContent = data.error || 'Failed to delete data.';
                }
            } catch (err) {
                deleteStatus.className = 'status-msg error-msg';
                deleteStatus.textContent = 'Network or server error occurred.';
                console.error(err);
            } finally {
                btnDeleteTicker.disabled = false;
                deleteSpinner.style.display = 'none';
            }
        });
    }

    // Initial data fetch
    loadRegistry();
});
