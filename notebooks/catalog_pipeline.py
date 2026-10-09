# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "marimo",
#     "polars",
#     "duckdb",
#     "pyarrow",
#     "sqlglot",
# ]
# ///
"""FiveThirtyEight data catalog: explore, profile, validate and ingest.

Interactive:  uvx marimo edit --sandbox notebooks/catalog_pipeline.py
Headless:     uv run notebooks/catalog_pipeline.py -- --out build
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App(width="medium")

with app.setup:
    import re
    from pathlib import Path

    import duckdb
    import marimo as mo
    import polars as pl

    REPO_ROOT = Path(__file__).resolve().parent.parent
    NULL_VALUES = ["", "NA", "N/A", "NaN", "null", "NULL"]


@app.function
def slug(text: str) -> str:
    """Lowercase and replace runs of non-alphanumerics with `_`."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", text.strip()).strip("_").lower()


@app.function
def snake_case_columns(columns: list[str]) -> list[str]:
    """Slugify column names, prefix leading digits, and de-duplicate."""
    seen: dict[str, int] = {}
    out = []
    for i, col in enumerate(columns):
        name = slug(col) or f"column_{i}"
        if name[0].isdigit():
            name = f"c_{name}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


@app.function
def read_csv(path: Path) -> pl.DataFrame:
    """Read a CSV tolerantly: lossy UTF-8, ragged lines, wide type inference.

    Normalizes old-Mac `\r` line endings (several files here use them) and
    falls back to all-string columns if type inference fails.
    """
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    opts = dict(
        encoding="utf8-lossy",
        null_values=NULL_VALUES,
        truncate_ragged_lines=True,
        ignore_errors=False,
    )
    try:
        return pl.read_csv(raw, infer_schema_length=10_000, **opts)
    except Exception:
        return pl.read_csv(raw, infer_schema=False, **opts)


@app.function
def is_lfs_pointer(path: Path) -> bool:
    """Large files in this repo are Git LFS pointers unless `git lfs pull` was run."""
    with open(path, "rb") as f:
        return f.read(40).startswith(b"version https://git-lfs")


@app.function
def table_name(rel_path: str) -> str:
    """`nfl-elo/nfl_elo.csv` -> `nfl_elo__nfl_elo`."""
    *dirs, file = Path(rel_path).with_suffix("").parts
    return f"{slug('_'.join(dirs)) or 'root'}__{slug(file)}"


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # FiveThirtyEight data catalog

    A starter data-engineering notebook over this repository's CSVs:

    1. **Discover** every CSV and join it to `index.csv`
    2. **Explore & profile** a dataset you pick
    3. **Query** it with SQL (DuckDB)
    4. **Validate** it with simple data-quality checks
    5. **Ingest** everything into a DuckDB database + Parquet files

    Run it headless with `uv run notebooks/catalog_pipeline.py -- --out build`.
    """)
    return


@app.cell
def _():
    # CLI args are only present in script mode: `-- --out build --limit 10`
    args = mo.cli_args()
    is_script = mo.app_meta().mode == "script"
    out_dir = REPO_ROOT / str(args.get("out", "build"))
    limit = int(args["limit"]) if "limit" in args else None
    return is_script, limit, out_dir


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ## 1. Discover
    """)
    return


@app.cell
def _():
    index = pl.read_csv(REPO_ROOT / "index.csv").with_columns(
        pl.col("dataset_url", "article_url").str.strip_chars(),
        folder=pl.col("dataset_url").str.extract(r"/tree/master/([^/]+)"),
    )

    files = sorted(
        p for p in REPO_ROOT.rglob("*.csv")
        if ".git" not in p.parts and p.parent != REPO_ROOT and "build" not in p.parts
    )
    catalog = (
        pl.DataFrame({
            "path": [str(p.relative_to(REPO_ROOT)) for p in files],
            "folder": [p.relative_to(REPO_ROOT).parts[0] for p in files],
            "size_kb": [round(p.stat().st_size / 1024, 1) for p in files],
            "lfs_pointer": [is_lfs_pointer(p) for p in files],
        })
        .join(index.select("folder", "article_url").unique("folder"), on="folder", how="left")
        .with_columns(table=pl.col("path").map_elements(table_name, return_dtype=pl.String))
    )
    mo.vstack([
        mo.md(
            f"**{catalog.height}** CSV files in **{catalog['folder'].n_unique()}** folders, "
            f"{catalog['size_kb'].sum() / 1024:,.1f} MB total."
        ),
        mo.md(
            "Folders without CSVs (e.g. `nfl-elo`) only link to data hosted elsewhere; "
            "`lfs_pointer` files need `git lfs pull` before they can be read."
        ),
        catalog,
    ])
    return (catalog,)


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ## 2. Explore & profile
    """)
    return


@app.cell
def _(catalog):
    folder_picker = mo.ui.dropdown(
        options=sorted(catalog.filter(~pl.col("lfs_pointer"))["folder"].unique().to_list()),
        value="bob-ross",
        label="Dataset folder",
        searchable=True,
    )
    folder_picker
    return (folder_picker,)


@app.cell
def _(catalog, folder_picker):
    _paths = catalog.filter(
        (pl.col("folder") == folder_picker.value) & ~pl.col("lfs_pointer")
    )["path"].to_list()
    file_picker = mo.ui.dropdown(options=_paths, value=_paths[0], label="File")
    file_picker
    return (file_picker,)


@app.cell
def _(catalog, file_picker):
    df = read_csv(REPO_ROOT / file_picker.value)
    _article = catalog.filter(pl.col("path") == file_picker.value)["article_url"][0]
    mo.vstack([
        mo.md(
            f"`{file_picker.value}`: **{df.height:,}** rows × **{df.width}** columns"
            + (f" · [article]({_article})" if _article else "")
        ),
        df,
    ])
    return (df,)


@app.cell
def _(df):
    profile = pl.DataFrame([
        {
            "column": name,
            "dtype": str(s.dtype),
            "nulls": s.null_count(),
            "null_pct": round(100 * s.null_count() / max(len(s), 1), 1),
            "distinct": s.n_unique(),
            "min": str(s.min()) if s.dtype.is_numeric() or s.dtype.is_temporal() else None,
            "max": str(s.max()) if s.dtype.is_numeric() or s.dtype.is_temporal() else None,
            "example": str(s.drop_nulls().head(1).to_list()[0]) if s.null_count() < len(s) else None,
        }
        for name, s in df.to_dict().items()
    ])
    profile
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ## 3. Query with SQL

    SQL cells run on DuckDB and can reference Python dataframes by name (`df` is the file picked above).
    """)
    return


