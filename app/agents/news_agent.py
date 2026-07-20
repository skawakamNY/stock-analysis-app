from .builder import create_agent

news_agent = create_agent('news')

import logging
logger = logging.getLogger("NewsAgent")

async def run_news_agent(state):
    logger.info(f"News Agent: Initiated. Running search and sentiment analysis for ticker: {state['ticker']}.")
    prompt = f"""
Gather and analyze the latest news and public/social media sentiment from the past 30 days for:

Company: {state["company_name"]}
Ticker: {state["ticker"]}

Conduct search queries using the internet search tool to find:
1. Significant news events, press releases, product updates, and earnings releases from the last 30 days.
2. Social media sentiment, discussion trends, and public buzz on platforms like Reddit, Twitter/X, or general finance forums.
3. Key executive, management, or regulatory updates.

Synthesize this information into a consolidated news and sentiment report. Highlight items that could materially affect the company's future outlook or current valuation.
"""
    logger.info(f"News Agent Prompt:\n{prompt}")
    response = await news_agent.run_async(prompt)
    report_text = response.text

    # Store news chunks in Chroma Vector Database
    try:
        import os
        from tools.corporate_documents_search import HybridSearcher
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        searcher = HybridSearcher(persist_directory=os.path.join(app_dir, "database"))
        
        # Split report text into paragraph chunks
        paragraphs = [p.strip() for p in report_text.split("\n\n") if p.strip()]
        chunks = []
        for idx, paragraph in enumerate(paragraphs):
            if len(paragraph) < 30:
                continue
            chunks.append({
                "text": paragraph,
                "metadata": {
                    "ticker": state["ticker"].upper().strip(),
                    "form_type": "NEWS",
                    "item_name": f"News Paragraph {idx + 1}"
                }
            })
            
        if chunks:
            searcher.ingest_chunks(chunks)
            # Register parent document representation for easy retrieval
            searcher.register_parent_doc(
                state["ticker"],
                "NEWS",
                "Latest News & Sentiment",
                report_text
            )
    except Exception as db_err:
        import logging
        logging.getLogger("NewsAgent").error(f"Failed to ingest news chunks to Vector DB: {db_err}")

    return {
        "latest_news_report": report_text
    }
