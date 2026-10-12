# The Vape Workshop Calculator — Design Language

> **Audience:** Future Cursor agents working on templates, CSS, and copy.  
> **Goal:** Every screen should feel like a **professional mixing workstation**, not a generic Bootstrap admin dashboard.

---

## Design Intent

The Vape Workshop is a **bench, not a back office**.

Users stand (or sit) with a scale, bottles, and a phone. The UI must:

- Put **one primary action** in focus at a time
- Show **context** (recipe, step, target grams) without clutter
- Use **dark surfaces** with high-contrast type and accent highlights
- Feel **tactile** — cards, borders, glow on active step, not flat spreadsheet rows
- Speak with **Old Goat voice** — direct, dry, useful

Avoid: white admin panels, dense data tables as primary UI, rainbow badge soup, cheerful empty states with cartoon illustrations.

---

## Visual System

### Palette (current direction — extend, don't replace)

| Role | Treatment |
|------|-----------|
| **Background** | Near-black gradients (`#101010` – `#1a1a1a`) |
| **Surface / card** | Dark grey with subtle border `rgba(255,255,255,0.08–0.12)` |
| **Primary accent** | Warm amber / gold (`#ffc107` family) — active step, CTAs, progress |
| **Success** | Muted green (`#198754`) — completion, ready status |
| **Warning / caution** | Orange-amber — policy warnings, caution steps |
| **Danger / recovery** | Red — recovery steps, hard stops (Lab) |
| **Info / instruction** | Cyan-teal — instruction steps, help status |
| **Muted text** | `rgba(255,255,255,0.58–0.72)` for kickers and labels |

Reference implementation: `app/static/tvw.css` (classes prefixed `tvw-`).

### Typography

- **Kickerson labels:** uppercase, letter-spaced, small (`tvw-kicker`, `tvw-copy-label`) — “NOW POURING”, “TARGET”
- **Hero numbers:** large, bold — grams on measure input and focus metrics
- **Body:** readable at arm's length on a phone; minimum 16px effective on inputs
- **No decorative fonts** — system stack or existing project fonts only

### Spacing & layout

- **Max content width:** ~960–980px for guided cockpit (`tvw-guided-cockpit`)
- **Generous padding** on focus cards; cramped tables are for admin only
- **Mobile first** for guided mix — measure input and primary button thumb-reachable

### Components (use existing patterns)

| Component | Class / pattern | Use for |
|-----------|-----------------|---------|
| Section shell | `tvw-section` | Grouped content blocks |
| Focus card | `tvw-focus-card` | Current guided step hero |
| Coach panel | `tvw-coach-panel` | Narrative / coaching copy |
| Policy panel | `tvw-policy-panel` | Warnings from `policy_feedback.js` |
| Action zone | `tvw-action-zone` | Measure input + confirm button |
| Step trail | `tvw-trail-row` | Secondary step history |
| Status badges | `tvw-status-wip`, `tvw-status-help`, `tvw-status-ready` | Recipe status |

### Step type styling (guided mix)

Each step type has a distinct border accent — users recognize state by colour:

- `tvw-step-tare` — neutral grey
- `tvw-step-add-ingredient` — amber (primary work)
- `tvw-step-caution` — orange
- `tvw-step-recovery` — red
- `tvw-step-instruction` — cyan
- `tvw-step-transition` — blue
- `tvw-step-completion` — green

**Do not collapse these into one generic “card”.**

---

## Screen Hierarchy

### Tier 1 — Workstation screens (highest design bar)

1. **Guided Mix Session** — single hero step, progress bar, trail collapsed by default
2. **Recipe Builder** — live calc panel, flavour row focus
3. **Quick mix / live calc outputs** — `tvw-output` pre-wrap, monospace-friendly numbers

### Tier 2 — Discovery screens

4. Recipe Library — scannable rows (`tvw-list-row`), status badges, not full tables
5. Recipe Detail — clear CTA to “Start Guided Mix”
6. Community Home — cards or rows, not dashboard widgets
7. Stash — quick-add prominent, quantities right-aligned

