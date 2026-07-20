from .builder import create_risk_agent


risk_agent = create_risk_agent()

import logging
logger = logging.getLogger("RiskAgent")

async def run_risk_agent(state):
    from database.db_helper import check_bypass_agents
    should_bypass, prev_record = check_bypass_agents(state["ticker"])
    if should_bypass and prev_record.get("risk_report"):
        logger.info(f"Risk Agent: Bypassed. Loading previous report from SQLite for ticker: {state['ticker']}.")
        return {
            "risk_report": prev_record["risk_report"]
        }

    import datetime
    now = datetime.datetime.now()
    current_year = now.year
    current_date_str = now.strftime("%Y-%m-%d")
    current_time_str = now.strftime("%H:%M:%S")
    
    logger.info(f"Risk Agent: Initiated. Running LLM analysis for ticker: {state['ticker']}.")
    prompt = f"""
Analyze investment risks for the following company
as part of a multi-agent equity research system.
IMPORTANT: The current date and time is {current_date_str} {current_time_str} (Current Year: {current_year}). You MUST explicitly request and analyze the latest available risk factors and material updates up to this current date (including fiscal years {current_year - 2}, {current_year - 1}, and all current quarters of {current_year}). Do not focus solely on older historical risks (like 2021-2023) unless comparing it to the latest results.

Company:

{state["company_name"]}


Ticker:

{state["ticker"]}


Identify the most material risks that could negatively affect:

- Revenue
- Earnings
- Cash flow
- Competitive position
- Long-term shareholder value


Use:

- SEC filings
- Annual reports
- Earnings calls
- Investor presentations
- Reliable news sources


For every major risk provide:

- Description
- Evidence
- Probability
- Potential impact
- Overall severity


Focus on company-specific risks rather than generic market risks.
"""

    logger.info(f"Risk Agent Prompt:\n{prompt}")
    response = await risk_agent.run_async(
        prompt
    )

    return {
        "risk_report": response.text
    }