@app.cell
def _(df):
    _df = mo.sql(
        f"""
        SELECT *
        FROM df
        LIMIT 100
        """
    )
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ## 4. Validate
    """)
    return


@app.function
def validate(df: pl.DataFrame) -> pl.DataFrame:
    """Simple data-quality checks; returns one row per check."""
    all_null = [c for c in df.columns if df[c].null_count() == df.height]
    renamed = [
        f"{a} → {b}" for a, b in zip(df.columns, snake_case_columns(df.columns)) if a != b
    ]
    dup_rows = df.height - df.unique().height if df.height else 0
    checks = [
        ("has rows", df.height > 0, f"{df.height:,} rows"),
        ("no fully-null columns", not all_null, ", ".join(all_null) or "—"),
        ("no duplicate rows", dup_rows == 0, f"{dup_rows:,} duplicates"),
        ("snake_case column names", not renamed, "; ".join(renamed[:5]) or "—"),
    ]
    return pl.DataFrame(
        [{"check": c, "status": "pass" if ok else "warn", "detail": d} for c, ok, d in checks]
    )


@app.cell
def _(df):
    validate(df)
    return


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ## 5. Ingest

    Loads every CSV (with snake_case columns) into `<out>/fivethirtyeight.duckdb`
    and writes one Parquet file per table to `<out>/parquet/`. In the editor, click the
    button; in script mode it runs automatically.
    """)
    return


@app.cell
def _():
    run_ingest = mo.ui.run_button(label="Run ingest")
    run_ingest
    return (run_ingest,)


@app.cell
def _(catalog, is_script, limit, out_dir, run_ingest):
    mo.stop(not (is_script or run_ingest.value), mo.md("_Click **Run ingest** to load all tables._"))

    (out_dir / "parquet").mkdir(parents=True, exist_ok=True)
    _rows = catalog.head(limit) if limit else catalog
    _report = []
    with duckdb.connect(str(out_dir / "fivethirtyeight.duckdb")) as _con:
        for _rec in mo.status.progress_bar(_rows.iter_rows(named=True), total=_rows.height, title="Ingesting"):
            if _rec["lfs_pointer"]:
                _report.append({**_rec, "rows": 0, "cols": 0, "status": "skipped",
                                "warnings": "Git LFS pointer; run `git lfs pull`"})
                continue
            try:
                _t = read_csv(REPO_ROOT / _rec["path"])
                _t.columns = snake_case_columns(_t.columns)
                _con.register("_incoming", _t)
                _con.execute(f'CREATE OR REPLACE TABLE "{_rec["table"]}" AS SELECT * FROM _incoming')
                _con.unregister("_incoming")
                _t.write_parquet(out_dir / "parquet" / f"{_rec['table']}.parquet")
                _warns = validate(_t).filter(pl.col("status") == "warn")["check"].to_list()
                _report.append({**_rec, "rows": _t.height, "cols": _t.width,
                                "status": "ok", "warnings": ", ".join(_warns)})
            except Exception as e:  # keep going; failures are reported below
                _report.append({**_rec, "rows": 0, "cols": 0, "status": "error", "warnings": str(e)})

    load_report = pl.DataFrame(_report)
    load_report.write_csv(out_dir / "load_report.csv")
    _counts = dict(load_report["status"].value_counts().iter_rows())
    _summary = (
        f"Loaded {_counts.get('ok', 0)}/{load_report.height} tables into {out_dir} "
        f"({_counts.get('skipped', 0)} skipped, {_counts.get('error', 0)} failed)"
    )
    if is_script:
        print(_summary)
        _failed = load_report.filter(pl.col("status") == "error")
        if _failed.height:
            # Non-zero exit so a scheduler (cron, CI, Airflow) sees the failure
            raise RuntimeError(f"{_failed.height} tables failed:\n{_failed.select('path', 'warnings')}")
    mo.vstack([mo.md(_summary), load_report])
    return


if __name__ == "__main__":
    app.run()
