from .builder import create_summary_agent


summary_agent = create_summary_agent()
investment_summary_agent = summary_agent

import logging
logger = logging.getLogger("SummaryAgent")

async def run_summary_agent(state):
    logger.info(f"Summary Agent: Initiated. Synthesizing consensus reports into investment summary for ticker: {state['ticker']}.")
    prompt = f"""
You are preparing an investment summary as part of a
multi-agent equity research system.

Company:
{state["company_name"]}

Ticker:
{state["ticker"]}


The following analyst reports are available:


## Research Agent Report

{state["research_report"]}


## Financial Agent Report

{state["financial_report"]}


## Risk Agent Report

{state["risk_report"]}


## Valuation Agent Report

{state["valuation_report"]}


Synthesize these reports into a comprehensive investment summary.

Evaluate:

1. Business Quality

Determine:
- Strength of competitive position
- Sustainability of growth
- Quality of business model


2. Financial Quality

Determine:
- Strength of historical financial performance
- Profitability quality
- Cash generation quality


3. Growth Opportunity

Determine:
- Key growth drivers
- Long-term growth potential
- Growth sustainability


4. Risk-Reward Profile

Determine:
- Major upside factors
- Major downside factors
- Whether risks are appropriately reflected


5. Valuation Perspective

Determine:
- Whether valuation supports the investment thesis
- Whether upside justifies current price


Provide:

## Investment Thesis Summary

Explain the overall investment case.


## Key Strengths

List the strongest reasons supporting the investment.


## Key Concerns

List the biggest factors that could invalidate the thesis.


## Investment Conviction Score

Assign a score:

90-100:
Exceptional opportunity

75-89:
Strong opportunity

60-74:
Interesting but meaningful concerns

40-59:
Weak risk/reward

Below 40:
Poor opportunity


## Overall Conclusion

Provide a balanced summary.

Do not provide short-term trading advice.
Do not predict stock price movements.
Base conclusions only on the provided analyst reports.
"""


    logger.info(f"Summary Agent Prompt:\n{prompt}")
    response = await summary_agent.run_async(
        prompt
    )

    return {
        "investment_summary": response.text
    }