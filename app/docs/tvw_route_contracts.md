# TVW Route Contracts (V1)

## Web Routes

- `/` -> community landing (public)
- `/community` -> authenticated community feed
- `/recipes/new` -> recipe creation shell
- `/recipes/<slug>` -> recipe detail shell
- `/recipes/<slug>/versions` -> version list shell
- `/recipes/<slug>/versions/<n>` -> version detail shell
- `/recipes/<slug>/mix/quick` -> quick mix shell
- `/recipes/<slug>/mix/guided` -> guided mix shell
- `/flavours` -> flavour index shell
- `/flavours/<id>` -> flavour detail shell
- `/stash` -> user stash shell
- `/profile/<username>` -> public profile

## API Routes (Implemented)

- `POST /api/recipes/<id>/status`
  - body: `{ "status": "wip|help|ready", "reason": "..." }`
- `POST /api/recipes/<id>/feedback`
  - body: `{ "feedback_type": "structured|freeform", "body": "...", "score_overall": 0-10, "metrics": { "sweetness": 3 } }`
- `POST /api/flavours/<id>/notes`
  - body: `{ "note": "..." }`
- `POST /api/flavours/<id>/reviews`
  - body: `{ "rating": 0-5, "review": "...", "steep_days": 7 }`
- `POST /api/stash`
  - body: `{ "flavour_id": 1, "quantity_ml": 30, "is_favorite": true }`
- `POST /api/mix/quick`
  - body: `{ "recipe_id": 1, "version_id": 2, "target_ml": 60, "target_nic_mg_ml": 3, "nic_base_mg_ml": 100, "pg_ratio": 30, "vg_ratio": 70, "nicotine_carrier": "PG" }`
- `POST /api/mix/guided/session`
  - same input as quick mix; creates session and persisted steps.

## API Routes (Planned next iteration)

- `GET /api/recipes`
- `POST /api/recipes`
- `POST /api/recipes/<id>/versions`
- `GET /api/recipes/<id>/feedback`
- `GET /api/flavours/<id>/notes`
- `GET /api/flavours/<id>/reviews`
- `PATCH /api/stash/<id>`
- `DELETE /api/stash/<id>`

