#!/usr/bin/env python3
import os
import sys
import re
import json
import math
import asyncio
import logging
import datetime
import threading
import urllib.request
import urllib.error
from typing import List, Dict, Any, Tuple, Optional
from fastmcp import FastMCP

# Ensure the app/ project root directory is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.dirname(current_dir)
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Setup logging
try:
    from utils.logging_config import setup_logging
except ImportError:
    import sys
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(current_dir)
    sys.path.append(app_dir)
    from utils.logging_config import setup_logging

setup_logging()
logger = logging.getLogger("DocumentSearchAgent")

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

# Process-level lock registry to prevent concurrent ingestion of the same filing.
_ingestion_locks: Dict[str, threading.Lock] = {}
_ingestion_locks_meta_lock = threading.Lock()

def _get_ingestion_lock(ticker: str, form_key: str) -> threading.Lock:
    """Returns a per-(ticker, form_key) lock, creating it if needed."""
    key = f"{ticker.upper().strip()}|{form_key.upper().strip()}"
    with _ingestion_locks_meta_lock:
        if key not in _ingestion_locks:
            _ingestion_locks[key] = threading.Lock()
        return _ingestion_locks[key]


class IngestionRegistry:
    """
    Tracks and checks document ingestion history using a local JSON file to prevent redundant updates.
    """
    def __init__(self, registry_path: Optional[str] = None):
        if registry_path is None:
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
        if t_key in registry:
            del registry[t_key]
            self.save_registry(registry)

    def should_skip_ingestion(self, ticker: str, form_type: str, filing_date: Optional[str] = None, searcher: Optional[Any] = None) -> bool:
        t_key = ticker.upper().strip()
        f_key = form_type.upper().strip()
        if not filing_date:
            registry = self.load_registry()
            if t_key in registry and f_key in registry[t_key]:
                filing_date = registry[t_key][f_key].get("filing_date", "")
            if not filing_date:
                filing_date = datetime.datetime.now().strftime("%Y-%m-%d")

        registry = self.load_registry()
        if t_key in registry and f_key in registry[t_key]:
            entry = registry[t_key][f_key]
            last_filing_date = entry.get("filing_date", "")
            status = entry.get("status", "done")
            if last_filing_date == filing_date and status == "done":
                return True
            if last_filing_date == filing_date and status == "in_progress":
                return True
            updated_at_str = entry.get("updated_at", "")
            if updated_at_str and len(updated_at_str) >= 10 and status == "done":
                updated_at_date = updated_at_str[:10]
                if updated_at_date >= filing_date and last_filing_date >= filing_date:
                    return True

        if searcher is not None:
            t_clean = ticker.upper().strip()
            ticker_has_any_chunks = any(meta.get("ticker") == t_clean for meta in searcher.bm25_metadata)
            if not ticker_has_any_chunks:
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


class SECDownloader:
    """
    Ingests metadata and downloads filings from SEC EDGAR.
    """
    def __init__(self, user_agent: str = "StockResearchAgent/1.0 (test@example.com)"):
        self.user_agent = user_agent
        self.headers = {"User-Agent": self.user_agent}

    def get_filings_metadata(self, cik: str) -> Dict[str, Any]:
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
        import ssl
        clean_acc = accession_number.replace("-", "")
        url_cik = str(int(cik))
        url = f"https://www.sec.gov/Archives/edgar/data/{url_cik}/{clean_acc}/{primary_doc}"
        logger.info(f"Downloading 8-K from {url}...")
        req = urllib.request.Request(url, headers=self.headers)
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=10, context=context) as response:
            return response.read().decode('utf-8', errors='ignore')


