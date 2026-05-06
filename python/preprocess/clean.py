"""Clean enriched title records and build embedding input text.

Input:  JSONL from python/ingest/enrich_titles.py
Output: cleaned JSONL with an additional `embedding_text` field.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def clean_text(value: str) -> str:
    text = value.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_embedding_text(row: dict[str, Any]) -> str:
    primary_title = clean_text((row.get("primary_title") or "").strip())
    original_title = clean_text((row.get("original_title") or "").strip())
    genres = row.get("genres") or []
    if isinstance(genres, list):
        genres_text = ", ".join(str(g).strip() for g in genres if str(g).strip())
    else:
        genres_text = ""
    plot = clean_text((row.get("plot") or "").strip())

    parts: list[str] = []
    if primary_title:
        parts.append(f"Title: {primary_title}.")
    if original_title and original_title.lower() != primary_title.lower():
        parts.append(f"Original title: {original_title}.")
    if genres_text:
        parts.append(f"Genres: {genres_text}.")
    if plot:
        parts.append(f"Plot: {plot}")
    return " ".join(parts).strip()


def load_jsonl(path: Path):
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line), line_no
        except json.JSONDecodeError:
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean records and create embedding_text")
    parser.add_argument("--input", default="data/processed/kdramas.jsonl")
    parser.add_argument("--output", default="data/processed/kdramas_clean.jsonl")
    parser.add_argument("--min-plot-chars", type=int, default=40)
    parser.add_argument(
        "--allowed-types",
        nargs="*",
        default=["tvSeries", "tvMiniSeries"],
        help="Allowed title types",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    total = 0
    kept = 0
    dropped_type = 0
    dropped_plot = 0

    with output_path.open("w", encoding="utf-8") as out:
        for row, _line_no in load_jsonl(input_path):
            total += 1

            title_type = row.get("type")
            if title_type not in set(args.allowed_types):
                dropped_type += 1
                continue

            plot = clean_text((row.get("plot") or "").strip())
            if len(plot) < args.min_plot_chars:
                dropped_plot += 1
                continue

            cleaned = {
                "imdb_id": row.get("imdb_id"),
                "type": row.get("type"),
                "primary_title": clean_text((row.get("primary_title") or "").strip()) or None,
                "original_title": clean_text((row.get("original_title") or "").strip()) or None,
                "start_year": row.get("start_year"),
                "end_year": row.get("end_year"),
                "genres": row.get("genres") or [],
                "plot": plot,
                "rating": row.get("rating"),
                "votes": row.get("votes"),
                "image_url": row.get("image_url"),
            }
            cleaned["embedding_text"] = build_embedding_text(cleaned)

            out.write(json.dumps(cleaned, ensure_ascii=False) + "\n")
            kept += 1

    print(
        f"Clean complete: total={total}, kept={kept}, "
        f"dropped_type={dropped_type}, dropped_plot={dropped_plot}, output={output_path}"
    )


if __name__ == "__main__":
    main()

