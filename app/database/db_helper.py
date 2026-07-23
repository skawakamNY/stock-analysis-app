import os
import sqlite3
import datetime
import logging

logger = logging.getLogger("DatabaseHelper")

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database")
DB_FILE = os.path.join(DB_DIR, "consensus_reports.db")

def init_db():
    """Initializes the SQLite database and creates the consensus_reports table if it doesn't exist."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consensus_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker VARCHAR(10) NOT NULL,
                created_date TIMESTAMP NOT NULL,
                research_report BLOB,
                financial_report BLOB,
                risk_report BLOB,
                latest_news_report BLOB,
                valuation_report BLOB,
                investment_summary BLOB,
                committee_decision BLOB
            );
        """)
        conn.commit()
        logger.info(f"Database successfully initialized at {DB_FILE}")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
    finally:
        conn.close()

def initiate_workflow_record(ticker: str) -> int:
    """Inserts a new record when the workflow is initiated, returning the generated record ID."""
    init_db()
    conn = sqlite3.connect(DB_FILE)
    record_id = -1
    try:
        cursor = conn.cursor()
        now = datetime.datetime.now()
        cursor.execute(
            "INSERT INTO consensus_reports (ticker, created_date) VALUES (?, ?);",
            (ticker, now)
        )
        conn.commit()
        record_id = cursor.lastrowid
        logger.info(f"Initiated workflow record in database. ID: {record_id} for ticker: {ticker}")
    except Exception as e:
        logger.error(f"Failed to initiate workflow record: {e}")
    finally:
        conn.close()
    return record_id

def update_agent_report(record_id: int, node_name: str, report_text: str):
    """Updates the corresponding BLOB column in the database with the agent's report text."""
    if record_id <= 0:
        return
        
    # Map node name to database column
    node_to_col = {
        "research": "research_report",
        "financial": "financial_report",
        "risk": "risk_report",
        "news": "latest_news_report",
        "valuation": "valuation_report",
        "summary": "investment_summary",
        "committee": "committee_decision"
    }
    
    col_name = node_to_col.get(node_name)
    if not col_name:
        logger.warning(f"Unknown node name for database persistence: {node_name}")
        return
        
    conn = sqlite3.connect(DB_FILE)
    try:
        cursor = conn.cursor()
        # Encode text as bytes to store it as a BLOB
        blob_data = report_text.encode('utf-8') if report_text else b''
        query = f"UPDATE consensus_reports SET {col_name} = ? WHERE id = ?;"
        cursor.execute(query, (sqlite3.Binary(blob_data), record_id))
        conn.commit()
        logger.info(f"Successfully persisted {node_name} report as BLOB in database (Record ID: {record_id})")
    except Exception as e:
        logger.error(f"Failed to update agent report in database: {e}")
    finally:
        conn.close()

def get_all_records():
    """Retrieves all consensus report records from the SQLite database, decoding BLOB fields back to strings."""
    init_db()
    conn = sqlite3.connect(DB_FILE)
    records = []
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM consensus_reports ORDER BY created_date DESC;")
        rows = cursor.fetchall()
        for row in rows:
            record = {
                "id": row["id"],
                "ticker": row["ticker"],
                "created_date": row["created_date"]
            }
            for col in ["research_report", "financial_report", "risk_report", "latest_news_report", "valuation_report", "investment_summary", "committee_decision"]:
                blob_val = row[col]
                record[col] = blob_val.decode('utf-8') if blob_val else ""
            records.append(record)
    except Exception as e:
        logger.error(f"Failed to fetch database records: {e}")
    finally:
        conn.close()
    return records

def get_latest_completed_report(ticker: str) -> dict:
    """Retrieves the most recent completed consensus report record for a ticker."""
    init_db()
    conn = sqlite3.connect(DB_FILE)
    record = None
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM consensus_reports 
            WHERE ticker = ? AND committee_decision IS NOT NULL AND committee_decision != ''
            ORDER BY created_date DESC LIMIT 1;
        """, (ticker.upper().strip(),))
        row = cursor.fetchone()
        if row:
            record = {
                "id": row["id"],
                "ticker": row["ticker"],
                "created_date": row["created_date"]
            }
            for col in ["research_report", "financial_report", "risk_report", "latest_news_report", "valuation_report", "investment_summary", "committee_decision"]:
                blob_val = row[col]
                record[col] = blob_val.decode('utf-8') if blob_val else ""
    except Exception as e:
        logger.error(f"Failed to fetch latest completed report: {e}")
    finally:
        conn.close()
    return record

def check_bypass_agents(ticker: str) -> tuple[bool, dict]:
    """
    Checks if research, financial, and risk agents can be bypassed.
    Returns (should_bypass, previous_record_dict).
    """
    prev_record = get_latest_completed_report(ticker)
    if not prev_record:
        return False, {}
        
    prev_date = prev_record["created_date"]
    if isinstance(prev_date, str):
        try:
            prev_dt = datetime.datetime.strptime(prev_date.split(".")[0], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return False, {}
    else:
        prev_dt = prev_date
        
    from mcp_server import IngestionRegistry
    try:
        registry = IngestionRegistry().load_registry()
        ticker_upper = ticker.upper().strip()
        if ticker_upper not in registry:
            return False, {}
            
        max_updated_dt = None
        for form_type, meta in registry[ticker_upper].items():
            updated_at_str = meta.get("updated_at")
            if updated_at_str:
                try:
                    updated_dt = datetime.datetime.strptime(updated_at_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
                    if max_updated_dt is None or updated_dt > max_updated_dt:
                        max_updated_dt = updated_dt
                except Exception:
                    pass
                    
        if max_updated_dt is None:
            return False, {}
            
        if prev_dt > max_updated_dt:
            logger.info(f"Agents bypass condition met for {ticker_upper}. SQLite run: {prev_dt} > Vector DB update: {max_updated_dt}")
            return True, prev_record
            
    except Exception as e:
        logger.error(f"Error checking bypass condition: {e}")
        
    return False, {}


def delete_records_for_ticker(ticker: str) -> int:
    """Deletes all consensus reports from SQLite for a given ticker, returning the number of rows deleted."""
    init_db()
    conn = sqlite3.connect(DB_FILE)
    rows_deleted = 0
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM consensus_reports WHERE UPPER(ticker) = ?;", (ticker.upper().strip(),))
        conn.commit()
        rows_deleted = cursor.rowcount
        logger.info(f"Deleted {rows_deleted} consensus reports from SQLite for ticker: {ticker}")
    except Exception as e:
        logger.error(f"Failed to delete consensus reports from SQLite for ticker {ticker}: {e}")
    finally:
        conn.close()
    return rows_deleted

