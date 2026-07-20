from typing import TypedDict


class StockAnalysisState(TypedDict):

    ticker: str
    company_name: str
    current_price: str
    market_cap: str
    shares_outstanding: str
    forward_pe: str

    # Specialist outputs
    research_report: str
    financial_report: str
    risk_report: str
    latest_news_report: str

    # Later stages
    valuation_report: str
    investment_summary: str
    committee_decision: str