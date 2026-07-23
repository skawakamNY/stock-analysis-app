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

    committee_decision = response.text

    # Accumulate all agent reports to write a complete markdown consensus report
    import os
    import datetime
    from tools.pdf_generator_tool import generate_pdf_report_tool

    ticker = state.get("ticker", "UNKNOWN")
    company_name = state.get("company_name", "UNKNOWN")
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    markdown_content = f"""# Consensus Investment Report: {company_name} ({ticker})
**Generated on**: {date_str}

## Executive Investment Committee Decision
{committee_decision}

## Investment Thesis & Summary
{state.get("investment_summary", "N/A")}

## Business & Operations Research
{state.get("research_report", "N/A")}

## Financial Statement Analysis
{state.get("financial_report", "N/A")}

## Investment Risk Assessment
{state.get("risk_report", "N/A")}

## Latest News & Public Sentiment Analysis
{state.get("latest_news_report", "N/A")}

## Valuation & Margin of Safety Analysis
{state.get("valuation_report", "N/A")}
"""

    # Resolve markdown directory (app/markdown/)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(current_dir)
    markdown_dir = os.path.join(app_dir, "markdown")
    os.makedirs(markdown_dir, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    md_filename = f"{ticker}_{timestamp}.md"
    md_filepath = os.path.join(markdown_dir, md_filename)

    try:
        with open(md_filepath, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        logger.info(f"Consensus report successfully saved to Markdown: {md_filepath}")
    except Exception as md_err:
        logger.error(f"Failed to generate Markdown report: {md_err}")

    # Generate the PDF file using the new tool
    full_state = dict(state)
    full_state["committee_decision"] = committee_decision
    try:
        pdf_path = generate_pdf_report_tool(ticker, company_name, full_state)
        logger.info(f"Consensus report successfully saved to PDF via tool: {pdf_path}")
    except Exception as pdf_err:
        logger.error(f"Failed to generate PDF report via tool: {pdf_err}")

    return {
        "committee_decision": committee_decision
    }