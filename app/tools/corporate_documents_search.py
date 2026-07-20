#!/usr/bin/env python3
"""
corporate_documents_search.py

A unified, self-contained Python script implementing an SEC stock research pipeline:
- SEC API Ingestion & 10-K HTML Downloader (with headers)
- HTML Parsing (BeautifulSoup fallback or native regex)
- SEC Item Detection (Regex-based section splitter)
- Semantic Chunking (Similarity-based splitter simulation/Sentence Splitter)
- Metadata Extraction (Ticker, CIK, FY, Item ID, Date)
- Chroma Ingestion (chromadb client wrapper with fallback)
- BM25 Retrieval (rank_bm25 wrapper with fallback)
- Hybrid Ranking (RRF - Reciprocal Rank Fusion)
- Cross-Encoder Reranking (Mock/Local Similarity Reranker)
- LangGraph Supervisor Multi-Agent Workflow (Simplified LangGraph simulation or imports)
- Evaluation Tests (Retrieval quality checking against ground truth)
- Async Ingestion Skeleton (Asyncio loop for parallel download)
"""

import os
import re
import json
import math
import asyncio
import logging
import datetime
from typing import List, Dict, Any, Tuple, Optional
import urllib.request
import urllib.error

# Setup logging
try:
    from utils.logging_config import setup_logging
except ImportError:
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils.logging_config import setup_logging

setup_logging()
logger = logging.getLogger("StockResearchAgent")

try:
    from config import (
        NUM_YEARS_TO_LOAD_10K,
        NUM_DAYS_TO_LOAD_8K,
        NUM_QUARTERS_TO_LOAD_10Q,
        NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS
    )
except ImportError:
    try:
        from app.config import (
            NUM_YEARS_TO_LOAD_10K,
            NUM_DAYS_TO_LOAD_8K,
            NUM_QUARTERS_TO_LOAD_10Q,
            NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS
        )
    except ImportError:
        NUM_YEARS_TO_LOAD_10K = 1
        NUM_DAYS_TO_LOAD_8K = 30
        NUM_QUARTERS_TO_LOAD_10Q = 1
        NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS = 1


import threading

# Process-level lock registry to prevent concurrent ingestion of the same filing.
# Key: (ticker_upper, form_key_upper), Value: threading.Lock()
_ingestion_locks: Dict[str, threading.Lock] = {}
_ingestion_locks_meta_lock = threading.Lock()

def _get_ingestion_lock(ticker: str, form_key: str) -> threading.Lock:
    """Returns a per-(ticker, form_key) lock, creating it if needed."""
    key = f"{ticker.upper().strip()}|{form_key.upper().strip()}"
    with _ingestion_locks_meta_lock:
        if key not in _ingestion_locks:
            _ingestion_locks[key] = threading.Lock()
        return _ingestion_locks[key]


# ==========================================
# 0. INGESTION CACHING REGISTRY
# ==========================================
class IngestionRegistry:
    """
    Tracks and checks document ingestion history using a local JSON file to prevent redundant updates.
    """
    def __init__(self, registry_path: Optional[str] = None):
        if registry_path is None:
            # app/tools/corporate_documents_search.py -> parent parent is root
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.registry_path = os.path.join(root_dir, "vector_db_registry.json")
        else:
            self.registry_path = registry_path

    def load_registry(self) -> Dict[str, Any]:
        if os.path.exists(self.registry_path):
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read registry file ({e}). Starting fresh.")
        return {}

    def reset_stale_in_progress(self):
        """Reset any 'in_progress' entries left by a previous crashed/killed run."""
        registry = self.load_registry()
        changed = False
        for ticker_key, forms in registry.items():
            for form_key, entry in forms.items():
                if isinstance(entry, dict) and entry.get("status") == "in_progress":
                    # Remove stale in-progress marker so it can be retried
                    del registry[ticker_key][form_key]
                    changed = True
                    logger.info(f"Reset stale in_progress marker for {ticker_key}/{form_key}")
        if changed:
            self.save_registry(registry)


    def save_registry(self, registry: Dict[str, Any]):
        try:
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to write registry file ({e}).")

    def remove_ticker_from_registry(self, ticker: str):
        registry = self.load_registry()
        t_key = ticker.upper().strip()
        # Also clean standard form keys like 10-K, 10-Q, 8-K, Earnings, etc.
        # But we can just delete the entire ticker entry key from registry
        if t_key in registry:
            del registry[t_key]
            self.save_registry(registry)

    def should_skip_ingestion(self, ticker: str, form_type: str, filing_date: Optional[str] = None, searcher: Optional[Any] = None) -> bool:
        t_key = ticker.upper().strip()
        f_key = form_type.upper().strip()
        # Make sure we get filing_date all the time before applying the condition
        if not filing_date:
            registry = self.load_registry()
            if t_key in registry and f_key in registry[t_key]:
                filing_date = registry[t_key][f_key].get("filing_date", "")
            if not filing_date:
                filing_date = datetime.datetime.now().strftime("%Y-%m-%d")

        registry = self.load_registry()
        
        # Check if this filing is already marked as done or in-progress in the registry.
        if t_key in registry and f_key in registry[t_key]:
            entry = registry[t_key][f_key]
            last_filing_date = entry.get("filing_date", "")
            status = entry.get("status", "done")
            # Skip if already completed for this filing_date
            if last_filing_date == filing_date and status == "done":
                return True
            # Skip if currently in-progress (another thread is processing it)
            if last_filing_date == filing_date and status == "in_progress":
                return True
            updated_at_str = entry.get("updated_at", "")
            if updated_at_str and len(updated_at_str) >= 10 and status == "done":
                updated_at_date = updated_at_str[:10]
                if updated_at_date >= filing_date:
                    return True

        # If the searcher is provided, also verify the database actually has chunks for this ticker.
        if searcher is not None:
            t_clean = ticker.upper().strip()
            f_clean = form_type.upper().strip()
            if f_clean.startswith("10-K"):
                f_val = "10-K"
            elif f_clean.startswith("10-Q"):
                f_val = "10-Q"
            elif f_clean.startswith("8-K"):
                f_val = "8-K"
            elif f_clean.startswith("EARNINGS"):
                f_val = "Earnings"
            else:
                f_val = f_clean
                
            ticker_has_any_chunks = any(meta.get("ticker") == t_clean for meta in searcher.bm25_metadata)
            if not ticker_has_any_chunks:
                # Database is empty for this ticker — force ingestion even if registry says done.
                return False

        return False

    def mark_in_progress(self, ticker: str, form_type: str, filing_date: str):
        """Atomically marks a filing as in-progress before download begins."""
        lock = _get_ingestion_lock(ticker, form_type)
        with lock:
            registry = self.load_registry()
            t_key = ticker.upper().strip()
            f_key = form_type.upper().strip()
            if t_key not in registry:
                registry[t_key] = {}
            registry[t_key][f_key] = {
                "filing_date": filing_date,
                "status": "in_progress",
                "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.save_registry(registry)

    def update_registry(self, ticker: str, form_type: str, filing_date: str):
        """Marks a filing as fully done after successful processing."""
        lock = _get_ingestion_lock(ticker, form_type)
        with lock:
            registry = self.load_registry()
            t_key = ticker.upper().strip()
            f_key = form_type.upper().strip()
            if t_key not in registry:
                registry[t_key] = {}
            registry[t_key][f_key] = {
                "filing_date": filing_date,
                "status": "done",
                "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.save_registry(registry)


# ==========================================
# 1. SEC API INGESTION & 10-K DOWNLOADER
# ==========================================
class SECDownloader:
    """
    Ingests metadata and downloads 10-K filings from SEC EDGAR.
    """
    def __init__(self, user_agent: str = "StockResearchAgent/1.0 (test@example.com)"):
        self.user_agent = user_agent
        self.headers = {"User-Agent": self.user_agent}

    def get_filings_metadata(self, cik: str) -> Dict[str, Any]:
        """
        Fetches submissions history for a given CIK (formatted to 10 digits).
        """
        import ssl
        padded_cik = cik.zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{padded_cik}.json"
        logger.info(f"Fetching metadata for CIK {padded_cik} from SEC EDGAR...")
        
        req = urllib.request.Request(url, headers=self.headers)
        try:
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=context) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            logger.warning(f"Failed to fetch real SEC data for CIK {cik} (using mock metadata): {e}")
            # Mock filings metadata for testing/standalone execution
            if "1045810" in cik:
                return {
                    "cik": padded_cik,
                    "entityName": "NVIDIA CORP",
                    "tickers": ["NVDA"],
                    "exchanges": ["NASDAQ"],
                    "filings": {
                        "recent": {
                            "accessionNumber": ["0001045810-25-000010", "0001045810-25-000015", "0001045810-26-000002"],
                            "filingDate": ["2025-02-18", "2025-05-15", "2026-07-13"],
                            "form": ["10-K", "10-Q", "8-K"],
                            "primaryDocument": ["nvda-20250126.htm", "nvda-20250426.htm", "nvda-20260713.htm"],
                            "size": [2456789, 1123456, 12543]
                        }
                    }
                }
            return {
                "cik": padded_cik,
                "entityName": "MOCK COMPANY INC",
                "tickers": ["MCIP"],
                "exchanges": ["NASDAQ"],
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000320193-23-000106", "0000320193-23-000110", "0000320193-26-000002"],
                        "filingDate": ["2023-11-03", "2024-02-10", "2026-07-13"],
                        "form": ["10-K", "10-Q", "8-K"],
                        "primaryDocument": ["amzn-20231231.htm", "amzn-20240331.htm", "amzn-20260713.htm"],
                        "size": [1234567, 654321, 10243]
                    }
                }
            }

    def download_10k_html(self, cik: str, accession_number: str, primary_doc: str) -> str:
        """
        Downloads a 10-K HTML filing.
        """
        import ssl
        clean_acc = accession_number.replace("-", "")
        url_cik = str(int(cik))
        url = f"https://www.sec.gov/Archives/edgar/data/{url_cik}/{clean_acc}/{primary_doc}"
        logger.info(f"Downloading 10-K from {url}...")
        
        req = urllib.request.Request(url, headers=self.headers)
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=10, context=context) as response:
            return response.read().decode('utf-8', errors='ignore')

    def download_10q_html(self, cik: str, accession_number: str, primary_doc: str) -> str:
        """
        Downloads a 10-Q HTML filing.
        """
        import ssl
        clean_acc = accession_number.replace("-", "")
        url_cik = str(int(cik))
        url = f"https://www.sec.gov/Archives/edgar/data/{url_cik}/{clean_acc}/{primary_doc}"
        logger.info(f"Downloading 10-Q from {url}...")
        
        req = urllib.request.Request(url, headers=self.headers)
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=10, context=context) as response:
            return response.read().decode('utf-8', errors='ignore')

    def download_8k_html(self, cik: str, accession_number: str, primary_doc: str) -> str:
        """
        Downloads an 8-K HTML filing.
        """
        import ssl
        clean_acc = accession_number.replace("-", "")
        url_cik = str(int(cik))
        url = f"https://www.sec.gov/Archives/edgar/data/{url_cik}/{clean_acc}/{primary_doc}"
        logger.info(f"Downloading 8-K from {url}...")
        
        req = urllib.request.Request(url, headers=self.headers)
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=10, context=context) as response:
            return response.read().decode('utf-8', errors='ignore')


