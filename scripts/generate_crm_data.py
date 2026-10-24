"""Generate a deterministic, intentionally dirty Salesforce-like CRM export.

Source of truth: docs/data_spec.md. Writes 4 CSV files (all columns as text)
plus a dirt manifest to data/raw/.

Run: python scripts/generate_crm_data.py

How dirt is tracked
-------------------
Every row is a dict with hidden bookkeeping keys (prefixed "__") that are never
written to CSV:
  __tags    set of dirt tags applied to the row ("5", or "11:website" for a
            sub-category). The manifest is built from these tags.
  __locked  columns already overwritten by a dirt function, so a later dirt
            cannot silently clobber them and the manifest counts stay honest.
  __stale   True for the older copies created by dirt #1.
"""

import csv
import json
import random
import re
import string
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

from faker import Faker

SEED = 42
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# ---------------------------------------------------------------------------
# Schema and domain values
# ---------------------------------------------------------------------------
USER_COLS = ["id", "name", "email", "role", "is_active"]
ACCOUNT_COLS = [
    "id", "name", "website", "industry", "billing_country", "billing_state",
    "number_of_employees", "annual_revenue", "description", "parent_account_id",
    "owner_id", "created_at", "updated_at", "_synced_at", "_is_deleted",
]
CONTACT_COLS = [
    "id", "account_id", "first_name", "last_name", "email", "phone", "title",
    "mailing_country", "lead_source", "owner_id", "created_at", "updated_at",
    "_synced_at", "_is_deleted",
]
OPP_COLS = [
    "id", "account_id", "name", "stage_name", "deal_type", "amount",
    "currency_code", "close_date", "probability", "is_closed", "is_won",
    "owner_id", "created_at", "updated_at", "_synced_at", "_is_deleted",
]

N_USERS, N_ACCOUNTS, N_CONTACTS, N_OPPS = 12, 300, 800, 400

STAGES = ["Sourced", "First Meeting", "Diligence", "IC Review", "Term Sheet",
          "Closed Won", "Closed Lost", "Passed"]
STAGE_WEIGHTS = [14, 14, 12, 8, 6, 22, 12, 12]
STAGE_PROBABILITY = {"Sourced": 10, "First Meeting": 20, "Diligence": 40,
                     "IC Review": 60, "Term Sheet": 80, "Closed Won": 100,
                     "Closed Lost": 0, "Passed": 0}
DEAL_TYPES = ["Series A", "Series B", "Series C", "Growth Equity", "Secondary"]
LEAD_SOURCES = ["Conference", "Referral", "Inbound", "Outbound", "Banker"]
INDUSTRIES = ["Software", "Fintech", "Healthcare", "Logistics", "Energy",
              "Consumer", "Industrials", "Media", "Education", "Real Estate"]
SECTORS = ["Systems", "Technologies", "Capital", "Labs", "Health", "Logistics",
           "Networks", "Analytics", "Robotics", "Foods", "Energy", "Media"]
TITLES = ["CEO", "CFO", "CTO", "COO", "VP Finance", "VP Engineering",
          "Head of Strategy", "Founder", "Managing Director", "Controller"]
ROLES = ["Partner", "Principal", "Vice President", "Associate", "Analyst",
         "Operations"]
# Country -> states. Some values are non-ASCII on purpose (mojibake candidates).
COUNTRIES = {
    "United States": ["California", "New York", "Texas", "Massachusetts", "Illinois"],
    "United Kingdom": ["England", "Scotland"],
    "Germany": ["Bavaria", "Baden-Württemberg", "Berlin"],
    "Brazil": ["São Paulo", "Rio de Janeiro"],
    "France": ["Île-de-France", "Provence"],
    "Canada": ["Ontario", "Quebec"],
}
COUNTRY_WEIGHTS = [50, 12, 10, 10, 8, 10]
COUNTRY_VARIANTS = {
    "United States": ["US", "USA", "U.S.", "united states", "UNITED STATES"],
    "United Kingdom": ["UK", "U.K.", "GB", "Great Britain"],
    "Germany": ["DE", "Deutschland", "GERMANY"],
    "Brazil": ["BR", "Brasil", "BRAZIL"],
    "France": ["FR", "FRANCE"],
    "Canada": ["CA", "CANADA"],
}
ACCENTED_FIRST = ["José", "André", "Gonçalo", "Zoë", "Renée", "Joaquín", "Sébastien"]
ACCENTED_LAST = ["Müller", "Gonçalves", "Núñez", "Çelik", "Sørensen", "Åkesson", "Lefèvre"]
PLACEHOLDERS = ["N/A", "null", "unknown", "-", ""]
CURRENCIES = ["USD", "EUR", "GBP"]
CURRENCY_WEIGHTS = [85, 10, 5]

