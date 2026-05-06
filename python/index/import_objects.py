"""Import cleaned metadata + embeddings into Weaviate."""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from tqdm import tqdm


UUID_NAMESPACE = uuid.UUID("f58f93ac-6173-49f1-a8d2-c1948242c4e4")


def weaviate_base_url() -> str:
    scheme = os.getenv("WEAVIATE_SCHEME", "http")
    host = os.getenv("WEAVIATE_HOST", "localhost")
    port = os.getenv("WEAVIATE_PORT", "8080")
    return f"{scheme}://{host}:{port}"


def read_jsonl(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def load_embeddings(path: Path) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for row in read_jsonl(path):
        imdb_id = row.get("imdb_id")
        vector = row.get("vector")
        if isinstance(imdb_id, str) and isinstance(vector, list):
            result[imdb_id] = vector
    return result


def chunked(items: list[dict[str, Any]], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def make_object(class_name: str, row: dict[str, Any], vector: list[float]) -> dict[str, Any]:
    imdb_id = row["imdb_id"]
    obj_uuid = str(uuid.uuid5(UUID_NAMESPACE, imdb_id))
    return {
        "class": class_name,
        "id": obj_uuid,
        "vector": vector,
        "properties": {
            "imdbId": imdb_id,
            "type": row.get("type"),
            "primaryTitle": row.get("primary_title"),
            "originalTitle": row.get("original_title"),
            "startYear": row.get("start_year"),
            "endYear": row.get("end_year"),
            "genres": row.get("genres") or [],
            "plot": row.get("plot"),
            "rating": row.get("rating"),
            "votes": row.get("votes"),
            "imageUrl": row.get("image_url"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import cleaned dramas + vectors into Weaviate")
    parser.add_argument("--clean-input", default="data/processed/kdramas_clean.jsonl")
    parser.add_argument("--embeddings-input", default="data/processed/kdramas_embeddings.jsonl")
    parser.add_argument("--class-name", default="KDrama")
    parser.add_argument("--base-url", default=weaviate_base_url())
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--sleep-seconds", type=float, default=0.1)
    args = parser.parse_args()

    clean_path = Path(args.clean_input)
    embeddings_path = Path(args.embeddings_input)
    if not clean_path.exists():
        raise FileNotFoundError(f"Clean input not found: {clean_path}")
    if not embeddings_path.exists():
        raise FileNotFoundError(f"Embeddings input not found: {embeddings_path}")

    embeddings = load_embeddings(embeddings_path)
    clean_rows = list(read_jsonl(clean_path))
    objects: list[dict[str, Any]] = []
    missing_vectors = 0

    for row in clean_rows:
        imdb_id = row.get("imdb_id")
        if not isinstance(imdb_id, str):
            continue
        vector = embeddings.get(imdb_id)
        if vector is None:
            missing_vectors += 1
            continue
        objects.append(make_object(args.class_name, row, vector))

    if not objects:
        raise ValueError("No importable objects found (check input files)")

    print(
        f"Prepared objects={len(objects)}, missing_vectors={missing_vectors}, "
        f"class={args.class_name}, batch_size={args.batch_size}"
    )

    total_errors = 0
    with httpx.Client(timeout=60) as client:
        progress = tqdm(total=len(objects), desc="Import Weaviate", unit="objects")
        for batch in chunked(objects, args.batch_size):
            resp = client.post(
                f"{args.base_url}/v1/batch/objects",
                json={"objects": batch},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            out = resp.json() or {}
            items = out if isinstance(out, list) else out.get("objects", []) if isinstance(out, dict) else []
            total_errors += sum(
                1 for item in items if isinstance(item, dict) and item.get("result", {}).get("errors")
            )
            progress.update(len(batch))
            progress.set_postfix(errors=total_errors)
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
        progress.close()

    print(f"Import done: inserted={len(objects)}, errors={total_errors}")


if __name__ == "__main__":
    main()

