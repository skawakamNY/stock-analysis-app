from .builder import create_valuation_agent


valuation_agent = create_valuation_agent()

import logging
logger = logging.getLogger("ValuationAgent")

async def run_valuation_agent(state):
    logger.info(f"Valuation Agent: Initiated. Running valuation models and cash flow scenarios for ticker: {state['ticker']}.")
    prompt = f"""
You are performing a valuation analysis as part of a
multi-agent equity research system.

Company:
{state["company_name"]}

Ticker:
{state["ticker"]}


Current Market Information:

Current Stock Price:
{state["current_price"]}

Market Capitalization:
{state["market_cap"]}

Shares Outstanding:
{state["shares_outstanding"]}

Forward P/E Ratio:
{state.get("forward_pe", "N/A")}


## Research Agent Report

{state["research_report"]}


## Financial Agent Report

{state["financial_report"]}


## Risk Agent Report

{state["risk_report"]}


## Latest News and Sentiment Report

{state["latest_news_report"]}


Determine whether the company's current valuation
is justified.


Perform:

1. Current Market Valuation Analysis

Evaluate:
- P/E ratio
- PEG ratio
- EV/EBITDA
- EV/Sales
- Price-to-Free-Cash-Flow
- Historical valuation multiples


2. Relative Valuation Analysis

Compare with:
- Industry peers
- Historical valuation range
- Growth-adjusted valuation


3. Intrinsic Value Analysis

Perform DCF when appropriate.

Explain:
- Revenue growth assumptions
- Margin assumptions
- Free cash flow assumptions
- Discount rate
- Terminal growth


4. Scenario Analysis

Develop:

Bull Case:
- Assumptions
- Valuation range

Base Case:
- Assumptions
- Valuation range

Bear Case:
- Assumptions
- Downside valuation


5. Margin of Safety

Evaluate:
- Current price vs intrinsic value
- Valuation sensitivity


6. Overall Valuation Assessment

Summarize:
- Attractive / Fair / Expensive
- Intrinsic value range
- Expected return
- Key uncertainties


Constraints:

- Do not repeat business analysis.
- Do not redo financial analysis.
- Do not independently analyze risks.
- Use provided reports as inputs.
"""


    logger.info(f"Valuation Agent Prompt:\n{prompt}")
    response = await valuation_agent.run_async(
        prompt
    )

    return {
        "valuation_report": response.text
    }