ISO_TS = "%Y-%m-%dT%H:%M:%SZ"
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
SYNC_BASE = datetime(2026, 10, 23, 2, 0, 0)
ID_CHARS = string.ascii_letters + string.digits

DIRT_NAMES = {
    1: "Duplicate rows (same id, different _synced_at and one changed business field)",
    2: "Soft deletes (_is_deleted = true), some on the LATEST version of a duplicated id",
    3: "Orphan opportunities (account_id points to a missing account)",
    4: "Contacts without account_id (VALID data, not an error)",
    5: "Same company under different names/ids",
    6: "stage_name variants",
    7: "Website in mixed formats",
    8: "Country variants",
    9: "Phone in mixed formats",
    10: "Emails with uppercase/spaces; same email on 2 contacts",
    11: "Nulls (website, amount, account name)",
    12: "Dirty amount text / negative amounts",
    13: "MM/DD/YYYY dates",
    14: "close_date earlier than created_at",
    15: "Test records",
    16: "sf_contact._synced_at ~2 days older than other tables",
    17: "Placeholders instead of NULL",
    18: "currency_code null",
    19: "Closed Won with is_won = false",
    20: "updated_at later than _synced_at",
    21: "Mojibake in names/states",
}
DIRT_NOTES = {
    1: "Applied to the 3 tables that carry _synced_at; sf_user has no _synced_at.",
    4: "Valid data: must NOT fail tests.",
    16: "Whole table shifted (base rows and their stale copies).",
}

_used_ids = set()


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def new_id(prefix):
    """Return a unique 18-char Salesforce-style id with the given object prefix."""
    while True:
        value = prefix + "".join(random.choices(ID_CHARS, k=15))
        if value not in _used_ids:
            _used_ids.add(value)
            return value


