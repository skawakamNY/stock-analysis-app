from langchain_chroma import Chroma
from sec_loader import download_sec_document, extract_pdf_text


class SECRAGTool:

    name = "sec_rag_search"

    description = """
    Search SEC filings.

    Useful for:
    - Business description
    - Products
    - Competition
    - Risk factors
    - Strategy
    - Management discussion

    Input:
    ticker
    query
    """

    def __init__(self, vectorstore):
        self.vectorstore = vectorstore

    async def search(self, ticker: str, query: str, k: int = 5):
        results = self.vectorstore.similarity_search(
            query, k=k, filter={"ticker": ticker}
        )
        return [{"content": r.page_content, "metadata": r.metadata} for r in results]
