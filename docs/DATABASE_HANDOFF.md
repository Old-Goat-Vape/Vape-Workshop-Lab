# Database Handoff & Backup Guide

## Is the database in GitHub?

**No.**

The live SQLite database is deliberately excluded from Git by `.gitignore`.

That is the correct setup because the database can contain:

- user accounts and password hashes;
- recipes and recipe versions;
- Production Jobs;
- production/operator history;
- supplier scrape data;
- costing data;
- purchase records;
- production-pay records;
- other business data.

GitHub should contain code and migrations, not the live production database.

---

## Fresh server vs existing data

### Option A — Fresh database

If Pauly deploys the repository without supplying an existing database, the application can create a new SQLite database and run its schema/migrations.

This is useful for testing.

It will **not** contain Wynand's current live data.

### Option B — Move the existing database

For the actual website, if the current recipes/users/jobs/pricing history must come with the application, securely transfer the current active SQLite DB to the server.

Do not transfer it through Git.

---

## Find the actual active database first

There may be old copies/backups on the development PC.

Before copying anything, confirm the database the running application is actually using.

Check:

- `DATABASE_PATH` if configured;
- application startup diagnostics;
- the local app-data directory used by the current build.

On Windows, the normal default documented by the project is:

`%LOCALAPPDATA%\TheVapeWorkshopCalculator\tvw_calculator.db`

However, do not assume a random `tvw_calculator.db` found elsewhere is the live one. Confirm it from the running app/startup log.

---

## Safe handoff process

1. Stop the local app or otherwise ensure the SQLite file is not being written.
2. Make a timestamped backup.
3. Copy the confirmed live database through a secure file-transfer method.
4. On Ubuntu, store it somewhere durable, for example:

   `/var/lib/tvw/tvw_calculator.db`

5. Restrict permissions so only the application account can read/write it.
6. Set:

   `DATABASE_PATH=/var/lib/tvw/tvw_calculator.db`

7. Back it up again on the server before the first run of a new code version.
8. Start the app and let startup migrations run.
9. Verify logins, recipes, Production Jobs and Lab data before switching public traffic over.

---

## SQLite sidecar files

If the database is actively using WAL mode, SQLite may have files such as:

- `tvw_calculator.db-wal`
- `tvw_calculator.db-shm`

The safest handoff is to stop the application cleanly before copying the DB so pending WAL content is checkpointed.

Do not casually copy only the main DB while it is being actively written.

---

## Backups

For a first production deployment, keep:

- a pre-deployment copy of the local DB;
- a pre-migration copy on the server;
- regular off-server backups.

Database backups must remain outside the Git repository.

