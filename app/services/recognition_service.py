from __future__ import annotations

from calendar import month_name
from datetime import date
import re

from db import get_db
from models import recipes_repo


SCHEMA = """
CREATE TABLE IF NOT EXISTS recipe_awards (
    kind TEXT NOT NULL CHECK (kind IN ('month', 'year')),
    period TEXT NOT NULL,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    PRIMARY KEY (kind, period)
);
CREATE INDEX IF NOT EXISTS idx_recipe_awards_recipe_id ON recipe_awards(recipe_id);
"""

_MIXER_MILESTONES = (
    ("Apprentice", 3, 1),
    ("Bench Regular", 10, 3),
    ("Seasoned Mixer", 25, 5),
)


def mixer_progress(activity):
    earned = ["Kid"]
    mixes = activity["quick_mixes"] + activity["guided_mixes"]
    for name, mix_goal, recipe_goal in _MIXER_MILESTONES:
        if mixes < mix_goal or activity["recipes_created"] < recipe_goal:
            return earned, {"name": name, "mix_goal": mix_goal, "recipe_goal": recipe_goal}
        earned.append(name)
    return earned, None


def mixer_badge(user_id):
    return mixer_badges({user_id})[user_id]


def mixer_badges(user_ids):
    activity_by_user = recipes_repo.fetch_mixer_activity_counts_for_users(user_ids)
    return {user_id: mixer_progress(activity)[0][-1] for user_id, activity in activity_by_user.items()}


def _award_eligibility_sql():
    return recipes_repo.tvw_recipe_public_list_predicate_sql("r") + " AND r.current_version_id IS NOT NULL"


def can_receive_recipe_award(recipe_id):
    return get_db().execute(
        f"SELECT 1 FROM recipes r WHERE r.id = ? AND {_award_eligibility_sql()}",
        (recipe_id,),
    ).fetchone() is not None


def record_recipe_award(recipe_id, period):
    period = period.strip()
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}", period):
            date.fromisoformat(f"{period}-01")
            kind = "month"
        elif re.fullmatch(r"[0-9]{4}", period):
            date.fromisoformat(f"{period}-01-01")
            kind = "year"
        else:
            raise ValueError
    except ValueError:
        raise ValueError("Enter a valid award period as YYYY-MM for a month or YYYY for a year.") from None

    db = get_db()
    with db:
        cursor = db.execute(
            f"""
            INSERT INTO recipe_awards (kind, period, recipe_id)
            SELECT ?, ?, r.id FROM recipes r WHERE r.id = ? AND {_award_eligibility_sql()}
            ON CONFLICT (kind, period) DO NOTHING
            """,
            (kind, period, recipe_id),
        )
        if cursor.rowcount == 1:
            return
        if not can_receive_recipe_award(recipe_id):
            raise ValueError("Only published public recipes can receive an award.")
        winner = db.execute(
            "SELECT recipe_id FROM recipe_awards WHERE kind = ? AND period = ?", (kind, period)
        ).fetchone()
        if winner["recipe_id"] != recipe_id:
            raise ValueError("This period already has a winning recipe.")


def annotate_recipes(recipes, *, include_mixers=True):
    if not recipes:
        return
    ids = tuple({recipe["id"] for recipe in recipes})
    marks = ", ".join("?" for _ in ids)
    awards = {}
    for row in get_db().execute(
        f"""
        SELECT a.recipe_id, a.kind, a.period
        FROM recipe_awards a JOIN recipes r ON r.id = a.recipe_id
        WHERE r.id IN ({marks}) AND {_award_eligibility_sql()}
        ORDER BY a.kind DESC, a.period DESC
        """,
        ids,
    ):
        period = row["period"]
        if row["kind"] == "month":
            label = f"Recipe of the Month · {month_name[int(period[5:])]} {period[:4]}"
        else:
            label = f"Recipe of the Year · {period}"
        awards.setdefault(row["recipe_id"], []).append(label)

    authors = {}
    if include_mixers:
        authors = dict(get_db().execute(
            f"""
            SELECT r.id, r.created_by FROM recipes r
            WHERE r.id IN ({marks}) AND r.created_by IS NOT NULL
              AND r.id NOT IN (SELECT recipe_id FROM recipe_references WHERE recipe_id IS NOT NULL)
            """,
            ids,
        ).fetchall())
    badges = mixer_badges(set(authors.values()))
    for recipe in recipes:
        recipe["recipe_awards"] = awards.get(recipe["id"], [])
        if not recipe.get("is_imported"):
            recipe["mixer_badge"] = badges.get(authors.get(recipe["id"]))
