import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load .env file from the root directory
    root_dir = Path(__file__).resolve().parent.parent
    env_path = root_dir / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        load_dotenv(override=True)
except ImportError:
    pass

# Ingestion configuration constants
NUM_YEARS_TO_LOAD_10K = int(os.environ.get("NUM_YEARS_TO_LOAD_10K", 1))
NUM_DAYS_TO_LOAD_8K = int(os.environ.get("NUM_DAYS_TO_LOAD_8K", 30))
NUM_QUARTERS_TO_LOAD_10Q = int(os.environ.get("NUM_QUARTERS_TO_LOAD_10Q", 1))
NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS = int(os.environ.get("NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS", 1))
