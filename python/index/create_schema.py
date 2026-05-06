"""Create Weaviate schema for K-drama vectors (idempotent)."""

from __future__ import annotations

import argparse
import os

import httpx


def weaviate_base_url() -> str:
    scheme = os.getenv("WEAVIATE_SCHEME", "http")
    host = os.getenv("WEAVIATE_HOST", "localhost")
    port = os.getenv("WEAVIATE_PORT", "8080")
    return f"{scheme}://{host}:{port}"


def class_payload(class_name: str) -> dict:
    return {
        "class": class_name,
        "description": "K-drama titles with externally provided embeddings",
        "vectorizer": "none",
        "vectorIndexType": "hnsw",
        "vectorIndexConfig": {"distance": "cosine"},
        "properties": [
            {"name": "imdbId", "dataType": ["text"]},
            {"name": "type", "dataType": ["text"]},
            {"name": "primaryTitle", "dataType": ["text"]},
            {"name": "originalTitle", "dataType": ["text"]},
            {"name": "startYear", "dataType": ["int"]},
            {"name": "endYear", "dataType": ["int"]},
            {"name": "genres", "dataType": ["text[]"]},
            {"name": "plot", "dataType": ["text"]},
            {"name": "rating", "dataType": ["number"]},
            {"name": "votes", "dataType": ["int"]},
            {"name": "imageUrl", "dataType": ["text"]},
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create KDrama class in Weaviate")
    parser.add_argument("--class-name", default="KDrama")
    parser.add_argument("--base-url", default=weaviate_base_url())
    parser.add_argument("--drop-if-exists", action="store_true")
    args = parser.parse_args()

    schema_url = f"{args.base_url}/v1/schema"
    class_url = f"{schema_url}/{args.class_name}"
    payload = class_payload(args.class_name)

    with httpx.Client(timeout=30) as client:
        existing = client.get(schema_url)
        existing.raise_for_status()
        classes = (existing.json() or {}).get("classes") or []
        exists = any(c.get("class") == args.class_name for c in classes if isinstance(c, dict))

        if exists and args.drop_if_exists:
            resp = client.delete(class_url)
            resp.raise_for_status()
            exists = False
            print(f"Dropped existing class: {args.class_name}")

        if exists:
            print(f"Class already exists: {args.class_name}")
            return

        resp = client.post(schema_url, json=payload)
        resp.raise_for_status()
        print(f"Created class: {args.class_name}")


if __name__ == "__main__":
    main()

