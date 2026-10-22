# Server Installation & Handover Guide

This is the practical deployment guide for **The Vape Workshop Lab**.

The project is a Python/Flask application. It contains two related areas:

- **Workshop** — recipes, recipe versions, recipe library, Guided Mix, personal mixes and community-facing recipe tools.
- **Lab** — costing, inventory/reference data, scrapers, Production Jobs, production pay and job profitability.

For a full Workshop + Lab deployment, enable the Lab modules.

---

## 1. Important: the live database is NOT in GitHub

The repository deliberately ignores:

- `*.db`
- `*.sqlite*`
- `instance/`
- `.env`
- virtual environments
- logs and local release files

That is intentional. GitHub contains the **application code, schema and migrations**, not Wynand's live data.

A clean server can create a fresh SQLite database automatically, but a fresh database will **not contain existing recipes, users, Production Jobs, scraper results, pricing history or other live records**.

If the website must start with existing data, the active SQLite database must be transferred to the server separately and securely. See `docs/DATABASE_HANDOFF.md`.

Do not add the live database to Git.

---

## 2. Recommended server

Current deployment notes target:

- Ubuntu Server 24.04 LTS
- Python 3.12 or 3.13
- Nginx
- Gunicorn
- SQLite

The existing detailed Ubuntu notes are in:

`app/README_UBUNTU_SERVER_DEPLOY.txt`

For the first public deployment, keep the application simple and conservative because SQLite and the Lab scheduler both perform writes. Start with **one application worker** unless the scheduler/database coordination is changed for multi-process deployment.

---

## 3. Clone the repository

Example:

```bash
cd /opt
sudo git clone https://github.com/Old-Goat-Vape/Vape-Workshop-Lab.git tvw
sudo chown -R $USER:$USER /opt/tvw
cd /opt/tvw
```

The repository is public, so cloning over HTTPS does not require GitHub authentication.

---

## 4. Create the Python environment

From the repository root:

```bash
cd /opt/tvw
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

The root `requirements.txt` points at `app/requirements.txt`.

For a production web server, install Gunicorn if it is not already included in the active production requirements:

```bash
pip install gunicorn
```

---

## 5. Environment configuration

Create:

`/opt/tvw/app/.env`

Use `.env.example` as the reference.

Minimum production settings:

```env
SECRET_KEY=<long-random-secret>
DATABASE_PATH=/var/lib/tvw/tvw_calculator.db
TVW_ENABLE_LAB_MODULES=1
FLASK_ENV=production
FLASK_USE_RELOADER=0
PYTHONUTF8=1
```

Never commit the real `.env`.

Create the database directory:

```bash
sudo mkdir -p /var/lib/tvw
sudo chown -R $USER:$USER /var/lib/tvw
```

If you supply an existing live database, place it at the path used by `DATABASE_PATH` before starting the production service.

---

## 6. First start / database migrations

Run from the `app/` directory:

```bash
cd /opt/tvw/app
../venv/bin/python app.py
```

On startup the app:

1. resolves the configured SQLite database;
2. creates the schema if the database is fresh;
3. applies idempotent startup migrations;
4. registers the enabled Lab modules.

Watch the console output closely on the first start.

If using a copied production database, make a backup before the first migration run.

---

## 7. Quick local test on the server

With the Flask process running, test locally on the server:

```bash
curl -I http://127.0.0.1:5000/
```

Useful application paths include:

- `/recipes` — recipe library
- `/recipes/new` — recipe builder
- `/lab/production-jobs` — Lab Production Jobs
- `/lab/production-jobs/earnings` — production earnings for authorised users

Authentication is required for protected pages.

---

## 8. Gunicorn

Run Gunicorn from `/opt/tvw/app`.

For the initial deployment, use one worker:

```bash
cd /opt/tvw/app
../venv/bin/gunicorn \
  -w 1 \
  -b 127.0.0.1:8000 \
  --timeout 120 \
  "app:app"
```

This is intentionally conservative because the current application uses SQLite and starts Lab scheduling logic in the application process.

Once it is stable, scaling can be revisited deliberately.

---

## 9. Nginx

Basic reverse proxy:

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Then enable HTTPS with the server's normal Certbot/Let's Encrypt setup.

---

## 10. What "the calculator" includes

The calculator is not one isolated page. The application uses the recipe database and Lab pricing data together.

Typical workflow:

1. Open **Recipes**.
2. Select an existing recipe or create one.
3. Use **Guided Mix** for an actual mix.
4. Use the **Lab costing tools** when costing recipes.
5. For a large named production run, use **Lab → Production Jobs**.
6. Add the required recipes and quantities to a Production Job.
7. Use **Mix for this job** from the job page so the resulting batch belongs to that job.

Important: mixing the same recipe normally from the Workshop does **not** count toward a Production Job.

See `docs/CALCULATOR_USER_GUIDE.md` for the operator guide.

---

## 11. Production Jobs

Production Jobs represent named manufacturing or fulfilment runs.

A Production Job can track:

- exact recipe/version required;
- target bottle size and count;
- completed / in-progress / remaining production;
- operator identity from the signed-in account;
- job-specific ingredient requirements and purchases;
- production pay;
- costs and profitability.

Only mixes launched through **Mix for this job** affect the job.

A normal personal mix has no Production Job link.

---

## 12. Scrapers and pricing

The Lab includes scraper integrations for pricing/reference data.

Examples include:

- BLCK Shopify product/variant data;
- Clyrolinx;
- VC flavour/reference data.

BLCK variant records are used for canonical VG, PG and nicotine costing.

Scraper data lives in the SQLite database. A fresh server database therefore will not automatically contain Wynand's current scrape history or manually configured live records until those are recreated or the existing DB is migrated.

---

## 13. Troubleshooting information to send Wynand

When something fails, send:

- the URL being opened;
- the signed-in username/role being used;
- exact error message;
- relevant browser screenshot;
- server console / Gunicorn log around the failure;
- Nginx error log if the request never reaches Flask;
- current commit SHA:
  `git rev-parse HEAD`
- Python version:
  `python --version`
- whether `TVW_ENABLE_LAB_MODULES=1`;
- the configured `DATABASE_PATH` (path only — no secrets).

Do not email or paste `SECRET_KEY`, passwords, cookies or session values.

---

## 14. Updating the server later

Typical update:

```bash
cd /opt/tvw
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
```

Back up the SQLite DB before applying an update that contains migrations, then restart Gunicorn/systemd.

---

## 15. If something does not make sense

This project grew from a local Workshop calculator into a larger Workshop + Lab system. There are some historical files and Windows launch helpers in the repository.

For Ubuntu deployment, the important pieces are:

- `app/app.py`
- `app/routes/`
- `app/services/`
- `app/models/`
- `app/templates/`
- `app/static/`
- `app/migrations.py`
- `app/schema.sql`
- `shared_mix_core/`
- the live SQLite database supplied separately
- the production `.env`

