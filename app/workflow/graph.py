from langgraph.graph import StateGraph, START, END

from .state import StockAnalysisState

from agents.research import run_research_agent
from agents.financial import run_financial_agent
from agents.risk import run_risk_agent
from agents.news_agent import run_news_agent

from agents.valuation import run_valuation_agent
from agents.summary import run_summary_agent
from agents.committee import run_committee_agent


def create_graph():

    graph = StateGraph(
        StockAnalysisState
    )


    # Nodes

    graph.add_node(
        "research",
        run_research_agent
    )

    graph.add_node(
        "financial",
        run_financial_agent
    )

    graph.add_node(
        "risk",
        run_risk_agent
    )

    graph.add_node(
        "news",
        run_news_agent
    )

    graph.add_node(
        "valuation",
        run_valuation_agent
    )

    graph.add_node(
        "summary",
        run_summary_agent
    )

    graph.add_node(
        "committee",
        run_committee_agent
    )


    # Parallel start

    graph.add_edge(
        START,
        "research"
    )

    graph.add_edge(
        START,
        "financial"
    )

    graph.add_edge(
        START,
        "risk"
    )

    graph.add_edge(
        START,
        "news"
    )


    # Join

    graph.add_edge(
        "research",
        "valuation"
    )

    graph.add_edge(
        "financial",
        "valuation"
    )

    graph.add_edge(
        "risk",
        "valuation"
    )

    graph.add_edge(
        "news",
        "valuation"
    )


    # Sequential

    graph.add_edge(
        "valuation",
        "summary"
    )

    graph.add_edge(
        "summary",
        "committee"
    )


    graph.add_edge(
        "committee",
        END
    )


    return graph.compile()