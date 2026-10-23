# Synthetic Salesforce CRM data spec

## Goal
Generate a deterministic, realistic RAW-layer export of a Salesforce-like CRM
for a growth equity firm. Output: 4 CSV files in `data/raw/`. All columns are
written as text (no types), as in a real file export.

## Reproducibility
- Use `Faker.seed(42)` and `random.seed(42)`. Same output on every run.
- Script path: `scripts/generate_crm_data.py`. Run: `python scripts/generate_crm_data.py`.

## Tables
| File | Rows (before dirt) | Key columns |
|---|---|---|
| sf_user.csv | 12 | id (005...), name, email, role, is_active |
| sf_account.csv | 300 | id (001...), name, website, industry, billing_country, billing_state, number_of_employees, annual_revenue, description, parent_account_id, owner_id, created_at, updated_at |
| sf_contact.csv | 800 | id (003...), account_id, first_name, last_name, email, phone, title, mailing_country, lead_source, owner_id, created_at, updated_at |
| sf_opportunity.csv | 400 | id (006...), account_id, name, stage_name, deal_type, amount, currency_code, close_date, probability, is_closed, is_won, owner_id, created_at, updated_at |

Account, contact and opportunity also carry `_synced_at` and `_is_deleted`.
IDs are 18 chars, with the Salesforce object prefix.

## Relationships
- account.owner_id, contact.owner_id, opportunity.owner_id -> user.id
- contact.account_id, opportunity.account_id -> account.id
- account.parent_account_id -> account.id (subsidiaries, ~10% of accounts)

## Domain values
- stage_name: Sourced, First Meeting, Diligence, IC Review, Term Sheet, Closed Won, Closed Lost, Passed
- deal_type: Series A, Series B, Series C, Growth Equity, Secondary
- amount: 5M to 150M (USD mostly), currency_code in USD/EUR/GBP
- lead_source: Conference, Referral, Inbound, Outbound, Banker

## Intentional dirt (approximate share)
1. Duplicate rows with the same id and different _synced_at (~3%, all tables)
2. Soft deletes: _is_deleted = true (~4%); a few ids whose LATEST version is deleted
3. Orphans: opportunity.account_id pointing to a missing account (~2%)
4. Contacts without account_id (~8%) - valid, must NOT fail tests
5. Same company, different names/ids: "Acme Inc.", "ACME Incorporated", "Acme, Inc" (~12% of accounts)
6. stage_name variants: "closed won", "Closed - Won", "ClosedWon" (~5%)
7. website in mixed formats: with/without scheme, www, trailing slash, uppercase (~25%)
8. Country variants: US, USA, United States, U.S.
9. Phone in mixed formats (~40%)
10. Emails with uppercase/spaces; same email on 2 contacts (~3%)
11. Nulls: website ~15%, amount ~10%, account.name null in 2 rows
12. amount as dirty text ("$12,500,000", "12.5M") in ~3%; 2 negative values
13. Dates: ISO mostly, MM/DD/YYYY in ~3%
14. close_date earlier than created_at (~2%)
15. Test records: "Test Account", "asdf", emails @example.com (~1%)
16. sf_contact._synced_at ~2 days older than the other tables (freshness warn)
17. Placeholders instead of NULL: "N/A", "null", "unknown", "-", "" (~3% in text columns)
18. currency_code null in ~2% of opportunities
19. Incoherent flags: Closed Won with is_won = false (~2%)
20. updated_at later than _synced_at (~1%)
21. Mojibake in names/cities (e.g. "SÃ£o Paulo") (~1%)

## Output
- Print a summary at the end: row count per file and count of each dirt type injected.
- Do not commit CSVs (data/raw/ is gitignored).