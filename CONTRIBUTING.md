# Contributing

Contributions are welcome.

## Before you start

Please keep the separation between **application code** and **live data** intact.

Never commit:

- production or personal SQLite databases;
- `.env` files or credentials;
- passwords, session cookies, API keys or private keys;
- backups or release archives containing live data.

The existing `.gitignore` is intentionally strict about these files.

## Development setup

Recommended Python versions are 3.12 or 3.13.

```bash
git clone https://github.com/Old-Goat-Vape/Vape-Workshop-Lab.git
cd Vape-Workshop-Lab
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example app/.env
```

On Windows, activate the environment with:

```bat
venv\Scripts\activate
```

## Making changes

Keep changes focused. When modifying database behaviour, prefer idempotent migrations and preserve compatibility with existing SQLite databases.

For changes to mixing calculations, Guided Mix, costing or Production Jobs, add or update tests where practical.

Run the relevant tests before opening a pull request.

```bash
cd app
../venv/bin/python -m pytest
```

## Pull requests

A useful pull request should explain:

- what changed;
- why it changed;
- how it was tested;
- whether it changes the database schema or migration path;
- whether it changes deployment/environment requirements.

Do not include live business data in test fixtures or screenshots.

## Security issues

Please do not publish working exploit details, real credentials or private data in a public issue. Contact the repository owner privately for security-sensitive reports.