def slug(text):
    """Lowercase ASCII-only alphanumeric slug (accents stripped)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_text.lower())


def rand_dt(start, end):
    """Random datetime between start and end (inclusive of seconds)."""
    return start + timedelta(seconds=random.randint(0, int((end - start).total_seconds())))


def new_row(**fields):
    """Build a row dict (all values as text) with hidden bookkeeping keys."""
    row = {k: str(v) for k, v in fields.items()}
    row.update({"__tags": set(), "__locked": set(), "__stale": False, "__has_stale": False})
    return row


def share(rows, fraction):
    """Number of rows for a given share (at least 1)."""
    return max(1, round(len(rows) * fraction))


def pick(rows, n, cols=(), tags=(), pred=None):
    """Randomly choose up to n base rows to dirty.

    Skips stale copies, rows where any of `cols` was already overwritten by
    another dirt, and rows failing `pred`. Locks `cols` and adds `tags` on the
    chosen rows, then returns them for the caller to mutate.
    """
    cols = set(cols)
    pool = [r for r in rows
            if not r["__stale"] and not (r["__locked"] & cols) and (pred is None or pred(r))]
    chosen = random.sample(pool, min(n, len(pool)))
    for r in chosen:
        r["__locked"] |= cols
        r["__tags"].update(tags)
    return chosen


def stage_flags(stage):
    """Return (probability, is_closed, is_won) as text for a canonical stage."""
    closed = stage in ("Closed Won", "Closed Lost", "Passed")
    return (str(STAGE_PROBABILITY[stage]), str(closed).lower(), str(stage == "Closed Won").lower())


# ---------------------------------------------------------------------------
# Clean table generators (one per table)
# ---------------------------------------------------------------------------
def generate_users(fake):
    """Generate sf_user rows (prefix 005). Users get no dirt."""
    rows = []
    for i in range(N_USERS):
        name = fake.name()
        rows.append(new_row(
            id=new_id("005"), name=name,
            email=f"{slug(name)}@growthfund.com",
            role=ROLES[i % len(ROLES)],
            is_active="false" if i in (5, 11) else "true",
        ))
    return rows


def generate_accounts(fake, users):
    """Generate sf_account rows (prefix 001); ~10% are subsidiaries of another account."""
    rows, used_cores = [], set()
    for _ in range(N_ACCOUNTS):
        core = f"{fake.last_name()} {random.choice(SECTORS)}"
        while core in used_cores:
            core = f"{fake.last_name()} {random.choice(SECTORS)}"
        used_cores.add(core)
        country = random.choices(list(COUNTRIES), COUNTRY_WEIGHTS)[0]
        created = rand_dt(datetime(2022, 1, 1), datetime(2026, 3, 31))
        updated = min(created + timedelta(days=random.randint(0, 120)), datetime(2026, 9, 30))
        row = new_row(
            id=new_id("001"), name=f"{core} Inc.", website=f"https://{slug(core)}.com",
            industry=random.choice(INDUSTRIES), billing_country=country,
            billing_state=random.choice(COUNTRIES[country]),
            number_of_employees=random.randint(10, 5000),
            annual_revenue=random.randint(5, 900) * 1_000_000,
            description=fake.catch_phrase(), parent_account_id="",
            owner_id=random.choice(users)["id"],
            created_at=created.strftime(ISO_TS), updated_at=updated.strftime(ISO_TS),
            _synced_at=(SYNC_BASE + timedelta(seconds=random.randint(0, 1800))).strftime(ISO_TS),
            _is_deleted="false",
        )
        row["__core"] = core
        rows.append(row)
    # Subsidiaries: pick a parent among the earlier accounts (no cycles).
    for i in random.sample(range(10, N_ACCOUNTS), share(rows, 0.10)):
        rows[i]["parent_account_id"] = random.choice(rows[:i])["id"]
    return rows


def generate_contacts(fake, accounts, users):
    """Generate sf_contact rows (prefix 003); ~10% carry accented names."""
    rows = []
    for i in range(N_CONTACTS):
        acct = random.choice(accounts)
        first = random.choice(ACCENTED_FIRST) if random.random() < 0.10 else fake.first_name()
        last = random.choice(ACCENTED_LAST) if random.random() < 0.10 else fake.last_name()
        start = datetime.strptime(acct["created_at"], ISO_TS)
        created = rand_dt(start, datetime(2026, 6, 30))
        updated = min(created + timedelta(days=random.randint(0, 120)), datetime(2026, 9, 30))
        digits = f"{random.randint(201, 989)}555{random.randint(0, 9999):04d}"
        rows.append(new_row(
            id=new_id("003"), account_id=acct["id"], first_name=first, last_name=last,
            email=f"{slug(first)}.{slug(last)}{i}@{slug(acct['__core'])}.com",
            phone=f"+1{digits}", title=random.choice(TITLES),
            mailing_country=acct["billing_country"],
            lead_source=random.choice(LEAD_SOURCES), owner_id=random.choice(users)["id"],
            created_at=created.strftime(ISO_TS), updated_at=updated.strftime(ISO_TS),
            _synced_at=(SYNC_BASE + timedelta(seconds=random.randint(0, 1800))).strftime(ISO_TS),
            _is_deleted="false",
        ))
    return rows


def generate_opportunities(accounts, users):
    """Generate sf_opportunity rows (prefix 006) with flags coherent with the stage."""
    rows = []
    for _ in range(N_OPPS):
        acct = random.choice(accounts)
        stage = random.choices(STAGES, STAGE_WEIGHTS)[0]
        prob, is_closed, is_won = stage_flags(stage)
        deal = random.choice(DEAL_TYPES)
        start = datetime.strptime(acct["created_at"], ISO_TS)
        created = rand_dt(start, datetime(2026, 6, 30))
        updated = min(created + timedelta(days=random.randint(0, 120)), datetime(2026, 9, 30))
        close = created.date() + timedelta(days=random.randint(30, 400))
        if is_closed == "true":
            close = min(close, date(2026, 10, 20))
        row = new_row(
            id=new_id("006"), account_id=acct["id"], name=f"{acct['__core']} - {deal}",
            stage_name=stage, deal_type=deal, amount=random.randint(50, 1500) * 100_000,
            currency_code=random.choices(CURRENCIES, CURRENCY_WEIGHTS)[0],
            close_date=close.isoformat(), probability=prob, is_closed=is_closed,
            is_won=is_won, owner_id=random.choice(users)["id"],
            created_at=created.strftime(ISO_TS), updated_at=updated.strftime(ISO_TS),
            _synced_at=(SYNC_BASE + timedelta(seconds=random.randint(0, 1800))).strftime(ISO_TS),
            _is_deleted="false",
        )
        row["__stage"] = stage  # canonical stage, used when building stale copies
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Dirt functions (one per dirt type). Each mutates rows in place.
# ---------------------------------------------------------------------------
def dirt_01_duplicates(rows, change_business_field):
    """#1: add ~3% stale copies of existing ids.

    The stale copy is an OLDER version: earlier _synced_at, updated_at reset to
    created_at, and one business field changed via `change_business_field`.
    The original row is therefore the latest version the dedup must keep.
    """
    new_rows = []
    for orig in random.sample(rows, share(rows, 0.03)):
        stale = dict(orig)
        stale["__locked"] = set(orig["__locked"])
        stale["__tags"] = {"1"} | (orig["__tags"] & {"16"})
        stale["__stale"], stale["__has_stale"] = True, False
        synced = datetime.strptime(orig["_synced_at"], ISO_TS)
        stale["_synced_at"] = (synced - timedelta(days=random.randint(1, 10),
                                                  minutes=random.randint(1, 600))).strftime(ISO_TS)
        stale["updated_at"] = orig["created_at"]
        change_business_field(stale, orig)
        orig["__has_stale"] = True
        new_rows.append(stale)
    rows.extend(new_rows)


def change_account_field(stale, orig):
    """Stale account copy had a different headcount."""
    stale["number_of_employees"] = str(int(orig["number_of_employees"]) + random.randint(5, 200))


def change_contact_field(stale, orig):
    """Stale contact copy had a different title."""
    stale["title"] = random.choice([t for t in TITLES if t != orig["title"]])


def change_opportunity_field(stale, orig):
    """Stale opportunity copy was in a different stage (flags stay coherent)."""
    stage = random.choice([s for s in STAGES if s != orig["__stage"]])
    stale["stage_name"] = stage
    stale["probability"], stale["is_closed"], stale["is_won"] = stage_flags(stage)


def dirt_02_soft_deletes(rows):
    """#2: ~4% of ids get _is_deleted = true on their latest version.

    At least 3 of them are ids that also have a stale (not deleted) copy, so a
    test can verify the latest version wins.
    """
    n = share([r for r in rows if not r["__stale"]], 0.04)
    pick(rows, 3, tags=("2",), pred=lambda r: r["__has_stale"])
    pick(rows, n - 3, tags=("2",), pred=lambda r: "2" not in r["__tags"])
    for r in rows:
        if "2" in r["__tags"]:
            r["_is_deleted"] = "true"
            if r["__has_stale"]:
                r["__tags"].add("2:latest_of_duplicated_id")


def dirt_03_orphan_opportunities(opps):
    """#3: ~2% of opportunities point to an account id that does not exist."""
    for r in pick(opps, share(opps, 0.02), ("account_id",), ("3",)):
        r["account_id"] = new_id("001")


