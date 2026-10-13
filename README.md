# The Vape Workshop Lab

Flask application for **The Vape Workshop** (community recipes & Guided Mix) and **Old Goat Lab** (inventory, scrapers, costing, Production Jobs).

Private repository: [Old-Goat-Vape/Vape-Workshop-Lab](https://github.com/Old-Goat-Vape/Vape-Workshop-Lab)

## Workshop vs Lab

| Area | Responsibility |
|------|----------------|
| **Workshop (TVW)** | Recipes, recipe versions, Guided Mix, personal mixes, recipe privacy/visibility, community surfaces |
| **Lab** | Inventory / reference library, scrapers, recipe costing, BLCK variant pricing, **Production Jobs**, production pay, job inventory & costs |

Production Jobs are **Lab-only**. A batch affects a job only when launched with **Mix for this job**. Personal Workshop mixes stay isolated (`production_job_id` null).

Lab modules are gated by `TVW_ENABLE_LAB_MODULES`.

## Repository layout

```
app/                 # Flask app (run from here)
shared_mix_core/     # Shared mix math used by Guided Mix
requirements.txt     # Points at app/requirements.txt
.env.example         # Safe env placeholders (copy to app/.env)
```

Do **not** commit SQLite databases, `.env`, or `venv/`. Live data normally lives under `%LOCALAPPDATA%\TheVapeWorkshopCalculator\` on Windows.

## Python setup

Recommended: **Python 3.12 or 3.13** (3.14+ may fail some wheels).

```bat
cd app
py -3.13 -m venv venv
venv\Scripts\pip install -r requirements.txt
```

Or use the Windows launchers (they create `venv` and install deps on first run):

- `app\Start_TVW_Calculator.bat` — Workshop + Lab bridge on (`TVW_ENABLE_LAB_MODULES=1`)
- `app\Start_Old_Goat_Lab.bat` — same Lab-oriented start
- `app\Start_TVW_Device_Testing.bat` — LAN testing (see `README_DEVICE_TESTING.txt`)

Default URL: `http://127.0.0.1:5000/recipes`

## Environment variables

Copy `.env.example` to `app/.env` (or copy `app/ENV_TEMPLATE.txt`). Never commit real secrets.

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | Flask session signing — set a long random value in production |
| `DATABASE_PATH` | Optional absolute path to SQLite file; blank = local app data dir |
| `TVW_LOCAL_DATA_DIR` | Optional parent directory for the DB |
| `TVW_ENABLE_LAB_MODULES` | `1` Lab on, `0` Workshop-only |
| `FLASK_USE_RELOADER` | Prefer `0` to avoid duplicate SQLite connections |
| `TVW_OLD_GOAT_PASSWORD` | Optional first-boot Old Goat password override |
| `PYTHONUTF8` | Prefer `1` on Windows consoles |

See `app/LAUNCH_QA_ENV.txt` for QA / TVW-only modes.

## Database & migrations

- SQLite is the datastore.
- On startup, `migrations.py` runs idempotent `CREATE TABLE IF NOT EXISTS` / column adds.
- Fresh installs also apply `schema.sql` when needed, then migrations.
- **Never commit** `*.db`, WAL/SHM sidecars, or `instance/` DB copies.

## Scrapers

Lab scrapers (BLCK Shopify `products.json`, Clyrolinx, VC flavours, etc.) use:

- `requests`
- `beautifulsoup4`

Declared in `app/requirements.txt`. Scrapers write into the local DB; temporary HTML/cache must stay out of git (see `.gitignore`).

BLCK variant pricing feeds canonical VG/PG/nicotine costing — prices come from scrape, not hard-coded Rands.

## Production Jobs (Lab)

Named manufacturing projects (e.g. Trinidad, Poison Rally):

- Job recipe requirements with **locked** recipe versions
- **Mix for this job** → Guided Mix with explicit job linkage
- Operator = logged-in user (no separate identity selector)
- Job inventory (separate from personal stash)
- Production pay credits + payment ledger
- Job costs / profitability tab

Entry: `/lab/production-jobs` (requires Lab modules + production-jobs capability/role).

Helper accounts (e.g. Riku) use `user_capabilities` such as `lab.production_jobs.view` / `lab.production_jobs.produce` without Master Admin access.


## Pauly / server handover

Practical handover docs:

- [Pauly deployment & server guide](docs/PAULY_DEPLOYMENT_GUIDE.md)
- [Calculator user guide](docs/CALCULATOR_USER_GUIDE.md)
- [Database handoff & backup guide](docs/DATABASE_HANDOFF.md)

**Important:** the live SQLite database is deliberately **not stored in GitHub**. Git contains the application code, schema and migrations. If the deployed website must include Wynand's existing recipes, users, Production Jobs, scraper/pricing history and other live records, the confirmed active SQLite database must be transferred to the server separately and securely. Never commit the live DB to this repository.

## Deployment notes

1. Set `SECRET_KEY` in the environment (do not rely on the development fallback in code).
2. Set `TVW_ENABLE_LAB_MODULES` appropriately for the host.
3. Point `DATABASE_PATH` / `TVW_LOCAL_DATA_DIR` at a durable location outside OneDrive if possible.
4. Install deps from `app/requirements.txt` into a venv.
5. Prefer the documented start scripts or `python app.py` from the `app/` directory.
6. Ubuntu notes: `app/README_UBUNTU_SERVER_DEPLOY.txt`.
7. Release ZIP packaging is local-only (`release/` is gitignored).

## Development docs

- `CURSOR_RULES.md`, `PROJECT_VISION.md`, `PRODUCT_ROADMAP.md`, `DESIGN_LANGUAGE.md`
- Guided Mix: `app/docs/` (bench rules, QA notes)
