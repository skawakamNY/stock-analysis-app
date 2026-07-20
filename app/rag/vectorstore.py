import chromadb
from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings


embeddings = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")

def create_sec_vectorstore(documents):
    vectorstore = Chroma.from_documents(
        documents=documents, embedding=embeddings, persist_directory="./chroma/sec"
    )

    return vectorstore


