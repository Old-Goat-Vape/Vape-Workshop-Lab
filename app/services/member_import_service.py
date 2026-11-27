"""Preview and apply member-owned calculator exports."""
import csv
import io
import json
import math
import re
from collections import Counter
from html import unescape

from bs4 import BeautifulSoup

from config.catalogue_identity import canonical_item, recipe_flavour_sql, visible_flavour_sql
from config.catalogue_product_policy import recipe_product_allowed
from db import get_db
from models import recipes_repo
from services import flavour_request_service, recipe_story_service

MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 2000
VC_HEADER = (
    "recipe id", "recipe name", "recipe created", "flavours", "total flavour percent",
    "notes", "steep time", "updated", "orig recipe id", "description", "revision",
    "released", "image", "profile",
)
STASH_HEADER = {"brand", "flavour", "quantity_ml"}
SD_STASH_HEADER = {"manufacturer", "flavor", "on hand (ml)", "cost/bottle", "cost/ml", "location"}
PROFILE_MAP = {name.lower(): name for name in recipe_story_service.PROFILES} | {
    "fruity": "Fruit", "nutty": "Nut", "spicy": "Spice", "cooling": "Menthol",
    "cake": "Bakery", "cookie": "Bakery", "ice cream": "Cream",
}


def _plain_text(value):
    if "<" not in value:
        return unescape(value).strip()
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(["br", "p", "div", "li"]):
        tag.append("\n")
    return unescape(soup.get_text()).strip()


def _percent(raw):
    try:
        value = float(raw.strip().removesuffix("%"))
    except ValueError:
        raise ValueError("An ingredient percentage or total is invalid.") from None
    if not math.isfinite(value) or not 0 < value < 100:
        raise ValueError("Flavour percentages must be greater than zero and below 100.")
    return value


def _vc_recipe(values):
    source_id, name, created, flavours, declared, notes, steep, _, original, description, revision, _, _, profile = values
    if not re.fullmatch(r"[0-9]{1,18}", source_id) or not name.strip():
        raise ValueError("Missing or invalid VC recipe ID or name.")
    ingredients = []
    for line in flavours.splitlines():
        if not line.strip():
            continue
        try:
            flavour_id, details = line.split(",", 1)
            flavour, brand, amount = (part.strip() for part in details.rsplit(",", 2))
        except ValueError:
            raise ValueError("Invalid ingredient line.") from None
        if not flavour_id.strip().isdigit() or not flavour or not brand:
            raise ValueError("An ingredient is missing its VC ID, name or brand.")
        ingredients.append((unescape(brand), unescape(flavour), _percent(amount)))
    total = math.fsum(amount for _, _, amount in ingredients)
    if not 0 < total < 100 or not math.isclose(total, _percent(declared), abs_tol=0.011):
        raise ValueError("The ingredient percentages and VC total disagree.")
    steep_match = re.fullmatch(r"(\d{1,3})\s*(day|week)s?", steep.strip(), re.I)
    days = int(steep_match[1]) * (7 if steep_match[2].lower() == "week" else 1) if steep_match else None
    if days is not None and days > 365:
        days = None
    try:
        profiles = json.loads(profile)
    except ValueError:
        profiles = []
    profiles = [PROFILE_MAP[p.lower()] for p in profiles if isinstance(p, str) and p.lower() in PROFILE_MAP] if isinstance(profiles, list) else []
    notes = "" if notes.strip() == "There are no notes for this recipe" else _plain_text(notes)
    source = f"VC recipe {source_id}"
    if original.strip().isdigit() and int(original):
        source += f" · original VC recipe {original}"
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", revision):
        source += f" · revision {revision}"
    if re.fullmatch(r"\d{4}-\d\d-\d\d(?: \d\d:\d\d:\d\d)?", created):
        source += f" · created {created}"
    if steep.strip() and days is None:
        source += f"\nVC steep time: {_plain_text(steep)}"
    return {
        "name": _plain_text(name), "source_id": str(int(source_id)), "ingredients": ingredients,
        "total": total, "notes": notes, "description": _plain_text(description),
        "profiles": profiles, "steep_days": days, "source": source,
    }


def _stash_quantity(raw):
    try:
        quantity = float(raw)
    except ValueError:
        raise ValueError("Enter a quantity in ml.") from None
    if not math.isfinite(quantity) or not 0 <= quantity <= 1_000_000:
        raise ValueError("Quantity must be between 0 and 1,000,000 ml.")
    return quantity


def _stash_item(brand, name, raw, *, allow_missing_amount=False):
    brand, name, raw = brand.strip(), name.strip(), raw.strip()
    if not brand or not name:
        return {"name": name or brand or "Unnamed flavour", "issue": "Manufacturer and flavour name needed"}
    row = {"name": f"{brand} · {name}", "identity": (brand, name), "key": canonical_item(brand, name)[2]}
    if raw or not allow_missing_amount:
        try:
            row["quantity"] = _stash_quantity(raw)
        except ValueError as error:
            row["issue"] = str(error)
    return row


