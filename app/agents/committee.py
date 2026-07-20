from .builder import create_committee_agent


committee_agent = create_committee_agent()
investment_committee_agent = committee_agent


import logging
logger = logging.getLogger("CommitteeAgent")

async def run_committee_agent(state):
    logger.info(f"Committee Agent: Initiated. Conducting final investment decision analysis for ticker: {state['ticker']}.")
    prompt = f"""
You are reviewing an investment opportunity as the final
investment committee.

Company:

{state["company_name"]}

Ticker:

{state["ticker"]}


Review the following analyst reports:


## Research Agent Report

{state["research_report"]}


## Financial Agent Report

{state["financial_report"]}


## Risk Agent Report

{state["risk_report"]}


## Valuation Agent Report

{state["valuation_report"]}


## Investment Summary Report

{state["investment_summary"]}



Make a final investment decision.


Evaluate:


## 1. Investment Quality

Assess:

- Business quality
- Financial quality
- Growth durability
- Competitive position


## 2. Risk / Reward

Assess:

- Upside potential
- Downside risks
- Key uncertainties


## 3. Valuation Discipline

Assess:

- Is the current valuation justified?
- Is there sufficient margin of safety?


## 4. Thesis Challenge

Identify:

- Weak assumptions
- Missing information
- Possible reasons the thesis could fail


## Final Decision

Provide:

Decision:
(APPROVE / WATCHLIST / REJECT)


Conviction Score:
0-100

Always include the following scoring description lookup text block directly below the Conviction Score:
- **90-100**: Exceptional company with strong fundamentals, manageable risks, and attractive valuation.
- **70-89**: High-quality company with favorable risk/reward.
- **50-69**: Mixed quality or valuation concerns.
- **30-49**: Significant risks or weak fundamentals.
- **0-29**: Poor investment profile.


Explain:

- Why this decision was made
- The strongest supporting evidence
- The biggest remaining concerns
- Conditions that would change the decision


Do not make short-term trading recommendations.
Do not predict stock prices.
"""
    
    logger.info(f"Committee Agent Prompt:\n{prompt}")
    response = await committee_agent.run_async(
        prompt
    )

    return {
        "committee_decision": response.text
    }