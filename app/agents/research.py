# agents/research.py

from .builder import create_research_agent
try:
    from config import (
        NUM_YEARS_TO_LOAD_10K,
        NUM_QUARTERS_TO_LOAD_10Q,
        NUM_DAYS_TO_LOAD_8K,
        NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS
    )
except ImportError:
    try:
        from app.config import (
            NUM_YEARS_TO_LOAD_10K,
            NUM_QUARTERS_TO_LOAD_10Q,
            NUM_DAYS_TO_LOAD_8K,
            NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS
        )
    except ImportError:
        NUM_YEARS_TO_LOAD_10K = 1
        NUM_DAYS_TO_LOAD_8K = 30
        NUM_QUARTERS_TO_LOAD_10Q = 1
        NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS = 1

research_agent = create_research_agent()


import logging
logger = logging.getLogger("ResearchAgent")

async def run_research_agent(state):
    from database.db_helper import check_bypass_agents
    should_bypass, prev_record = check_bypass_agents(state["ticker"])
    if should_bypass and prev_record.get("research_report"):
        logger.info(f"Research Agent: Bypassed. Loading previous report from SQLite for ticker: {state['ticker']}.")
        return {
            "research_report": prev_record["research_report"],
            "current_price": state.get("current_price", "$142.50"),
            "market_cap": state.get("market_cap", "$395.4 Billion"),
            "shares_outstanding": state.get("shares_outstanding", "2.78 Billion")
        }

    import datetime
    now = datetime.datetime.now()
    current_year = now.year
    current_date_str = now.strftime("%Y-%m-%d")
    current_time_str = now.strftime("%H:%M:%S")
    
    logger.info(f"Research Agent: Initiated. Running LLM analysis for ticker: {state['ticker']}.")
    prompt = f"""
Analyze the following company as part of a multi-agent equity research system.
IMPORTANT: The current date and time is {current_date_str} {current_time_str} (Current Year: {current_year}). You MUST explicitly request and analyze the latest available filings, earnings transcripts, and news details up to this current date (including fiscal years {current_year - 2}, {current_year - 1}, and all current quarters of {current_year}). Do not focus solely on older data (like 2021-2023) unless comparing it to the latest results.

Company: {state["company_name"]}
Ticker: {state["ticker"]}

You MUST query the corporate documents database (using the `doc_rag_search` tool) to retrieve and load details across these specific horizons:
1. Last {NUM_YEARS_TO_LOAD_10K} years of 10-K filings for long-term structural and strategic business model analysis.
2. Last {NUM_QUARTERS_TO_LOAD_10Q} quarters of 10-Q filings for mid-term financial and operational trends.
3. Last {NUM_DAYS_TO_LOAD_8K} days of 8-K filings for immediate material events, announcements, or corporate changes.
4. Last {NUM_QUARTERS_TO_LOAD_EARNINGS_CALLS} quarters of Earnings Call Transcripts to capture executive dialogues and strategist insights.

Perform a comprehensive business and competitive analysis. Focus on understanding:
- Business model
- Competitive position
- Market opportunity
- Long-term growth potential
"""

    logger.info(f"Research Agent Prompt:\n{prompt}")
    response = await research_agent.run_async(
        prompt
    )

    # 1. Fetch real-time price from Yahoo Finance Chart API
    ticker = state["ticker"]
    current_price = state.get("current_price", "$142.50")
    try:
        import urllib.request
        import json
        req = urllib.request.Request(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        res = json.loads(urllib.request.urlopen(req).read().decode())['chart']['result'][0]['meta']
        price_val = res.get('regularMarketPrice')
        if price_val:
            current_price = f"${price_val:.2f}"
    except Exception as e:
        print(f"Error fetching stock price: {e}")

    # 2. Get market cap and shares outstanding (with fallback values if not provided)
    market_cap = state.get("market_cap")
    shares_outstanding = state.get("shares_outstanding")
    if not market_cap or not shares_outstanding:
        if ticker == "ORCL":
            market_cap = "$395.4 Billion"
            shares_outstanding = "2.78 Billion"
        elif ticker == "NVDA":
            market_cap = "$3.2 Trillion"
            shares_outstanding = "24.5 Billion"
        else:
            market_cap = market_cap or "$100.0 Billion"
            shares_outstanding = shares_outstanding or "1.00 Billion"

    return {
        "research_report": response.text,
        "current_price": current_price,
        "market_cap": market_cap,
        "shares_outstanding": shares_outstanding
    }