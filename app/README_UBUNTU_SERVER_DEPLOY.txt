================================================================================
  Ubuntu Server 24.04 LTS — deployment notes (The Vape Workshop Calculator)
================================================================================

This document describes a typical production-style layout. Adjust paths for
your server. No Docker here; see your own Compose file when you add one.

--------------------------------------------------------------------------------
1. System packages
--------------------------------------------------------------------------------

  sudo apt update
  sudo apt install -y python3.12 python3.12-venv python3-pip nginx

Use Python 3.12 or 3.13 for parity with Windows QA. Avoid 3.14+ until deps are
verified.

--------------------------------------------------------------------------------
2. Application user and layout
--------------------------------------------------------------------------------

  sudo adduser --system --group --home /opt/tvw tvw
  sudo mkdir -p /opt/tvw/app
  sudo chown -R tvw:tvw /opt/tvw

Copy the application tree so shared_mix_core can be imported. Prefer either:

  /opt/tvw/app/                    app.py, requirements.txt, routes/, core/, ...
  /opt/tvw/app/shared_mix_core/    full package (__init__.py + modules)

or the split layout:

  /opt/tvw/app/
  /opt/tvw/shared_mix_core/

core/shared_mix_path.py walks upward from the app directory (and from the
process working directory) until it finds a directory named shared_mix_core
with __init__.py. You do not need PYTHONPATH for the two layouts above.

PYTHONPATH remains optional if you install the package somewhere else and want
Python to resolve it without copying into /opt/tvw.

--------------------------------------------------------------------------------
3. Virtual environment and requirements
--------------------------------------------------------------------------------

  cd /opt/tvw/app
  sudo -u tvw python3.12 -m venv venv
  sudo -u tvw ./venv/bin/pip install --upgrade pip
  sudo -u tvw ./venv/bin/pip install -r requirements.txt

--------------------------------------------------------------------------------
4. Environment variables (.env or systemd Environment=)
--------------------------------------------------------------------------------

Create /opt/tvw/app/.env owned by tvw mode 600. Keys are loaded at startup if
the file exists (see deploy_env.py). Example:

  SECRET_KEY=change-me-long-random-string
  DATABASE_PATH=/var/lib/tvw/tvw_calculator.db
  TVW_ENABLE_LAB_MODULES=1
  FLASK_ENV=production

Optional:

  PYTHONUTF8=1
  TVW_OLD_GOAT_PASSWORD=only-if-you-override-bootstrap-password

Exact variable reference: see ENV_TEMPLATE.txt in this folder.

--------------------------------------------------------------------------------
5. Database file location
--------------------------------------------------------------------------------

If DATABASE_PATH is unset, SQLite is created next to app.py (tvw_calculator.db).
For production, point DATABASE_PATH at a persistent directory:

  sudo mkdir -p /var/lib/tvw
  sudo chown tvw:tvw /var/lib/tvw

--------------------------------------------------------------------------------
6. Gunicorn (example)
--------------------------------------------------------------------------------

Install gunicorn in the venv:

  sudo -u tvw ./venv/bin/pip install gunicorn

Run from /opt/tvw/app (WorkingDirectory should be the app folder):

  cd /opt/tvw/app
  sudo -u tvw ./venv/bin/gunicorn \
    -w 4 -b 127.0.0.1:8000 \
    --timeout 120 \
    "app:app"

If you rely on a custom PYTHONPATH for shared_mix_core, prefix the same line
with env PYTHONPATH=... as needed.

Use a systemd unit to set WorkingDirectory, User, EnvironmentFile=-/opt/tvw/app/.env,
and ExecStart to the line above.

--------------------------------------------------------------------------------
7. Nginx reverse proxy (sketch)
--------------------------------------------------------------------------------

  server {
      listen 80;
      server_name your.example.com;
      location / {
          proxy_pass http://127.0.0.1:8000;
          proxy_set_header Host $host;
          proxy_set_header X-Real-IP $remote_addr;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;
      }
  }

Enable TLS with certbot when ready.

--------------------------------------------------------------------------------
8. TVW_ENABLE_LAB_MODULES
--------------------------------------------------------------------------------

  0 or unset at process start: community-only build; Lab mix/inventory routes
     are not registered.
  1: full merged Lab bridge (see LAUNCH_QA_ENV.txt).

--------------------------------------------------------------------------------
9. Local dev on Linux
--------------------------------------------------------------------------------

Use ./run_tvw_dev.sh from this directory for a quick venv + Flask dev server.

  chmod +x run_tvw_dev.sh
  ./run_tvw_dev.sh

================================================================================
