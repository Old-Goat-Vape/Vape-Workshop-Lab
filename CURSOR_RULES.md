# Cursor Agent Rules — The Vape Workshop Calculator

> **Audience:** Every Cursor agent session on this project.  
> **Companion docs:** `PROJECT_VISION.md`, `PRODUCT_ROADMAP.md`, `DESIGN_LANGUAGE.md`

Read those three documents before non-trivial work. This file is the **operating contract** for code changes.

---

## Mission

Ship a **launch-ready community mixing workstation** with **Guided Mixing** as the hero feature. Default to polish and simplification — not expansion.

---

## Golden Rules

### 1. Do not add features unless explicitly requested

- No new routes, tables, blueprints, or user-facing capabilities “while you're in there.”
- Bug fixes, polish, tests, docs, and launch blockers are always in scope when relevant.
- If you see a nice idea, mention it — don't build it.

### 2. Never duplicate mix math

- **All** mix calculations: `shared_mix_core` (`calculator.py`, `policy.py`, `guided_steps.py`, `guided_narrative.py`).
- Import via `core/calculator.py` — never reimplement PG/VG/nicotine/flavour grams in services, routes, or template JavaScript.
- **TVW path:** `services/tvw_mix_service.py` → shared core.
- **Guided sessions:** `services/guided_mix_session_service.py` → shared core.
- **Lab legacy:** `services/mixing_engine.py` — do not add new TVW features here.

### 3. Protect `shared_mix_core`

- Treat as a **semver-stable internal package**.
- Changes require: unit test updates, policy behaviour check (TVW vs Lab), and awareness of release packaging.
- Do not add Flask, SQLite, or HTTP imports inside `shared_mix_core/`.
- Keep `core/shared_mix_path.py` discovery logic working — no hardcoded `parents[N]` hacks.

### 4. TVW is forgiving; Lab is strict

| | TVW | Lab |
|---|-----|-----|
| Policy | `tvw_mix_policy()` | `lab_mix_policy()` |
| Overpour | Warn + recovery step | Hard stop / scrap paths |
| HTTP | `/api/mix/guided/...` | `/mix/reconcile`, inventory deduct |
| Audience | Community | Production / audit |

Never apply Lab hard-stop behaviour to TVW guided mix without explicit product approval.

### 5. No AI code

Do not add LLM APIs, embedding search, “AI suggest”, chat bots, or Cursor-style agents inside the app unless the user explicitly requests it.

### 6. Minimize diff scope

- Match existing naming, patterns, and file layout.
- No drive-by refactors, no unrelated formatting, no new abstractions for one call site.
- Prefer editing the module that already owns the behaviour.

### 7. Do not break the release zip contract

- `shared_mix_core/` must ship inside release artifacts (`app/RELEASE_PACKAGING.txt`).
- Do not require manual `PYTHONPATH` for end users.
- Do not commit `.env`, real DB files, or `venv/`.

### 8. Verify every Python edit before marking done

Every task that edits Python code **must** end with:

1. `python -m py_compile` on **every modified** `.py` file.
2. `python -m unittest tests.test_tvw_api` (or the relevant test suite for the area changed).

Run both from `app/` using the project venv (`.\venv\Scripts\python.exe`) when system Python lacks dependencies.

**If compilation fails, do not consider the task complete.**

---

## Architecture Quick Reference

```
app/
├── app.py              # Entry, flags, blueprint registration
├── schema.sql          # Base schema (fresh DB only)
├── migrations.py       # Canonical schema evolution — always idempotent
├── core/
│   ├── calculator.py   # Re-exports shared_mix_core
│   └── shared_mix_path.py
├── shared_mix_core/    # MUST exist for runtime (copy from release build if missing in dev)
├── routes/
│   ├── tvw_*.py        # TVW product (always on)
│   ├── auth.py
│   └── mix.py, inventory.py, ...  # Lab only (gated)
├── services/
│   ├── tvw_mix_service.py
│   ├── guided_mix_session_service.py
│   └── mixing_engine.py  # Legacy Lab — avoid for new TVW work
├── models/             # Raw SQL repos — no ORM
├── templates/          # tvw_* = community; others often Lab
└── static/tvw.css, policy_feedback.js
```

**Feature flag:** `TVW_ENABLE_LAB_MODULES` (default `false` in code).

---

## Change Checklist (Before You Finish)