def read_export(data):
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Choose a CSV file up to 4 MB.")
    try:
        raw = data.decode("utf-8-sig")
        if "\x00" in raw:
            raise ValueError("Choose a UTF-8 CSV export.")
        first, _, rest = raw.partition("\n")
        delimiter = ";" if first.count(";") > first.count(",") else ","
        header = tuple(cell.strip().lower() for cell in next(csv.reader([first], delimiter=delimiter, strict=True)))
        if header == VC_HEADER:
            # VC downloads have a comma-delimited header and semicolon-delimited records.
            body_delimiter = ";" if re.match(r'\s*"?[0-9]+"?\s*;', rest) else delimiter
            kind = "recipes"
            read_row = _vc_recipe
        elif len(header) == 3 and set(header) == STASH_HEADER:
            body_delimiter = delimiter
            kind = "stash"
            read_row = lambda values: _stash_item(*(values[header.index(key)] for key in ("brand", "flavour", "quantity_ml")))
        elif len(header) == len(SD_STASH_HEADER) and set(header) == SD_STASH_HEADER:
            body_delimiter = delimiter
            kind = "stash"
            read_row = lambda values: _stash_item(
                *(values[header.index(key)] for key in ("manufacturer", "flavor", "on hand (ml)")),
                allow_missing_amount=True,
            )
        else:
            raise ValueError("Use a VC recipe export, an SD stash export, or a stash CSV with brand, flavour, quantity_ml columns.")

        entries = []
        for number, values in enumerate(csv.reader(io.StringIO(rest, newline=""), delimiter=body_delimiter, strict=True), 1):
            if number > MAX_ROWS:
                raise ValueError(f"Split the export into files of at most {MAX_ROWS:,} records.")
            if not values or not any(value.strip() for value in values):
                continue
            try:
                if len(values) != len(header):
                    raise ValueError("The row has the wrong number of columns.")
                entry = read_row(values)
                if not entry["name"]:
                    raise ValueError("The name is blank.")
            except ValueError as error:
                entry = {"name": f"Record {number}", "issue": str(error)}
            entries.append(entry)
    except (UnicodeError, csv.Error, StopIteration):
        raise ValueError("The file could not be read as UTF-8 CSV.") from None
    if not entries:
        raise ValueError("This file contains no records.")
    return kind, entries


def _reference_index(db):
    index = {}
    for row in db.execute(f"""SELECT id, brand, name, category FROM flavor_reference fr
        WHERE COALESCE(active,1)=1 AND COALESCE(is_nicotine,0)=0 AND {recipe_flavour_sql('fr')}
        ORDER BY (COALESCE(price_per_ml,0)>0) DESC, id"""):
        index.setdefault(canonical_item(row["brand"], row["name"], row["category"])[2], row["id"])
    index.pop(None, None)
    return index


def _stash_index(db, user_id, lab_linked):
    if lab_linked:
        rows = db.execute("""SELECT li.reference_id AS target, fr.brand, fr.name, fr.category FROM library_items li
            JOIN flavor_reference fr ON fr.id=li.reference_id WHERE li.active=1""").fetchall()
    else:
        rows = db.execute("""SELECT f.id AS target, f.brand, f.canonical_name AS name, f.category FROM user_stash_items s
            JOIN flavours f ON f.id=s.flavour_id WHERE s.user_id=?""", (user_id,)).fetchall()
    existing = {row["target"] for row in rows}
    existing_keys = {canonical_item(row["brand"], row["name"], row["category"])[2] for row in rows}
    index = {}
    for row in db.execute(f"""SELECT id, source_flavor_ref_id, brand, canonical_name, category FROM flavours f
        WHERE {visible_flavour_sql('f')} ORDER BY source_flavor_ref_id IS NULL, id"""):
        target = row["source_flavor_ref_id"] if lab_linked else row["id"]
        if target is not None:
            index.setdefault(canonical_item(row["brand"], row["canonical_name"], row["category"])[2], target)
    index.pop(None, None)
    return index, existing, existing_keys


def _reviewed_matches(db, *, use_references):
    reviewed_keys = {row[0] for row in db.execute("SELECT DISTINCT identity_key FROM flavour_requests")}
    matches = {}
    for row in db.execute(f"""SELECT r.identity_key, f.id, f.source_flavor_ref_id
        FROM flavour_requests r JOIN flavours f ON f.id = r.flavour_id
        WHERE r.status IN ('added', 'matched') AND {visible_flavour_sql('f')}
        ORDER BY r.id DESC"""):
        target = row["source_flavor_ref_id"] if use_references else row["id"]
        if target is not None:
            matches.setdefault(row["identity_key"], target)
    return reviewed_keys, matches


def _request_candidate(brand, name, reviewed_keys):
    try:
        key = flavour_request_service.request_identity(brand, name)
    except ValueError:
        return None
    if key not in reviewed_keys and recipe_product_allowed(brand, name, "Flavor"):
        return key, brand, name
    return None


def review_candidates(rows):
    """One request per catalogue identity, including ingredients repeated across recipes."""
    return {
        key: (brand, name)
        for row in rows for key, brand, name in row.get("review_flavours", ())
    }


