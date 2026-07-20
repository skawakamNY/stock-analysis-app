import React, { useState, useEffect } from 'react';
import './ChromaExplorer.css';

function ChromaExplorer({ onNavigateBack }) {
  // Sidebar State
  const [ingestTicker, setIngestTicker] = useState('NVDA');
  const [ingesting, setIngesting] = useState(false);
  const [ingestStatus, setIngestStatus] = useState({ type: '', text: '' });
  
  const [deleteTicker, setDeleteTicker] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [deleteStatus, setDeleteStatus] = useState({ type: '', text: '' });

  const [registry, setRegistry] = useState({});
  const [uniqueTickers, setUniqueTickers] = useState([]);

  // Active Tab
  const [activeTab, setActiveTab] = useState('search'); // 'search' | 'evals' | 'inspect'

  // Search Tab State
  const [searchQuery, setSearchQuery] = useState('');
  const [filterTicker, setFilterTicker] = useState('');
  const [filterForm, setFilterForm] = useState('');
  const [topK, setTopK] = useState(5);
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [resultsCountText, setResultsCountText] = useState('0 chunks found');

  // Evaluation Tab State
  const [evalForm, setEvalForm] = useState('10-K');
  const [evalRunning, setEvalRunning] = useState(false);
  const [evalResults, setEvalResults] = useState(null); // { mrr, details }

  // Database Inspector Tab State
  const [inspectData, setInspectData] = useState([]);
  const [inspectLoading, setInspectLoading] = useState(false);
  const [inspectFilterTicker, setInspectFilterTicker] = useState('');
  const [inspectFilterForm, setInspectFilterForm] = useState('');
  const [inspectFilterItem, setInspectFilterItem] = useState('');
  const [inspectCurrentPage, setInspectCurrentPage] = useState(1);
  const itemsPerPage = 15;

  // Modal Details State
  const [selectedChunk, setSelectedChunk] = useState(null); // chunk object or null

  // Fetch Ingestion Registry
  const fetchRegistry = async () => {
    try {
      const res = await fetch('/api/registry');
      const data = await res.json();
      setRegistry(data);
      setUniqueTickers(Object.keys(data));
    } catch (err) {
      console.error('Failed to load registry:', err);
    }
  };

  // Run Ingestion Pipeline
  const handleIngest = async () => {
    const tickerClean = ingestTicker.trim().toUpperCase();
    if (!tickerClean) {
      alert('Please specify a ticker symbol.');
      return;
    }
    setIngesting(true);
    setIngestStatus({ type: 'info', text: `Running concurrent SEC download and parsing pipeline for ${tickerClean}...` });
    try {
      const res = await fetch('/api/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: tickerClean })
      });
      const data = await res.json();
      if (res.ok) {
        setIngestStatus({ type: 'success', text: data.message || 'Ingestion completed successfully.' });
        fetchRegistry();
      } else {
        setIngestStatus({ type: 'error', text: data.error || 'Ingestion failed.' });
      }
    } catch (err) {
      setIngestStatus({ type: 'error', text: 'Network or server error occurred.' });
      console.error(err);
    } finally {
      setIngesting(false);
    }
  };

  // Run Delete Ticker data
  const handleDeleteTicker = async () => {
    if (!deleteTicker) {
      alert('Please select a ticker to delete.');
      return;
    }
    if (!window.confirm(`Are you sure you want to delete all vectors, chunks, and cached ingestion records for ticker ${deleteTicker}?`)) {
      return;
    }
    setDeleting(true);
    setDeleteStatus({ type: 'info', text: `Deleting vector chunks and cache registry for ${deleteTicker}...` });
    try {
      const res = await fetch('/api/delete-ticker', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: deleteTicker })
      });
      const data = await res.json();
      if (res.ok) {
        setDeleteStatus({ type: 'success', text: data.message || `Deleted data for ${deleteTicker} successfully.` });
        setDeleteTicker('');
        fetchRegistry();
      } else {
        setDeleteStatus({ type: 'error', text: data.error || 'Failed to delete data.' });
      }
    } catch (err) {
      setDeleteStatus({ type: 'error', text: 'Network or server error occurred.' });
      console.error(err);
    } finally {
      setDeleting(false);
    }
  };

  // Run Search query
  const handleSearch = async () => {
    const qClean = searchQuery.trim();
    if (!qClean) {
      alert('Please enter a query.');
      return;
    }
    setSearching(true);
    setSearchResults([]);
    try {
      const res = await fetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          query: qClean, 
          ticker: filterTicker, 
          form_type: filterForm, 
          top_k: topK 
        })
      });
      const data = await res.json();
      if (res.ok) {
        setSearchResults(data.candidates || []);
        setResultsCountText(`${(data.candidates || []).length} chunks found`);
      } else {
        throw new Error(data.error || 'Query failed');
      }
    } catch (err) {
      console.error(err);
      alert(`Search error: ${err.message}`);
    } finally {
      setSearching(false);
    }
  };

  // Run Evaluations
  const handleRunEval = async () => {
    setEvalRunning(true);
    setEvalResults(null);
    try {
      const res = await fetch('/api/eval', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ form_type: evalForm })
      });
      const data = await res.json();
      if (res.ok) {
        setEvalResults({
          mrr: data.mrr || 0,
          details: data.details || []
        });
      } else {
        alert(data.error || 'Evaluation run failed');
      }
    } catch (err) {
      console.error(err);
      alert('Evaluation network error');
    } finally {
      setEvalRunning(false);
    }
  };

  // Fetch Inspect items
  const fetchInspectData = async () => {
    setInspectLoading(true);
    try {
      const res = await fetch('/api/inspect');
      const data = await res.json();
      setInspectData(data.items || []);
      setInspectCurrentPage(1);
    } catch (err) {
      console.error('Failed to load inspect data:', err);
    } finally {
      setInspectLoading(false);
    }
  };

  useEffect(() => {
    fetchRegistry();
  }, []);

  useEffect(() => {
    if (activeTab === 'inspect') {
      fetchInspectData();
    }
  }, [activeTab]);

  // Derived options for Inspect Filters
  const inspectTickers = Array.from(new Set(inspectData.map(item => item.metadata?.ticker).filter(Boolean)));
  const inspectForms = Array.from(new Set(
    inspectData
      .filter(item => !inspectFilterTicker || item.metadata?.ticker === inspectFilterTicker)
      .map(item => item.metadata?.form_type)
      .filter(Boolean)
  ));
  const inspectItems = Array.from(new Set(
    inspectData
      .filter(item => 
        (!inspectFilterTicker || item.metadata?.ticker === inspectFilterTicker) && 
        (!inspectFilterForm || item.metadata?.form_type === inspectFilterForm)
      )
      .map(item => item.metadata?.item_name)
      .filter(Boolean)
  ));

  // Filter inspect items
  const filteredInspectItems = inspectData.filter(item => {
    const meta = item.metadata || {};
    if (inspectFilterTicker && meta.ticker !== inspectFilterTicker) return false;
    if (inspectFilterForm && meta.form_type !== inspectFilterForm) return false;
    if (inspectFilterItem && meta.item_name !== inspectFilterItem) return false;
    return true;
  });

  const totalInspectPages = Math.ceil(filteredInspectItems.length / itemsPerPage) || 1;
  const startIdx = (inspectCurrentPage - 1) * itemsPerPage;
  const pageItems = filteredInspectItems.slice(startIdx, startIdx + itemsPerPage);

  const handleOpenChunkDetails = (chunk) => {
    setSelectedChunk(chunk);
  };

  return (
    <div className="chroma-explorer-layout">
      {/* Sidebar Panel */}
      <aside className="sidebar">
        <div className="brand">
          <div className="logo-icon"></div>
          <h2>Chroma Explorer</h2>
        </div>
        
        {/* Ingest Filings */}
        <div className="sidebar-section">
          <h3>Ingest Filings</h3>
          <div className="form-group">
            <label htmlFor="ticker-select">Ticker Symbol</label>
            <input 
              type="text" 
              id="ticker-select" 
              value={ingestTicker} 
              onChange={(e) => setIngestTicker(e.target.value.toUpperCase())}
              placeholder="e.g. AAPL, NVDA"
            />
          </div>
          <button onClick={handleIngest} disabled={ingesting} className="btn-primary" style={{ width: '100%' }}>
            <span className="btn-text">Ingest All Corporate Data</span>
            {ingesting && <span className="spinner" id="ingest-spinner"></span>}
          </button>
          {ingestStatus.text && (
            <div id="ingest-status" className={`status-msg ${ingestStatus.type === 'success' ? 'success-msg' : ingestStatus.type === 'error' ? 'error-msg' : ''}`}>
              {ingestStatus.text}
            </div>
          )}
        </div>
        
        {/* Delete Ticker Section */}
        <div className="sidebar-section">
          <h3>Delete Ticker Data</h3>
          <div className="form-group">
            <label htmlFor="delete-ticker-select">Select Ticker</label>
            <select 
              id="delete-ticker-select" 
              className="form-control"
              style={{ width: '100%', padding: '8px', borderRadius: '6px', backgroundColor: 'var(--bg-panel)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
              value={deleteTicker} 
              onChange={(e) => setDeleteTicker(e.target.value)}
            >
              <option value="">-- Choose Ticker --</option>
              {uniqueTickers.map(t => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
          <button onClick={handleDeleteTicker} disabled={deleting || !deleteTicker} className="btn-danger" style={{ width: '100%' }}>
            <span className="btn-text">Delete Ticker Vector Data</span>
            {deleting && <span className="spinner" id="delete-spinner"></span>}
          </button>
          {deleteStatus.text && (
            <div id="delete-status" className={`status-msg ${deleteStatus.type === 'success' ? 'success-msg' : deleteStatus.type === 'error' ? 'error-msg' : ''}`}>
              {deleteStatus.text}
            </div>
          )}
        </div>
        
        {/* Cached Registry */}
        <div className="sidebar-section registry-section">
          <h3>Cached Registry</h3>
          <div className="table-container">
            <table id="registry-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Form</th>
                  <th>Filing Date</th>
                </tr>
              </thead>
              <tbody>
                {uniqueTickers.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="empty-msg">No data ingested yet</td>
                  </tr>
                ) : (
                  Object.entries(registry).flatMap(([ticker, forms]) => 
                    Object.entries(forms).map(([formType, meta]) => (
                      <tr key={`${ticker}-${formType}`}>
                        <td><strong>{ticker}</strong></td>
                        <td><span className="badge badge-form">{formType}</span></td>
                        <td className="code-font">{meta.filing_date || 'N/A'}</td>
                      </tr>
                    ))
                  )
                )}
              </tbody>
            </table>
          </div>
        </div>

        <button onClick={onNavigateBack} className="btn-back-workspace">
          Back to Workspace
        </button>
      </aside>
      
      {/* Main Content Pane */}
      <main className="main-content">
        <header className="main-header">
          <div className="header-tabs">
            <button 
              className={`tab-btn ${activeTab === 'search' ? 'active' : ''}`}
              onClick={() => setActiveTab('search')}
            >
              Search & RAG Pipeline
            </button>
            <button 
              className={`tab-btn ${activeTab === 'evals' ? 'active' : ''}`}
              onClick={() => setActiveTab('evals')}
            >
              Evaluation Dashboard
            </button>
            <button 
              className={`tab-btn ${activeTab === 'inspect' ? 'active' : ''}`}
              onClick={() => setActiveTab('inspect')}
            >
              Database Inspector
            </button>
          </div>
          <div className="db-badge">
            <span className="badge-dot"></span>
            <span id="db-status-text">Ephemeral In-Memory Chroma DB</span>
          </div>
        </header>
        
        {/* Tab: Search & Pipeline */}
        {activeTab === 'search' && (
          <section id="tab-search" className="tab-pane active">
            <div className="search-bar-container">
              <div className="search-input-wrapper">
                <input 
                  type="text" 
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Ask a question about filings, e.g. What is Blackwell architecture demand drivers?"
                  onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                />
                <button onClick={handleSearch} disabled={searching} className="btn-search">Search</button>
              </div>
              <div className="search-options">
                <div className="option-item">
                  <label htmlFor="filter-ticker">Filter Ticker</label>
                  <select id="filter-ticker" value={filterTicker} onChange={(e) => setFilterTicker(e.target.value)}>
                    <option value="">No Filter</option>
                    {uniqueTickers.map(t => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
                <div className="option-item">
                  <label htmlFor="filter-form">Filter Form</label>
                  <select id="filter-form" value={filterForm} onChange={(e) => setFilterForm(e.target.value)}>
                    <option value="">No Filter</option>
                    <option value="10-K">10-K</option>
                    <option value="10-Q">10-Q</option>
                    <option value="8-K">8-K</option>
                    <option value="Earnings">Earnings Transcript</option>
                  </select>
                </div>
                <div className="option-item slider-item">
                  <label htmlFor="top-k-slider">Candidates: <span id="top-k-val">{topK}</span></label>
                  <input 
                    type="range" 
                    id="top-k-slider" 
                    min="1" 
                    max="15" 
                    value={topK}
                    onChange={(e) => setTopK(parseInt(e.target.value))}
                  />
                </div>
              </div>
            </div>
            
            {/* Visual Pipeline Layout */}
            <div className="pipeline-flow">
              {[
                'Metadata Filter',
                'Hybrid (Vector/BM25)',
                'RRF Ranker',
                'Cross Encoder',
                'Deduplicate',
                'Parent Retrieval'
              ].map((step, idx) => (
                <React.Fragment key={step}>
                  <div className={`flow-step ${idx === 5 ? 'highlight' : ''}`}>
                    <div className="step-num">{idx + 1}</div>
                    <div className="step-label">{step}</div>
                  </div>
                  {idx < 5 && <div className="flow-arrow">→</div>}
                </React.Fragment>
              ))}
            </div>
            
            {/* Results Layout */}
            <div className="results-layout">
              <div className="results-header">
                <h3>Retrieved Candidates</h3>
                <span id="results-count" className="results-count">{resultsCountText}</span>
              </div>
              
              {searching ? (
                <div className="empty-state">
                  <span className="spinner" style={{ display: 'inline-block' }}></span>
                  <p style={{ marginTop: '1rem' }}>Executing hybrid search and pipeline rankings...</p>
                </div>
              ) : searchResults.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-state-icon">🔍</div>
                  <p>Enter a query above to execute the RAG search pipeline</p>
                </div>
              ) : (
                <div className="results-grid">
                  {searchResults.map((cand, idx) => {
                    const meta = cand.metadata || {};
                    return (
                      <div 
                        key={idx} 
                        className="card"
                        onClick={() => handleOpenChunkDetails(cand)}
                      >
                        <div className="card-header">
                          <div className="card-tags">
                            <span className="badge badge-ticker">{meta.ticker || 'UNKNOWN'}</span>
                            <span className="badge badge-form">{meta.form_type || 'UNKNOWN'}</span>
                            <span className="badge badge-item">{meta.item_name || 'Item'}</span>
                          </div>
                          <div className="score-badge">Rank #{idx + 1}</div>
                        </div>
                        <div className="card-body">
                          {cand.text}
                        </div>
                        <div className="card-footer">
                          <span>Filing Date: {meta.filing_date || 'N/A'}</span>
                          <span>Length: {meta.char_count || cand.text.length} chars</span>
                        </div>
                        <div className="card-footer" style={{ borderTop: '1px dashed var(--border-color)', paddingTop: '0.5rem', justifyContent: 'flex-start', gap: '0.5rem', wordBreak: 'break-all' }}>
                          <strong>URL:</strong> 
                          <a 
                            href={meta.url || '#'} 
                            target="_blank" 
                            rel="noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            style={{ color: 'var(--primary)', textDecoration: 'underline', fontSize: '0.7rem' }}
                          >
                            {meta.url || 'N/A'}
                          </a>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </section>
        )}
        
        {/* Tab: Evaluation Dashboard */}
        {activeTab === 'evals' && (
          <section id="tab-evals" className="tab-pane active">
            <div className="eval-control-panel">
              <div className="eval-desc">
                <h3>Mean Reciprocal Rank (MRR@5) Evaluation</h3>
                <p>Evaluates retrieval relevance across pre-defined queries for selected document types, comparing matches inside the hybrid search index.</p>
              </div>
              <div className="eval-actions">
                <select id="eval-form-select" value={evalForm} onChange={(e) => setEvalForm(e.target.value)}>
                  <option value="10-K">Form 10-K</option>
                  <option value="10-Q">Form 10-Q</option>
                  <option value="8-K">Form 8-K</option>
                  <option value="Earnings">Earnings Transcripts</option>
                </select>
                <button 
                  onClick={handleRunEval} 
                  disabled={evalRunning} 
                  className="btn-primary"
                >
                  <span className="btn-text">Run Evaluation Suite</span>
                  {evalRunning && <span className="spinner" id="eval-spinner"></span>}
                </button>
              </div>
            </div>
            
            <div className="eval-results-container">
              <div className="eval-mrr-card">
                <h4>MRR Score</h4>
                <div id="mrr-val-display" className="mrr-value">
                  {evalRunning ? '...' : evalResults ? evalResults.mrr.toFixed(4) : '0.0000'}
                </div>
                <div className="progress-bar-bg">
                  <div 
                    id="mrr-progress-bar" 
                    className="progress-bar-fill" 
                    style={{ width: `${(evalResults?.mrr || 0) * 100}%` }}
                  ></div>
                </div>
              </div>
              
              <div className="eval-details-card">
                <h4>Query Execution Detail</h4>
                <div className="table-container">
                  <table id="eval-details-table">
                    <thead>
                      <tr>
                        <th>Query</th>
                        <th>Expected Target Section</th>
                        <th>Matched Rank</th>
                        <th>Score Contribution</th>
                      </tr>
                    </thead>
                    <tbody>
                      {evalRunning ? (
                        <tr>
                          <td colSpan={4} className="empty-msg">Running retrieval evaluations...</td>
                        </tr>
                      ) : !evalResults ? (
                        <tr>
                          <td colSpan={4} className="empty-msg">No evaluation run has completed</td>
                        </tr>
                      ) : evalResults.details.length === 0 ? (
                        <tr>
                          <td colSpan={4} className="empty-msg">No details returned</td>
                        </tr>
                      ) : (
                        evalResults.details.map((row, idx) => (
                          <tr key={idx}>
                            <td><strong>{row.query}</strong></td>
                            <td><span className="badge badge-item">{row.expected}</span></td>
                            <td className="code-font" style={{ color: row.rank <= 5 ? 'var(--success)' : 'var(--danger)' }}>
                              Rank {row.rank === 999 ? 'N/A' : row.rank}
                            </td>
                            <td className="code-font font-bold">{row.reciprocal_rank.toFixed(4)}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </section>
        )}
        
        {/* Tab: Database Inspector */}
        {activeTab === 'inspect' && (
          <section id="tab-inspect" className="tab-pane active">
            <div className="eval-control-panel">
              <div className="eval-desc">
                <h3>Database Inspector</h3>
                <p>View all stored chunks, vector documents, and metadata inside the active database collection.</p>
              </div>
              <div className="eval-actions">
                <button onClick={fetchInspectData} disabled={inspectLoading} className="btn-primary">
                  <span>Refresh Collection Contents</span>
                </button>
              </div>
            </div>
            
            <div className="search-options" style={{ backgroundColor: 'var(--bg-panel)', border: '1px solid var(--border-color)', borderRadius: '12px', padding: '1.25rem', display: 'flex', gap: '2rem', alignItems: 'center' }}>
              <div className="option-item">
                <label htmlFor="inspect-filter-ticker">Filter Ticker</label>
                <select 
                  id="inspect-filter-ticker"
                  value={inspectFilterTicker} 
                  onChange={(e) => {
                    setInspectFilterTicker(e.target.value);
                    setInspectFilterForm('');
                    setInspectFilterItem('');
                    setInspectCurrentPage(1);
                  }}
                >
                  <option value="">All Tickers</option>
                  {inspectTickers.map(t => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <div className="option-item">
                <label htmlFor="inspect-filter-form">Filter Form</label>
                <select 
                  id="inspect-filter-form"
                  value={inspectFilterForm} 
                  onChange={(e) => {
                    setInspectFilterForm(e.target.value);
                    setInspectFilterItem('');
                    setInspectCurrentPage(1);
                  }}
                  disabled={!inspectFilterTicker}
                >
                  <option value="">All Forms</option>
                  {inspectForms.map(f => (
                    <option key={f} value={f}>{f}</option>
                  ))}
                </select>
              </div>
              <div className="option-item">
                <label htmlFor="inspect-filter-item">Filter Section / Item</label>
                <select 
                  id="inspect-filter-item"
                  value={inspectFilterItem} 
                  onChange={(e) => {
                    setInspectFilterItem(e.target.value);
                    setInspectCurrentPage(1);
                  }}
                  disabled={!inspectFilterForm}
                >
                  <option value="">All Sections</option>
                  {inspectItems.map(i => (
                    <option key={i} value={i}>{i}</option>
                  ))}
                </select>
              </div>
            </div>
            
            <div className="table-container">
              <table id="inspect-table">
                <thead>
                  <tr>
                    <th style={{ width: '140px' }}>ID</th>
                    <th style={{ width: '80px' }}>Ticker</th>
                    <th style={{ width: '80px' }}>Form</th>
                    <th style={{ width: '150px' }}>Section / Item</th>
                    <th>Document Chunk Content</th>
                  </tr>
                </thead>
                <tbody>
                  {inspectLoading ? (
                    <tr>
                      <td colSpan={5} className="empty-msg">Querying collection...</td>
                    </tr>
                  ) : pageItems.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="empty-msg">No chunks match the selected filters</td>
                    </tr>
                  ) : (
                    pageItems.map((item) => {
                      const meta = item.metadata || {};
                      const snippet = item.text.length > 120 ? item.text.substring(0, 120) + '...' : item.text;
                      return (
                        <tr 
                          key={item.id} 
                          style={{ cursor: 'pointer' }}
                          onClick={() => handleOpenChunkDetails(item)}
                        >
                          <td className="code-font">{item.id}</td>
                          <td><span className="badge badge-ticker">{meta.ticker || 'N/A'}</span></td>
                          <td><span className="badge badge-form">{meta.form_type || 'N/A'}</span></td>
                          <td><span className="badge badge-item">{meta.item_name || 'N/A'}</span></td>
                          <td style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>{snippet}</td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
            
            {/* Pagination Controls */}
            <div className="pagination-container" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '1rem', padding: '0 0.5rem' }}>
              <div id="pagination-info" style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                {filteredInspectItems.length === 0 
                  ? 'Showing 0-0 of 0 entries'
                  : `Showing ${startIdx + 1}-${Math.min(startIdx + itemsPerPage, filteredInspectItems.length)} of ${filteredInspectItems.length} entries (Page ${inspectCurrentPage} of ${totalInspectPages})`
                }
              </div>
              <div className="pagination-buttons" style={{ display: 'flex', gap: '0.5rem' }}>
                <button 
                  id="btn-page-prev" 
                  className="btn-primary" 
                  style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem' }}
                  disabled={inspectCurrentPage === 1}
                  onClick={() => setInspectCurrentPage(prev => Math.max(prev - 1, 1))}
                >
                  Previous
                </button>
                <button 
                  id="btn-page-next" 
                  className="btn-primary" 
                  style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem' }}
                  disabled={inspectCurrentPage === totalInspectPages}
                  onClick={() => setInspectCurrentPage(prev => Math.min(prev + 1, totalInspectPages))}
                >
                  Next
                </button>
              </div>
            </div>
          </section>
        )}
      </main>
      
      {/* Detail Chunk Modal Popup */}
      {selectedChunk && (
        <div id="parent-modal" className="modal show-modal" onClick={() => setSelectedChunk(null)}>
          <div className="modal-content glass" onClick={(e) => e.stopPropagation()}>
            <span className="close-modal" onClick={() => setSelectedChunk(null)}>&times;</span>
            <div className="modal-header-meta">
              <span id="modal-meta-ticker" className="modal-badge badge-ticker">{selectedChunk.metadata?.ticker || 'UNKNOWN'}</span>
              <span id="modal-meta-form" className="modal-badge badge-form">{selectedChunk.metadata?.form_type || 'UNKNOWN'}</span>
              <span id="modal-meta-item" className="modal-badge badge-item">{selectedChunk.metadata?.item_name || 'Item'}</span>
            </div>
            <h3 id="modal-title">Parent Document Context</h3>
            <div className="modal-body-container">
              <div className="modal-split">
                <div className="modal-pane-chunk">
                  <h4>Retrieved Embedded Chunk</h4>
                  <div className="chunk-metadata-box" style={{ marginBottom: '0.75rem', fontSize: '0.75rem', backgroundColor: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-color)', borderRadius: '6px', padding: '0.5rem 0.75rem', display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0.4rem', color: 'var(--text-secondary)' }}>
                    <div><strong>Ticker:</strong> <span>{selectedChunk.metadata?.ticker || 'N/A'}</span></div>
                    <div><strong>Form Type:</strong> <span>{selectedChunk.metadata?.form_type || 'N/A'}</span></div>
                    <div><strong>Section:</strong> <span>{selectedChunk.metadata?.item_name || 'N/A'}</span></div>
                    <div><strong>Filing Date:</strong> <span>{selectedChunk.metadata?.filing_date || 'N/A'}</span></div>
                    <div><strong>Char Count:</strong> <span>{selectedChunk.metadata?.char_count || selectedChunk.text.length}</span></div>
                    <div><strong>Word Count:</strong> <span>{selectedChunk.metadata?.word_count || selectedChunk.text.split(/\s+/).length}</span></div>
                    <div style={{ gridColumn: 'span 2', wordBreak: 'break-all' }}><strong>Source URL:</strong> <a id="modal-chunk-url" href={selectedChunk.metadata?.url || '#'} target="_blank" rel="noreferrer" style={{ color: 'var(--primary)', textDecoration: 'underline' }}>{selectedChunk.metadata?.url || 'N/A'}</a></div>
                  </div>
                  <div id="modal-chunk-text" className="code-font text-highlight">{selectedChunk.text}</div>
                </div>
                <div className="modal-pane-parent">
                  <h4>Resolved Parent Plaintext</h4>
                  <div id="modal-parent-text" className="code-font scrollable-text">
                    {selectedChunk.parent_text || 
                     (selectedChunk.metadata?.ticker && selectedChunk.metadata?.form_type && selectedChunk.metadata?.item_name ? 
                      'Parent document text is resolved dynamically in the RAG pipeline view.' :
                      'No parent metadata associated.')
                    }
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ChromaExplorer;