# ==========================================
# 2. HTML PARSING & SEC ITEM DETECTION
# ==========================================
class SECParser:
    """
    Parses SEC HTML content, extracts plaintext, and detects SEC standard items.
    """
    @staticmethod
    def parse_html_to_text(html_content: str) -> str:
        """
        Strips HTML tags to retrieve clean, readable text, converting HTML tables
        into structured Markdown format before embedding.
        """
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, "html.parser")
            
            # Convert HTML tables to Markdown format
            for table in soup.find_all("table"):
                markdown_rows = []
                for row in table.find_all("tr"):
                    cells = [cell.get_text(strip=True).replace("\n", " ") for cell in row.find_all(["td", "th"])]
                    if any(cells):
                        markdown_rows.append("| " + " | ".join(cells) + " |")
                if markdown_rows:
                    # Construct table header separator
                    cols_count = len(markdown_rows[0].split("|")) - 2
                    separator = "|" + "---|"*cols_count
                    if len(markdown_rows) > 1:
                        markdown_rows.insert(1, separator)
                    else:
                        markdown_rows.append(separator)
                    table_md = "\n\n" + "\n".join(markdown_rows) + "\n\n"
                    table.replace_with(soup.new_string(table_md))
            
            return soup.get_text(separator="\n\n")
        except Exception as e:
            logger.debug(f"BeautifulSoup parsing failed or not installed ({e}). Using custom regex parser.")
            # Basic fallback html parsing
            text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html_content)
            text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', text)
            # Replace block tags with double newlines to preserve structure
            text = re.compile(r'</?(?:p|div|tr|h[1-6]|br|table|li|blockquote)[^>]*>', re.IGNORECASE).sub('\n\n', text)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\r', '', text)
            text = re.sub(r'\n\s*\n', '\n\n', text)
            return text.strip()

    @staticmethod
    def detect_sec_items(html_content: str) -> Dict[str, str]:
        """
        Detects and extracts specific 10-K Item sections from HTML dynamically using pattern matching.
        """
        text = SECParser.parse_html_to_text(html_content)
        
        # Split text into lines for line-based matching
        lines = text.split("\n")
        
        # Try to locate the items using dynamic line-based patterns
        detected_items = {}
        for idx, line in enumerate(lines):
            # Normalize whitespace
            normalized_line = " ".join(line.split()).replace('\u00a0', ' ')
            
            # Case 1: Same line match (e.g. "Item 8. Financial Statements...")
            m = re.match(r'^Item\s+([0-9A-Z]+)\b[\.\-:\s]*\s*(.+)$', normalized_line, re.IGNORECASE)
            if m:
                item_num = m.group(1).upper()
                category_name = m.group(2).strip()
                # Compute exact character index in original text
                char_idx = sum(len(l) for l in lines[:idx]) + idx
                detected_items[item_num] = (char_idx, category_name)
                continue
                
            # Case 2: Split line match (e.g. "Item 8." on one line, category on next non-empty line)
            m_split = re.match(r'^Item\s+([0-9A-Z]+)\b[\.\-:\s]*$', normalized_line, re.IGNORECASE)
            if m_split:
                item_num = m_split.group(1).upper()
                category_name = ""
                for next_idx in range(idx + 1, min(len(lines), idx + 5)):
                    if lines[next_idx].strip():
                        normalized_next = " ".join(lines[next_idx].split()).replace('\u00a0', ' ')
                        # If next line is not another Item header
                        if not re.match(r'^Item\s+([0-9A-Z]+)\b', normalized_next, re.IGNORECASE):
                            category_name = normalized_next
                        break
                if category_name:
                    char_idx = sum(len(l) for l in lines[:idx]) + idx
                    detected_items[item_num] = (char_idx, category_name)
                
        # If dynamic matching succeeded for at least some items, build sections
        sections = {}
        if len(detected_items) >= 2:
            # Sort detected items by start character index
            sorted_items = sorted(detected_items.items(), key=lambda x: x[1][0])
            for i in range(len(sorted_items)):
                item_num, (start_idx, category_name) = sorted_items[i]
                
                # The section ends where the next item starts
                if i + 1 < len(sorted_items):
                    end_idx = sorted_items[i+1][1][0]
                else:
                    end_idx = len(text)
                    
                section_text = text[start_idx:end_idx].strip()
                if len(section_text) > 20:
                    # Explicitly override standard item names to guarantee exact key names
                    standard_names = {
                        "1": "Business",
                        "1A": "Risk Factors",
                        "1B": "Unresolved Staff Comments",
                        "1C": "Cybersecurity",
                        "2": "Properties",
                        "3": "Legal Proceedings",
                        "4": "Mine Safety Disclosures",
                        "5": "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities",
                        "6": "[Reserved]",
                        "7": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
                        "7A": "Quantitative and Qualitative Disclosures about Market Risk",
                        "8": "Financial Statements and Supplementary Data",
                        "9": "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure",
                        "9A": "Controls and Procedures",
                        "9B": "Other Information",
                        "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
                        "10": "Directors, Executive Officers and Corporate Governance",
                        "11": "Executive Compensation",
                        "12": "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters",
                        "13": "Certain Relationships and Related Transactions, and Director Independence",
                        "14": "Principal Accountant Fees and Services",
                        "15": "Exhibits and Financial Statement Schedules",
                        "16": "Form 10-K Summary",
                    }
                    key_name = standard_names.get(item_num, category_name)
                    sections[key_name] = section_text
        
        # If dynamic matching failed to locate sections, fall back to loose character regex search
        if not sections:
            logger.info("Dynamic line-based matching yielded too few sections. Falling back to loose regex search.")
            loose_patterns = {
                "Business": re.compile(r'(?:ITEM\s+1\.)\s+(BUSINESS)', re.IGNORECASE),
                "Risk Factors": re.compile(r'(?:ITEM\s+1A\.)\s+(RISK\s+FACTORS)', re.IGNORECASE),
                "Unresolved Staff Comments": re.compile(r'(?:ITEM\s+1B\.)\s+(UNRESOLVED\s+STAFF\s+COMMENTS)', re.IGNORECASE),
                "Cybersecurity": re.compile(r'(?:ITEM\s+1C\.)\s+(CYBERSECURITY)', re.IGNORECASE),
                "Properties": re.compile(r'(?:ITEM\s+2\.)\s+(PROPERTIES)', re.IGNORECASE),
                "Legal Proceedings": re.compile(r'(?:ITEM\s+3\.)\s+(LEGAL\s+PROCEEDINGS)', re.IGNORECASE),
                "Mine Safety Disclosures": re.compile(r'(?:ITEM\s+4\.)\s+(MINE\s+SAFETY\s+DISCLOSURES)', re.IGNORECASE),
                "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities": re.compile(r'(?:ITEM\s+5\.)\s+(MARKET\s+FOR\s+REGISTRANT)', re.IGNORECASE),
                "[Reserved]": re.compile(r'(?:ITEM\s+6\.)\s+(\[?RESERVED\]?)', re.IGNORECASE),
                "Management's Discussion and Analysis of Financial Condition and Results of Operations": re.compile(r'(?:ITEM\s+7\.)\s+(MANAGEMENT\'S\s+DISCUSSION)', re.IGNORECASE),
                "Quantitative and Qualitative Disclosures about Market Risk": re.compile(r'(?:ITEM\s+7A\.)\s+(QUANTITATIVE\s+AND\xa0QUALITATIVE|QUANTITATIVE\s+AND\s+QUALITATIVE)', re.IGNORECASE),
                "Financial Statements and Supplementary Data": re.compile(r'(?:ITEM\s+8\.)\s+(FINANCIAL\s+STATEMENTS)', re.IGNORECASE),
                "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure": re.compile(r'(?:ITEM\s+9\.)\s+(CHANGES\s+IN\s+AND\s+DISAGREEMENTS)', re.IGNORECASE),
                "Controls and Procedures": re.compile(r'(?:ITEM\s+9A\.)\s+(CONTROLS\s+AND\s+PROCEDURES)', re.IGNORECASE),
                "Other Information": re.compile(r'(?:ITEM\s+9B\.)\s+(OTHER\s+INFORMATION)', re.IGNORECASE),
                "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections": re.compile(r'(?:ITEM\s+9C\.)\s+(DISCLOSURE\s+REGARDING\s+FOREIGN)', re.IGNORECASE),
                "Directors, Executive Officers and Corporate Governance": re.compile(r'(?:ITEM\s+10\.)\s+(DIRECTORS)', re.IGNORECASE),
                "Executive Compensation": re.compile(r'(?:ITEM\s+11\.)\s+(EXECUTIVE\s+COMPENSATION)', re.IGNORECASE),
                "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters": re.compile(r'(?:ITEM\s+12\.)\s+(SECURITY\s+OWNERSHIP)', re.IGNORECASE),
                "Certain Relationships and Related Transactions, and Director Independence": re.compile(r'(?:ITEM\s+13\.)\s+(CERTAIN\s+RELATIONSHIPS)', re.IGNORECASE),
                "Principal Accountant Fees and Services": re.compile(r'(?:ITEM\s+14\.)\s+(PRINCIPAL\s+ACCOUNTANT)', re.IGNORECASE),
                "Exhibits and Financial Statement Schedules": re.compile(r'(?:ITEM\s+15\.)\s+(EXHIBITS)', re.IGNORECASE),
                "Form 10-K Summary": re.compile(r'(?:ITEM\s+16\.)\s+(FORM\s+10-K\s+SUMMARY)', re.IGNORECASE),
            }
            
            matches = []
            for item_name, pat in loose_patterns.items():
                for m in pat.finditer(text):
                    matches.append((m.start(), item_name))
            
            matches.sort()
            
            # Group sections
            temp_sections = {}
            for i in range(len(matches)):
                start_idx, item_name = matches[i]
                end_idx = matches[i+1][0] if i + 1 < len(matches) else len(text)
                section_text = text[start_idx:end_idx].strip()
                if len(section_text) > 20:
                    temp_sections[item_name] = section_text
            
            sections = temp_sections

        # If all detection yields nothing, try a fallback heuristic segmenter
        if not sections:
            logger.warning("Regex item detection failed. Splitting using fallback heuristics.")
            sections["Business"] = text[:len(text)//23]
            sections["Risk Factors"] = text[len(text)//23: 2*len(text)//23]
            sections["Unresolved Staff Comments"] = text[2*len(text)//23: 3*len(text)//23]
            sections["Cybersecurity"] = text[3*len(text)//23: 4*len(text)//23]
            sections["Properties"] = text[4*len(text)//23: 5*len(text)//23]
            sections["Legal Proceedings"] = text[5*len(text)//23: 6*len(text)//23]
            sections["Mine Safety Disclosures"] = text[6*len(text)//23: 7*len(text)//23]
            sections["Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities"] = text[7*len(text)//23: 8*len(text)//23]
            sections["[Reserved]"] = text[8*len(text)//23: 9*len(text)//23]
            sections["Management's Discussion and Analysis of Financial Condition and Results of Operations"] = text[9*len(text)//23: 10*len(text)//23]
            sections["Quantitative and Qualitative Disclosures about Market Risk"] = text[10*len(text)//23: 11*len(text)//23]
            sections["Financial Statements and Supplementary Data"] = text[11*len(text)//23: 12*len(text)//23]
            sections["Changes in and Disagreements with Accountants on Accounting and Financial Disclosure"] = text[12*len(text)//23: 13*len(text)//23]
            sections["Controls and Procedures"] = text[13*len(text)//23: 14*len(text)//23]
            sections["Other Information"] = text[14*len(text)//23: 15*len(text)//23]
            sections["Disclosure Regarding Foreign Jurisdictions that Prevent Inspections"] = text[15*len(text)//23: 16*len(text)//23]
            sections["Directors, Executive Officers and Corporate Governance"] = text[16*len(text)//23: 17*len(text)//23]
            sections["Executive Compensation"] = text[17*len(text)//23: 18*len(text)//23]
            sections["Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters"] = text[18*len(text)//23: 19*len(text)//23]
            sections["Certain Relationships and Related Transactions, and Director Independence"] = text[19*len(text)//23: 20*len(text)//23]
            sections["Principal Accountant Fees and Services"] = text[20*len(text)//23: 21*len(text)//23]
            sections["Exhibits and Financial Statement Schedules"] = text[21*len(text)//23: 22*len(text)//23]
            sections["Form 10-K Summary"] = text[22*len(text)//23:]
            
        return sections

    @staticmethod
    def detect_10q_items(html_content: str) -> Dict[str, str]:
        """
        Detects and extracts specific 10-Q Item sections from HTML dynamically using pattern matching.
        """
        text = SECParser.parse_html_to_text(html_content)
        lines = text.split("\n")
        
        detected_items = {}
        current_part = "PART I"  # Default part
        
        for idx, line in enumerate(lines):
            # Normalize whitespace
            normalized_line = " ".join(line.split()).replace('\u00a0', ' ')
            
            # Trace Part section
            if re.match(r'^PART\s+I\b', normalized_line, re.IGNORECASE):
                if not re.match(r'^PART\s+II\b', normalized_line, re.IGNORECASE):
                    current_part = "PART I"
            elif re.match(r'^PART\s+II\b', normalized_line, re.IGNORECASE):
                current_part = "PART II"
                
            # Case 1: Same line match (e.g. "Item 1. Financial Statements...")
            m = re.match(r'^Item\s+([0-9A-Z]+)\b[\.\-:\s]*\s*(.+)$', normalized_line, re.IGNORECASE)
            if m:
                item_num = m.group(1).upper()
                category_name = m.group(2).strip()
                char_idx = sum(len(l) for l in lines[:idx]) + idx
                item_key = f"{current_part} - ITEM {item_num}"
                detected_items[item_key] = (char_idx, category_name)
                continue
                
            # Case 2: Split line match
            m_split = re.match(r'^Item\s+([0-9A-Z]+)\b[\.\-:\s]*$', normalized_line, re.IGNORECASE)
            if m_split:
                item_num = m_split.group(1).upper()
                category_name = ""
                for next_idx in range(idx + 1, min(len(lines), idx + 5)):
                    if lines[next_idx].strip():
                        normalized_next = " ".join(lines[next_idx].split()).replace('\u00a0', ' ')
                        if not re.match(r'^Item\s+([0-9A-Z]+)\b', normalized_next, re.IGNORECASE) and not re.match(r'^Part\s+', normalized_next, re.IGNORECASE):
                            category_name = normalized_next
                        break
                if category_name:
                    char_idx = sum(len(l) for l in lines[:idx]) + idx
                    item_key = f"{current_part} - ITEM {item_num}"
                    detected_items[item_key] = (char_idx, category_name)
                    
        # Assemble sections
        sections = {}
        if len(detected_items) >= 2:
            sorted_items = sorted(detected_items.items(), key=lambda x: x[1][0])
            for i in range(len(sorted_items)):
                item_key, (start_idx, category_name) = sorted_items[i]
                if i + 1 < len(sorted_items):
                    end_idx = sorted_items[i+1][1][0]
                else:
                    end_idx = len(text)
                    
                section_text = text[start_idx:end_idx].strip()
                if len(section_text) > 20:
                    standard_names = {
                        "PART I - ITEM 1": "Financial Statements",
                        "PART I - ITEM 2": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
                        "PART I - ITEM 3": "Quantitative and Qualitative Disclosures About Market Risk",
                        "PART I - ITEM 4": "Controls and Procedures",
                        "PART II - ITEM 1": "Legal Proceedings",
                        "PART II - ITEM 1A": "Risk Factors",
                        "PART II - ITEM 2": "Unregistered Sales of Equity Securities and Use of Proceeds",
                        "PART II - ITEM 3": "Defaults Upon Senior Securities",
                        "PART II - ITEM 4": "Mine Safety Disclosures",
                        "PART II - ITEM 5": "Other Information",
                        "PART II - ITEM 6": "Exhibits",
                    }
                    key_name = standard_names.get(item_key, category_name)
                    sections[key_name] = section_text
                    
        # Fallback if dynamic parsing yields nothing
        if not sections:
            logger.warning("Regex 10-Q item detection failed. Splitting using fallback heuristics.")
            sections["Financial Statements"] = text[:len(text)//4]
            sections["Management's Discussion and Analysis of Financial Condition and Results of Operations"] = text[len(text)//4: len(text)//2]
            sections["Risk Factors"] = text[len(text)//2: 3*len(text)//4]
            sections["Controls and Procedures"] = text[3*len(text)//4:]
            
        return sections

    @staticmethod
    def detect_8k_items(html_content: str) -> Dict[str, str]:
        """
        Detects and extracts specific 8-K Item sections from HTML dynamically using pattern matching.
        """
        text = SECParser.parse_html_to_text(html_content)
        lines = text.split("\n")
        
        detected_items = {}
        for idx, line in enumerate(lines):
            # Normalize whitespace
            normalized_line = " ".join(line.split()).replace('\u00a0', ' ')
            
            # Case 1: Same line match (e.g. "Item 2.02 Results of Operations...")
            m = re.match(r'^Item\s+(\d\.\d\d)\b[\.\-:\s]*\s*(.+)$', normalized_line, re.IGNORECASE)
            if m:
                item_num = m.group(1).upper()
                category_name = m.group(2).strip()
                char_idx = sum(len(l) for l in lines[:idx]) + idx
                detected_items[item_num] = (char_idx, category_name)
                continue
                
            # Case 2: Split line match
            m_split = re.match(r'^Item\s+(\d\.\d\d)\b[\.\-:\s]*$', normalized_line, re.IGNORECASE)
            if m_split:
                item_num = m_split.group(1).upper()
                category_name = ""
                for next_idx in range(idx + 1, min(len(lines), idx + 5)):
                    if lines[next_idx].strip():
                        normalized_next = " ".join(lines[next_idx].split()).replace('\u00a0', ' ')
                        if not re.match(r'^Item\s+\d\.\d\d\b', normalized_next, re.IGNORECASE):
                            category_name = normalized_next
                        break
                if category_name:
                    char_idx = sum(len(l) for l in lines[:idx]) + idx
                    detected_items[item_num] = (char_idx, category_name)
                    
        # Assemble sections
        sections = {}
        if len(detected_items) >= 2:
            sorted_items = sorted(detected_items.items(), key=lambda x: x[1][0])
            for i in range(len(sorted_items)):
                item_num, (start_idx, category_name) = sorted_items[i]
                if i + 1 < len(sorted_items):
                    end_idx = sorted_items[i+1][1][0]
                else:
                    end_idx = len(text)
                    
                section_text = text[start_idx:end_idx].strip()
                if len(section_text) > 20:
                    standard_names = {
                        "1.01": "Entry into a Material Definitive Agreement",
                        "1.02": "Termination of a Material Definitive Agreement",
                        "2.01": "Completion of Acquisition or Disposition of Assets",
                        "2.02": "Results of Operations and Financial Condition",
                        "2.03": "Creation of a Direct Financial Obligation",
                        "3.02": "Unregistered Sales of Equity Securities",
                        "5.02": "Departure of Directors or Certain Officers",
                        "8.01": "Other Events",
                        "9.01": "Financial Statements and Exhibits",
                    }
                    key_name = standard_names.get(item_num, category_name)
                    sections[key_name] = section_text
                    
        # Fallback if dynamic parsing yields nothing
        if not sections:
            logger.warning("Regex 8-K item detection failed. Splitting using fallback heuristics.")
            sections["Results of Operations and Financial Condition"] = text[:len(text)//2]
            sections["Financial Statements and Exhibits"] = text[len(text)//2:]
            
        return sections




# ==========================================
# 3. SEMANTIC CHUNKING & METADATA EXTRACTION
# ==========================================
class SemanticChunker:
    """
    Groups paragraphs/sentences based on semantic similarity.
    For this self-contained script, we simulate semantic chunking using sentence/paragraph
    grouping and distance measurement of TF-IDF vectors, ensuring a clean implementation without dependencies.
    """
    def __init__(self, target_chunk_size: int = 800, overlap: int = 150):
        self.target_chunk_size = target_chunk_size
        self.overlap = overlap

    def chunk_section(self, text: str, metadata_base: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Chunks a given section text and adds metadata to each chunk.
        """
        # Split by double newlines or paragraph patterns
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if len(p.strip()) > 20]
        
        chunks = []
        current_chunk = []
        current_length = 0
        
        for p in paragraphs:
            current_chunk.append(p)
            current_length += len(p)
            
            if current_length >= self.target_chunk_size:
                chunk_text = "\n\n".join(current_chunk)
                chunks.append({
                    "text": chunk_text,
                    "metadata": {
                        **metadata_base,
                        "char_count": len(chunk_text),
                        "word_count": len(chunk_text.split())
                    }
                })
                # Retain the last paragraph for overlap
                current_chunk = current_chunk[-1:]
                current_length = len(current_chunk[0]) if current_chunk else 0
                
        if current_chunk and current_length > 50:
            chunk_text = "\n\n".join(current_chunk)
            chunks.append({
                "text": chunk_text,
                "metadata": {
                    **metadata_base,
                    "char_count": len(chunk_text),
                    "word_count": len(chunk_text.split())
                }
            })
            
        return chunks


    def add(self, documents: List[str], metadatas: List[Dict[str, Any]], ids: List[str]):
        self.documents.extend(documents)
        self.metadatas.extend(metadatas)
        self.ids.extend(ids)
        self.save()

    def save(self):
        if self.persist_path:
            try:
                with open(self.persist_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "documents": self.documents,
                        "metadatas": self.metadatas,
                        "ids": self.ids
                    }, f, indent=4)
            except Exception as e:
                logger.error(f"Failed to save mock chroma collection to disk: {e}")

    def get(self) -> Dict[str, Any]:
        return {
            "ids": self.ids,
            "documents": self.documents,
            "metadatas": self.metadatas
        }

    def query(self, query_texts: List[str], n_results: int = 5, where: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Simple simulated TF-IDF similarity vector search
        results = []
        for doc, meta, doc_id in zip(self.documents, self.metadatas, self.ids):
            if where:
                match = True
                if "$and" in where:
                    for cond in where["$and"]:
                        for k, v_op in cond.items():
                            val = v_op["$eq"] if isinstance(v_op, dict) and "$eq" in v_op else v_op
                            if meta.get(k) != val:
                                match = False
                                break
                        if not match:
                            break
                else:
                    for k, v_op in where.items():
                        val = v_op["$eq"] if isinstance(v_op, dict) and "$eq" in v_op else v_op
                        if meta.get(k) != val:
                            match = False
                            break
                if not match:
                    continue
            # Simple word overlap similarity mock
            q_words = set(query_texts[0].lower().split())
            d_words = set(doc.lower().split())
            overlap = len(q_words.intersection(d_words))
            score = overlap / (math.log(len(doc) + 1) + 1)
            results.append((score, doc, meta, doc_id))
        
        results.sort(key=lambda x: x[0], reverse=True)
        top = results[:n_results]
        
        return {
            "documents": [[t[1] for t in top]],
            "metadatas": [[t[2] for t in top]],
            "ids": [[t[3] for t in top]],
            "distances": [[1.0 - t[0] for t in top]] # Pseudo-distance
        }

try:
    import chromadb
    USE_REAL_CHROMA = True
except ImportError:
    USE_REAL_CHROMA = False
    logger.info("chromadb not installed. Using local Mock Chroma DB.")

    def get_scores(self, query: str) -> List[float]:
        q_words = query.lower().split()
        scores = []
        k1 = 1.5
        b = 0.75
        for i, doc_freq in enumerate(self.doc_freqs):
            score = 0.0
            dl = self.doc_lengths[i]
            for word in q_words:
                if word not in doc_freq:
                    continue
                tf = doc_freq[word]
                # Calculate simple IDF
                df = self.df.get(word, 0)
                idf = math.log((self.corpus_size - df + 0.5) / (df + 0.5) + 1.0)
                # BM25 formula
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * (dl / self.avgdl))
                score += idf * (numerator / denominator)
            scores.append(score)
        return scores

try:
    from rank_bm25 import BM25Okapi
    USE_REAL_BM25 = True
except ImportError:
    USE_REAL_BM25 = False
    logger.info("rank_bm25 not installed. Using local Mock BM25 implementation.")


class HybridSearcher:
    """
    Manages vector index (Chroma) and lexical index (BM25) to run hybrid retrieval and reranking.
    """
    def __init__(self, persist_directory: Optional[str] = None):
        self.persist_directory = persist_directory
        if self.persist_directory:
            self.chroma_client = chromadb.PersistentClient(path=self.persist_directory)
            self.collection = self.chroma_client.get_or_create_collection("sec_filings")
        else:
            self.chroma_client = chromadb.EphemeralClient()
            try:
                self.chroma_client.delete_collection("sec_filings")
            except Exception:
                pass
            self.collection = self.chroma_client.get_or_create_collection("sec_filings")
            
        self.bm25_corpus: List[str] = []
        self.bm25_metadata: List[Dict[str, Any]] = []
        self.bm25_model: Optional[Any] = None
        self.parent_docs: Dict[Tuple[str, str, str], str] = {} # Key: (ticker, form_type, item_name) -> Full plaintext
        
        # Load saved metadata (BM25 & Parents) if persistent
        self.load_metadata()

    def save_metadata(self):
        if not self.persist_directory:
            return
        os.makedirs(self.persist_directory, exist_ok=True)
        meta_path = os.path.join(self.persist_directory, "hybrid_index_meta.json")
        try:
            # Serialize parent_docs dictionary (tuple keys to string keys)
            serialized_parents = {}
            for (ticker, form, item), text in self.parent_docs.items():
                key_str = f"{ticker}|{form}|{item}"
                serialized_parents[key_str] = text
                
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({
                    "bm25_corpus": self.bm25_corpus,
                    "bm25_metadata": self.bm25_metadata,
                    "parent_docs": serialized_parents
                }, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save hybrid index metadata: {e}")

    def load_metadata(self):
        if not self.persist_directory:
            return
        meta_path = os.path.join(self.persist_directory, "hybrid_index_meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.bm25_corpus = data.get("bm25_corpus", [])
                    self.bm25_metadata = data.get("bm25_metadata", [])
                    
                    # Deserialize parent_docs
                    serialized_parents = data.get("parent_docs", {})
                    self.parent_docs = {}
                    for key_str, text in serialized_parents.items():
                        parts = key_str.split("|", 2)
                        if len(parts) == 3:
                            self.parent_docs[(parts[0], parts[1], parts[2])] = text
                            
                # Re-initialize BM25 model if corpus is loaded
                if self.bm25_corpus:
                    tokenized_corpus = [doc.lower().split() for doc in self.bm25_corpus]
                    self.bm25_model = BM25Okapi(tokenized_corpus)
            except Exception as e:
                logger.error(f"Failed to load hybrid index metadata: {e}")

    def register_parent_doc(self, ticker: str, form_type: str, item_name: str, text: str):
        ticker_c = ticker.upper().strip()
        form_c = form_type.upper().strip()
        item_c = item_name.strip()
        self.parent_docs[(ticker_c, form_c, item_c)] = text
        self.save_metadata()

    def get_parent_doc(self, ticker: str, form_type: str, item_name: str, date: Optional[str] = None) -> Optional[str]:
        ticker_c = ticker.upper().strip()
        form_c = form_type.upper().strip()
        item_c = item_name.strip()
        if date:
            res = self.parent_docs.get((ticker_c, form_c, f"{item_c}_{date.strip()}"))
            if res:
                return res
        return self.parent_docs.get((ticker_c, form_c, item_c))

    def delete_ticker_data(self, ticker: str):
        ticker_c = ticker.upper().strip()
        # 1. Delete from Chroma
        if USE_REAL_CHROMA:
            try:
                self.collection.delete(where={"ticker": ticker_c})
            except Exception as e:
                logger.error(f"Failed to delete ticker {ticker_c} from Chroma: {e}")
        else:
            new_docs = []
            new_metas = []
            new_ids = []
            for doc, meta, doc_id in zip(self.collection.documents, self.collection.metadatas, self.collection.ids):
                if meta.get("ticker") != ticker_c:
                    new_docs.append(doc)
                    new_metas.append(meta)
                    new_ids.append(doc_id)
            self.collection.documents = new_docs
            self.collection.metadatas = new_metas
            self.collection.ids = new_ids
            self.collection.save()
            
        # 2. Delete from BM25
        new_bm25_corpus = []
        new_bm25_metadata = []
        for doc, meta in zip(self.bm25_corpus, self.bm25_metadata):
            if meta.get("ticker") != ticker_c:
                new_bm25_corpus.append(doc)
                new_bm25_metadata.append(meta)
        self.bm25_corpus = new_bm25_corpus
        self.bm25_metadata = new_bm25_metadata
        if self.bm25_corpus:
            tokenized_corpus = [doc.lower().split() for doc in self.bm25_corpus]
            self.bm25_model = BM25Okapi(tokenized_corpus)
        else:
            self.bm25_model = None
            
        # 3. Delete from parent docs
        new_parent_docs = {}
        for (t, f, i), text in self.parent_docs.items():
            if t != ticker_c:
                new_parent_docs[(t, f, i)] = text
        self.parent_docs = new_parent_docs
        
        self.save_metadata()
        
        # 4. Delete from IngestionRegistry
        try:
            IngestionRegistry().remove_ticker_from_registry(ticker)
        except Exception as e:
            logger.error(f"Failed to remove ticker {ticker_c} from registry: {e}")

    def ingest_chunks(self, chunks: List[Dict[str, Any]]):
        """
        Stores chunks in Vector Store and initializes BM25 corpus.
        """
        documents = [c["text"] for c in chunks]
        metadatas = [c["metadata"] for c in chunks]
        ids = [f"chunk_{i}_{hash(c['text'])}" for i, c in enumerate(chunks)]
        
        self.collection.add(documents=documents, metadatas=metadatas, ids=ids)
        
        self.bm25_corpus.extend(documents)
        self.bm25_metadata.extend(metadatas)
        
        tokenized_corpus = [doc.lower().split() for doc in self.bm25_corpus]
        if USE_REAL_BM25:
            self.bm25_model = BM25Okapi(tokenized_corpus)
        else:
            self.bm25_model = None
            
        self.save_metadata()
        logger.info(f"Successfully ingested {len(chunks)} chunks into Hybrid Index.")

    def rrf_hybrid_retrieve(self, query: str, top_k: int = 5, rrf_k: int = 60, metadata_filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Performs hybrid retrieval using Reciprocal Rank Fusion (RRF) with metadata filtering.
        """
        chroma_where = None
        if metadata_filter:
            if len(metadata_filter) > 1:
                chroma_where = {
                    "$and": [{k: {"$eq": v}} for k, v in metadata_filter.items()]
                }
            elif len(metadata_filter) == 1:
                k, v = list(metadata_filter.items())[0]
                chroma_where = {k: {"$eq": v}}
                
        # 1. Vector Search
        if USE_REAL_CHROMA:
            vector_results = self.collection.query(query_texts=[query], n_results=top_k * 2, where=chroma_where)
        else:
            vector_results = self.collection.query(query_texts=[query], n_results=top_k * 2, where=chroma_where)
            
        v_docs = vector_results.get("documents", [[]])[0]
        v_metas = vector_results.get("metadatas", [[]])[0]
        
        v_ranking = []
        for doc, meta in zip(v_docs, v_metas):
            v_ranking.append(doc)
            
        # 2. BM25 Search
        bm25_scores = []
        if USE_REAL_BM25 and self.bm25_model:
            tokenized_query = query.lower().split()
            bm25_scores = self.bm25_model.get_scores(tokenized_query)
        elif self.bm25_model:
            bm25_scores = self.bm25_model.get_scores(query)
            
        bm25_ranking = []
        if len(bm25_scores) > 0:
            scored_docs = list(zip(self.bm25_corpus, self.bm25_metadata, bm25_scores))
            # Apply metadata filter on BM25 candidates
            if metadata_filter:
                filtered_scored = []
                for doc, meta, score in scored_docs:
                    match = True
                    for k, v in metadata_filter.items():
                        if meta.get(k) != v:
                            match = False
                            break
                    if match:
                        filtered_scored.append((doc, meta, score))
                scored_docs = filtered_scored
                
            scored_docs.sort(key=lambda x: x[2], reverse=True)
            bm25_ranking = [item[0] for item in scored_docs[:top_k * 2]]

        # 3. Reciprocal Rank Fusion
        rrf_scores: Dict[str, float] = {}
        doc_metadata_map: Dict[str, Dict[str, Any]] = {}
        
        # Build metadata map
        for doc, meta in zip(v_docs, v_metas):
            doc_metadata_map[doc] = meta
        for doc, meta in zip(self.bm25_corpus, self.bm25_metadata):
            doc_metadata_map[doc] = meta

        # Add vector ranks
        for rank, doc in enumerate(v_ranking):
            rrf_scores[doc] = rrf_scores.get(doc, 0.0) + (1.0 / (rrf_k + rank + 1))
            
        # Add BM25 ranks
        for rank, doc in enumerate(bm25_ranking):
            rrf_scores[doc] = rrf_scores.get(doc, 0.0) + (1.0 / (rrf_k + rank + 1))

        # Sort and take top_k
        sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        
        hybrid_results = []
        for doc, score in sorted_docs[:top_k]:
            hybrid_results.append({
                "text": doc,
                "metadata": doc_metadata_map.get(doc, {}),
                "rrf_score": score
            })
            
        return hybrid_results


# ==========================================
# 5. CROSS-ENCODER RERANKING
# ==========================================
class CrossEncoderReranker:
    """
    Reranks documents using a Cross-Encoder similarity checker.
    """
    def __init__(self):
        pass

    def rerank(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Re-ranks top candidates based on fine-grained keyword match & semantic coverage.
        """
        reranked = []
        q_words = set(query.lower().split())
        
        for candidate in candidates:
            doc_text = candidate["text"].lower()
            # Calculate intersection ratio
            exact_match_score = sum(1 for word in q_words if word in doc_text) / max(len(q_words), 1)
            # Factor in length penalty and keyword distance
            rerank_score = candidate["rrf_score"] * 0.3 + exact_match_score * 0.7
            
            new_candidate = candidate.copy()
            new_candidate["rerank_score"] = rerank_score
            reranked.append(new_candidate)
            
        reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
        return reranked


# ==========================================
# 6. LANGGRAPH MULTI-AGENT WORKFLOW
# ==========================================
class MultiAgentSupervisor:
    """
    Simulates a multi-agent system coordinated by a central Supervisor agent.
    If 'langgraph' isn't available, executes a functional state transition graph.
    """
    def __init__(self, searcher: HybridSearcher, reranker: CrossEncoderReranker, ticker: str = "NVDA", form_type: str = "10-K"):
        self.searcher = searcher
        self.reranker = reranker
        self.ticker = ticker
        self.form_type = form_type

    def run_agent_workflow(self, query: str) -> Dict[str, Any]:
        """
        Runs the Supervisor orchestrator which delegates subtasks to:
        - Ingestion Agent (ensures index status)
        - Retrieval Agent (performs hybrid retrieval + reranking)
        - Analyst Agent (synthesizes the answer based on data)
        """
        logger.info(f"Supervisor received inquiry: '{query}'")
        state = {
            "query": query,
            "routing_decision": "IngestionAgent",
            "retrieved_documents": [],
            "final_answer": "",
            "logs": []
        }

        # Step 1: Supervisor routes to Ingestion Agent to ensure DB is populated
        state["logs"].append("Routed to IngestionAgent")
        self._ingestion_agent_node(state)
        
        # Step 2: Supervisor routes to Retrieval Agent
        state["logs"].append("Routed to RetrievalAgent")
        self._retrieval_agent_node(state)
        
        # Step 3: Supervisor routes to Analyst Agent
        state["logs"].append("Routed to FinancialAnalystAgent")
        self._analyst_agent_node(state)
        
        # Return final state
        return state

    def _ingestion_agent_node(self, state: Dict[str, Any]):
        # Ensures index database is active
        state["ingestion_status"] = "Verified Active"
        logger.info("Ingestion Agent: Vector store verified and ready.")

    def _retrieval_agent_node(self, state: Dict[str, Any]):
        # 1. Metadata Filter
        ticker_c = self.ticker.upper().strip()
        form_c = self.form_type.upper().strip()
        form_val = "Earnings" if form_c in ("EARNINGS_CALL", "EARNINGS") else form_c
        metadata_filter = {
            "ticker": ticker_c,
            "form_type": form_val
        }
        
        # 2. Vector + BM25 RRF Search (Top 30 candidates)
        candidates = self.searcher.rrf_hybrid_retrieve(
            state["query"],
            top_k=30,
            metadata_filter=metadata_filter
        )
        
        # 3. Cross Encoder Reranker
        reranked = self.reranker.rerank(state["query"], candidates)
        
        # 4. Deduplicate
        seen_texts = set()
        deduped = []
        for doc in reranked:
            t_clean = doc["text"].strip()
            if t_clean not in seen_texts:
                seen_texts.add(t_clean)
                deduped.append(doc)
                
        # 5. Parent Retrieval
        final_docs = []
        for doc in deduped[:5]:
            meta = doc["metadata"]
            t_meta = meta.get("ticker", ticker_c)
            f_meta = meta.get("form_type", form_val)
            item_meta = meta.get("item_name", "Section")
            d_meta = meta.get("filing_date")
            
            parent_text = self.searcher.get_parent_doc(t_meta, f_meta, item_meta, d_meta)
            if parent_text:
                logger.info(f"Parent Retrieval matched: {t_meta} {f_meta} {item_meta}_{d_meta}. Substituting parent context.")
                new_doc = doc.copy()
                if len(parent_text) > 30000:
                    logger.info(f"Truncating parent document from {len(parent_text)} to 30000 characters to prevent token limit crash.")
                    parent_text = parent_text[:30000] + "... [Content Truncated to Avoid Token Limits] ..."
                new_doc["text"] = parent_text
                final_docs.append(new_doc)
            else:
                final_docs.append(doc)
                
        # 6. LLM Context Builder
        state["retrieved_documents"] = final_docs
        logger.info(f"Retrieval Agent: Retrieved {len(final_docs)} deduplicated parent context items.")

    def _analyst_agent_node(self, state: Dict[str, Any]):
        # Synthesize final output from retrieved files
        docs = state["retrieved_documents"]
        if not docs:
            state["final_answer"] = "No financial details could be retrieved to answer this request."
            return
            
        context_blocks = []
        for idx, doc in enumerate(docs):
            source = f"Source: {doc['metadata'].get('ticker', 'Unknown')} {doc['metadata'].get('form_type', 'SEC')} ({doc['metadata'].get('item_name', 'Section')})"
            context_blocks.append(f"[{idx+1}] {source}\n{doc['text']}")
        context_str = "\n\n---\n\n".join(context_blocks)
        
        # Simulated analysis response based on parent text context
        state["final_answer"] = (
            f"Based on the corporate reports, here is the analysis:\n"
            f"{context_str}\n\n"
            f"[Analysis Source: {docs[0]['metadata'].get('item_name', 'SEC Document')}]"
        )
        logger.info("Financial Analyst Agent: Synthesis completed.")


# ==========================================
# 7. ASYNC INGESTION SKELETON
# ==========================================
async def async_fetch_and_parse(cik: str, downloader: SECDownloader, parser: SECParser, searcher: Optional[HybridSearcher] = None, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Asynchronously retrieves filings, downloads HTML, and parses them for up to specified years of 10-K filings.
    """
    logger.info(f"Starting async 10-K ingestion pipeline for CIK {cik} (up to {NUM_YEARS_TO_LOAD_10K} years)...")
    
    loop = asyncio.get_event_loop()
    if metadata is None:
        metadata = await loop.run_in_executor(None, downloader.get_filings_metadata, cik)
    
    recent_filings = metadata.get("filings", {}).get("recent", {})
    if not recent_filings:
        logger.error("No filings found in metadata.")
        return []
        
    # Find up to 10 matching 10-K filings
    indices = []
    forms = recent_filings.get("form", [])
    for i, form_type in enumerate(forms):
        if form_type == "10-K":
            indices.append(i)
            if len(indices) >= NUM_YEARS_TO_LOAD_10K:
                break
                
    if not indices:
        logger.error("No 10-K filing found in metadata.")
        return []
        
    ticker = metadata.get("tickers", ["UNKNOWN"])[0]
    registry = IngestionRegistry()
    all_chunks = []
    
    for idx in indices:
        acc_num = recent_filings["accessionNumber"][idx]
        primary_doc = recent_filings["primaryDocument"][idx]
        date = recent_filings["filingDate"][idx]
        
        # Include specific year/date in registry check so they ingestion state is properly tracked individually
        if registry.should_skip_ingestion(ticker, f"10-K-{date}", date, searcher):
            logger.info(f"Ingestion check: Form 10-K for {ticker} (filing date {date}) is already up-to-date. Skipping.")
            continue
        
        # Mark as in-progress immediately to block any concurrent thread from also downloading this filing
        registry.mark_in_progress(ticker, f"10-K-{date}", date)
            
        try:
            html_content = await loop.run_in_executor(None, downloader.download_10k_html, cik, acc_num, primary_doc)
            sections = parser.detect_sec_items(html_content)
            chunker = SemanticChunker()
            
            for item_name, section_text in sections.items():
                base_meta = {
                    "cik": cik,
                    "ticker": ticker,
                    "filing_date": date,
                    "item_name": item_name,
                    "form_type": "10-K",
                    "url": f"https://www.sec.gov/Archives/edgar/data/{str(int(cik))}/{acc_num.replace('-', '')}/{primary_doc}"
                }
                if searcher:
                    # Save with date suffix to allow distinct parent retrievals per year
                    searcher.register_parent_doc(ticker, "10-K", f"{item_name}_{date}", section_text)
                item_chunks = chunker.chunk_section(section_text, base_meta)
                all_chunks.extend(item_chunks)
                
            logger.info(f"Parsed Form 10-K for {ticker} (filing date {date}) successfully.")
            registry.update_registry(ticker, f"10-K-{date}", date)
        except Exception as e:
            logger.error(f"Error parsing 10-K on date {date}: {e}")
            
    return all_chunks

async def async_fetch_and_parse_10q(cik: str, downloader: SECDownloader, parser: SECParser, searcher: Optional[HybridSearcher] = None, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Asynchronously retrieves filings, downloads HTML for 10-Q, and parses them for up to specified quarters.
    """
    logger.info(f"Starting async 10-Q ingestion pipeline for CIK {cik} (up to {NUM_QUARTERS_TO_LOAD_10Q} quarters)...")
    
    loop = asyncio.get_event_loop()
    if metadata is None:
        metadata = await loop.run_in_executor(None, downloader.get_filings_metadata, cik)
    
    recent_filings = metadata.get("filings", {}).get("recent", {})
    if not recent_filings:
        logger.error("No filings found in metadata.")
        return []
        
    # Find up to specified matching 10-Q filings
    indices = []
    forms = recent_filings.get("form", [])
    for i, form_type in enumerate(forms):
        if form_type == "10-Q":
            indices.append(i)
            if len(indices) >= NUM_QUARTERS_TO_LOAD_10Q:
                break
                
    if not indices:
        logger.error("No 10-Q filing found in metadata.")
        return []
        
    ticker = metadata.get("tickers", ["UNKNOWN"])[0]
    registry = IngestionRegistry()
    all_chunks = []
    
    for idx in indices:
        acc_num = recent_filings["accessionNumber"][idx]
        primary_doc = recent_filings["primaryDocument"][idx]
        date = recent_filings["filingDate"][idx]
        
        if registry.should_skip_ingestion(ticker, f"10-Q-{date}", date, searcher):
            logger.info(f"Ingestion check: Form 10-Q for {ticker} (filing date {date}) is already up-to-date. Skipping.")
            continue
        
        # Mark as in-progress immediately to block any concurrent thread from also downloading this filing
        registry.mark_in_progress(ticker, f"10-Q-{date}", date)
            
        try:
            html_content = await loop.run_in_executor(None, downloader.download_10q_html, cik, acc_num, primary_doc)
            sections = parser.detect_10q_items(html_content)
            chunker = SemanticChunker()
            
            for item_name, section_text in sections.items():
                base_meta = {
                    "cik": cik,
                    "ticker": ticker,
                    "filing_date": date,
                    "item_name": item_name,
                    "form_type": "10-Q",
                    "url": f"https://www.sec.gov/Archives/edgar/data/{str(int(cik))}/{acc_num.replace('-', '')}/{primary_doc}"
                }
                if searcher:
                    searcher.register_parent_doc(ticker, "10-Q", f"{item_name}_{date}", section_text)
                item_chunks = chunker.chunk_section(section_text, base_meta)
                all_chunks.extend(item_chunks)
                
            logger.info(f"Parsed Form 10-Q for {ticker} (filing date {date}) successfully.")
            registry.update_registry(ticker, f"10-Q-{date}", date)
        except Exception as e:
            logger.error(f"Error parsing 10-Q on date {date}: {e}")
            
    return all_chunks

async def async_fetch_and_parse_8k(cik: str, downloader: SECDownloader, parser: SECParser, searcher: Optional[HybridSearcher] = None, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Asynchronously retrieves filings, downloads HTML for 8-K, and parses them for up to the last specified days.
    """
    logger.info(f"Starting async 8-K ingestion pipeline for CIK {cik} (up to {NUM_DAYS_TO_LOAD_8K} days)...")
    
    loop = asyncio.get_event_loop()
    if metadata is None:
        metadata = await loop.run_in_executor(None, downloader.get_filings_metadata, cik)
    
    recent_filings = metadata.get("filings", {}).get("recent", {})
    if not recent_filings:
        logger.error("No filings found in metadata.")
        return []
    # Find all 8-K indices filed within days specified
    import datetime
    two_years_ago = datetime.datetime.now() - datetime.timedelta(days=NUM_DAYS_TO_LOAD_8K)
    indices = []
    forms = recent_filings.get("form", [])
    for i, form_type in enumerate(forms):
        if form_type == "8-K":
            date_str = recent_filings["filingDate"][i]
            try:
                filing_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                if filing_date >= two_years_ago:
                    indices.append(i)
            except Exception:
                # Fallback to include recent ones if parsing fails
                if len(indices) < 5:
                    indices.append(i)
                    
    if not indices:
        logger.error("No 8-K filing found in metadata.")
        return []
        
    ticker = metadata.get("tickers", ["UNKNOWN"])[0]
    registry = IngestionRegistry()
    all_chunks = []
    
    # Cap 8-K at a reasonable count (e.g. 15 filings) to prevent API rate exhaustion
    for idx in indices[:15]:
        acc_num = recent_filings["accessionNumber"][idx]
        primary_doc = recent_filings["primaryDocument"][idx]
        date = recent_filings["filingDate"][idx]
        
        if registry.should_skip_ingestion(ticker, f"8-K-{date}", date, searcher):
            logger.info(f"Ingestion check: Form 8-K for {ticker} (filing date {date}) is already up-to-date. Skipping.")
            continue
        
        # Mark as in-progress immediately to block any concurrent thread from also downloading this filing
        registry.mark_in_progress(ticker, f"8-K-{date}", date)
            
        try:
            html_content = await loop.run_in_executor(None, downloader.download_8k_html, cik, acc_num, primary_doc)
            sections = parser.detect_8k_items(html_content)
            chunker = SemanticChunker()
            
            for item_name, section_text in sections.items():
                base_meta = {
                    "cik": cik,
                    "ticker": ticker,
                    "filing_date": date,
                    "item_name": item_name,
                    "form_type": "8-K",
                    "url": f"https://www.sec.gov/Archives/edgar/data/{str(int(cik))}/{acc_num.replace('-', '')}/{primary_doc}"
                }
                if searcher:
                    searcher.register_parent_doc(ticker, "8-K", f"{item_name}_{date}", section_text)
                item_chunks = chunker.chunk_section(section_text, base_meta)
                all_chunks.extend(item_chunks)
                
            logger.info(f"Parsed Form 8-K for {ticker} (filing date {date}) successfully.")
            registry.update_registry(ticker, f"8-K-{date}", date)
        except Exception as e:
            logger.error(f"Error parsing 8-K on date {date}: {e}")
            
    return all_chunks

def fetch_transcripts_from_filingapi(ticker_clean: str) -> str:
    """
    Fetch the raw earnings transcript JSON string for *ticker_clean* from filingapi.dev.

    Returns the raw response body as a string.
    Raises ValueError if the API key is missing, or propagates any network error.

    To swap this implementation for a different API, MCP, or web service, replace
    this function (or point EarningsCallManager.fetch_transcripts_raw at a new one).
    """
    import os
    import urllib.request
    import ssl

    api_key = ""
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip() and not line.startswith("#"):
                        parts = line.split("=", 1)
                        if len(parts) == 2 and parts[0].strip() == "FILING_API_KEY":
                            api_key = parts[1].strip()
                            break
        except Exception:
            pass
    if not api_key:
        api_key = os.environ.get("FILING_API_KEY", "")

    if not api_key:
        raise ValueError("FILING_API_KEY not found in env or .env file. Cannot fetch earnings transcript.")

    url = f"https://filingapi.dev/v1/transcripts/{ticker_clean.lower()}"
    logger.info(f"Fetching transcripts from filingapi.dev for {ticker_clean}...")
    req = urllib.request.Request(
        url,
        headers={
            "X-API-Key": api_key,
            "User-Agent": "Mozilla/5.0"
        }
    )
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(req, context=context, timeout=10) as response:
        res_body = response.read().decode("utf-8")
    logger.info(f"Successfully fetched transcripts list from filingapi.dev for {ticker_clean}")
    return res_body


class EarningsCallManager:
    """
    Handles fetching, parsing, and semantic chunking of corporate Earnings Call transcripts.

    The actual HTTP/API call is delegated to ``self.fetch_transcripts_raw``, which
    defaults to :func:`fetch_transcripts_from_filingapi`.  To switch to a different
    provider, MCP, or web service, simply reassign that attribute before calling
    ``fetch_and_parse_transcript``::

        manager = EarningsCallManager()
        manager.fetch_transcripts_raw = my_custom_fetch_function
    """
    def __init__(self):
        # Pointer to the raw-fetch implementation.  Replace to swap providers.
        self.fetch_transcripts_raw = fetch_transcripts_from_filingapi

    def fetch_and_parse_transcript(self, ticker: str, searcher: Optional[Any] = None) -> List[Dict[str, Any]]:
        ticker_clean = ticker.upper().strip()
        registry = IngestionRegistry()
        
        logger.info(f"Retrieving earnings call transcripts for ticker: {ticker_clean} (up to {NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS} quarters)...")
        
        import os
        import time
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cache_path = os.path.join(root_dir, f"transcripts_cache_{ticker_clean}.json")
        
        res_body = None
        # Try loading from cache if it is fresh (less than 24 hours old)
        if os.path.exists(cache_path):
            file_age = time.time() - os.path.getmtime(cache_path)
            if file_age < 86400: # 24 hours
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        res_body = f.read()
                    logger.info(f"Loaded transcripts list from local cache for {ticker_clean}")
                except Exception as cache_err:
                    logger.warning(f"Failed to read transcripts cache: {cache_err}")
                    
        if not res_body:
            res_body = self.fetch_transcripts_raw(ticker_clean)

            # Save to cache
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(res_body)
            except Exception as cache_err:
                logger.warning(f"Failed to write transcripts cache: {cache_err}")

        
        all_transcript_chunks = []
        try:
            data = json.loads(res_body)
            transcripts_list = []
            if isinstance(data, dict) and "transcripts" in data and isinstance(data["transcripts"], list):
                transcripts_list = data["transcripts"]
            elif isinstance(data, list):
                transcripts_list = data
                
            if not transcripts_list:
                logger.warning("No transcripts found in API response.")
                return []
                
            chunker = SemanticChunker()
            for t_idx, trans in enumerate(transcripts_list[:NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS]):
                t_date = trans.get("date") or trans.get("filing_date") or f"Q_{t_idx + 1}"
                logger.info(f"Processing transcript quarter index {t_idx+1} dated {t_date} for {ticker_clean}...")
                
                base_meta = {
                    "cik": "0001045810" if ticker_clean == "NVDA" else "0000320193",
                    "ticker": ticker_clean,
                    "filing_date": t_date,
                    "item_name": f"Earnings Call Transcript {t_date}",
                    "form_type": "Earnings",
                    "url": f"https://filingapi.dev/v1/transcripts/{ticker_clean.lower()}"
                }
                
                turns = []
                sections = trans.get("sections", [])
                if sections:
                    for sec in sections:
                        speaker = sec.get("speaker") or "Unknown"
                        text = sec.get("text") or ""
                        if text.strip():
                            turns.append({
                                "speaker": speaker,
                                "text": text.strip()
                            })
                            
                # Fallback to plain text transcript if sections are not structured
                if not turns:
                    raw_content = trans.get("transcript") or trans.get("text") or trans.get("content") or ""
                    if raw_content:
                        raw_paragraphs = [p.strip() for p in raw_content.split("\n") if p.strip()]
                        current_speaker = "Unknown"
                        current_text = []
                        for para in raw_paragraphs:
                            match = re.match(r"^\s*([A-Z][a-zA-Z\s\.,\-\(\)/&]+?)(?::|--)\s*(.*)", para)
                            if match:
                                if current_text:
                                    turns.append({
                                        "speaker": current_speaker,
                                        "text": "\n\n".join(current_text)
                                    })
                                current_speaker = match.group(1).strip()
                                current_text = [match.group(2).strip()]
                            else:
                                current_text.append(para)
                        if current_text:
                            turns.append({
                                "speaker": current_speaker,
                                "text": "\n\n".join(current_text)
                            })
                            
                if not turns:
                    continue
                    
                chunks = []
                chunk_idx = 0
                i = 0
                while i < len(turns):
                    turn = turns[i]
                    speaker = turn["speaker"]
                    text = turn["text"]
                    if speaker.lower() in ("operator", "unknown") and len(text) < 150:
                        i += 1
                        continue
                    if len(text) < 40 and ("thank you" in text.lower() or "good morning" in text.lower() or "next question" in text.lower()):
                        i += 1
                        continue
                    combined_text = f"{speaker}: {text}"
                    if i + 1 < len(turns):
                        next_turn = turns[i+1]
                        next_speaker = next_turn["speaker"]
                        next_text = next_turn["text"]
                        if len(combined_text) + len(next_text) < 1200:
                            combined_text += f"\n\n{next_speaker}: {next_text}"
                            i += 1
                            
                    chunks.append({
                        "text": combined_text,
                        "metadata": {
                            **base_meta,
                            "paragraph_index": chunk_idx,
                            "char_count": len(combined_text),
                            "word_count": len(combined_text.split())
                        }
                    })
                    chunk_idx += 1
                    i += 1
                    
                all_transcript_chunks.extend(chunks)
                if searcher and chunks:
                    parent_text = "\n\n".join([c["text"] for c in chunks])
                    searcher.register_parent_doc(ticker_clean, "Earnings", f"Earnings Call Transcript_{t_date}", parent_text)
                    
            latest_date = "N/A"
            if transcripts_list:
                latest_date = transcripts_list[0].get("date") or transcripts_list[0].get("filing_date") or "N/A"
            registry.update_registry(ticker_clean, "Earnings", latest_date)
            logger.info(f"Successfully processed {len(transcripts_list[:12])} quarters of earnings call transcripts into {len(all_transcript_chunks)} total chunks.")
            return all_transcript_chunks
        except Exception as e:
            logger.error(f"Error parsing transcripts list: {e}")
            return []


async def async_fetch_and_parse_earnings_call(ticker: str, manager: EarningsCallManager, searcher: Optional[HybridSearcher] = None) -> List[Dict[str, Any]]:
    """
    Asynchronously triggers the earnings call retrieval and chunking pipeline.
    """
    loop = asyncio.get_event_loop()
    chunks = await loop.run_in_executor(None, manager.fetch_and_parse_transcript, ticker, searcher)
    return chunks


async def async_ingest_all_corporate_data(ticker_symbol: str, searcher: HybridSearcher) -> str:
    """
    Asynchronously downloads and indexes all corporate documents (10-K, 10-Q, 8-K, and Earnings Calls)
    for a given ticker symbol in parallel, caching updates based on registry metadata.
    """
    logger.info(f"Initiating full parallel corporate ingestion pipeline for {ticker_symbol}...")
    downloader = SECDownloader()
    parser = SECParser()
    cik = get_cik_from_ticker(ticker_symbol)
    manager = EarningsCallManager()
    
    loop = asyncio.get_event_loop()
    metadata = await loop.run_in_executor(None, downloader.get_filings_metadata, cik)
    
    tasks = [
        async_fetch_and_parse(cik, downloader, parser, searcher, metadata=metadata),
        async_fetch_and_parse_10q(cik, downloader, parser, searcher, metadata=metadata),
        async_fetch_and_parse_8k(cik, downloader, parser, searcher, metadata=metadata),
        async_fetch_and_parse_earnings_call(ticker_symbol, manager, searcher)
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_chunks = []
    summary = []
    form_types = ["10-K", "10-Q", "8-K", "Earnings"]
    
    for form_type, res in zip(form_types, results):
        if isinstance(res, Exception):
            logger.error(f"Failed to ingest Form {form_type} for {ticker_symbol}: {res}")
            summary.append(f"{form_type}: failed ({res})")
        else:
            all_chunks.extend(res)
            summary.append(f"{form_type}: successfully ingested {len(res)} chunks")
            
    if all_chunks:
        searcher.ingest_chunks(all_chunks)
        
    status_str = ", ".join(summary)
    return f"Ingestion summary for {ticker_symbol}: {status_str}. Total chunks ingested: {len(all_chunks)}."


def ingest_all_corporate_data(ticker_symbol: str, searcher: HybridSearcher) -> str:
    """
    Agent tool wrapper to execute dynamic parallel retrieval and ingestion of all filings (10-K, 10-Q, 8-K, Earnings).
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    if loop.is_running():
        # Schedule it in the running loop using run_coroutine_threadsafe or use nest_asyncio-style simulation
        # For our synchronous agent thread, we run it within the thread loop
        import threading
        result_holder = []
        def run_in_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            res = new_loop.run_until_complete(async_ingest_all_corporate_data(ticker_symbol, searcher))
            result_holder.append(res)
            new_loop.close()
        t = threading.Thread(target=run_in_thread)
        t.start()
        t.join()
        return result_holder[0] if result_holder else "Ingestion failed or timed out."
    else:
        return loop.run_until_complete(async_ingest_all_corporate_data(ticker_symbol, searcher))


# ==========================================


# ==========================================


# ==========================================
# 8. EVALUATION TESTS
# ==========================================
class RetrievalEvaluator:
    """
    Validates retrieval quality using Mean Reciprocal Rank (MRR).
    """
    @staticmethod
    def run_eval_suite(searcher: HybridSearcher, form_type: str = "10-K"):
        logger.info(f"Running retrieval evaluation suite for Form {form_type}...")
        
        # Test Cases: Query -> Expected Item Source
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
        
        rr_sum = 0.0
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
            rr_sum += reciprocal_rank
            logger.info(f"Query: '{query}' -> Expected: {expected_item} -> Found at rank: {rank} (RR: {reciprocal_rank})")
            
        mrr = rr_sum / len(test_cases)
        logger.info(f"Evaluation complete. Mean Reciprocal Rank (MRR@5): {mrr:.4f}")
        return mrr


def get_cik_from_ticker(ticker: str) -> str:
    """
    Resolves stock ticker symbol to CIK number using SEC company tickers listing.
    """
    ticker_clean = ticker.upper().strip()
    # Direct common mapping overrides
    static_map = {
        "NVDA": "0001045810",
        "AAPL": "0000320193",
        "MSFT": "0000789019",
        "GOOG": "0001652044",
        "AMZN": "0001018724",
        "TSLA": "0001318605"
    }
    if ticker_clean in static_map:
        return static_map[ticker_clean]
        
    try:
        import ssl
        import urllib.request
        url = "https://www.sec.gov/files/company_tickers.json"
        req = urllib.request.Request(url, headers={"User-Agent": "StockResearchAgent/1.0 (test@example.com)"})
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            for entry in data.values():
                if entry.get("ticker", "").upper() == ticker_clean:
                    return str(entry.get("cik_str")).zfill(10)
    except Exception as e:
        logger.warning(f"Could not dynamically map ticker {ticker_clean} to CIK ({e}). Falling back to default CIK.")
    return "0001045810"


# ==========================================
# 9. EXPOSED AGENT RAG SEARCH TOOL
# ==========================================
_default_searcher = None
_default_reranker = None

def get_default_searcher() -> HybridSearcher:
    global _default_searcher
    if _default_searcher is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        db_dir = os.path.abspath(os.path.join(current_dir, "..", "database"))
        _default_searcher = HybridSearcher(persist_directory=db_dir)
    return _default_searcher

def get_default_reranker() -> CrossEncoderReranker:
    global _default_reranker
    if _default_reranker is None:
        _default_reranker = CrossEncoderReranker()
    return _default_reranker

def doc_rag_search(query: str, ticker: str, form_type: str) -> str:
    """
    Executes a Retrieval-Augmented Generation (RAG) search over corporate filings and transcripts.
    This tool should be used to retrieve synthesized answers to research questions about specific 
    companies using official SEC filings and earnings call dialogue.
    
    Args:
        query (str): The search query, question, or research topic (e.g., 'Blackwell chip shipment timelines').
        ticker (str): The stock ticker symbol of the company (e.g., 'NVDA', 'AAPL', 'MSFT').
        form_type (str): The source filing category to target. Must be one of:
            - "10-K": Annual reports detailing long-term business overview, financial statements, and Item 1A Risk Factors. Supports retrieval across the last 5-10 years.
            - "10-Q": Quarterly reports containing recent balance sheets, operations remarks, and short-term trends. Supports retrieval across the last 8-12 quarters (2-3 years).
            - "8-K": Current reports filed to report immediate material events (e.g., acquisitions, earnings press releases). Supports retrieval across the last 1-2 years.
            - "Earnings" (or "EARNINGS_CALL"): Earnings call transcripts capturing dialogue between analysts and executives. Supports retrieval across the last 8-12 quarters (2-3 years).
            
    Returns:
        str: A synthesized, factual response generated by the multi-agent supervisor system.
    """
    searcher = get_default_searcher()
    reranker = get_default_reranker()
    
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    if loop.is_running():
        import threading
        def run_in_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            new_loop.run_until_complete(async_ingest_all_corporate_data(ticker, searcher))
            new_loop.close()
        t = threading.Thread(target=run_in_thread)
        t.start()
        t.join()
    else:
        loop.run_until_complete(async_ingest_all_corporate_data(ticker, searcher))
        
    agent_system = MultiAgentSupervisor(searcher, reranker, ticker, form_type)
    state = agent_system.run_agent_workflow(query)
    return state.get("final_answer", "")


# ==========================================
# MAIN EXECUTION ROUTINE
# ==========================================
async def main(ticker_symbol: str = "AAPL", form_type: str = "10-Q"):
    import sys
    # Reconfigure stdout to use UTF-8 to prevent encoding crashes on Windows consoles
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    logger.info(f"Initializing Stock Research Agent Pipeline for ticker: {ticker_symbol} (Evaluating Form {form_type})...")
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    db_dir = os.path.abspath(os.path.join(current_dir, "..", "database"))
    searcher = HybridSearcher(persist_directory=db_dir)
    reranker = CrossEncoderReranker()
    
    # 1. Trigger dynamic parallel retrieval and ingestion of all filings (10-K, 10-Q, 8-K, Earnings)
    summary_str = await async_ingest_all_corporate_data(ticker_symbol, searcher)
    logger.info(summary_str)
    
    # 2. Run Evaluation Suite
    mrr_score = RetrievalEvaluator.run_eval_suite(searcher, form_type)
    if form_type != "EARNINGS":
        assert mrr_score > 0.0, "Retrieval quality is zero. Evaluation failed!"
    
    # 3. Initialize and query Multi-Agent workflow
    agent_system = MultiAgentSupervisor(searcher, reranker, ticker_symbol, form_type)
    form_clean = form_type.upper().strip()
    if form_clean == "10-Q":
        query = f"What are the quarterly business performance and key risk factors for {ticker_symbol} in their recent 10-Q filing?"
    elif form_clean == "8-K":
        query = f"What are the current material event disclosures and operational announcements for {ticker_symbol} in their recent 8-K report?"
    elif form_clean in ("EARNINGS_CALL", "EARNINGS"):
        query = f"What are the chief themes, strategic remarks, and growth commentary highlighted in the latest {ticker_symbol} Earnings Call?"
    elif ticker_symbol.upper() == "NVDA":
        query = "What is the demand driver for NVIDIA AI processors and Blackwell architecture in fiscal year 2025?"
    else:
        query = f"What are the main business goals, risk factors, and financial results for {ticker_symbol} in their recent 10-K filing?"
    state = agent_system.run_agent_workflow(query)
    
    print("\n" + "="*50)
    print("FINAL WORKFLOW EXECUTION SUMMARY:")
    print("="*50)
    print(f"User Query: {state['query']}")
    print(f"Flow Logs:  {', '.join(state['logs'])}")
    print(f"Response:\n{state['final_answer']}")
    print("="*50 + "\n")


if __name__ == "__main__":
    import sys
    ticker = "MRVL"
    form = "EARNINGS"
    if len(sys.argv) > 1:
        ticker = sys.argv[1]
    if len(sys.argv) > 2:
        form = sys.argv[2]
    asyncio.run(main(ticker, form))
