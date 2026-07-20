import React, { useState, useEffect } from 'react';
import { marked } from 'marked';
import { 
  TrendingUp, 
  Building, 
  DollarSign, 
  Coins, 
  Share2, 
  Play, 
  Cpu, 
  CheckCircle2, 
  Circle, 
  Loader2, 
  Sliders,
  Search,
  BarChart3,
  AlertTriangle,
  Scale,
  FileSpreadsheet,
  Gavel,
  Newspaper
} from 'lucide-react';
import './App.css';
import ChromaExplorer from './ChromaExplorer';

// Forward frontend logs to FastAPI log folder
const sendLogToBackend = (level, ...args) => {
  const message = args.map(arg => typeof arg === 'object' ? JSON.stringify(arg) : String(arg)).join(' ');
  const timestamp = new Date().toISOString();
  fetch('/api/log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ level, message, timestamp })
  }).catch(() => {});
};
const originalLog = console.log;
const originalError = console.error;
const originalWarn = console.warn;
const originalInfo = console.info;
console.log = (...args) => { originalLog(...args); sendLogToBackend('info', ...args); };
console.error = (...args) => { originalError(...args); sendLogToBackend('error', ...args); };
console.warn = (...args) => { originalWarn(...args); sendLogToBackend('warning', ...args); };
console.info = (...args) => { originalInfo(...args); sendLogToBackend('info', ...args); };

const nodeConfig = {
  research: { label: 'Research', key: 'research_report', icon: Search, tabIndex: 0 },
  financial: { label: 'Financial', key: 'financial_report', icon: BarChart3, tabIndex: 1 },
  risk: { label: 'Risk', key: 'risk_report', icon: AlertTriangle, tabIndex: 2 },
  news: { label: 'News', key: 'latest_news_report', icon: Newspaper, tabIndex: 3 },
  valuation: { label: 'Valuation', key: 'valuation_report', icon: Scale, tabIndex: 4 },
  summary: { label: 'Summary', key: 'investment_summary', icon: FileSpreadsheet, tabIndex: 5 },
  committee: { label: 'Committee', key: 'committee_decision', icon: Gavel, tabIndex: 6 },
};

