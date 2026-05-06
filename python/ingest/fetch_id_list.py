"""Build a K-drama IMDb ID list from IMDb non-commercial datasets.

Expected input files (download manually to data/raw/imdb/):
- title.basics.tsv.gz
- title.akas.tsv.gz
"""

from __future__ import annotations

import argparse
import csv
import gzip
import sys
import time
from pathlib import Path
from urllib.request import urlretrieve

import httpx
from tqdm import tqdm

IMDB_BASICS_URL = "https://datasets.imdbws.com/title.basics.tsv.gz"
IMDB_AKAS_URL = "https://datasets.imdbws.com/title.akas.tsv.gz"
IMDB_API_BASE_URL = "https://api.imdbapi.dev"


def configure_csv_field_limit() -> None:
    """Raise csv parser limit to handle large IMDb TSV fields."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            break
        except OverflowError:
            limit = limit // 10


def iter_gzip_tsv(path: Path):
    with gzip.open(path, mode="rt", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            yield row


def count_tsv_rows(path: Path) -> int:
    """Count data rows (excluding header) for progress percentage."""
    with gzip.open(path, mode="rt", encoding="utf-8", newline="") as f:
        total_lines = sum(1 for _ in f)
    return max(0, total_lines - 1)


def has_kr_region_aka(row: dict[str, str]) -> bool:
    return row.get("region") == "KR"


def is_original_title_row(row: dict[str, str]) -> bool:
    """Detect the canonical original-title signal in title.akas."""
    if row.get("isOriginalTitle") != "1":
        return False
    row_type = row.get("types") or ""
    # Usually "original", but keep fallback for rows where types is missing.
    return (row_type == "original") or (row_type in {"\\N", ""})


def build_candidate_ids(basics_path: Path, akas_path: Path) -> list[str]:
    allowed_types = {"tvSeries", "tvMiniSeries"}
    basics_total = count_tsv_rows(basics_path)
    akas_total = count_tsv_rows(akas_path)

    series_ids: set[str] = set()
    for row in tqdm(
        iter_gzip_tsv(basics_path),
        desc="Scan title.basics",
        unit="rows",
        total=basics_total,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {percentage:3.0f}%",
    ):
        if row.get("titleType") in allowed_types:
            tconst = row.get("tconst")
            if tconst:
                series_ids.add(tconst)

    kr_region_ids: set[str] = set()
    original_ids: set[str] = set()
    for row in tqdm(
        iter_gzip_tsv(akas_path),
        desc="Scan title.akas",
        unit="rows",
        total=akas_total,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {percentage:3.0f}%",
    ):
        tconst = row.get("titleId")
        if not tconst or tconst not in series_ids:
            continue

        if has_kr_region_aka(row):
            kr_region_ids.add(tconst)
        if is_original_title_row(row):
            original_ids.add(tconst)

    # Single source-of-truth candidate rule:
    # 1) titleType in {tvSeries, tvMiniSeries}
    # 2) has KR-localized AKA
    # 3) has canonical original-title row
    # Final hard filter by originCountries=KR is applied via imdbapi.dev afterwards.
    final_ids = kr_region_ids.intersection(original_ids)
    return sorted(final_ids)


def batch_get_titles(client: httpx.Client, ids: list[str], max_retries: int = 8) -> list[dict[str, object]]:
    params: list[tuple[str, str]] = [("titleIds", imdb_id) for imdb_id in ids]
    for attempt in range(max_retries):
        try:
            resp = client.get("/titles:batchGet", params=params, timeout=45)
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

        if resp.status_code == 400:
            # Some batches/IDs can be rejected depending on backend limits/validation.
            # Let caller split the batch instead of failing the whole job.
            raise httpx.HTTPStatusError("400 Bad Request", request=resp.request, response=resp)

        resp.raise_for_status()
        payload = resp.json()
        titles = payload.get("titles") or []
        if not isinstance(titles, list):
            return []
        return titles

    raise RuntimeError(f"Failed to fetch batch after retries (size={len(ids)})")


def fetch_titles_resilient(client: httpx.Client, ids: list[str]) -> tuple[list[dict[str, object]], list[str]]:
    """Fetch a batch; if 400 occurs, split recursively until isolating bad IDs."""
    try:
        return batch_get_titles(client, ids), []
    except httpx.HTTPStatusError as exc:
        if exc.response is None or exc.response.status_code != 400:
            raise

    if len(ids) == 1:
        # Irrecoverable bad ID for this endpoint; skip it.
        return [], ids

    mid = len(ids) // 2
    left_titles, left_bad = fetch_titles_resilient(client, ids[:mid])
    right_titles, right_bad = fetch_titles_resilient(client, ids[mid:])
    return left_titles + right_titles, left_bad + right_bad


def chunked(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def has_kr_origin(title_row: dict[str, object]) -> bool:
    countries = title_row.get("originCountries") or []
    if not isinstance(countries, list):
        return False
    for c in countries:
        if isinstance(c, dict) and c.get("code") == "KR":
            return True
    return False


def validate_with_imdb_api(
    candidate_ids: list[str], batch_size: int, sleep_seconds: float
) -> list[str]:
    """Hard filter: keep only titles whose originCountries include KR."""
    if not candidate_ids:
        return []

    kept: set[str] = set()
    skipped_bad_ids: list[str] = []
    progress = tqdm(
        total=len(candidate_ids),
        desc="Validate KR origin (imdbapi.dev)",
        unit="ids",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {percentage:3.0f}%",
    )
    with httpx.Client(base_url=IMDB_API_BASE_URL, headers={"accept": "application/json"}) as client:
        for batch in chunked(candidate_ids, batch_size):
            titles, bad_ids = fetch_titles_resilient(client, batch)
            if bad_ids:
                skipped_bad_ids.extend(bad_ids)
            for row in titles:
                if isinstance(row, dict) and has_kr_origin(row):
                    imdb_id = row.get("id")
                    if isinstance(imdb_id, str) and imdb_id:
                        kept.add(imdb_id)
            progress.update(len(batch))
            progress.set_postfix(kept=len(kept), skipped=len(skipped_bad_ids))
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    progress.close()
    if skipped_bad_ids:
        print(f"Warning: skipped {len(skipped_bad_ids)} IDs due to repeated 400 responses")
    return sorted(kept)


def download_if_missing(target_path: Path, url: str, enabled: bool) -> None:
    if target_path.exists():
        return
    if not enabled:
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url} -> {target_path}")
    urlretrieve(url, target_path)


def read_ids_file(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"IDs file not found: {path}")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    configure_csv_field_limit()

    parser = argparse.ArgumentParser(description="Create kdrama_ids.txt from IMDb TSV files")
    parser.add_argument(
        "--raw-dir",
        default="data/raw/imdb",
        help="Directory containing title.basics.tsv.gz and title.akas.tsv.gz",
    )
    parser.add_argument(
        "--output",
        default="data/processed/kdrama_ids.txt",
        help="Output text file (one imdb id per line)",
    )
    parser.add_argument(
        "--download-missing",
        action="store_true",
        help="Download missing IMDb source files automatically from datasets.imdbws.com",
    )
    parser.add_argument(
        "--api-batch-size",
        type=int,
        default=5,
        help="Batch size for /titles:batchGet validation requests",
    )
    parser.add_argument(
        "--api-sleep-seconds",
        type=float,
        default=0.5,
        help="Optional sleep between validation batches (seconds)",
    )
    parser.add_argument(
        "--validate-existing-candidates",
        action="store_true",
        help="Skip TSV scan and validate IDs from --candidate-ids-file (or --output if not set)",
    )
    parser.add_argument(
        "--candidate-ids-file",
        default="",
        help="Input file for --validate-existing-candidates (one imdb id per line)",
    )
    args = parser.parse_args()
    if args.api_batch_size < 2:
        raise ValueError("--api-batch-size must be >= 2")

    raw_dir = Path(args.raw_dir)
    basics_path = raw_dir / "title.basics.tsv.gz"
    akas_path = raw_dir / "title.akas.tsv.gz"
    output_path = Path(args.output)

    download_if_missing(basics_path, IMDB_BASICS_URL, args.download_missing)
    download_if_missing(akas_path, IMDB_AKAS_URL, args.download_missing)

    if not basics_path.exists() or not akas_path.exists():
        raise FileNotFoundError(
            "Missing input files. Expected:\n"
            f"- {basics_path}\n"
            f"- {akas_path}\n\n"
            "Either place them manually or rerun with --download-missing"
        )

    if args.validate_existing_candidates:
        candidate_path = Path(args.candidate_ids_file) if args.candidate_ids_file else output_path
        ids = read_ids_file(candidate_path)
        print(f"Loaded existing candidate IDs: {len(ids)} from {candidate_path}")
    else:
        ids = build_candidate_ids(basics_path, akas_path)
        print(f"Candidate IDs from TSV filters: {len(ids)}")

    ids = validate_with_imdb_api(ids, batch_size=args.api_batch_size, sleep_seconds=args.api_sleep_seconds)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")

    print(f"Wrote {len(ids)} IDs to {output_path} (TSV candidates + originCountries=KR validation)")


if __name__ == "__main__":
    main()

