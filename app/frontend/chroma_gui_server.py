import os
import json
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging

import sys

# Get absolute paths to frontend and tools directories relative to this file
frontend_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.dirname(frontend_dir)
tools_dir = os.path.join(app_dir, "tools")

if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

from mcp_server import (
    HybridSearcher,
    CrossEncoderReranker,
    IngestionRegistry,
    RetrievalEvaluator,
    ingest_all_corporate_data
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ChromaGUIServer")

# Global persistent instances (simulates a persistent database session)
# Use the app/database directory
searcher = HybridSearcher(persist_directory=os.path.join(app_dir, "database"))
reranker = CrossEncoderReranker()

class ChromaGUIRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Serve static frontend files
        if self.path == "/" or self.path == "/index.html":
            self.serve_file("index.html", "text/html")
        elif self.path == "/index.css":
            self.serve_file("index.css", "text/css")
        elif self.path == "/index.js":
            self.serve_file("index.js", "application/javascript")
        elif self.path == "/api/registry":
            self.handle_get_registry()
        elif self.path == "/api/inspect":
            self.handle_get_inspect()
        else:
            self.send_error(404, "File Not Found")
            
    def do_POST(self):
        # Parse JSON request body
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8')
        try:
            body = json.loads(post_data) if post_data else {}
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON body"}, status=400)
            return

        if self.path == "/api/ingest":
            self.handle_post_ingest(body)
        elif self.path == "/api/query":
            self.handle_post_query(body)
        elif self.path == "/api/eval":
            self.handle_post_eval(body)
        else:
            self.send_error(404, "API Endpoint Not Found")

    def serve_file(self, filename, content_type):
        file_path = os.path.join(frontend_dir, filename)
        if not os.path.exists(file_path):
            self.send_error(404, f"{filename} Not Found")
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        except Exception as e:
            self.send_error(500, f"Internal Server Error: {e}")

    def send_json(self, data, status=200):
        try:
            response_body = json.dumps(data)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(response_body.encode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to send JSON response: {e}")

    def handle_get_registry(self):
        registry = IngestionRegistry()
        data = registry.load_registry()
        self.send_json(data)

    def handle_get_inspect(self):
        try:
            res = searcher.collection.get()
            ids = res.get("ids", [])
            documents = res.get("documents", [])
            metadatas = res.get("metadatas", [])
            response_data = []
            for i in range(len(ids)):
                response_data.append({
                    "id": ids[i],
                    "text": documents[i] if i < len(documents) else "",
                    "metadata": metadatas[i] if i < len(metadatas) else {}
                })
            self.send_json({"items": response_data})
        except Exception as e:
            logger.error(f"Inspect collection failed: {e}")
            self.send_json({"error": str(e)}, status=500)

    def handle_post_ingest(self, body):
        ticker = body.get("ticker", "").strip().upper()
        if not ticker:
            self.send_json({"error": "Ticker symbol is required"}, status=400)
            return
        
        logger.info(f"GUI triggering ingestion for ticker: {ticker}")
        try:
            summary = ingest_all_corporate_data(ticker, searcher)
            self.send_json({"message": summary})
        except Exception as e:
            logger.error(f"Ingestion failed: {e}")
            self.send_json({"error": str(e)}, status=500)

    def handle_post_query(self, body):
        query = body.get("query", "").strip()
        ticker_filter = body.get("ticker", "").strip().upper()
        form_filter = body.get("form_type", "").strip()
        top_k = body.get("top_k", 5)
        
        if not query:
            self.send_json({"error": "Query string is required"}, status=400)
            return

        logger.info(f"GUI executing query: '{query}' (filters: ticker={ticker_filter or 'None'}, form={form_filter or 'None'})")
        
        metadata_filter = {}
        if ticker_filter:
            metadata_filter["ticker"] = ticker_filter
        if form_filter:
            f_val = "Earnings" if form_filter in ("Earnings", "EARNINGS_CALL", "EARNINGS") else form_filter
            metadata_filter["form_type"] = f_val
            
        try:
            # Step 1: Hybrid retrieval
            raw_results = searcher.rrf_hybrid_retrieve(query, top_k=top_k * 2, metadata_filter=metadata_filter)
            
            # Step 2: Cross-Encoder reranking
            reranked = reranker.rerank(query, raw_results)
            
            # Step 3: Text-level deduplication
            seen = set()
            deduped = []
            for item in reranked:
                normalized_text = " ".join(item["text"].split()).lower()
                if normalized_text not in seen:
                    seen.add(normalized_text)
                    deduped.append(item)
            
            # Slice to top k
            final_candidates = deduped[:top_k]
            
            # Step 4: Resolve Parent documents
            response_candidates = []
            for item in final_candidates:
                meta = item.get("metadata", {})
                t_val = meta.get("ticker", "")
                f_val = meta.get("form_type", "")
                i_val = meta.get("item_name", "")
                
                # Fetch full parent plaintext section
                parent_text = searcher.get_parent_doc(t_val, f_val, i_val)
                
                response_candidates.append({
                    "text": item["text"],
                    "metadata": meta,
                    "score": item.get("rerank_score", 0.0),
                    "parent_text": parent_text
                })
                
            self.send_json({"candidates": response_candidates})
        except Exception as e:
            logger.error(f"Query search failed: {e}")
            self.send_json({"error": str(e)}, status=500)

    def handle_post_eval(self, body):
        form_type = body.get("form_type", "10-K")
        logger.info(f"GUI running retrieval evaluation for: {form_type}")
        
        try:
            # 1. Execute standard run_eval_suite to get final MRR
            mrr = RetrievalEvaluator.run_eval_suite(searcher, form_type)
            
            # 2. Re-simulate queries to capture granular details for GUI display
            form_clean = form_type.upper().strip()
            form_val = "Earnings" if form_clean in ("EARNINGS_CALL", "EARNINGS") else form_clean
            metadata_filter = {"form_type": form_val}
            
            if form_clean == "10-Q":
                test_cases = [
                    ("What are the quarterly risk factors and supply chain issues?", "Risk Factors"),
                    ("Tell me about the financial statements and income statement tables", "Financial Statements"),
                    ("Are disclosure controls and procedures verified to be effective?", "Controls and Procedures"),
                ]
            elif form_clean == "8-K":
                test_cases = [
                    ("What operations financial performance is announced?", "Results of Operations and Financial Condition"),
                    ("What exhibits are filed with this report?", "Financial Statements and Exhibits"),
                ]
            elif form_clean in ("EARNINGS_CALL", "EARNINGS"):
                test_cases = [
                    ("What was the quarterly record revenue reported?", "Earnings Call Transcript"),
                    ("Can you comment on the supply chain constraints for product shipments?", "Earnings Call Transcript"),
                ]
            else:
                test_cases = [
                    ("What are the risk factors and supply chain challenges?", "Item 1A"),
                    ("Tell me about the net income and liabilities balance sheet", "Item 8"),
                    ("What is the core business strategy and wearable services?", "Item 1"),
                ]
            
            details = []
            for query, expected_item in test_cases:
                results = searcher.rrf_hybrid_retrieve(query, top_k=5, metadata_filter=metadata_filter)
                rank = 999
                for idx, res in enumerate(results):
                    item_name_meta = res["metadata"].get("item_name", "").lower()
                    matched = False
                    if expected_item == "Item 1" and "business" in item_name_meta:
                        matched = True
                    elif expected_item == "Item 1A" and "risk" in item_name_meta:
                        matched = True
                    elif expected_item == "Item 7" and "management" in item_name_meta:
                        matched = True
                    elif expected_item == "Item 8" and "financial" in item_name_meta:
                        matched = True
                    elif expected_item.lower() in item_name_meta:
                        matched = True
                    
                    if matched:
                        rank = idx + 1
                        break
                
                reciprocal_rank = 1.0 / rank if rank <= 5 else 0.0
                details.append({
                    "query": query,
                    "expected": expected_item,
                    "rank": rank,
                    "reciprocal_rank": reciprocal_rank
                })
                
            self.send_json({"mrr": mrr, "details": details})
        except Exception as e:
            logger.error(f"Evaluation dashboard run failed: {e}")
            self.send_json({"error": str(e)}, status=500)

def run(server_class=HTTPServer, handler_class=ChromaGUIRequestHandler, port=None):
    if port is None:
        port = int(os.environ.get("CHROMA_GUI_PORT", 8001))
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logger.info(f"Chroma DB Explorer GUI successfully started at http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down GUI server.")
        httpd.server_close()

if __name__ == "__main__":
    run()
