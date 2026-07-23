import pytest
import asyncio
import sys
import os

# Ensure the app/tools and app directories are in the import path
test_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(test_dir)
tools_dir = os.path.join(project_dir, "app", "tools")
app_dir = os.path.join(project_dir, "app")
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from mcp_server import (
    SECDownloader,
    SECParser,
    SemanticChunker,
    HybridSearcher,
    CrossEncoderReranker,
    MultiAgentSupervisor,
    async_fetch_and_parse,
    async_fetch_and_parse_10q,
    async_fetch_and_parse_8k,
    EarningsCallManager,
    async_fetch_and_parse_earnings_call,
    IngestionRegistry,
    ingest_all_corporate_data
)
from corporate_documents_search import (
    doc_rag_search,
)

@pytest.fixture(autouse=True)
def clean_registry():
    import os
    if os.path.exists("vector_db_registry.json"):
        os.remove("vector_db_registry.json")
    yield
    if os.path.exists("vector_db_registry.json"):
        os.remove("vector_db_registry.json")


def test_nvidia_10k_metadata_fetching():
    """
    Test metadata fetching for NVIDIA Corp. (CIK: 0001045810).
    Ensures that the submissions metadata can be fetched and parsed, and that the first 10-K filing is detected.
    """
    downloader = SECDownloader()
    cik_nvidia = "0001045810"
    
    metadata = downloader.get_filings_metadata(cik_nvidia)
    assert metadata is not None
    assert "cik" in metadata
    assert "filings" in metadata
    
    recent_filings = metadata["filings"]["recent"]
    assert "form" in recent_filings
    assert "accessionNumber" in recent_filings
    assert "primaryDocument" in recent_filings
    
    # Ensure there is at least one 10-K form in the filings history
    forms = recent_filings["form"]
    assert "10-K" in forms, "No 10-K filing found in NVIDIA metadata"


def test_nvidia_10k_download_and_parsing():
    """
    Test downloading and parsing of NVIDIA 2025 10-K.
    Verifies that we retrieve valid content, extract specific sections, and chunk them.
    """
    downloader = SECDownloader()
    parser = SECParser()
    cik_nvidia = "0001045810"
    
    # Fetch metadata and download HTML for the 10-K
    metadata = downloader.get_filings_metadata(cik_nvidia)
    recent_filings = metadata["filings"]["recent"]
    
    idx = -1
    for i, form_type in enumerate(recent_filings["form"]):
        if form_type == "10-K":
            idx = i
            break
            
    assert idx != -1, "No 10-K filing found in metadata"
    
    acc_num = recent_filings["accessionNumber"][idx]
    primary_doc = recent_filings["primaryDocument"][idx]
    
    html_content = downloader.download_10k_html(cik_nvidia, acc_num, primary_doc)
    assert html_content is not None
    assert len(html_content) > 100
    
    # Run parsing and item detection
    sections = parser.detect_sec_items(html_content)
    assert len(sections) > 0, "No sections parsed from the 10-K filing"
    
    # Check that standard sections (Item 1, 1A, 7 or 8) were detected
    detected_items = list(sections.keys())
    assert any(any(x in item.lower() for x in ["business", "risk factors", "management", "financial statements"]) for item in detected_items)
    
    # Run chunking
    chunker = SemanticChunker()
    base_meta = {
        "cik": cik_nvidia,
        "ticker": "NVDA",
        "filing_date": recent_filings["filingDate"][idx],
        "item_name": detected_items[0]
    }
    chunks = chunker.chunk_section(sections[detected_items[0]], base_meta)
    assert len(chunks) > 0
    assert "text" in chunks[0]
    assert "metadata" in chunks[0]
    assert chunks[0]["metadata"]["ticker"] == "NVDA"


