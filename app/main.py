import os
from dotenv import load_dotenv
from openai import OpenAI
from agents.research import run_research_agent
from agents.financial import run_financial_agent

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

load_dotenv(override=True)
google_api_key = os.getenv("GOOGLE_API_KEY")

gemini = OpenAI(
    base_url=GEMINI_BASE_URL,
    api_key=google_api_key,
)

import asyncio
from workflow.graph import create_graph

async def main():
    app = create_graph()
    result = await app.ainvoke(
        {
            "ticker": "ORCL",
            "company_name": "Oracle Corporation",
            "current_price": "$142.50",
            "market_cap": "$395.4 Billion",
            "shares_outstanding": "2.78 Billion"
        }
    )

    print("\n=== RESEARCH REPORT ===")
    print(result.get("research_report", "No report"))
    print("\n=== FINANCIAL REPORT ===")
    print(result.get("financial_report", "No report"))
    print("\n=== RISK REPORT ===")
    print(result.get("risk_report", "No report"))
    print("\n=== VALUATION REPORT ===")
    print(result.get("valuation_report", "No report"))
    print("\n=== INVESTMENT SUMMARY ===")
    print(result.get("investment_summary", "No summary"))
    print("\n=== COMMITTEE DECISION ===")
    print(result.get("committee_decision", "No decision"))
    print("==========================")

if __name__ == "__main__":
    asyncio.run(main())