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
| Seed | A small CSV versioned in the repo (`seeds/`) that `dbt seed` loads as a table; used for mappings |
| Var | A project-level variable (`vars:` in `dbt_project.yml`) read in SQL with `var()` |
| Severity | How a failing test is reported: `error` fails the build, `warn` only reports |
| Generic vs. singular test | Generic = reusable, declared in YAML (`unique`). Singular = a one-off `.sql` in `tests/` returning bad rows |
| `try_cast` | A cast that returns `NULL` instead of failing when the value cannot be converted |

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
| Grain | What one row of a table represents (e.g. "one live account per id"). Every model must state it |
| Resurrection (anti-pattern) | Filtering soft deletes *before* dedup, which lets an older live copy of a deleted id survive |
| Placeholder | A fake value standing for "no value": `N/A`, `null`, `unknown`, `-`, `""`. Turned into a real `NULL` in staging |
| Normalized key | A technical version of a value used only for matching: lowercase, no spaces or punctuation (`Closed - Won` -> `closedwon`) |
| Mapping table | A lookup that translates every known variant of a value to one canonical value (here, a dbt seed) |
| Canonical value | The single official form of a value (`Closed Won`, ISO-2 country `US`) |
| Audit column (`_raw`) | A column keeping the original value next to the cleaned one, so a cleaning rule can be checked or redone |
| Flag | A boolean column (`is_...`, `has_...`) that marks a problem without removing the row |
| Mojibake | Garbled text from reading bytes with the wrong encoding: UTF-8 read as Latin-1 turns `São` into `SÃ£o` |
| Test record | A row created to test the CRM (`Test Account`, `asdf`, `@example.com`), not real business data |
| ISO 3166-1 alpha-2 | The standard 2-letter country codes (`US`, `GB`, `BR`) |
| E.164 | The international phone format `+<country code><number>` (e.g. `+14155550199`) |

## Spec-Driven Development
| Term | Meaning |
|---|---|
| Spec-Driven Development (SDD) | Writing and agreeing on a specification (what and how to verify) before writing code |
| Spec | A document with goal, decisions, rules, acceptance criteria, expected tests and out-of-scope items |
| Acceptance criterion | A verifiable statement that must be true when the work is done (e.g. "`stg_crm__account` has one row per id") |
| Out of scope | What the spec explicitly does not cover, so nobody assumes it was done |