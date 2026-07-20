import httpx
from pypdf import PdfReader
from io import BytesIO
from langchain_text_splitters import RecursiveCharacterTextSplitter

SEC_HEADERS = {"User-Agent": "stock-analysis-agent skawakam@hotmail.com"}

async def download_sec_document(url: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=SEC_HEADERS)
        response.raise_for_status()
        return response.content


def extract_pdf_text(pdf_bytes):
    reader = PdfReader(BytesIO(pdf_bytes))
    text = ""
    for page in reader.pages:
        text += page.extract_text()

    return text

def split_document(text):
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = splitter.split_text(text)
    return chunks


metadata = {
    "ticker": "NVDA",
    "company": "NVIDIA",
    "filing": "10-K",
    "year": "2025",
    "section": "Business",
}

from langchain_core.documents import Document


doc = Document(
    page_content=chunk,
    metadata={"ticker": "NVDA", "filing": "10-K", "section": "Competition"},
)