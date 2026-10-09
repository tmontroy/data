# Notebooks

## `catalog_pipeline.py`

A [marimo](https://marimo.io) notebook that treats this repo as a data source:

1. **Discover**: finds every CSV and joins it to `index.csv` (article links, Git LFS pointer detection)
2. **Explore & profile**: pick a dataset, then see per-column types, nulls, distinct counts, min/max
3. **Query**: run DuckDB SQL directly against the selected dataframe
4. **Validate**: simple data-quality checks (empty tables, fully-null columns, duplicate rows, column naming)
5. **Ingest**: loads all CSVs with snake_case columns into `build/fivethirtyeight.duckdb` and `build/parquet/*.parquet`, and writes `build/load_report.csv`

Dependencies are declared inline (PEP 723), so [uv](https://docs.astral.sh/uv/) handles the environment.

```bash
# Interactive editor
uvx marimo edit --sandbox notebooks/catalog_pipeline.py

# Headless batch run (exits non-zero if any table fails to load)
uv run notebooks/catalog_pipeline.py -- --out build
uv run notebooks/catalog_pipeline.py -- --out build --limit 20   # quick smoke test

# Query the result
duckdb build/fivethirtyeight.duckdb -c "SHOW TABLES"
```

The helper functions (`read_csv`, `snake_case_columns`, `validate`, ...) are plain top-level
functions, so other code and tests can import them:

```python
from notebooks.catalog_pipeline import read_csv, validate
```

Notes:
- Several CSVs use old Mac `\r` line endings; `read_csv` normalizes them.
- `science-giving` and `scrabble-games` are Git LFS files and are skipped unless you run `git lfs pull`.
- Folders such as `nfl-elo` only link to data hosted elsewhere, so they have no CSVs here.
