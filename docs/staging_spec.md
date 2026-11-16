# Staging layer spec

Status: approved in the Spec-Driven Development brainstorm (block a).
Scope: `models/staging/crm/`. This document defines *what* the staging models must
deliver and *how to verify it*. It contains no SQL.

## 1. Goal

Turn the four raw CRM exports (`source('crm', ...)`, all columns text) into four
clean, typed views with **one row per live CRM record**, so that the
intermediate layer never has to deal with technical dirt (duplicates, soft
deletes, placeholders, formats, encoding).

Staging does **technical cleanup only**:

| Staging does | Staging does not |
|---|---|
| Rename, cast, standardize formats | Join entities (account <-> opportunity) |
| Turn placeholders into NULL | Merge duplicate companies (entity resolution) |
| Keep one row per id, drop soft deletes | Exclude rows for business reasons (test records, orphans) |
| Add **flags** describing a problem | Decide which conflicting field is right (#14, #19) |

The only join allowed in staging is with the **mapping seeds** (section 4), which
are lookup tables, not business entities.

## 2. Decisions

| ID | Decision | Rationale |
|---|---|---|
| D1 | Dedup first (latest `_synced_at` per id), **then** drop `_is_deleted = true` | Filtering first would "resurrect" an id whose latest version is deleted, by letting an older live copy win |
| D2 | Macro `clean_text` on **every** text column, as the first step: trim + placeholders -> NULL (case-insensitive). List kept in a project `var` | One rule, one place; later casts never see `"N/A"`. Accepted risk: a real value equal to a placeholder (e.g. a company named "Unknown") becomes NULL |
| D3 | `amount` parsed by macro `parse_amount`; negatives -> NULL + `is_amount_invalid = true`; original kept in `amount_raw`; source NULLs stay NULL | Amount feeds every money metric; we never guess (no `abs()`, no imputation) |
| D4 | Categorical values (stage, country) normalized via a **normalized key** + **mapping seed**; unmapped -> NULL, original in `*_raw` | New spellings are fixed by editing a CSV, not SQL; the stage seed also carries funnel attributes used in intermediate |
| D5 | Phone -> digits only (`phone_digits`), extension dropped, no country inference; original in `phone_raw` | Deterministic and comparable; E.164 would require guessing the country |
| D6 | Test records are **flagged** (`is_test_record`) in staging and **filtered** in intermediate | Keeps them auditable; propagating to child rows needs joins (intermediate); filtering here would create artificial orphans |
| D7 | Mojibake **fixed** by macro `fix_mojibake` (generated substitution pairs) + residue test (warn) | Otherwise "SÃ£o Paulo" and "São Paulo" become two values and break entity resolution |
| D8 | Email trimmed + lowercased; invalid format -> NULL, original in `email_raw`. Same email on different contact ids is **not** merged; uniqueness tested with severity `warn` | A shared email can be legitimate (assistant, shared inbox); wrongly merging people is worse than a known duplicate |
| D9 | Website reduced to its domain (`website_domain`); original in `website_raw` | The domain is the strongest key for account entity resolution (intermediate) |
| D10 | Casts are tolerant (`try_cast` / `try_strptime`): a bad value becomes NULL and a test reports it; the build never fails on a cast | A single bad cell must not stop the pipeline; tests make the loss visible |
| D11 | Primary key renamed to `<object>_id` (`account_id`, `contact_id`, `opportunity_id`, `user_id`); foreign keys keep their names | dbt Labs style convention; makes joins self-describing |

## 3. Processing order (every model with `_synced_at`)

```
source -> clean_text on all text columns -> dedup (latest _synced_at per id)
       -> drop _is_deleted = true -> casts and normalizations -> flags
```

- Dedup key: `id`. Order: `_synced_at` descending (cast to timestamp), tie-break
  `updated_at` descending. Ties on (`id`, `_synced_at`) are not expected and are
  tested (T-STG-02).
- `sf_user` has no `_synced_at` / `_is_deleted`: no dedup, no delete filter.
- `_is_deleted` is not exposed (always false after the filter). `_synced_at` is
  exposed as a timestamp, with the same name, as load metadata.

## 4. Macros, seeds and vars

These are *contracts*; implementation comes later.

### Project vars (`dbt_project.yml`)
| Var | Value |
|---|---|
| `placeholder_values` | `['n/a', 'null', 'unknown', '-', '']` (compared after trim + lower) |
| `test_record_names` | `['test account', 'asdf', 'test opportunity', 'test user']` (compared after trim + lower) |
| `test_email_domains` | `['example.com']` |

### Macros
| Macro | Input -> output | Rules |
|---|---|---|
| `clean_text(col)` | text -> text or NULL | `trim`; NULL if lower(value) is in `placeholder_values` |
| `normalize_key(col)` | text -> text | lowercase, remove every non-alphanumeric char. `"Closed - Won"` -> `closedwon`, `"U.S."` -> `us` |
| `parse_amount(col)` | text -> decimal(18,2) or NULL | remove `$`, `,`, spaces and a trailing currency code (`USD`/`EUR`/`GBP`); suffix `K`/`M`/`B` multiplies by 1e3/1e6/1e9; `try_cast`. Returns the signed number (negatives handled by the model, D3) |
| `parse_timestamp(col)` | text -> timestamp or NULL | try ISO (`YYYY-MM-DD[THH:MM:SS[Z]]`), then `MM/DD/YYYY` (midnight) |
| `parse_date(col)` | text -> date or NULL | same formats as `parse_timestamp`, returns a date |
| `clean_email(col)` | text -> text or NULL | trim + lower; NULL unless it matches `^[^@\s]+@[^@\s]+\.[a-z]{2,}$` |
| `phone_digits(col)` | text -> text or NULL | cut everything from `ext` / `x` (extension) onwards; keep digits only; NULL if fewer than 7 or more than 15 digits |
| `website_domain(col)` | text -> text or NULL | lower; remove scheme (`http://`, `https://`), leading `www.`, and anything from the first `/`, `?` or `#`; NULL if the result has no `.` or contains a space |
| `fix_mojibake(col)` | text -> text | undo UTF-8-read-as-Latin-1: replace each pair `Ã` + char U+0080..U+00BF with the char at code point +0x40, and each pair `Â` + char U+0080..U+00BF with that char. The 128 pairs are generated by a Jinja loop, **not typed by hand**, because some of them involve invisible control characters (e.g. `Ç` becomes `Ã` + U+0087) |

Order inside one column: `clean_text` -> `fix_mojibake` (free-text columns only)
-> type-specific macro.

### Seeds (`seeds/`)
| Seed | Columns | Content |
|---|---|---|
| `stage_mapping.csv` | `stage_key, stage_name, stage_order, is_closed, is_won` | One row per canonical stage (8). Because `normalize_key` collapses every variant (`closed won`, `Closed - Won`, `ClosedWon` -> `closedwon`), no variant rows are needed. `stage_order` 1..8 in spec order |
| `country_mapping.csv` | `country_key, country_code, country_name` | One row per known spelling: `us`, `usa`, `unitedstates` -> `US`; `uk`, `gb`, `greatbritain`, `unitedkingdom` -> `GB`; `de`, `germany`, `deutschland` -> `DE`; `br`, `brazil`, `brasil` -> `BR`; `fr`, `france` -> `FR`; `ca`, `canada` -> `CA` |

`country_code` is ISO 3166-1 alpha-2. `ca` means Canada (the column is a country,
never a state).

## 5. Rules per model and column

Legend: **clean** = `clean_text`. Every text column goes through `clean_text`;
the table only lists additional rules.

### `stg_crm__user` (source `sf_user`, grain: one row per user)
| Column | Type | Rule |
|---|---|---|
| `user_id` | varchar | from `id` |
| `user_name` | varchar | from `name` |
| `email` | varchar | `clean_email` |
| `role` | varchar | clean |
| `is_active` | boolean | `'true'` / `'false'` -> boolean |

### `stg_crm__account` (source `sf_account`, grain: one row per live account id)
| Column | Type | Rule | Dirt |
|---|---|---|---|
| `account_id` | varchar | from `id`; dedup + delete filter | #1, #2 |
| `account_name` | varchar | clean + `fix_mojibake`; NULL allowed (tested, warn) | #11, #17, #21 |
| `website_domain` | varchar | `website_domain(clean(website))` | #7, #11, #17 |
| `website_raw` | varchar | `website` as received | |
| `industry` | varchar | clean | #17 |
| `billing_country` | varchar | ISO-2 via `country_mapping` on `normalize_key`; unmapped -> NULL | #8, #17 |
| `billing_country_raw` | varchar | as received | |
| `billing_state` | varchar | clean + `fix_mojibake` | #17, #21 |
| `number_of_employees` | integer | `try_cast` | |
| `annual_revenue` | decimal(18,2) | `try_cast` | |
| `description` | varchar | clean + `fix_mojibake` | #17 |
| `parent_account_id` | varchar | clean (empty -> NULL) | |
| `owner_id` | varchar | clean | |
| `created_at`, `updated_at` | timestamp | `parse_timestamp` | #13 |
| `_synced_at` | timestamp | `parse_timestamp` | |
| `is_test_record` | boolean | lower(trim(`account_name`)) in `test_record_names` | #15 |

### `stg_crm__contact` (source `sf_contact`, grain: one row per live contact id)
| Column | Type | Rule | Dirt |
|---|---|---|---|
| `contact_id` | varchar | from `id`; dedup + delete filter | #1, #2 |
| `account_id` | varchar | clean; **NULL is valid** | #4 |
| `first_name`, `last_name` | varchar | clean + `fix_mojibake` | #17, #21 |
| `email` | varchar | `clean_email`; may repeat across contacts | #10 |
| `email_raw` | varchar | as received | |
| `phone_digits` | varchar | `phone_digits(clean(phone))` | #9, #17 |
| `phone_raw` | varchar | as received | |
| `title` | varchar | clean | #17 |
| `mailing_country` | varchar | ISO-2 via `country_mapping`; unmapped -> NULL | #8, #17 |
| `mailing_country_raw` | varchar | as received | |
| `lead_source` | varchar | clean | #17 |
| `owner_id` | varchar | clean | |
| `created_at`, `updated_at` | timestamp | `parse_timestamp` | #13 |
| `_synced_at` | timestamp | `parse_timestamp` | #16 |
| `is_test_record` | boolean | email domain in `test_email_domains`, **or** lower(`first_name` + ' ' + `last_name`) in `test_record_names`, **or** both names = `asdf` | #15 |

### `stg_crm__opportunity` (source `sf_opportunity`, grain: one row per live opportunity id)
| Column | Type | Rule | Dirt |
|---|---|---|---|
| `opportunity_id` | varchar | from `id`; dedup + delete filter | #1, #2 |
| `account_id` | varchar | clean; not validated here (orphans handled in intermediate) | #3 |
| `opportunity_name` | varchar | clean | |
| `stage_name` | varchar | canonical name via `stage_mapping` on `normalize_key`; unmapped -> NULL | #6 |
| `stage_name_raw` | varchar | as received | |
| `deal_type` | varchar | clean | #17 |
| `amount` | decimal(18,2) | `parse_amount`; NULL if parsed value <= 0 | #11, #12 |
| `amount_raw` | varchar | as received | |
| `is_amount_invalid` | boolean | true when `amount_raw` is not NULL but `amount` is NULL (unparseable or <= 0) | #12 |
| `currency_code` | varchar | clean + upper; NULL kept (decision deferred to intermediate) | #18 |
| `close_date` | date | `parse_date` | #13, #14 |
| `probability` | integer | `try_cast` | |
| `is_closed`, `is_won` | boolean | as received, cast only (coherence checked in intermediate) | #19 |
| `owner_id` | varchar | clean | |
| `created_at`, `updated_at` | timestamp | `parse_timestamp` | #13, #14 |
| `_synced_at` | timestamp | `parse_timestamp` | |
| `is_test_record` | boolean | lower(trim(`opportunity_name`)) in `test_record_names` | #15 |

## 6. Dirt coverage (all 21)

Action: **fix** = value corrected; **flag** = boolean column added; **filter** =
row removed; **test** = only asserted; **defer** = handled in another layer.

| # | Dirt | Layer | Action | How |
|---|---|---|---|---|
| 1 | Duplicate ids, different `_synced_at` | staging | fix | latest `_synced_at` per id (D1) |
| 2 | Soft deletes | staging | filter | after dedup (D1) |
| 3 | Orphan `opportunity.account_id` | intermediate | defer | staging only tests (warn) |
| 4 | Contact without `account_id` | staging | test | valid; no `not_null`; `relationships` ignores NULLs |
| 5 | Same company, different names/ids | intermediate | defer | entity resolution; staging provides `website_domain` |
| 6 | `stage_name` variants | staging | fix | `normalize_key` + `stage_mapping` (D4) |
| 7 | Website formats | staging | fix | `website_domain` (D9) |
| 8 | Country variants | staging | fix | `normalize_key` + `country_mapping` (D4) |
| 9 | Phone formats | staging | fix | `phone_digits` (D5) |
| 10 | Email case/spaces; shared email | staging | fix + test | `clean_email`; uniqueness warn (D8); flag in intermediate |
| 11 | Nulls (website, amount, account name) | staging | test | kept NULL, no imputation; `account_name` not_null warn |
| 12 | Dirty / negative amount | staging | fix + flag | `parse_amount`, `is_amount_invalid` (D3) |
| 13 | MM/DD/YYYY dates | staging | fix | `parse_timestamp` / `parse_date` |
| 14 | `close_date` < `created_at` | intermediate | defer | staging only types the columns |
| 15 | Test records | staging -> intermediate | flag -> filter | `is_test_record` (D6) |
| 16 | Contact sync ~2 days behind | source | test | source freshness (already in `_crm__sources.yml`) |
| 17 | Placeholders | staging | fix | `clean_text` (D2) |
| 18 | `currency_code` NULL | intermediate | defer | staging keeps NULL |
| 19 | Closed Won with `is_won = false` | intermediate | defer | staging keeps the raw flags |
| 20 | `updated_at` > `_synced_at` | staging | test | warn; dedup does not depend on `updated_at` |
| 21 | Mojibake | staging | fix + test | `fix_mojibake` + residue test warn (D7) |

## 7. Acceptance criteria

Each criterion is verifiable with a query or `dbt build`, using the data from
`python scripts/generate_crm_data.py` (seed 42). Counts come from
`data/raw/_dirt_manifest.json`.

| ID | Criterion |
|---|---|
| AC-01 | Each staging model has exactly one row per primary key (`unique` + `not_null` pass) |
| AC-02 | Row counts: `stg_crm__user` = 12, `stg_crm__account` = 288, `stg_crm__contact` = 768, `stg_crm__opportunity` = 384 (base rows minus soft-deleted ids) |
| AC-03 | No id whose latest source version has `_is_deleted = true` appears in staging (the 4 + 5 + 4 ids tagged `latest_of_duplicated_id` included) |
| AC-04 | For every id kept, the staging row equals the source row with the greatest `_synced_at` |
| AC-05 | No text column of any staging model contains a value whose trim + lower is in `placeholder_values`. Exception: `*_raw` columns, which keep the original value on purpose |
| AC-06 | `stage_name` is never NULL and is always one of the 8 canonical stages |
| AC-07 | `billing_country` and `mailing_country` are NULL or one of `US, GB, DE, BR, FR, CA`; none is NULL when the raw value is a known spelling |
| AC-08 | `amount` is NULL or > 0. Exactly 2 rows have `is_amount_invalid = true` with a negative `amount_raw`; the 12 text amounts (`$12,500,000`, `12.5M`, `12,500,000 USD`, ...) are parsed to the correct number |
| AC-09 | `email` is NULL or matches the email regex, and is lowercase with no spaces |
| AC-10 | `phone_digits` is NULL or matches `^[0-9]{7,15}$`; extensions are not included |
| AC-11 | `website_domain` is NULL or lowercase, with no scheme, no `www.`, no `/` |
| AC-12 | No `created_at`, `updated_at`, `_synced_at` or `close_date` is NULL when the raw value is ISO or `MM/DD/YYYY` |
| AC-13 | No name/state column contains `Ã` or `Â` followed by a char in U+0080..U+00BF; `São Paulo`, `Baden-Württemberg`, `Müller`, `Çelik` appear correctly spelled |
| AC-14 | `is_test_record = true` for the 3 accounts, 8 contacts and 4 opportunities generated as test records (manifest #15), and for no other row |
| AC-15 | Contacts with NULL `account_id` (dirt #4) are present in `stg_crm__contact` and no test with severity `error` fails because of them |
| AC-16 | `dbt build --select staging` finishes with zero errors; warnings are only the ones listed as `warn` in section 8 |

## 8. Expected tests

Generic tests live in `models/staging/crm/_crm__models.yml`; singular tests in
`tests/staging/`. Severity choice: **error** = a staging guarantee is broken;
**warn** = known, tolerated dirt that is documented or handled downstream. Block
(d) of the brainstorm may refine this list.

| ID | Test | Target | Severity | AC |
|---|---|---|---|---|
| T-STG-01 | `unique` + `not_null` | PK of the 4 models | error | AC-01 |
| T-STG-02 | singular: no (`id`, `_synced_at`) tie in source | 3 sources | error | AC-04 |
| T-STG-03 | singular: no latest-deleted id in staging | account, contact, opportunity | error | AC-03 |
| T-STG-04 | generic `no_placeholders` | every text column except `*_raw` | error | AC-05 |
| T-STG-05 | `not_null` + `accepted_values` (8 stages) | `stage_name` | error | AC-06 |
| T-STG-06 | `accepted_values` (ISO-2 list) | `billing_country`, `mailing_country` | error | AC-07 |
| T-STG-07 | singular: raw country present but mapped country NULL | account, contact | warn | AC-07 |
| T-STG-08 | expression `amount > 0` (NULL allowed) | `amount` | error | AC-08 |
| T-STG-09 | regex | `email`, `phone_digits`, `website_domain` | error | AC-09..11 |
| T-STG-10 | singular: raw date present but parsed date NULL | all date columns | error | AC-12 |
| T-STG-11 | singular: mojibake residue | name/state/description columns | warn | AC-13 |
| T-STG-12 | `unique` | `stg_crm__contact.email` | warn | D8 |
| T-STG-13 | `not_null` | `stg_crm__account.account_name` | warn | #11 |
| T-STG-14 | singular: `updated_at` > `_synced_at` | account, contact, opportunity | warn | #20 |
| T-STG-15 | `relationships` to `stg_crm__user` | every `owner_id` | error | |
| T-STG-16 | `relationships` to `stg_crm__account` | `opportunity.account_id`, `contact.account_id` | warn | #3, #4 |
| T-STG-17 | `accepted_values` | `deal_type` (5), `lead_source` (5), `currency_code` (3), all NULL-tolerant | error | |

## 9. Out of scope

- Entity resolution of accounts, orphans, incoherent flags, `close_date` <
  `created_at`, currency defaults, filtering of test records: **intermediate spec**.
- Merging contacts that share an email.
- Converting amounts to USD (exchange rates).
- E.164 phone formatting or phone country inference.
- Normalizing `billing_state`, `industry`, `title` beyond placeholders and mojibake.
- Fixing the encoding at ingestion (in production this is the right fix; here
  there is no ingestion team).
- Incremental materialization or snapshots: all staging models are views.