### Tier 3 — Admin (functional over beautiful)

8. TVW Admin (`tvw_admin_*.html`) — tables acceptable; keep consistent header/nav
9. Lab / Master Admin — legacy dark theme; do not redesign unless asked

---

## Interaction Principles

### Guided mix

- **One step in focus.** Trail and history are secondary (collapsible).
- **Measure input is sacred.** Large tap target, numeric keyboard on mobile (`inputmode="decimal"`).
- **Confirm is explicit.** Don't auto-advance on blur.
- **Policy feedback appears inline** via `policy_feedback.js` — warnings visible before confirm, not only in toast.
- **Recovery steps insert visibly** — user must see *why* a new step appeared.

### Forms (recipe builder, stash)

- Live feedback beats submit-then-see-errors.
- Inline validation messages, not alert boxes.
- Destructive actions need confirmation; routine saves do not.

### Lists

- Prefer `tvw-list-row` cards over `<table>` on TVW surfaces.
- Admin tables may use tables; add pagination when >50 rows.

---

## Voice & Microcopy (Old Goat Tone)

### Personality

| Trait | Example |
|-------|---------|
| **Grumpy** | “You're not done. Enter a weight.” |
| **Useful** | “Overshot by 0.3 g — recovery step added. Keep going.” |
| **Funny (dry)** | “That's a lot of sweetener. Your coils won't thank you.” |
| **Direct** | “Target: 2.40 g. Pour. Confirm.” |

### Avoid

- Exclamation marks everywhere
- “Awesome”, “Yay”, “Oopsie”, “Let's go!”
- Talking like a chatbot (“How can I help you today?”)
- Childish emoji in UI copy (UI chrome may use minimal functional icons only)

### TVW vs Lab copy

| Situation | TVW | Lab |
|-----------|-----|-----|
| Overpour | Warn, offer recovery / continue | Hard stop or scrap required |
| Missing measure | “Need a reading before we move on.” | Block with audit reason |
| Completion | “Batch done. Steep it. Argue about it later.” | Formal batch reference, supervisor notes |

---

## Bootstrap Usage

Bootstrap (or Bootstrap-like utilities) is a **layout scaffold**, not the design system.

**Do:**
- Use grid, spacing utilities, form-control base
- Override with `tvw-*` classes for brand feel

**Don't:**
- Ship default light `bg-light` admin layouts on TVW pages
- Use stock Bootstrap primary blue as the main CTA colour
- Add new CSS frameworks or component libraries without explicit request

---

## Accessibility Baseline

- Sufficient contrast on dark backgrounds (WCAG AA target for body text)
- Visible focus states on inputs and buttons
- Labels on all form fields (not placeholder-only)
- `autocomplete` attributes on login/signup
- Don't rely on colour alone for step state — use labels (“Step 3 · Add ingredient”)

---

## Assets & Branding

- TVW surfaces: community product branding, not “Old Goat Lab” (per cleanup plan)
- Lab bridge surfaces may retain legacy naming when flag enabled
- No stock photo heroes, no generic “vape cloud” clip art
- Recipe upload images: user-provided, moderated via admin

---

## Anti-Patterns (Do Not Introduce)

| Anti-pattern | Why |
|--------------|-----|
| Dashboard widget grid on community home | Feels like SaaS admin, not a bench |
| Sidebar + topbar admin layout on mix screens | Wastes mobile space |
| Modal stacked on modal | Blocks the scale workflow |
| Duplicated mix results in template JS | Math must come from API / shared core |
| Light theme toggle (for now) | Scope creep; dark is the brand |
| AI “assistant” panel | Explicit non-goal |

---

## When Changing UI

1. Read `app/static/tvw.css` — extend existing classes before adding new ones.
2. Match guided session patterns for any new “workflow” screen.
3. Test at 375px width (iPhone SE class).
4. Keep copy in templates editable — short strings, no paragraphs in alert().
5. If unsure between “prettier” and “simpler”, choose **simpler**.
