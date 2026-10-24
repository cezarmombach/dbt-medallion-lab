# dbt-medallion-lab

A hands-on dbt Core + DuckDB project that models a synthetic Salesforce-like CRM
for a growth equity firm, using a medallion layout
(`source` -> `staging` -> `intermediate` -> `marts`).

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/generate_crm_data.py   # writes raw CSVs to data/raw/
dbt debug                             # validates project and connection
```

## Project structure

```
models/
  staging/        # 1:1 with sources: renaming, casting, technical cleanup
  intermediate/   # joins and business logic
  marts/          # dim_ and fct_ tables for analysis
tests/            # singular tests
macros/           # reusable Jinja macros
scripts/          # synthetic data generator
docs/             # data specification
```

| File | Purpose |
|---|---|
| `dbt_project.yml` | Defines the project and how each layer is materialized |
| `profiles.yml` | Defines where dbt runs (a local DuckDB file, `dev.duckdb`) |

## Why the dbt project is created manually (not with `dbt init`)

- **Repo root layout.** `dbt init` creates a subfolder, so the dbt project would
  not live at the repository root. Creating the files by hand keeps one flat,
  predictable structure.
- **Self-contained repo.** `dbt init` writes `profiles.yml` to `~/.dbt/`, outside
  the repository. Here it lives in the repo, so anyone who clones it can run
  `dbt debug` without extra setup. dbt looks for `profiles.yml` in the current
  directory first.
- **No example code.** `dbt init` generates sample models that would have to be
  deleted.
- **Learning goal.** Writing each file shows what it does.

`profiles.yml` contains no secrets (DuckDB is a local file). For warehouses like
Snowflake, credentials must come from environment variables and never be committed.