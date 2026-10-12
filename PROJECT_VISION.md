# The Vape Workshop Calculator — Project Vision

> **Audience:** Future Cursor agents and contributors.  
> **Read this first** before making any change to the product.

---

## What This Product Is

The Vape Workshop Calculator is **not just another e-liquid calculator**.

It is a **community mixing workstation** — a place where home mixers build recipes, share knowledge, track what they own, and **mix with confidence at the scale**. The differentiator is not “it does PG/VG math.” Every app does that. The differentiator is **Guided Mixing**: a step-by-step, policy-aware, recovery-capable mixing session that feels like standing at a real bench with a grumpy expert looking over your shoulder.

The product has two modes in one codebase:

| Mode | Audience | Philosophy |
|------|----------|------------|
| **TVW (The Vape Workshop)** | Community mixers | Forgiving, encouraging, warn-don't-block |
| **Lab (Old Goat Lab bridge)** | Production / inventory operators | Strict, auditable, hard-stop on policy violations |

TVW is the launch product. Lab is a gated bridge (`TVW_ENABLE_LAB_MODULES`) for legacy production workflows — not the public face.

---

## Core Beliefs

1. **Guided mixing is the killer feature.** Everything else (recipe library, stash, community) exists to feed into and out of the mixing session.
2. **Feel like a workstation, not a Bootstrap admin panel.** Dark, focused, tactile UI. One hero action per screen. Progress and context visible at a glance.
3. **Old Goat tone:** grumpy, useful, funny, direct — never childish, never corporate, never saccharine. The app talks like someone who's mixed ten thousand batches and has no patience for nonsense, but will absolutely help you not ruin yours.
4. **Math is sacred and centralized.** All mix calculations live in `shared_mix_core`. Never duplicate formulas in routes, templates, or services.
5. **Community is trust-based.** Recipes have status (WIP / Help / Ready), visibility, moderation, and feedback. The platform should reward honest work-in-progress, not just polished finals.
6. **Launch readiness beats feature breadth.** Polish what exists. Do not add features unless explicitly requested.
7. **No AI code for now.** Do not add LLM integrations, AI assistants, or “smart suggestions” powered by external models unless the user explicitly asks.

---

## What Success Looks Like at Launch

A new mixer can:

1. Sign up and land on a community feed that feels alive.
2. Browse or build a recipe with live calculation feedback.
3. Start a **Guided Mix session** and complete it on a phone or desktop without confusion.
4. Recover gracefully from an overpour (warning + recovery step, not a dead end).
5. Save flavours to their stash and see “what can I make” from what they own.
6. Trust that the numbers on screen match what they'd weigh on a scale.

An admin can moderate flavours, recipes, users, and reports without touching Lab machinery.

---

## Architectural North Star

```
Browser (Jinja + vanilla JS)
    ↓
Flask routes (TVW always on; Lab gated)
    ↓
Services (tvw_mix_service, guided_mix_session_service)
    ↓
core/calculator.py  →  shared_mix_core  (pure math + policy)
    ↓
SQLite (schema.sql + migrations.py)
```

**Legacy path:** `services/mixing_engine.py` is Lab orchestration (reconcile, conversion, inventory). Do not route new TVW features through it. Extend `shared_mix_core` + `tvw_mix_service` + `guided_mix_session_service` instead.

---

## Product Personality (Voice & Copy)

| Do | Don't |
|----|-------|
| Short, direct sentences | Long tutorials inline |
| Dry humour, bench slang | Memes, emoji spam, “awesome sauce” |
| Explain *why* a step matters | Talk down to the user |
| Warn clearly on policy issues | Block without explanation (TVW) |
| “Pour it. We’ll deal with it.” | “Oopsie! Something went wrong!” |

Tone examples:

- **Good:** “You overshot by 0.4 g. Not the end of the world — we’ve added a recovery step.”
- **Bad:** “🎉 Great job! You poured too much! Let’s try again!”

---

## What Must Never Be Broken

These are non-negotiable invariants. Breaking any of them is a regression, not a refactor side effect.

| System | Why it matters |
|--------|----------------|
| **`shared_mix_core` import path** | App exits if package missing. Release zip must include it. |
| **Mix math correctness** | Users weigh real ingredients. Wrong grams = ruined batch. |
| **Guided session persistence** | `mix_sessions` + `mix_session_steps` must survive refresh and resume. |
| **Policy layer** | TVW lenient (`overpour_hard_stop=False`); Lab strict. Never swap silently. |
| **Recipe versioning** | Edits create versions; never mutate locked history. |
| **Auth + account gates** | Banned/suspended users blocked on TVW surfaces. |
| **Startup migrations** | `migrations.py` must remain idempotent. |
| **Stash deduplication** | One logical row per (user, flavour). |
| **Release packaging layout** | `shared_mix_core` discoverable via `core/shared_mix_path.py`. |

---

## Release Packaging Rules

See also `app/RELEASE_PACKAGING.txt`. Summary for agents:

1. **Include** `shared_mix_core/` inside the release zip (recommended: `app/shared_mix_core/`).
2. **Exclude** `venv/`, `__pycache__/`, `.env`, WAL/SHM files, local DB with real data.
3. **Never ship** default secrets — use `ENV_TEMPLATE.txt`.
4. **Production** must set `SECRET_KEY`, change bootstrap passwords, run behind a real WSGI server (not `debug=True`).
5. Do not maintain two divergent source trees — `release/` is a build artifact, not a second place to edit.

---

## Screens That Matter Most (Launch)

Priority order for UX polish and QA:

1. **Guided Mix Session** (`tvw_guided_mix_session.html`) — the product.
2. **Recipe Builder** (`tvw_recipe_builder.html`) — creation funnel.
3. **Recipe Detail** (`tvw_recipe_detail.html`) — discovery → mix entry point.
4. **Recipe Library** (`tvw_recipe_library.html`) — browse and filter.
5. **Community Home** (`tvw_community_home.html`) — first impression.
6. **Stash** (`tvw_stash.html`) — ownership and “what can I make”.
7. **Login / Signup** — low friction, TVW-branded.

Lower priority until after launch: public profile page (currently JSON stub), Lab master admin surfaces, sales/consignment modules.

---

## Explicit Non-Goals (For Now)

- AI-powered recipe generation or chat assistants
- Native mobile apps (mobile-web is the target)
- Multi-tenant SaaS / Postgres migration (SQLite is current truth)
- Social features beyond recipes, flavours, stash, ratings, and reports
- Rewriting the entire Lab stack into TVW

---

## How Agents Should Use This Document

Before any task, ask:

1. Does this serve **Guided Mixing** or the path leading to it?
2. Am I **polishing** or **expanding**? (Expansion needs explicit user request.)
3. Am I touching **mix math**? If yes, only in `shared_mix_core`.
4. Am I changing **TVW policy** vs **Lab policy**? They are intentionally different.
5. Will this still work from a **fresh release zip** on a clean machine?

When in doubt: simplify, don't add.