def dirt_04_contacts_without_account(contacts):
    """#4: ~8% of contacts have no account_id (valid data)."""
    for r in pick(contacts, share(contacts, 0.08), ("account_id",), ("4",)):
        r["account_id"] = ""


def dirt_05_duplicate_companies(accounts):
    """#5: ~12% of accounts become a differently named copy of another company.

    The row keeps its own id but takes the website and a name variant
    ("Acme Inc.", "ACME Incorporated", "Acme, Inc") of another account.
    """
    chosen = pick(accounts, share(accounts, 0.12), ("name",), ("5",))
    targets = [a for a in accounts if "5" not in a["__tags"]]
    for r in chosen:
        target = random.choice(targets)
        core = target["__core"]
        r["name"] = random.choice([f"{core} Inc.", f"{core.upper()} Incorporated", f"{core}, Inc"])
        r["website"] = target["website"]


def dirt_06_stage_variants(opps):
    """#6: ~5% of stage_name values are spelled differently ("closed won", "ClosedWon")."""
    for r in pick(opps, share(opps, 0.05), ("stage_name",), ("6",),
                  pred=lambda r: r["stage_name"] in STAGES):
        s = r["stage_name"]
        options = {s.lower(), s.replace(" ", " - ", 1), s.replace(" ", "")} - {s}
        r["stage_name"] = random.choice(sorted(options))