def test_nvidia_10k_retrieval_and_agent_workflow():
    """
    Runs the full ingestion, search, and agentic workflow simulation.
    Ensures that documents can be searched and the MultiAgentSupervisor produces a valid synthesis.
    """
    async def run_pipeline():
        downloader = SECDownloader()
        parser = SECParser()
        searcher = HybridSearcher()
        reranker = CrossEncoderReranker()
        cik_nvidia = "0001045810"
        
        # 1. Async fetch and parse
        chunks = await async_fetch_and_parse(cik_nvidia, downloader, parser)
        assert len(chunks) > 0, "Failed to ingest chunks"
        assert chunks[0]["metadata"]["form_type"] == "10-K"
        
        # 2. Ingest chunks into Hybrid Index
        searcher.ingest_chunks(chunks)
        
        # 3. Perform hybrid search
        query = "What is the core computing platform or Blackwell/Hopper architectures of NVIDIA?"
        hybrid_results = searcher.rrf_hybrid_retrieve(query, top_k=3)
        assert len(hybrid_results) > 0, "Hybrid search returned no results"
        
        # 4. Rerank candidates
        reranked_results = reranker.rerank(query, hybrid_results)
        assert len(reranked_results) > 0
        assert "rerank_score" in reranked_results[0]
        
        # 5. Multi-agent workflow
        agent_system = MultiAgentSupervisor(searcher, reranker)
        state = agent_system.run_agent_workflow(query)
        
        assert state is not None
        assert "final_answer" in state
        assert len(state["final_answer"]) > 0
        assert "FinancialAnalystAgent" in "".join(state["logs"])

    asyncio.run(run_pipeline())


def test_nvidia_10q_processing():
    """
    Validates dynamic Form 10-Q parsing, metadata mapping, and section indexing.
    """
    async def run_10q_test():
        downloader = SECDownloader()
        parser = SECParser()
        searcher = HybridSearcher()
        cik_nvidia = "0001045810"
        
        # Ingest and parse 10-Q chunks
        chunks = await async_fetch_and_parse_10q(cik_nvidia, downloader, parser)
        assert len(chunks) > 0, "No 10-Q chunks parsed"
        
        # Verify metadata mapping
        item_names = [c["metadata"]["item_name"] for c in chunks]
        assert "Financial Statements" in item_names
        assert "Risk Factors" in item_names
        assert chunks[0]["metadata"]["ticker"] == "NVDA"
        assert chunks[0]["metadata"]["form_type"] == "10-Q"
        
        # Ingest and retrieve
        searcher.ingest_chunks(chunks)
        results = searcher.rrf_hybrid_retrieve("What are the quarterly risk factors and supply chain issues?", top_k=3)
        assert len(results) > 0
        retrieved_items = [r["metadata"]["item_name"] for r in results]
        assert "Risk Factors" in retrieved_items

    asyncio.run(run_10q_test())


def test_nvidia_8k_processing():
    """
    Validates dynamic Form 8-K parsing, metadata mapping, and section indexing.
    """
    async def run_8k_test():
        downloader = SECDownloader()
        parser = SECParser()
        searcher = HybridSearcher()
        cik_nvidia = "0001045810"
        
        # Ingest and parse 8-K chunks
        chunks = await async_fetch_and_parse_8k(cik_nvidia, downloader, parser)
        assert len(chunks) > 0, "No 8-K chunks parsed"
        
        # Verify metadata mapping
        item_names = [c["metadata"]["item_name"] for c in chunks]
        assert "Results of Operations and Financial Condition" in item_names
        assert "Financial Statements and Exhibits" in item_names
        assert chunks[0]["metadata"]["ticker"] == "NVDA"
        assert chunks[0]["metadata"]["form_type"] == "8-K"
        
        # Ingest and retrieve
        searcher.ingest_chunks(chunks)
        results = searcher.rrf_hybrid_retrieve("What current operations announcements are released?", top_k=3)
        assert len(results) > 0
        retrieved_items = [r["metadata"]["item_name"] for r in results]
        assert "Results of Operations and Financial Condition" in retrieved_items

    asyncio.run(run_8k_test())


