"""Enrich IMDb IDs using imdbapi.dev and write JSONL records.

Default behavior limits processing to 10 IDs so results can be reviewed quickly.
Increase --limit after validating output quality.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx
from tqdm import tqdm

BASE_URL = "https://api.imdbapi.dev"


def read_ids(ids_file: Path) -> list[str]:
    lines = ids_file.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def read_existing_ids(output_file: Path) -> set[str]:
    if not output_file.exists():
        return set()

    existing: set[str] = set()
    for line in output_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        imdb_id = row.get("imdb_id")
        if imdb_id:
            existing.add(imdb_id)
    return existing


def normalize_title_row(row: dict[str, Any]) -> dict[str, Any]:
    rating_obj = row.get("rating") or {}
    image_obj = row.get("primaryImage") or {}
    return {
        "imdb_id": row.get("id"),
        "type": row.get("type"),
        "primary_title": row.get("primaryTitle"),
        "original_title": row.get("originalTitle"),
        "start_year": row.get("startYear"),
        "end_year": row.get("endYear"),
        "runtime_seconds": row.get("runtimeSeconds"),
        "genres": row.get("genres") or [],
        "plot": row.get("plot"),
        "rating": rating_obj.get("aggregateRating"),
        "votes": rating_obj.get("voteCount"),
        "image_url": image_obj.get("url"),
    }


def batch_get_titles(client: httpx.Client, ids: list[str], max_retries: int = 8) -> list[dict[str, Any]]:
    # imdbapi.dev accepts repeated titleIds params.
    params: list[tuple[str, str]] = [("titleIds", imdb_id) for imdb_id in ids]
    for attempt in range(max_retries):
        try:
            resp = client.get("/titles:batchGet", params=params, timeout=30)
        except (httpx.TimeoutException, httpx.NetworkError):
            sleep_for = min(20.0, 0.8 * (2**attempt))
            time.sleep(sleep_for)
            continue

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            try:
                retry_after_secs = float(retry_after) if retry_after else 0.0
            except ValueError:
                retry_after_secs = 0.0
            sleep_for = max(retry_after_secs, min(30.0, 1.2 * (2**attempt)))
            time.sleep(sleep_for)
            continue

        resp.raise_for_status()
        data = resp.json()
        titles = data.get("titles") or []
        if not isinstance(titles, list):
            return []
        return titles

    raise RuntimeError(f"Failed to fetch batch after retries (size={len(ids)})")


def chunked(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich kdrama IMDb IDs via imdbapi.dev")
    parser.add_argument("--ids-file", default="data/processed/kdrama_ids.txt")
    parser.add_argument("--output", default="data/processed/kdramas.jsonl")
    parser.add_argument("--limit", type=int, default=10, help="Max number of IDs to process")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    args = parser.parse_args()

    ids_file = Path(args.ids_file)
    output_file = Path(args.output)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if not ids_file.exists():
        raise FileNotFoundError(f"IDs file not found: {ids_file}")

    ids = read_ids(ids_file)
    if args.limit > 0:
        ids = ids[: args.limit]

    existing_ids = read_existing_ids(output_file)
    ids = [imdb_id for imdb_id in ids if imdb_id not in existing_ids]

    if not ids:
        print("No new IDs to process.")
        return

    print(
        f"Processing {len(ids)} IDs (limit={args.limit}, batch_size={args.batch_size}, "
        f"already_in_output={len(existing_ids)})"
    )

    written = 0
    with httpx.Client(base_url=BASE_URL, headers={"accept": "application/json"}) as client:
        with output_file.open("a", encoding="utf-8") as f:
            progress = tqdm(
                total=len(ids),
                desc="Enrich titles",
                unit="ids",
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {percentage:3.0f}%",
            )
            for batch in chunked(ids, args.batch_size):
                titles = batch_get_titles(client, batch)
                rows = [normalize_title_row(row) for row in titles if row.get("id")]
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    written += 1
                progress.update(len(batch))
                progress.set_postfix(received=len(rows), written=written)
                time.sleep(args.sleep_seconds)
            progress.close()

    print(f"Done. Wrote {written} new records to {output_file}")


if __name__ == "__main__":
    main()