def dirt_07_website_formats(accounts):
    """#7: ~25% of websites in mixed formats (scheme, www, trailing slash, uppercase)."""
    for r in pick(accounts, share(accounts, 0.25), ("website",), ("7",),
                  pred=lambda r: r["website"].startswith("https://")):
        host = r["website"][len("https://"):]
        r["website"] = random.choice([
            host, f"www.{host}", f"http://{host}", f"http://www.{host}/",
            f"https://{host}/", f"https://www.{host}", f"HTTPS://{host.upper()}",
        ])


def dirt_08_country_variants(accounts, contacts):
    """#8: ~30% of country values use US/USA/U.S./UK/... variants."""
    for rows, col in ((accounts, "billing_country"), (contacts, "mailing_country")):
        for r in pick(rows, share(rows, 0.30), (col,), ("8",),
                      pred=lambda r, c=col: r[c] in COUNTRY_VARIANTS):
            r[col] = random.choice(COUNTRY_VARIANTS[r[col]])


def dirt_09_phone_formats(contacts):
    """#9: ~40% of phones in mixed formats."""
    for r in pick(contacts, share(contacts, 0.40), ("phone",), ("9",),
                  pred=lambda r: re.fullmatch(r"\+1\d{10}", r["phone"])):
        d = r["phone"][2:]
        a, b, c = d[:3], d[3:6], d[6:]
        r["phone"] = random.choice([
            f"({a}) {b}-{c}", f"{a}-{b}-{c}", f"{a}.{b}.{c}", f"+1 {a} {b} {c}",
            f"1-{a}-{b}-{c} ext. {random.randint(1, 999)}", f"{a}{b}{c}",
        ])


def dirt_10_email_issues(contacts):
    """#10: ~6% emails with uppercase/spaces; ~3% share an email with another contact."""
    for r in pick(contacts, share(contacts, 0.03), ("email",), ("10", "10:shared_email")):
        r["email"] = random.choice([c for c in contacts if c is not r])["email"]
    for r in pick(contacts, share(contacts, 0.06), ("email",), ("10", "10:case_or_spaces"),
                  pred=lambda r: r["email"] == r["email"].strip().lower()):
        e = r["email"]
        r["email"] = random.choice([e.upper(), f" {e}", f"{e} ", e.capitalize()])


def dirt_11_nulls(accounts, opps):
    """#11: null website (~15%), null amount (~10%), account.name null in 2 rows."""
    for r in pick(accounts, share(accounts, 0.15), ("website",), ("11", "11:website")):
        r["website"] = ""
    for r in pick(opps, share(opps, 0.10), ("amount",), ("11", "11:amount")):
        r["amount"] = ""
    for r in pick(accounts, 2, ("name",), ("11", "11:account_name")):
        r["name"] = ""


def dirt_12_dirty_amounts(opps):
    """#12: ~3% amounts as text ("$12,500,000", "12.5M") and 2 negative amounts."""
    for r in pick(opps, share(opps, 0.03), ("amount",), ("12", "12:text"),
                  pred=lambda r: r["amount"].isdigit()):
        a = int(r["amount"])
        r["amount"] = random.choice([f"${a:,}", f"{a:,}.00", f"{a / 1e6:g}M", f"{a:,} USD"])
    for r in pick(opps, 2, ("amount",), ("12", "12:negative"),
                  pred=lambda r: r["amount"].isdigit()):
        r["amount"] = "-" + r["amount"]


def dirt_13_us_dates(accounts, contacts, opps):
    """#13: ~3% of date values per column in MM/DD/YYYY instead of ISO."""
    plan = ((accounts, ["created_at", "updated_at"]), (contacts, ["created_at", "updated_at"]),
            (opps, ["created_at", "updated_at", "close_date"]))
    for rows, cols in plan:
        for col in cols:
            for r in pick(rows, share(rows, 0.03), (col,), ("13",),
                          pred=lambda r, c=col: ISO_DATE_RE.match(r[c])):
                r[col] = datetime.strptime(r[col][:10], "%Y-%m-%d").strftime("%m/%d/%Y")