def queue_review_requests(rows, *, user_id):
    return sum(
        flavour_request_service.submit(user_id, brand, name, "", commit=False)
        for brand, name in review_candidates(rows).values()
    )


def preview(kind, entries, *, user_id, lab_linked):
    db = get_db()
    reviewed_keys, reviewed_matches = _reviewed_matches(db, use_references=kind == "recipes" or lab_linked)
    if kind == "recipes":
        references = reviewed_matches | _reference_index(db)
        existing = {row[0] for row in db.execute("SELECT recipe_code FROM recipes WHERE recipe_code LIKE ?", (f"member-vc-{user_id}-%",))}
        duplicates = Counter(entry.get("source_id") for entry in entries)
    else:
        catalogue, existing, existing_keys = _stash_index(db, user_id, lab_linked)
        targets = reviewed_matches | catalogue
        duplicates = Counter(targets.get(entry["key"]) for entry in entries if "issue" not in entry)
    results = []
    for entry in entries:
        result = dict(entry)
        if "issue" not in result:
            if kind == "recipes":
                source_id = entry["source_id"]
                code = f"member-vc-{user_id}-{source_id}"
                items = {}
                missing = []
                candidates = []
                for brand, name, amount in entry["ingredients"]:
                    reference = references.get(canonical_item(brand, name)[2])
                    if reference is None:
                        missing.append(f"{brand} · {name}")
                        candidate = _request_candidate(brand, name, reviewed_keys)
                        if candidate:
                            candidates.append(candidate)
                    else:
                        items[reference] = items.get(reference, 0) + amount
                if code in existing:
                    result["issue"] = "Already imported"
                elif duplicates[source_id] > 1:
                    result["issue"] = "Duplicate VC recipe ID in this file"
                elif missing:
                    result["issue"] = "Catalogue match needed for " + ", ".join(dict.fromkeys(missing))
                    result["review_flavours"] = candidates
                else:
                    result.update(code=code, items=list(items.items()))
            else:
                key = entry["key"]
                target = targets.get(key)
                if key in existing_keys or target in existing:
                    result["issue"] = "Already in your stash. Saved quantity kept."
                elif target is None:
                    result["issue"] = "Catalogue match needed"
                    candidate = _request_candidate(*entry["identity"], reviewed_keys)
                    if candidate:
                        result["review_flavours"] = [candidate]
                elif duplicates[target] > 1:
                    result["issue"] = "Duplicate flavour in this file. Combine the quantities before importing."
                else:
                    result["target"] = target
        results.append(result)
    return results


def fill_missing_amounts(rows, form):
    """Apply entered amounts only to matched, new items whose export lacked a quantity."""
    for index, row in enumerate(rows):
        if "issue" in row or "quantity" in row:
            continue
        raw = form.get(f"quantity_{index}", "").strip()
        if not raw:
            continue
        row["entered_quantity"] = raw
        try:
            row["quantity"] = _stash_quantity(raw)
        except ValueError as error:
            row["quantity_error"] = str(error)


def apply(kind, rows, *, user_id, lab_linked):
    """Save only reviewed rows inside the caller's transaction."""
    db = get_db()
    count = 0
    for row in rows:
        if row.get("issue"):
            continue
        if kind == "recipes":
            recipe_id = recipes_repo.insert_recipe(name=row["name"], recipe_code=row["code"], status="WIP", created_by=user_id)
            slug = re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-")[:100] or "recipe"
            recipes_repo.update_recipe_community_fields(
                recipe_id=recipe_id, visibility="private", status="WIP",
                slug=f"{slug}-{recipe_id}", library_type="personal",
            )
            version_id = recipes_repo.insert_recipe_version(
                recipe_id=recipe_id, version_number="1.0", target_vg_pg_ratio="70/30",
                total_flavor_percent=row["total"], notes=row["notes"], default_nic_mg_ml=0,
            )
            recipe_story_service.save(version_id=version_id, recipe_id=recipe_id, user_id=user_id, values={
                "short_description": "", "description": row["description"], "profiles": row["profiles"],
                "steep_days": row["steep_days"] if row["steep_days"] is not None else "", "private_notes": row["source"],
            })
            recipes_repo.insert_recipe_items(version_id=version_id, items=row["items"])
            recipes_repo.set_current_version(recipe_id=recipe_id, version_id=version_id)
        elif lab_linked:
            quantity = row.get("quantity")
            status = "Quantity unknown" if quantity is None else "In Use" if quantity > 0.05 else "Empty"
            db.execute("""INSERT INTO library_items
                (reference_id, current_stock, unit, low_stock_threshold, status, active)
                VALUES (?, ?, 'ml', 10.0, ?, 1)""",
                (row["target"], quantity if quantity is not None else 0.0, status))
        else:
            db.execute("INSERT INTO user_stash_items (user_id, flavour_id, quantity_ml) VALUES (?, ?, ?)",
                       (user_id, row["target"], row.get("quantity")))
        count += 1
    return count
