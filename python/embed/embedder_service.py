"""Minimal embedding HTTP service for query-time vectors."""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer


class EmbedRequest(BaseModel):
    text: str


class EmbedResponse(BaseModel):
    model: str
    dim: int
    vector: list[float]


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    model_name = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    return SentenceTransformer(model_name)


app = FastAPI(title="kdrama-embedder")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    text = (req.text or "").strip()
    if not text:
        return {"model": "", "dim": 0, "vector": []}

    model_name = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    model = get_model()
    vec = model.encode([text], normalize_embeddings=True)[0].tolist()
    return {"model": model_name, "dim": len(vec), "vector": vec}