def dirt_14_close_before_created(opps):
    """#14: ~2% of opportunities have close_date earlier than created_at."""
    for r in pick(opps, share(opps, 0.02), ("close_date", "created_at"), ("14",)):
        created = datetime.strptime(r["created_at"], ISO_TS).date()
        r["close_date"] = (created - timedelta(days=random.randint(5, 180))).isoformat()


def dirt_15_test_records(accounts, contacts, opps):
    """#15: ~1% test records (Test Account, asdf, @example.com)."""
    names = ["Test Account", "asdf", "TEST ACCOUNT"]
    for i, r in enumerate(pick(accounts, share(accounts, 0.01), ("name",), ("15",))):
        r["name"] = names[i % len(names)]
    for i, r in enumerate(pick(contacts, share(contacts, 0.01),
                               ("first_name", "last_name", "email"), ("15",))):
        r["first_name"], r["last_name"] = ("Test", "User") if i % 2 else ("asdf", "asdf")
        r["email"] = f"test{i}@example.com"
    for i, r in enumerate(pick(opps, share(opps, 0.01), ("name",), ("15",))):
        r["name"] = "Test Opportunity" if i % 2 else "asdf"


def dirt_16_contact_sync_lag(contacts):
    """#16: sf_contact._synced_at is ~2 days older than the other tables."""
    for r in contacts:
        synced = datetime.strptime(r["_synced_at"], ISO_TS)
        r["_synced_at"] = (synced - timedelta(days=2)).strftime(ISO_TS)
        r["__tags"].add("16")


def dirt_17_placeholders(tables):
    """#17: ~3% of cells in text columns hold N/A, null, unknown, - or an empty string."""
    cols_by_table = {
        "sf_account": ["industry", "billing_country", "billing_state", "description", "website"],
        "sf_contact": ["first_name", "last_name", "phone", "title", "mailing_country", "lead_source"],
        "sf_opportunity": ["deal_type"],
    }
    for table, cols in cols_by_table.items():
        for col in cols:
            for r in pick(tables[table], share(tables[table], 0.03), (col,), ("17",)):
                r[col] = random.choice(PLACEHOLDERS)


def dirt_18_null_currency(opps):
    """#18: ~2% of opportunities have a null currency_code."""
    for r in pick(opps, share(opps, 0.02), ("currency_code",), ("18",)):
        r["currency_code"] = ""


def dirt_19_incoherent_flags(opps):
    """#19: ~2% of opportunities are Closed Won with is_won = false."""
    for r in pick(opps, share(opps, 0.02), ("is_won",), ("19",),
                  pred=lambda r: r["stage_name"] == "Closed Won" and r["is_won"] == "true"):
        r["is_won"] = "false"


def dirt_20_updated_after_sync(accounts, contacts, opps):
    """#20: ~1% of rows have updated_at later than _synced_at."""
    for rows in (accounts, contacts, opps):
        for r in pick(rows, share(rows, 0.01), ("updated_at",), ("20",)):
            synced = datetime.strptime(r["_synced_at"], ISO_TS)
            r["updated_at"] = (synced + timedelta(days=random.randint(1, 5))).strftime(ISO_TS)


def mojibake(text):
    """Simulate a UTF-8 file decoded as latin-1 ("São" -> "SÃ£o")."""
    return text.encode("utf-8").decode("latin-1")


def dirt_21_mojibake(accounts, contacts):
    """#21: ~1% of names/states are garbled by a wrong encoding."""
    def has_accent(col):
        return lambda r: any(ord(ch) > 127 for ch in r[col])

    for r in pick(contacts, share(contacts, 0.01), ("last_name",), ("21",), has_accent("last_name")):
        r["last_name"] = mojibake(r["last_name"])
    for r in pick(accounts, share(accounts, 0.01), ("billing_state",), ("21",), has_accent("billing_state")):
        r["billing_state"] = mojibake(r["billing_state"])