def test_nvidia_earnings_call_processing():
    """
    Validates dynamic Earnings Call parsing, metadata mapping, and section indexing.
    """
    async def run_earnings_test():
        manager = EarningsCallManager()
        searcher = HybridSearcher()
        
        # Ingest and parse Earnings Call chunks
        chunks = await async_fetch_and_parse_earnings_call("NVDA", manager)
        assert len(chunks) > 1, f"Expected more than one chunk for NVDA Earnings transcript, got {len(chunks)}"
        
        # Verify metadata mapping
        assert chunks[0]["metadata"]["item_name"].startswith("Earnings Call Transcript")
        assert chunks[0]["metadata"]["ticker"] == "NVDA"
        assert chunks[0]["metadata"]["form_type"] == "Earnings"
        
        # Ingest and retrieve
        searcher.ingest_chunks(chunks)
        results = searcher.rrf_hybrid_retrieve("What is Blackwell packaging demand and TSMC partnership comments?", top_k=3)
        assert len(results) > 0
        retrieved_items = [r["metadata"]["item_name"] for r in results]
        assert any(item.startswith("Earnings Call Transcript") for item in retrieved_items)

    asyncio.run(run_earnings_test())


def test_nvidia_ingestion_registry_cache_skip():
    """
    Validates that if a document of matching or older filing date is already registered in the JSON file,
    the pipeline skips downloading, parsing, chunking, and returns empty chunks [].
    """
    import os
    registry_file = "test_registry.json"
    if os.path.exists(registry_file):
        os.remove(registry_file)
        
    registry = IngestionRegistry(registry_file)
    
    # 1. Setup initial state: register a document at a specific date
    registry.update_registry("NVDA", "10-K", "2025-02-18")
    
    # 2. Check if a same-date or older filing date should skip
    assert registry.should_skip_ingestion("NVDA", "10-K", "2025-02-18") is True
    assert registry.should_skip_ingestion("NVDA", "10-K", "2025-01-01") is True
    
    # 3. Check if a newer filing date should NOT skip
    assert registry.should_skip_ingestion("NVDA", "10-K", "2026-02-18") is False
    
    # 4. Verify structural nested JSON format matches user specifications
    data = registry.load_registry()
    assert "NVDA" in data
    assert "10-K" in data["NVDA"]
    assert data["NVDA"]["10-K"]["filing_date"] == "2025-02-18"
    
    # Clean up
    if os.path.exists(registry_file):
        os.remove(registry_file)


def test_nvidia_ingest_all_corporate_data_tool():
    """
    Validates the exposed agent tool ingest_all_corporate_data:
    Ensures that it triggers all parallel paths and updates the database.
    """
    import os
    # Clean registry so that it runs full ingestion
    if os.path.exists("vector_db_registry.json"):
        os.remove("vector_db_registry.json")
        
    searcher = HybridSearcher()
    summary = ingest_all_corporate_data("NVDA", searcher)
    assert "successfully ingested" in summary
    assert "10-K" in summary
    assert "10-Q" in summary
    assert "8-K" in summary
    assert "Earnings" in summary
    
    # Assert that all chunks have been loaded into searcher bm25 corpus
    assert len(searcher.bm25_corpus) > 0


def test_nvidia_html_table_to_markdown():
    """
    Validates that HTML table conversion correctly extracts grid structures
    into aligned Markdown strings.
    """
    html_with_table = """
    <html>
        <body>
            <table>
                <tr>
                    <th>Year</th>
                    <th>Revenue</th>
                </tr>
                <tr>
                    <td>2025</td>
                    <td>$26.0B</td>
                </tr>
            </table>
        </body>
    </html>
    """
    parsed_text = SECParser.parse_html_to_text(html_with_table)
    assert "| Year | Revenue |" in parsed_text
    assert "|---|---|" in parsed_text
    assert "| 2025 | $26.0B |" in parsed_text


def test_nvidia_doc_rag_search_tool():
    """
    Validates that the exposed doc_rag_search tool successfully processes
    a query against corporate reports and returns synthesized analysis answers.
    """
    import os
    if os.path.exists("vector_db_registry.json"):
        os.remove("vector_db_registry.json")
        
    answer = doc_rag_search(
        query="What is the demand driver for NVIDIA AI processors and Blackwell architecture in fiscal year 2025?",
        ticker="NVDA",
        form_type="10-K"
    )
    assert len(answer) > 0
    assert "Based on the corporate reports" in answer







