import os

import pandas as pd
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_community.document_loaders import PyPDFLoader
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import SETTINGS, ensure_org_runtime_files, load_org_profile


ORG_PROFILE = load_org_profile()
ensure_org_runtime_files(ORG_PROFILE)
os.environ.setdefault("VOX_CACHE_DIR", ORG_PROFILE["cache_dir"])

DATA_FOLDER = ORG_PROFILE["source_data_dir"]
CHROMA_DIR = ORG_PROFILE["chroma_dir"]
EMBED_MODEL = SETTINGS.embedding_model or ORG_PROFILE.get("embedding_model", "nomic-embed-text")


def load_pdfs(folder):
    docs = []
    for file in os.listdir(folder):
        if file.lower().endswith(".pdf"):
            print(f"Loading PDF: {file}")
            loader = PyPDFLoader(os.path.join(folder, file))
            docs.extend(loader.load())
    return docs


def load_excel(folder):
    docs = []
    for file in os.listdir(folder):
        if file.lower().endswith((".xlsx", ".xls")):
            print(f"Loading Excel: {file}")
            df = pd.read_excel(os.path.join(folder, file))
            for i, row in df.iterrows():
                text = "\n".join(f"{column}: {row.get(column, '')}" for column in df.columns)
                docs.append(Document(page_content=text, metadata={"source": file, "row": i}))
    return docs


if __name__ == "__main__":
    print(f"Indexing organization: {ORG_PROFILE.get('organization_name', ORG_PROFILE['org_id'])}")
    documents = load_pdfs(DATA_FOLDER) + load_excel(DATA_FOLDER)
    print(f"\nTotal documents loaded: {len(documents)}")

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    chunks = splitter.split_documents(documents)
    print(f"Total chunks to index: {len(chunks)}")

    print("\nIndexing into ChromaDB. This may take a few minutes...")
    Chroma.from_documents(
        documents=chunks,
        embedding=OllamaEmbeddings(model=EMBED_MODEL),
        persist_directory=CHROMA_DIR,
    )
    print(f"\nIndexing complete. ChromaDB saved to {CHROMA_DIR}")
