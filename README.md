# The Vape Workshop Lab

Open-source Flask application for **The Vape Workshop** recipe/mixing tools and **Old Goat Lab** production, costing, inventory and scraper workflows.

> **Important:** this repository contains application code, schema and migrations. It does **not** contain the live production database, real `.env` files, passwords or other deployment secrets.

## What it does

The project has two related areas:

| Area | Responsibility |
|---|---|
| **Workshop (TVW)** | Recipes, recipe versions, Guided Mix, personal mixes, recipe visibility and community-facing recipe tools |
| **Lab** | Inventory/reference data, scraper integrations, recipe costing, Production Jobs, production pay and job profitability |

Production Jobs are Lab-only. A batch affects a Production Job only when it is launched through **Mix for this job**. Normal Workshop mixes remain separate.

Lab modules are controlled by `TVW_ENABLE_LAB_MODULES`.

## Repository layout

```text
app/                 Flask application
shared_mix_core/     Shared mixing calculations and Guided Mix logic
docs/                User, database and deployment documentation
requirements.txt     Root dependency entry point
.env.example         Safe environment-variable template
```

## Requirements

Recommended:

- Python 3.12 or 3.13
- SQLite
- A Python virtual environment
- For production on Linux: Gunicorn + Nginx

Python 3.14+ has not been the primary development target and may encounter dependency-wheel compatibility issues.

## Quick start — Windows

From the repository root:

```bat
cd app
py -3.13 -m venv venv
venv\Scripts\pip install -r requirements.txt
copy ..\.env.example .env
python app.py
```

Or use one of the included launchers:

- `app\Start_TVW_Calculator.bat`
- `app\Start_Old_Goat_Lab.bat`
- `app\Start_TVW_Device_Testing.bat`

Default local URL:

```text
http://127.0.0.1:5000/recipes
```

## Quick start — Linux

```bash
git clone https://github.com/Old-Goat-Vape/Vape-Workshop-Lab.git
cd Vape-Workshop-Lab

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example app/.env
cd app
../venv/bin/python app.py
```

For an Internet-facing deployment, do not expose the Flask development server directly. See the deployment documentation below.

## Environment variables

Copy `.env.example` to `app/.env`. Never commit the real file.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session signing key. Use a long random value in production. |
| `DATABASE_PATH` | Absolute path to the SQLite database. |
| `TVW_LOCAL_DATA_DIR` | Optional parent directory for local database storage. |
| `TVW_ENABLE_LAB_MODULES` | `1` enables Lab modules; `0` runs Workshop-only. |
| `FLASK_USE_RELOADER` | Prefer `0` when using SQLite. |
| `TVW_OLD_GOAT_PASSWORD` | Optional first-boot account password override. |
| `PYTHONUTF8` | Enables UTF-8 mode where needed. |

## Database and privacy

SQLite is the datastore. Startup migrations are designed to create/update the schema when required.

The live database is intentionally excluded from Git because it may contain user accounts, password hashes, recipes, Production Jobs, costing records, purchases and other operational data.

The following must never be committed:

- `*.db`, `*.sqlite*` and SQLite WAL/SHM sidecars
- `.env` and real credentials
- private keys/certificates
- local backups and release archives containing data

See [Database Handoff & Backup Guide](docs/DATABASE_HANDOFF.md).

## Production deployment

The intended first production deployment uses:

- Ubuntu
- one Gunicorn worker
- Nginx as a reverse proxy
- SQLite stored outside the Git working tree
- a production `SECRET_KEY`
- HTTPS

Start with one Gunicorn worker because the current application uses SQLite and Lab scheduling logic in the application process.

See:

- [Server deployment guide](docs/PAULY_DEPLOYMENT_GUIDE.md)
- [Database handoff & backup guide](docs/DATABASE_HANDOFF.md)
- [Calculator user guide](docs/CALCULATOR_USER_GUIDE.md)
- [Detailed Ubuntu notes](app/README_UBUNTU_SERVER_DEPLOY.txt)

## Scrapers and costing

The Lab contains scraper integrations for supplier/reference pricing and flavour data. Scraper results are written to SQLite and are therefore not part of the repository.

Supplier prices used by costing logic should come from stored/scraped data rather than hard-coded live prices.

## Contributing

Bug fixes, improvements, documentation and sensible feature additions are welcome.

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes.

## Security

Do not post real passwords, session cookies, API keys, `.env` contents or production database files in issues, pull requests or screenshots.

If you discover a security issue, contact the repository owner privately rather than publishing exploit details in an issue.

## License

This project is released under the [MIT License](LICENSE).