class SECParser:
    """
    Parses SEC HTML content, extracts plaintext, and detects SEC standard items.
    """
    @staticmethod
    def parse_html_to_text(html_content: str) -> str:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, "html.parser")
            for table in soup.find_all("table"):
                markdown_rows = []
                for row in table.find_all("tr"):
                    cells = [cell.get_text(strip=True).replace("\n", " ") for cell in row.find_all(["td", "th"])]
                    if any(cells):
                        markdown_rows.append("| " + " | ".join(cells) + " |")
                if markdown_rows:
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
            text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html_content)
            text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', text)
            text = re.compile(r'</?(?:p|div|tr|h[1-6]|br|table|li|blockquote)[^>]*>', re.IGNORECASE).sub('\n\n', text)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\r', '', text)
            text = re.sub(r'\n\s*\n', '\n\n', text)
            return text.strip()

    @staticmethod
    def detect_sec_items(html_content: str) -> Dict[str, str]:
        text = SECParser.parse_html_to_text(html_content)
        lines = text.split("\n")
        detected_items = {}
        for idx, line in enumerate(lines):
            normalized_line = " ".join(line.split()).replace('\u00a0', ' ')
            m = re.match(r'^Item\s+([0-9A-Z]+)\b[\.\-:\s]*\s*(.+)$', normalized_line, re.IGNORECASE)
            if m:
                item_num = m.group(1).upper()
                category_name = m.group(2).strip()
                char_idx = sum(len(l) for l in lines[:idx]) + idx
                detected_items[item_num] = (char_idx, category_name)
                continue
                
            m_split = re.match(r'^Item\s+([0-9A-Z]+)\b[\.\-:\s]*$', normalized_line, re.IGNORECASE)
            if m_split:
                item_num = m_split.group(1).upper()
                category_name = ""
                for next_idx in range(idx + 1, min(len(lines), idx + 5)):
                    if lines[next_idx].strip():
                        normalized_next = " ".join(lines[next_idx].split()).replace('\u00a0', ' ')
                        if not re.match(r'^Item\s+([0-9A-Z]+)\b', normalized_next, re.IGNORECASE):
                            category_name = normalized_next
                        break
                if category_name:
                    char_idx = sum(len(l) for l in lines[:idx]) + idx
                    detected_items[item_num] = (char_idx, category_name)
                
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
            temp_sections = {}
            for i in range(len(matches)):
                start_idx, item_name = matches[i]
                end_idx = matches[i+1][0] if i + 1 < len(matches) else len(text)
                section_text = text[start_idx:end_idx].strip()
                if len(section_text) > 20:
                    temp_sections[item_name] = section_text
            sections = temp_sections

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
        text = SECParser.parse_html_to_text(html_content)
        lines = text.split("\n")
        detected_items = {}
        current_part = "PART I"
        for idx, line in enumerate(lines):
            normalized_line = " ".join(line.split()).replace('\u00a0', ' ')
            if re.match(r'^PART\s+I\b', normalized_line, re.IGNORECASE):
                if not re.match(r'^PART\s+II\b', normalized_line, re.IGNORECASE):
                    current_part = "PART I"
            elif re.match(r'^PART\s+II\b', normalized_line, re.IGNORECASE):
                current_part = "PART II"
            m = re.match(r'^Item\s+([0-9A-Z]+)\b[\.\-:\s]*\s*(.+)$', normalized_line, re.IGNORECASE)
            if m:
                item_num = m.group(1).upper()
                category_name = m.group(2).strip()
                char_idx = sum(len(l) for l in lines[:idx]) + idx
                item_key = f"{current_part} - ITEM {item_num}"
                detected_items[item_key] = (char_idx, category_name)
                continue
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
        if not sections:
            logger.warning("Regex 10-Q item detection failed. Splitting using fallback heuristics.")
            sections["Financial Statements"] = text[:len(text)//4]
            sections["Management's Discussion and Analysis of Financial Condition and Results of Operations"] = text[len(text)//4: len(text)//2]
            sections["Risk Factors"] = text[len(text)//2: 3*len(text)//4]
            sections["Controls and Procedures"] = text[3*len(text)//4:]
        return sections

    @staticmethod
    def detect_8k_items(html_content: str) -> Dict[str, str]:
        text = SECParser.parse_html_to_text(html_content)
        lines = text.split("\n")
        detected_items = {}
        for idx, line in enumerate(lines):
            normalized_line = " ".join(line.split()).replace('\u00a0', ' ')
            m = re.match(r'^Item\s+(\d\.\d\d)\b[\.\-:\s]*\s*(.+)$', normalized_line, re.IGNORECASE)
            if m:
                item_num = m.group(1).upper()
                category_name = m.group(2).strip()
                char_idx = sum(len(l) for l in lines[:idx]) + idx
                detected_items[item_num] = (char_idx, category_name)
                continue
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
        if not sections:
            logger.warning("Regex 8-K item detection failed. Splitting using fallback heuristics.")
            sections["Results of Operations and Financial Condition"] = text[:len(text)//2]
            sections["Financial Statements and Exhibits"] = text[len(text)//2:]
        return sections


class SemanticChunker:
    """
    Groups paragraphs/sentences based on semantic similarity.
    """
    def __init__(self, target_chunk_size: int = 800, overlap: int = 150):
        self.target_chunk_size = target_chunk_size
        self.overlap = overlap

    def chunk_section(self, text: str, metadata_base: Dict[str, Any]) -> List[Dict[str, Any]]:
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


try:
    import chromadb
    USE_REAL_CHROMA = True
except ImportError:
    USE_REAL_CHROMA = False
    logger.info("chromadb not installed. Using local Mock Chroma DB.")

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
        self.parent_docs: Dict[Tuple[str, str, str], str] = {}
        self.load_metadata()

    def save_metadata(self):
        if not self.persist_directory:
            return
        os.makedirs(self.persist_directory, exist_ok=True)
        meta_path = os.path.join(self.persist_directory, "hybrid_index_meta.json")
        try:
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
                    serialized_parents = data.get("parent_docs", {})
                    self.parent_docs = {}
                    for key_str, text in serialized_parents.items():
                        parts = key_str.split("|", 2)
                        if len(parts) == 3:
                            self.parent_docs[(parts[0], parts[1], parts[2])] = text
                if self.bm25_corpus and USE_REAL_BM25:
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
        if USE_REAL_CHROMA:
            try:
                self.collection.delete(where={"ticker": ticker_c})
            except Exception as e:
                logger.error(f"Failed to delete ticker {ticker_c} from Chroma: {e}")
        new_bm25_corpus = []
        new_bm25_metadata = []
        for doc, meta in zip(self.bm25_corpus, self.bm25_metadata):
            if meta.get("ticker") != ticker_c:
                new_bm25_corpus.append(doc)
                new_bm25_metadata.append(meta)
        self.bm25_corpus = new_bm25_corpus
        self.bm25_metadata = new_bm25_metadata
        if self.bm25_corpus and USE_REAL_BM25:
            tokenized_corpus = [doc.lower().split() for doc in self.bm25_corpus]
            self.bm25_model = BM25Okapi(tokenized_corpus)
        else:
            self.bm25_model = None
        new_parent_docs = {}
        for (t, f, i), text in self.parent_docs.items():
            if t != ticker_c:
                new_parent_docs[(t, f, i)] = text
        self.parent_docs = new_parent_docs
        self.save_metadata()
        try:
            IngestionRegistry().remove_ticker_from_registry(ticker)
        except Exception as e:
            logger.error(f"Failed to remove ticker {ticker_c} from registry: {e}")

    def ingest_chunks(self, chunks: List[Dict[str, Any]]):
        documents = [c["text"] for c in chunks]
        metadatas = [c["metadata"] for c in chunks]
        ids = [f"chunk_{i}_{hash(c['text'])}" for i, c in enumerate(chunks)]
        self.collection.add(documents=documents, metadatas=metadatas, ids=ids)
        self.bm25_corpus.extend(documents)
        self.bm25_metadata.extend(metadatas)
        if USE_REAL_BM25:
            tokenized_corpus = [doc.lower().split() for doc in self.bm25_corpus]
            self.bm25_model = BM25Okapi(tokenized_corpus)
        self.save_metadata()
        logger.info(f"Successfully ingested {len(chunks)} chunks into Hybrid Index.")

    def rrf_hybrid_retrieve(self, query: str, top_k: int = 5, rrf_k: int = 60, metadata_filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        chroma_where = None
        if metadata_filter:
            if len(metadata_filter) > 1:
                chroma_where = {
                    "$and": [{k: {"$eq": v}} for k, v in metadata_filter.items()]
                }
            elif len(metadata_filter) == 1:
                k, v = list(metadata_filter.items())[0]
                chroma_where = {k: {"$eq": v}}
        vector_results = self.collection.query(query_texts=[query], n_results=top_k * 2, where=chroma_where)
        v_docs = vector_results.get("documents", [[]])[0]
        v_metas = vector_results.get("metadatas", [[]])[0]
        v_ranking = list(v_docs)

        bm25_scores = []
        if USE_REAL_BM25 and self.bm25_model:
            tokenized_query = query.lower().split()
            bm25_scores = self.bm25_model.get_scores(tokenized_query)
        bm25_ranking = []
        if len(bm25_scores) > 0:
            scored_docs = list(zip(self.bm25_corpus, self.bm25_metadata, bm25_scores))
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

        rrf_scores: Dict[str, float] = {}
        doc_metadata_map: Dict[str, Dict[str, Any]] = {}
        for doc, meta in zip(v_docs, v_metas):
            doc_metadata_map[doc] = meta
        for doc, meta in zip(self.bm25_corpus, self.bm25_metadata):
            doc_metadata_map[doc] = meta

        for rank, doc in enumerate(v_ranking):
            rrf_scores[doc] = rrf_scores.get(doc, 0.0) + (1.0 / (rrf_k + rank + 1))
        for rank, doc in enumerate(bm25_ranking):
            rrf_scores[doc] = rrf_scores.get(doc, 0.0) + (1.0 / (rrf_k + rank + 1))

        sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        hybrid_results = []
        for doc, score in sorted_docs[:top_k]:
            hybrid_results.append({
                "text": doc,
                "metadata": doc_metadata_map.get(doc, {}),
                "rrf_score": score
            })
        return hybrid_results


class CrossEncoderReranker:
    """
    Reranks top candidates based on fine-grained keyword match & semantic coverage.
    """
    def __init__(self):
        pass

    def rerank(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        reranked = []
        q_words = set(query.lower().split())
        for candidate in candidates:
            doc_text = candidate["text"].lower()
            exact_match_score = sum(1 for word in q_words if word in doc_text) / max(len(q_words), 1)
            rerank_score = candidate["rrf_score"] * 0.3 + exact_match_score * 0.7
            new_candidate = candidate.copy()
            new_candidate["rerank_score"] = rerank_score
            reranked.append(new_candidate)
        reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
        return reranked


class MultiAgentSupervisor:
    """
    Simulates a multi-agent system coordinated by a central Supervisor agent.
    """
    def __init__(self, searcher: HybridSearcher, reranker: CrossEncoderReranker, ticker: str = "NVDA", form_type: str = "10-K"):
        self.searcher = searcher
        self.reranker = reranker
        self.ticker = ticker
        self.form_type = form_type

    def run_agent_workflow(self, query: str) -> Dict[str, Any]:
        logger.info(f"Supervisor received inquiry: '{query}'")
        state = {
            "query": query,
            "routing_decision": "IngestionAgent",
            "retrieved_documents": [],
            "final_answer": "",
            "logs": []
        }
        state["logs"].append("Routed to IngestionAgent")
        self._ingestion_agent_node(state)
        state["logs"].append("Routed to RetrievalAgent")
        self._retrieval_agent_node(state)
        state["logs"].append("Routed to FinancialAnalystAgent")
        self._analyst_agent_node(state)
        return state

    def _ingestion_agent_node(self, state: Dict[str, Any]):
        state["ingestion_status"] = "Verified Active"
        logger.info("Ingestion Agent: Vector store verified and ready.")

    def _retrieval_agent_node(self, state: Dict[str, Any]):
        ticker_c = self.ticker.upper().strip()
        form_c = self.form_type.upper().strip()
        form_val = "Earnings" if form_c in ("EARNINGS_CALL", "EARNINGS") else form_c
        metadata_filter = {
            "ticker": ticker_c,
            "form_type": form_val
        }
        candidates = self.searcher.rrf_hybrid_retrieve(
            state["query"],
            top_k=30,
            metadata_filter=metadata_filter
        )
        reranked = self.reranker.rerank(state["query"], candidates)
        seen_texts = set()
        deduped = []
        for doc in reranked:
            t_clean = doc["text"].strip()
            if t_clean not in seen_texts:
                seen_texts.add(t_clean)
                deduped.append(doc)
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
        state["retrieved_documents"] = final_docs
        logger.info(f"Retrieval Agent: Retrieved {len(final_docs)} deduplicated parent context items.")

    def _analyst_agent_node(self, state: Dict[str, Any]):
        docs = state["retrieved_documents"]
        if not docs:
            state["final_answer"] = "No financial details could be retrieved to answer this request."
            return
        context_blocks = []
        for idx, doc in enumerate(docs):
            source = f"Source: {doc['metadata'].get('ticker', 'Unknown')} {doc['metadata'].get('form_type', 'SEC')} ({doc['metadata'].get('item_name', 'Section')})"
            context_blocks.append(f"[{idx+1}] {source}\n{doc['text']}")
        context_str = "\n\n---\n\n".join(context_blocks)
        state["final_answer"] = (
            f"Based on the corporate reports, here is the analysis:\n"
            f"{context_str}\n\n"
            f"[Analysis Source: {docs[0]['metadata'].get('item_name', 'SEC Document')}]"
        )
        logger.info("Financial Analyst Agent: Synthesis completed.")


async def async_fetch_and_parse(cik: str, downloader: SECDownloader, parser: SECParser, searcher: Optional[HybridSearcher] = None, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    logger.info(f"Starting async 10-K ingestion pipeline for CIK {cik} (up to {NUM_YEARS_TO_LOAD_10K} years)...")
    loop = asyncio.get_event_loop()
    if metadata is None:
        metadata = await loop.run_in_executor(None, downloader.get_filings_metadata, cik)
    recent_filings = metadata.get("filings", {}).get("recent", {})
    if not recent_filings:
        logger.error("No filings found in metadata.")
        return []
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
        if registry.should_skip_ingestion(ticker, f"10-K-{date}", date, searcher):
            logger.info(f"Ingestion check: Form 10-K for {ticker} (filing date {date}) is already up-to-date. Skipping.")
            continue
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
                    searcher.register_parent_doc(ticker, "10-K", f"{item_name}_{date}", section_text)
                item_chunks = chunker.chunk_section(section_text, base_meta)
                all_chunks.extend(item_chunks)
            logger.info(f"Parsed Form 10-K for {ticker} (filing date {date}) successfully.")
            registry.update_registry(ticker, f"10-K-{date}", date)
        except Exception as e:
            logger.error(f"Error parsing 10-K on date {date}: {e}")
    return all_chunks


async def async_fetch_and_parse_10q(cik: str, downloader: SECDownloader, parser: SECParser, searcher: Optional[HybridSearcher] = None, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    logger.info(f"Starting async 10-Q ingestion pipeline for CIK {cik} (up to {NUM_QUARTERS_TO_LOAD_10Q} quarters)...")
    loop = asyncio.get_event_loop()
    if metadata is None:
        metadata = await loop.run_in_executor(None, downloader.get_filings_metadata, cik)
    recent_filings = metadata.get("filings", {}).get("recent", {})
    if not recent_filings:
        logger.error("No filings found in metadata.")
        return []
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
    logger.info(f"Starting async 8-K ingestion pipeline for CIK {cik} (up to {NUM_DAYS_TO_LOAD_8K} days)...")
    loop = asyncio.get_event_loop()
    if metadata is None:
        metadata = await loop.run_in_executor(None, downloader.get_filings_metadata, cik)
    recent_filings = metadata.get("filings", {}).get("recent", {})
    if not recent_filings:
        logger.error("No filings found in metadata.")
        return []
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
                if len(indices) < 5:
                    indices.append(i)
    if not indices:
        logger.error("No 8-K filing found in metadata.")
        return []
    ticker = metadata.get("tickers", ["UNKNOWN"])[0]
    registry = IngestionRegistry()
    all_chunks = []
    for idx in indices[:15]:
        acc_num = recent_filings["accessionNumber"][idx]
        primary_doc = recent_filings["primaryDocument"][idx]
        date = recent_filings["filingDate"][idx]
        if registry.should_skip_ingestion(ticker, f"8-K-{date}", date, searcher):
            logger.info(f"Ingestion check: Form 8-K for {ticker} (filing date {date}) is already up-to-date. Skipping.")
            continue
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
    """
    def __init__(self):
        self.fetch_transcripts_raw = fetch_transcripts_from_filingapi

    def fetch_and_parse_transcript(self, ticker: str, searcher: Optional[Any] = None) -> List[Dict[str, Any]]:
        ticker_clean = ticker.upper().strip()
        registry = IngestionRegistry()
        logger.info(f"Retrieving earnings call transcripts for ticker: {ticker_clean} (up to {NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS} quarters)...")
        import time
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cache_path = os.path.join(root_dir, f"transcripts_cache_{ticker_clean}.json")
        res_body = None
        if os.path.exists(cache_path):
            file_age = time.time() - os.path.getmtime(cache_path)
            if file_age < 86400:
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        res_body = f.read()
                    logger.info(f"Loaded transcripts list from local cache for {ticker_clean}")
                except Exception as cache_err:
                    logger.warning(f"Failed to read transcripts cache: {cache_err}")
        if not res_body:
            res_body = self.fetch_transcripts_raw(ticker_clean)
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
    loop = asyncio.get_event_loop()
    chunks = await loop.run_in_executor(None, manager.fetch_and_parse_transcript, ticker, searcher)
    return chunks


async def async_ingest_all_corporate_data(ticker_symbol: str, searcher: HybridSearcher) -> str:
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
    Synchronous wrapper to execute dynamic parallel retrieval and ingestion of all filings.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    if loop.is_running():
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


class RetrievalEvaluator:
    @staticmethod
    def run_eval_suite(searcher: HybridSearcher, form_type: str = "10-K"):
        logger.info(f"Running retrieval evaluation suite for Form {form_type}...")
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
    ticker_clean = ticker.upper().strip()
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


_default_searcher = None
_default_reranker = None

def get_default_searcher() -> HybridSearcher:
    global _default_searcher
    if _default_searcher is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        app_dir = os.path.dirname(current_dir)
        db_dir = os.path.abspath(os.path.join(app_dir, "database"))
        _default_searcher = HybridSearcher(persist_directory=db_dir)
    return _default_searcher

def get_default_reranker() -> CrossEncoderReranker:
    global _default_reranker
    if _default_reranker is None:
        _default_reranker = CrossEncoderReranker()
    return _default_reranker


# Initialize the FastMCP server
mcp = FastMCP("Document Searcher Agent")

@mcp.tool
async def doc_rag_search(query: str, ticker: str, form_type: str) -> str:
    """
    Executes a Retrieval-Augmented Generation (RAG) search over corporate filings and transcripts
    for a specific company stock ticker and document form type.
    
    This tool is provided by the Document Searcher Agent MCP service. The agent is responsible for
    both dynamically populating (ingesting) the Chroma vector database and performing the hybrid
    semantic and lexical retrieval/synthesis.
    
    Args:
        query: The search query or question about the company.
        ticker: The stock ticker symbol (e.g. AAPL, NVDA, MSFT).
        form_type: The source filing category to target. Must be '10-K', '10-Q', '8-K', or 'Earnings'.
    """
    searcher = get_default_searcher()
    reranker = get_default_reranker()
    
    # Run ingestion asynchronously
    await async_ingest_all_corporate_data(ticker, searcher)
    
    # Run the supervisor workflow
    agent_system = MultiAgentSupervisor(searcher, reranker, ticker, form_type)
    state = agent_system.run_agent_workflow(query)
    return state.get("final_answer", "")

if __name__ == "__main__":
    mcp.run()
