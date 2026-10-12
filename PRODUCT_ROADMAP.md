# The Vape Workshop Calculator — Product Roadmap

> **Audience:** Future Cursor agents.  
> **Purpose:** Launch priorities, post-launch ideas, and what to work on when the user hasn't specified a task.

**Default agent behaviour:** Prefer **before-launch** items. Do not implement **after-launch** items unless explicitly requested.

---

## Current State (Baseline)

Milestones 1–4 are **done** (per `app/docs/tvw_rollout_test_gates.md`):

- Product boundary and Lab feature flag
- Reusable `shared_mix_core` + TVW adapter services
- Community schema (stash, sessions, feedback, flavours)
- TVW route and API surface

Milestone 5 (**Hardening**) is **in progress** — this is the launch runway.

---

## Before Launch — Priorities

Ordered by impact. Work top-down unless the user directs otherwise.

### P0 — Blockers (must fix before public launch)

| Item | Detail | Key files |
|------|--------|-----------|
| **Ship `shared_mix_core` in dev tree** | `app/shared_mix_core/` must exist; app hard-exits without it | `core/shared_mix_path.py`, `RELEASE_PACKAGING.txt` |
| **Production security** | No default `SECRET_KEY`; no bootstrap passwords in production | `app.py`, `ENV_TEMPLATE.txt` |
| **Production server config** | `debug=False`; document gunicorn/waitress deploy | `app.py`, `README_UBUNTU_SERVER_DEPLOY.txt` |
| **Guided mix QA pass** | All branches in `mixing_guide_v2_launch_qa.md` green | `test_tvw_api.py`, guided templates |

### P1 — Launch-critical polish

| Item | Detail |
|------|--------|
| **Guided Mix session UX** | Hero step, trail, measure input, policy feedback, recovery flow — workstation feel |
| **Mobile usability** | Guided mix and recipe builder usable on phone (touch targets, layout, no horizontal scroll) |
| **Recipe builder live calc** | Instant feedback; no page reload for percentage changes |
| **Community home** | Feed loads fast; featured/newest/updated/top-rated all populated correctly |
| **Stash → library integration** | Stash sort, “what can I make”, quick-add all work |
| **Auth flow** | Signup, login, ban/suspend gates, role sync on request |

### P2 — Hardening (Milestone 5)

| Item | Detail |
|------|--------|
| **API test coverage** | Extend `test_tvw_api.py` for each contract in `tvw_route_contracts.md` |
| **Pagination** | List endpoints and admin tables that can grow unbounded |
| **Schema constraints** | CHECK constraints, indexes for hot queries where safe |
| **Docs sync** | Update `tvw_route_contracts.md` to match implemented endpoints |
| **Profile page decision** | Build `tvw_profile.html` or remove/hide stub route until ready |

### P3 — Pre-launch cleanup (no new features)

| Item | Detail |
|------|--------|
| **Lab branding removal from TVW surfaces** | Per `tvw_lab_cleanup_plan.md` |
| **Dead code audit** | `_archive/`, unused templates, stale repair scripts |
| **Starter script defaults** | Clarify `TVW_ENABLE_LAB_MODULES` default for end users vs developers |
| **OneDrive / SQLite warning** | Document DB path best practice for deployed instances |

---

## After Launch — Ideas (Do Not Build Unless Asked)

These are recorded for product continuity. **Agents must not implement these proactively.**

### Near-term (v1.1)

- Public user profile page (recipes, stash visibility settings, activity)
- `GET/POST /api/recipes` REST CRUD (currently form-based web routes)
- Recipe import/export (JSON or standard format)
- Notification digests (favourites updated, replies to feedback)
- Improved flavour search (fuzzy match, alias resolution)

### Medium-term (v1.x)

- Recipe forking and attribution
- Steep-time tracking and reminders (no AI — just timers/notes)
- Batch size presets and vessel profiles saved per user
- Community collections / curated lists by moderators
- Light analytics for recipe authors (views, mix starts, ratings trend)

### Long-term (v2+)

- Postgres migration for multi-user hosting
- Separate Lab deployable (extract from monolith)
- Optional self-hosted flavour DB sync service
- PWA / offline guided mix (service worker, local session cache)

### Explicitly deferred

- AI recipe suggestions, chat, or “generate me a mango ice”
- Marketplace / payments
- Native iOS/Android apps
- Real-time collaborative mixing

---

## Feature Flags & Product Modes

| Flag | Default (code) | Effect |
|------|----------------|--------|
| `TVW_ENABLE_LAB_MODULES` | `false` | When `true`: Lab blueprints, master admin, `/mix/reconcile`, inventory, scrapers |

**TVW launch target:** Community product works fully with flag `false`.  
**Developer convenience:** Windows `.bat` starters may set flag `true` — do not assume that for production TVW.

---

## QA Gates Before Any Release Tag

1. `python -m unittest tests.test_tvw_api` passes from `app/`
2. Guided mix browser checklist in `mixing_guide_v2_launch_qa.md` completed
3. Fresh zip extract → `pip install -r requirements.txt` → app boots → guided session completes
4. No default secrets in deployed `.env`
5. `shared_mix_core` import succeeds without manual `PYTHONPATH`

---

## What “Done” Means for Launch

Launch is not “every feature exists.” Launch is:

- A stranger can sign up, build or find a recipe, and **complete a guided mix** on mobile or desktop.
- Overpour recovery works without calling Lab-only `/mix/reconcile`.
- Admin can moderate content.
- Numbers are correct.
- The app feels like a **mixing bench**, not a CRUD admin.

---

## Roadmap Maintenance

When the user completes a roadmap item, note it in the commit message — do not edit this file unless asked. This document is a **planning anchor**, not a changelog.
