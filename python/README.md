**Python / ML side:** Sentence Transformers, dataset cleaning, embedding generation, optional Weaviate batch upload, small evaluation scripts.

Use **`uv`** as the package manager:

- From repo root or `python/`: `uv venv && uv pip install -r requirements.txt` (or migrate to `pyproject.toml` + `uv sync` when added).
- Run: `uv run python …`

Phase 2 scripts (dataset ingest):

- Build ID list from IMDb TSV files:
  - `uv run python python/ingest/fetch_id_list.py --raw-dir data/raw/imdb --output data/processed/kdrama_ids.txt`
  - Auto-download missing source files:
    - `uv run python python/ingest/fetch_id_list.py --download-missing`
  - Fixed single pipeline (no modes/fallbacks):
    1) local TSV candidates (`tvSeries|tvMiniSeries` + KR AKA + original-title signal)
    2) API validation (`originCountries` must include `KR`)
  - Re-validate existing candidate file only (skip TSV scan):
    - `uv run python python/ingest/fetch_id_list.py --validate-existing-candidates --candidate-ids-file data/processed/kdrama_ids.txt --output data/processed/kdrama_ids.txt`
  - Validation is resilient:
    - handles `429` with backoff
    - handles `400` by splitting batches recursively and skipping only irrecoverable single IDs
  - Rate-limit defaults are conservative (`--api-batch-size`, `--api-sleep-seconds`) to reduce API errors.
- Enrich IDs via imdbapi.dev (**default limit = 10**):
  - `uv run python python/ingest/enrich_titles.py --ids-file data/processed/kdrama_ids.txt --output data/processed/kdramas.jsonl`
  - Script retries API rate limits (`429`) with backoff automatically; tune with:
    - `--batch-size` (smaller = safer)
    - `--sleep-seconds` (higher = safer)
- After validation, process more IDs:
  - `uv run python python/ingest/enrich_titles.py --limit 0` (0 = no limit)

Phase 3 scripts (preprocess + embeddings):

- Clean enriched rows and build embedding text:
  - `uv run python python/preprocess/clean.py --input data/processed/kdramas.jsonl --output data/processed/kdramas_clean.jsonl`
- Build embeddings from cleaned rows:
  - `uv run python python/embed/build_embeddings.py --input data/processed/kdramas_clean.jsonl --output data/processed/kdramas_embeddings.jsonl --manifest data/processed/kdramas_embeddings_manifest.json`

Phase 4/5 scripts (Weaviate schema + import):

- Create schema (idempotent):
  - `uv run python python/index/create_schema.py --class-name KDrama`
- Import cleaned rows + vectors:
  - `uv run python python/index/import_objects.py --clean-input data/processed/kdramas_clean.jsonl --embeddings-input data/processed/kdramas_embeddings.jsonl --class-name KDrama`

Phase 6 (query-time embeddings + API recommend):

- Query-time embedder service:
  - `uv run uvicorn python.embed.embedder_service:app --host 0.0.0.0 --port 8000`
- Docker Compose now includes:
  - `embedder` (FastAPI + Sentence Transformers)
  - `api` uses `EMBEDDER_URL=http://embedder:8000/embed`
- API endpoint:
  - `POST /api/recommend` with body `{ "query": "...", "k": 5 }`

Notebooks in `../notebooks/` should import or call code from here as the project grows.