function App() {
  const [ticker, setTicker] = useState('ORCL');
  const [companyName, setCompanyName] = useState('Oracle Corporation');
  
  const [activeTab, setActiveTab] = useState(0);
  const [loading, setLoading] = useState(false);
  const [agentStates, setAgentStates] = useState({
    research: 'idle', // 'idle' | 'running' | 'completed'
    financial: 'idle',
    risk: 'idle',
    news: 'idle',
    valuation: 'idle',
    summary: 'idle',
    committee: 'idle',
  });

  const [reports, setReports] = useState({
    research_report: '',
    financial_report: '',
    risk_report: '',
    latest_news_report: '',
    valuation_report: '',
    investment_summary: '',
    committee_decision: '',
  });

  const [viewMode, setViewMode] = useState(
    window.location.pathname === '/database' ? 'database' : 
    window.location.pathname === '/chroma' ? 'chroma' : 'workspace'
  );
  const [dbRecords, setDbRecords] = useState([]);
  const [loadingDb, setLoadingDb] = useState(false);
  const [expandedRecordId, setExpandedRecordId] = useState(null);
  const [expandedTab, setExpandedTab] = useState('research_report');
  const [deleteTicker, setDeleteTicker] = useState('');

  const fetchDbRecords = async () => {
    setLoadingDb(true);
    try {
      const response = await fetch('/api/db/records');
      const data = await response.json();
      if (data.records) {
        setDbRecords(data.records);
      } else {
        alert(data.error || 'Failed to fetch database records');
      }
    } catch (err) {
      console.error(err);
      alert('Error connecting to database explorer API');
    } finally {
      setLoadingDb(false);
    }
  };
  const handleDeleteTickerRecords = async () => {
    if (!deleteTicker.trim()) {
      alert('Please specify a ticker to delete');
      return;
    }
    if (!window.confirm(`Are you sure you want to delete all consensus reports for ticker ${deleteTicker}?`)) {
      return;
    }
    try {
      const response = await fetch(`/api/db/records/ticker/${deleteTicker}`, {
        method: 'DELETE'
      });
      const data = await response.json();
      if (data.status === 'success') {
        alert(`Successfully deleted all records for ${deleteTicker}.`);
        setDeleteTicker('');
        fetchDbRecords();
      } else {
        alert(data.error || 'Failed to delete records');
      }
    } catch (err) {
      console.error(err);
      alert('Error calling delete API');
    }
  };


  useEffect(() => {
    const handlePopState = () => {
      setViewMode(
        window.location.pathname === '/database' ? 'database' : 
        window.location.pathname === '/chroma' ? 'chroma' : 'workspace'
      );
    };
    window.addEventListener('popstate', handlePopState);
    if (window.location.pathname === '/database') {
      fetchDbRecords();
    }
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const handleNavigate = (mode) => {
    setViewMode(mode);
    if (mode === 'database') {
      window.history.pushState(null, '', '/database');
      fetchDbRecords();
    } else if (mode === 'chroma') {
      window.history.pushState(null, '', '/chroma');
    } else {
      window.history.pushState(null, '', '/');
    }
  };


  const handleLoadDbRecord = (record) => {
    setTicker(record.ticker);
    setCompanyName(record.ticker + " (Loaded Run)");
    setReports({
      research_report: record.research_report || '',
      financial_report: record.financial_report || '',
      risk_report: record.risk_report || '',
      latest_news_report: record.latest_news_report || '',
      valuation_report: record.valuation_report || '',
      investment_summary: record.investment_summary || '',
      committee_decision: record.committee_decision || '',
    });
    setAgentStates({
      research: record.research_report ? 'completed' : 'idle',
      financial: record.financial_report ? 'completed' : 'idle',
      risk: record.risk_report ? 'completed' : 'idle',
      news: record.latest_news_report ? 'completed' : 'idle',
      valuation: record.valuation_report ? 'completed' : 'idle',
      summary: record.investment_summary ? 'completed' : 'idle',
      committee: record.committee_decision ? 'completed' : 'idle',
    });
    setViewMode('workspace');
    window.history.pushState(null, '', '/');
    setActiveTab(0); // Focus research first
  };

  const handleStartWorkflow = async (e) => {
    e.preventDefault();
    setLoading(true);
    
    // Reset state
    setAgentStates({
      research: 'idle',
      financial: 'idle',
      risk: 'idle',
      valuation: 'idle',
      summary: 'idle',
      committee: 'idle',
    });
    setReports({
      research_report: '',
      financial_report: '',
      risk_report: '',
      valuation_report: '',
      investment_summary: '',
      committee_decision: '',
    });
    setActiveTab(0);

    try {
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker,
          company_name: companyName
        })
      });

      if (!response.ok) {
        throw new Error(`Server returned error status: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      // Move research state to running immediately
      setAgentStates(prev => ({ ...prev, research: 'running' }));

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const dataStr = line.slice(6).trim();
            if (!dataStr) continue;

            const data = JSON.parse(dataStr);
            
            if (data.error) {
              alert(`Error: ${data.error}`);
              break;
            }

            if (data.node) {
              const node = data.node;
              const config = nodeConfig[node];
              
              if (config) {
                // Update specific report content
                setReports(prev => ({
                  ...prev,
                  [config.key]: data.updates[config.key] || ''
                }));

                // Mark current node as completed
                setAgentStates(prev => {
                  const updated = { ...prev, [node]: 'completed' };
                  
                  // Transition downstream nodes to running
                  if (node === 'research') {
                    updated.financial = 'running';
                    updated.risk = 'running';
                  } else if (node === 'financial' || node === 'risk') {
                    if (updated.financial === 'completed' && updated.risk === 'completed') {
                      updated.valuation = 'running';
                    }
                  } else if (node === 'valuation') {
                    updated.summary = 'running';
                  } else if (node === 'summary') {
                    updated.committee = 'running';
                  }

                  return updated;
                });

                // Auto shift focus to completed tab
                setActiveTab(config.tabIndex);
              }
            }
          }
        }
      }
    } catch (err) {
      console.error(err);
      alert(`Workflow execution failed: ${err.message}`);
    } finally {
      setLoading(false);
      setAgentStates(prev => {
        const final = { ...prev };
        Object.keys(final).forEach(k => {
          if (final[k] === 'running') final[k] = 'idle';
        });
        return final;
      });
    }
  };

  return (
    <div className="app-container">
      {/* Background glow glows */}
      <div className="glass-bg-glows">
        <div className="glow-sphere sphere-1"></div>
        <div className="glow-sphere sphere-2"></div>
      </div>

      {/* Main Grid Layout */}
      {viewMode === 'workspace' && (
        <main className="app-main">
          {/* Sidebar Parameters Form */}
          <section className="sidebar-card">
            <form onSubmit={handleStartWorkflow} className="parameters-form">
              <div className="form-group">
                <label>Stock Ticker</label>
                <div className="input-wrapper">
                  <TrendingUp size={16} />
                  <input 
                    type="text" 
                    value={ticker} 
                    onChange={(e) => setTicker(e.target.value.toUpperCase())}
                    required 
                  />
                </div>
              </div>
              
              <div className="form-group">
                <label>Company Name</label>
                <div className="input-wrapper">
                  <Building size={16} />
                  <input 
                    type="text" 
                    value={companyName} 
                    onChange={(e) => setCompanyName(e.target.value)}
                    required 
                  />
                </div>
              </div>

              <button type="submit" disabled={loading} className="submit-btn">
                {loading ? (
                  <>
                    <span>Processing...</span>
                    <Loader2 size={18} className="fa-spin" />
                  </>
                ) : (
                  <>
                    <span>Generate Report</span>
                    <Play size={16} />
                  </>
                )}
              </button>
            </form>

            {/* Node Activity Monitor */}
            <div className="monitor-panel">
              <div className="monitor-header">
                <h3>Agent Activity Monitor</h3>
              </div>
              <div className="agent-nodes-list">
                {Object.entries(nodeConfig).map(([nodeName, config]) => {
                  const state = agentStates[nodeName];
                  return (
                    <div key={nodeName} className={`agent-node ${state}`}>
                      <span className="node-dot"></span>
                      <span className="node-label">{config.label}</span>
                      <span className="node-status">
                        {state === 'running' && 'Running'}
                        {state === 'completed' && 'Completed'}
                        {state === 'idle' && 'Idle'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </section>

          {/* Tab-based Results Panel */}
          <section className="results-container">
            <div className="tabs-nav">
              {Object.entries(nodeConfig).map(([nodeName, config]) => {
                const Icon = config.icon;
                const isCompleted = agentStates[nodeName] === 'completed';
                return (
                  <button
                    key={nodeName}
                    className={`tab-btn ${activeTab === config.tabIndex ? 'active' : ''} ${isCompleted ? 'completed-tab' : ''}`}
                    onClick={() => setActiveTab(config.tabIndex)}
                  >
                    <Icon size={16} />
                    <span>{config.label.split(' ')[0]}</span>
                  </button>
                );
              })}
            </div>

            <div className="tabs-content">
              {Object.entries(nodeConfig).map(([nodeName, config]) => {
                const state = agentStates[nodeName];
                const content = reports[config.key];
                
                if (activeTab !== config.tabIndex) return null;

                return (
                  <div key={nodeName} className="tab-panel active">
                    {state === 'idle' && !content && (
                      <div className="placeholder-msg">
                        <Sliders size={40} />
                        <p>Configure parameters and initiate the consensus workflow to start analysis.</p>
                      </div>
                    )}
                    {state === 'running' && (
                      <div className="loading-spinner">
                        <div className="spinner"></div>
                        <p className="loading-text">Agent executing analysis model...</p>
                      </div>
                    )}
                    {content && (
                      <div 
                        className="report-render markdown-body"
                        dangerouslySetInnerHTML={{ __html: marked.parse(content) }}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        </main>
      )}
      {viewMode === 'database' && (
        <main className="db-explorer-main">
          <div className="db-explorer-card">
            <div className="card-header-row">
              <h3>Relational Database Records</h3>
              <div className="db-actions-row" style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <input 
                  type="text" 
                  placeholder="Ticker (e.g. APH)" 
                  value={deleteTicker} 
                  onChange={(e) => setDeleteTicker(e.target.value.toUpperCase())}
                  className="delete-ticker-input"
                  style={{
                    padding: '6px 12px',
                    borderRadius: '6px',
                    border: '1px solid rgba(255,255,255,0.15)',
                    background: 'rgba(0,0,0,0.2)',
                    color: '#fff',
                    outline: 'none',
                    width: '140px'
                  }}
                />
                <button 
                  onClick={handleDeleteTickerRecords} 
                  className="delete-btn"
                  style={{
                    background: 'rgba(239, 68, 68, 0.2)',
                    border: '1px solid rgba(239, 68, 68, 0.4)',
                    color: '#f87171',
                    padding: '6px 12px',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    fontSize: '13px'
                  }}
                >
                  Delete Ticker Records
                </button>
                <button onClick={fetchDbRecords} className="refresh-btn">Refresh Records</button>
              </div>
            </div>
            
            {loadingDb ? (
              <div className="loading-spinner">
                <div className="spinner"></div>
                <p>Loading database records...</p>
              </div>
            ) : dbRecords.length === 0 ? (
              <div className="placeholder-msg">
                <p>No historical consensus records found in SQLite.</p>
              </div>
            ) : (
              <div className="db-table-wrapper">
                <table className="db-records-table">
                  <thead>
                    <tr>
                      <th>Record ID</th>
                      <th>Ticker</th>
                      <th>Execution Timestamp</th>
                      <th>Reports Saved</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dbRecords.map((record) => {
                      const savedCount = [
                        record.research_report,
                        record.financial_report,
                        record.risk_report,
                        record.latest_news_report,
                        record.valuation_report,
                        record.investment_summary,
                        record.committee_decision
                      ].filter(Boolean).length;

                      return (
                        <React.Fragment key={record.id}>
                          <tr 
                            onClick={() => setExpandedRecordId(expandedRecordId === record.id ? null : record.id)}
                            className={`summary-row ${expandedRecordId === record.id ? 'row-expanded' : ''}`}
                            style={{ cursor: 'pointer' }}
                          >
                            <td className="record-id">#{record.id}</td>
                            <td className="record-ticker">{record.ticker}</td>
                            <td className="record-date">{new Date(record.created_date).toLocaleString()}</td>
                            <td className="record-count">
                              <span className={`count-badge ${savedCount === 7 ? 'complete' : 'partial'}`}>
                                {savedCount}/7 Reports
                              </span>
                            </td>
                            <td className="record-actions">
                              <button 
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleLoadDbRecord(record);
                                }} 
                                className="load-record-btn"
                              >
                                Load Workspace
                              </button>
                            </td>
                          </tr>
                          {expandedRecordId === record.id && (
                            <tr className="expanded-row">
                              <td colSpan={5} className="expanded-cell">
                                <div className="expanded-details-container">
                                  <div className="expanded-tabs-nav">
                                    {[
                                      { key: 'research_report', label: 'Research' },
                                      { key: 'financial_report', label: 'Financial' },
                                      { key: 'risk_report', label: 'Risk' },
                                      { key: 'latest_news_report', label: 'News' },
                                      { key: 'valuation_report', label: 'Valuation' },
                                      { key: 'investment_summary', label: 'Summary' },
                                      { key: 'committee_decision', label: 'Committee' }
                                    ].map((subTab) => {
                                      const hasData = !!record[subTab.key];
                                      return (
                                        <button
                                          key={subTab.key}
                                          className={`sub-tab-btn ${expandedTab === subTab.key ? 'active' : ''} ${!hasData ? 'no-data' : ''}`}
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            setExpandedTab(subTab.key);
                                          }}
                                        >
                                          {subTab.label}
                                        </button>
                                      );
                                    })}
                                  </div>
                                  <div className="expanded-tab-content">
                                    {record[expandedTab] ? (
                                      <div 
                                        className="report-render markdown-body"
                                        dangerouslySetInnerHTML={{ __html: marked.parse(record[expandedTab]) }}
                                      />
                                    ) : (
                                      <div className="no-report-placeholder">
                                        <p>No report data exists for this column in the database record.</p>
                                      </div>
                                    )}
                                  </div>
                                </div>
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </main>
      )}
      {viewMode === 'chroma' && (
        <ChromaExplorer onNavigateBack={() => handleNavigate('workspace')} />
      )}
    </div>
  );
}

export default App;