- [ ] Mix math touched? → Only in `shared_mix_core`, tests updated.
- [ ] New TVW endpoint? → Account gate via `tvw_enforce_account_before_request` pattern in `tvw_api.py`.
- [ ] Schema change? → `migrations.py` idempotent `CREATE TABLE IF NOT EXISTS` / `_add_column_if_missing` — not request-time DDL.
- [ ] Template change? → `DESIGN_LANGUAGE.md` — workstation feel, mobile check.
- [ ] Copy change? → Old Goat tone — direct, not childish.
- [ ] Python edited? → `py_compile` every modified file + run relevant unittest suite (Golden Rule #8).
- [ ] Lab-only code? → Guarded by `TVW_ENABLE_LAB_MODULES` or Lab blueprint registration.
- [ ] Secrets? → Never hardcode; use env vars documented in `ENV_TEMPLATE.txt`.

---

## Files & Areas — Hands Off Unless Task Requires

| Area | Agent guidance |
|------|----------------|
| `app/venv/` | Never edit |
| `release/` | Build output — edit `app/`, rebuild release |
| `app/_archive/` | Do not resurrect without explicit ask |
| `fix_db.py`, `repair_db.py` | Legacy — prefer `migrations.py` |
| `mixing_engine.py` | Only for Lab/reconcile bugs — not new TVW features |
| Bootstrap users in `app.py` | Don't remove without migration plan; do fix security for production docs |

---

## Testing

**Mandatory gate (Golden Rule #8):** after any Python edit, compile every touched file, then run tests. Do not report the task complete until both pass.

```powershell
Set-Location app

# 1. Compile every modified .py file
python -m py_compile path\to\modified_file.py

# 2. Run the relevant suite (default for most TVW work)
python -m unittest tests.test_tvw_api
python -m unittest tests.test_shared_mix_layout
```

Guided mix critical tests listed in `app/docs/mixing_guide_v2_launch_qa.md`.

No CI in repo yet — local compile + test before claiming done is mandatory.

---

## Git & Commits

- **Only commit when the user asks.**
- Never update git config, force-push, or skip hooks unless explicitly instructed.
- Do not commit `.env`, `*.db`, `venv/`, or secrets.

---

## Documentation Hierarchy

When docs conflict, prefer:

1. User's explicit instruction in the current message
2. This file (`CURSOR_RULES.md`)
3. `PROJECT_VISION.md` / `PRODUCT_ROADMAP.md` / `DESIGN_LANGUAGE.md`
4. `app/docs/tvw_route_contracts.md`, `tvw_rollout_test_gates.md`, `RELEASE_PACKAGING.txt`
5. Code comments and inline docstrings

If `tvw_route_contracts.md` disagrees with `tvw_api.py`, **trust the code** and note the doc drift.

---

## Common Tasks — Where to Go

| Task | Start here |
|------|------------|
| Guided mix bug | `guided_mix_session_service.py`, `tvw_api.py`, `tvw_guided_mix_session.html`, `policy_feedback.js` |
| Mix numbers wrong | `shared_mix_core/calculator.py`, then `tvw_mix_service.py` |
| Overpour / policy | `shared_mix_core/policy.py` |
| Recipe save/version | `tvw_recipes.py`, `recipes_repo.py` |
| Stash | `stash_service.py`, `tvw_api.py` stash routes |
| Admin moderation | `tvw_admin.py`, `tvw_admin_repo.py` |
| Lab reconcile | `mixing_engine.reconcile_batch_plan`, `routes/mix.py` (Lab flag on) |
| New migration | `migrations.py` only |
| UI polish | `tvw.css`, Tier 1 screens in `DESIGN_LANGUAGE.md` |

---

## What Must Never Be Broken

1. `shared_mix_core` import and packaging
2. Mix calculation accuracy (grams ↔ ml with density)
3. Guided session create → step complete → resume flow
4. TVW vs Lab policy semantics
5. Idempotent startup migrations
6. Auth ban/suspend gates on TVW
7. Recipe version immutability when locked
8. Stash one-row-per-(user, flavour) invariant

---

## Explicit Anti-Goals for Agents

- Adding REST recipe CRUD API “for completeness”
- Building public profile page without request
- Migrating to Postgres “while we're at it”
- Splitting `mixing_engine.py` in a drive-by refactor
- Introducing React, Vue, or a CSS framework
- Enabling Lab modules by default in production TVW
- Writing AI features

---

## Tone Reminder

You are helping build a tool for people who weigh chemicals on a scale. Be precise in code, direct in copy, conservative in scope. Grumpy expertise, not enthusiastic clutter.