# ---------------------------------------------------------------------------
# Orchestration, manifest and output
# ---------------------------------------------------------------------------
def apply_dirt(tables):
    """Run every dirt function in an order that keeps their effects independent."""
    accounts, contacts, opps = tables["sf_account"], tables["sf_contact"], tables["sf_opportunity"]
    dirt_05_duplicate_companies(accounts)
    dirt_15_test_records(accounts, contacts, opps)
    dirt_04_contacts_without_account(contacts)
    dirt_03_orphan_opportunities(opps)
    dirt_19_incoherent_flags(opps)   # needs canonical stage_name, so before #6
    dirt_06_stage_variants(opps)
    dirt_11_nulls(accounts, opps)
    dirt_12_dirty_amounts(opps)
    dirt_17_placeholders(tables)
    dirt_18_null_currency(opps)
    dirt_10_email_issues(contacts)
    dirt_09_phone_formats(contacts)
    dirt_08_country_variants(accounts, contacts)
    dirt_07_website_formats(accounts)
    dirt_21_mojibake(accounts, contacts)
    dirt_14_close_before_created(opps)   # before #13 so ISO dates can be parsed
    dirt_16_contact_sync_lag(contacts)   # before #20 and #1 (stale copies inherit it)
    dirt_20_updated_after_sync(accounts, contacts, opps)
    dirt_13_us_dates(accounts, contacts, opps)
    # Duplicates and soft deletes go last: they operate on the final base rows.
    dirt_01_duplicates(accounts, change_account_field)
    dirt_01_duplicates(contacts, change_contact_field)
    dirt_01_duplicates(opps, change_opportunity_field)
    for rows in (accounts, contacts, opps):
        dirt_02_soft_deletes(rows)


def build_manifest(tables):
    """Count affected rows per dirt type and table from the row tags."""
    dirt = {}
    for n, name in DIRT_NAMES.items():
        entry = {"name": name, "rows_affected": {}, "total_rows": 0}
        if n in DIRT_NOTES:
            entry["note"] = DIRT_NOTES[n]
        breakdown = {}
        for table, rows in tables.items():
            for r in rows:
                for tag in r["__tags"]:
                    head, _, label = tag.partition(":")
                    if int(head) != n:
                        continue
                    if label:
                        sub = breakdown.setdefault(label, {})
                        sub[table] = sub.get(table, 0) + 1
                    else:
                        entry["rows_affected"][table] = entry["rows_affected"].get(table, 0) + 1
        entry["total_rows"] = sum(entry["rows_affected"].values())
        if breakdown:
            entry["breakdown"] = breakdown
        dirt[str(n)] = entry
    return {
        "seed": SEED,
        "row_counts": {f"{t}.csv": len(rows) for t, rows in tables.items()},
        "dirt": dirt,
    }


def write_outputs(tables, columns, manifest):
    """Write the 4 CSVs (text only, hidden keys dropped) and the manifest JSON."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for table, rows in tables.items():
        with open(OUT_DIR / f"{table}.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns[table], extrasaction="ignore",
                                    lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    with open(OUT_DIR / "_dirt_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def print_summary(manifest):
    """Print row counts per file and the dirt summary."""
    print("Row counts:")
    for name, n in manifest["row_counts"].items():
        print(f"  {name:<22}{n:>6}")
    print("\nDirt summary (rows affected per table):")
    for n, entry in manifest["dirt"].items():
        detail = ", ".join(f"{t}={c}" for t, c in entry["rows_affected"].items()) or "-"
        print(f"  #{n:>2} {entry['name']}\n      total={entry['total_rows']}  {detail}")
        for label, sub in entry.get("breakdown", {}).items():
            print(f"      - {label}: " + ", ".join(f"{t}={c}" for t, c in sub.items()))
        if "note" in entry:
            print(f"      note: {entry['note']}")


def main():
    """Generate clean tables, inject dirt, shuffle rows and write all outputs."""
    random.seed(SEED)
    Faker.seed(SEED)
    fake = Faker("en_US")

    users = generate_users(fake)
    accounts = generate_accounts(fake, users)
    contacts = generate_contacts(fake, accounts, users)
    opps = generate_opportunities(accounts, users)
    tables = {"sf_user": users, "sf_account": accounts, "sf_contact": contacts,
              "sf_opportunity": opps}
    columns = {"sf_user": USER_COLS, "sf_account": ACCOUNT_COLS, "sf_contact": CONTACT_COLS,
               "sf_opportunity": OPP_COLS}

    apply_dirt(tables)
    # Shuffle so the latest version of a duplicated id is not always the last row.
    for name in ("sf_account", "sf_contact", "sf_opportunity"):
        random.shuffle(tables[name])

    manifest = build_manifest(tables)
    write_outputs(tables, columns, manifest)
    print_summary(manifest)


if __name__ == "__main__":
    main()
