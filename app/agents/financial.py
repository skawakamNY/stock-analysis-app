from .builder import create_financial_agent


financial_agent = create_financial_agent()

import logging
logger = logging.getLogger("FinancialAgent")

async def run_financial_agent(state):
    from database.db_helper import check_bypass_agents
    should_bypass, prev_record = check_bypass_agents(state["ticker"])
    if should_bypass and prev_record.get("financial_report"):
        logger.info(f"Financial Agent: Bypassed. Loading previous report from SQLite for ticker: {state['ticker']}.")
        return {
            "financial_report": prev_record["financial_report"]
        }

    import datetime
    now = datetime.datetime.now()
    current_year = now.year
    current_date_str = now.strftime("%Y-%m-%d")
    current_time_str = now.strftime("%H:%M:%S")
    
    logger.info(f"Financial Agent: Initiated. Running LLM analysis for ticker: {state['ticker']}.")
    prompt = f"""
Analyze the historical financial performance of the following company
as part of a multi-agent equity research system.
IMPORTANT: The current date and time is {current_date_str} {current_time_str} (Current Year: {current_year}). You MUST explicitly request and analyze the latest available quantitative financial statements and earnings reports up to this current date (including fiscal years {current_year - 2}, {current_year - 1}, and all current quarters of {current_year}). Do not focus solely on older historical data (like 2021-2023) unless comparing it to the latest results.

Company:

{state["company_name"]}


Ticker:

{state["ticker"]}


Use available financial information:

- Annual reports
- Quarterly reports
- SEC filings
- Earnings reports


Evaluate financial performance over multiple years.

Focus on:

- Revenue growth
- Profitability
- Cash generation
- Balance sheet strength
- Capital efficiency
- Capital allocation


Provide a quantitative financial analysis report.
"""

    logger.info(f"Financial Agent Prompt:\n{prompt}")
    response = await financial_agent.run_async(
        prompt
    )

    return {
        "financial_report": response.text
    }