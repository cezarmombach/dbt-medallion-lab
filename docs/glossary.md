# Glossary

Short definitions of the terms used in this project. Updated at the end of each step.

## Git and GitHub
| Term | Meaning |
|---|---|
| Repository | A folder with full change history, tracked by Git |
| Branch | An isolated line of work that does not touch `main` until merged |
| Commit | A saved snapshot of changes, with a message |
| Conventional Commits | Commit message style: `type: description` (`feat`, `fix`, `docs`, `chore`, `build`) |
| Push / pull | Upload commits to / download commits from the remote (`origin`) |
| Pull request (PR) | A request on GitHub to merge a branch into another, with review and discussion |
| Approve vs. merge | Approve = reviewer agrees. Merge = the change actually enters `main` (usually done by the author) |

## dbt
| Term | Meaning |
|---|---|
| Model | A `.sql` file with one `SELECT`; dbt turns it into a view or table |
| Source | A declaration of a raw table dbt reads but does not build (`source()`) |
| `ref()` | Function that references another model; dbt infers execution order from it |
| Materialization | How a model is stored: `view` (saved query) or `table` (physical result) |
| `dbt_project.yml` | Defines the project and how each layer is materialized |
| `profiles.yml` | Defines where dbt runs (the connection / `target`) |
| Test | An assertion on data: `not_null`, `unique`, `relationships`, `accepted_values` |
| Freshness | A check that a source was loaded recently enough |
| Lineage | The dependency graph between sources and models |
| Macro | Reusable Jinja code, like a function for SQL |

## Data engineering
| Term | Meaning |
|---|---|
| Medallion architecture | Layers of increasing quality: bronze (raw), silver (clean), gold (analytics) |
| Staging / intermediate / marts | The dbt names for the medallion layers (staging+intermediate = silver, marts = gold) |
| ELT | Extract and Load raw data first, then Transform inside the warehouse (dbt is the T) |
| OLTP vs. OLAP | Transactional workloads (apps, SQLite, Postgres) vs. analytical workloads (DuckDB, Snowflake) |
| Warehouse | A database built for analytics at scale (Snowflake, BigQuery, Databricks) |
| DuckDB | Embedded, columnar, analytical database; here a free local stand-in for a warehouse |
| Soft delete | A row flagged as deleted (`_is_deleted = true`) instead of physically removed |
| Orphan record | A row whose foreign key points to a record that does not exist |
| Deduplication | Keeping one row per key, typically the latest (`row_number()` + `qualify`) |
| Fan-out | A join that multiplies rows (usually from duplicate keys) and inflates sums |
| Entity resolution | Deciding that different records refer to the same real-world entity |