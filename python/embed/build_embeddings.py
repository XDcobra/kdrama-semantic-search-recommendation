"""Build sentence embeddings from cleaned K-drama records."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sentence_transformers import SentenceTransformer
from tqdm import tqdm


def read_clean_jsonl(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in tqdm(lines, desc="Read cleaned rows", unit="lines"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("imdb_id") and row.get("embedding_text"):
            yield row


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate embeddings for cleaned records")
    parser.add_argument("--input", default="data/processed/kdramas_clean.jsonl")
    parser.add_argument("--output", default="data/processed/kdramas_embeddings.jsonl")
    parser.add_argument("--manifest", default="data/processed/kdramas_embeddings_manifest.json")
    parser.add_argument("--model", default=os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--normalize", action="store_true", default=True)
    parser.add_argument("--no-normalize", action="store_false", dest="normalize")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    manifest_path = Path(args.manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    rows = list(read_clean_jsonl(input_path))
    if not rows:
        raise ValueError(f"No valid rows with imdb_id and embedding_text found in {input_path}")
    print(f"Loaded {len(rows)} cleaned rows")

    texts = [row["embedding_text"] for row in rows]
    model = SentenceTransformer(args.model)

    vectors = model.encode(
        texts,
        batch_size=args.batch_size,
        normalize_embeddings=args.normalize,
        show_progress_bar=True,
    )

    with output_path.open("w", encoding="utf-8") as out:
        for row, vector in tqdm(zip(rows, vectors), total=len(rows), desc="Write embeddings", unit="rows"):
            out.write(
                json.dumps(
                    {
                        "imdb_id": row["imdb_id"],
                        "vector": vector.tolist(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    manifest = {
        "model_id": args.model,
        "normalize_embeddings": args.normalize,
        "vector_dim": int(len(vectors[0])),
        "row_count": len(rows),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "output_path": str(output_path),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"Embeddings complete: rows={len(rows)}, dim={manifest['vector_dim']}, "
        f"output={output_path}, manifest={manifest_path}"
    )


if __name__ == "__main__":
    main()

