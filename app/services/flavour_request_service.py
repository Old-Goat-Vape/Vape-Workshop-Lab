"""Member requests stay separate from the catalogue until an admin reviews them."""
from urllib.parse import urlsplit

from config.canonical_library_brands import normalize_flavor_identity_name
from config.catalogue_identity import canonical_item, visible_flavour_sql
from config.catalogue_product_policy import recipe_product_allowed
from db import get_db
from models import inventory_repo, recipes_repo, tvw_admin_repo

SCHEMA = """
CREATE TABLE IF NOT EXISTS flavour_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    brand TEXT NOT NULL,
    name TEXT NOT NULL,
    product_url TEXT NOT NULL DEFAULT '',
    identity_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'added', 'matched', 'declined')),
    flavour_id INTEGER REFERENCES flavours(id) ON DELETE SET NULL,
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_flavour_requests_pending_identity
    ON flavour_requests(identity_key) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_flavour_requests_status ON flavour_requests(status, id);
CREATE INDEX IF NOT EXISTS idx_flavour_requests_requester ON flavour_requests(requester_id, id);
"""


def submit(requester_id, brand, name, product_url):
    if not (0 < len(brand) <= 100 and 0 < len(name) <= 160):
        raise ValueError("Enter a brand and flavour name within the field limits.")
    _, _, key = canonical_item(brand, name)
    if not key:
        raise ValueError("Enter a valid brand and flavour name.")
    if product_url:
        try:
            url = urlsplit(product_url)
            valid = (
                len(product_url) <= 500 and url.scheme in {"https", "http"}
                and url.hostname and url.username is None and url.password is None
                and not any(ch.isspace() or ord(ch) < 32 or ch == '\\' for ch in product_url)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Enter a valid HTTP or HTTPS product link.")
    db = get_db()
    with db:
        result = db.execute(
            """
            INSERT INTO flavour_requests (requester_id, brand, name, product_url, identity_key)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(identity_key) WHERE status = 'pending' DO NOTHING
            """,
            (requester_id, brand, name, product_url, key),
        )
        return result.rowcount == 1


def pending_count():
    return get_db().execute("SELECT COUNT(*) FROM flavour_requests WHERE status = 'pending'").fetchone()[0]


def member_requests(user_id):
    return get_db().execute(
        "SELECT brand, name, status FROM flavour_requests WHERE requester_id = ? ORDER BY id DESC LIMIT 20",
        (user_id,),
    ).fetchall()


def pending_requests(page, page_size):
    return get_db().execute(
        """
        SELECT r.id, r.brand, r.name, r.created_at, u.username
        FROM flavour_requests r LEFT JOIN users u ON u.id = r.requester_id
        WHERE r.status = 'pending'
        ORDER BY r.id LIMIT ? OFFSET ?
        """,
        (page_size, (page - 1) * page_size),
    ).fetchall()


def fetch(request_id):
    return get_db().execute("SELECT * FROM flavour_requests WHERE id = ?", (request_id,)).fetchone()


def _create_flavour(db, request):
    brand, name, key = canonical_item(request["brand"], request["name"])
    if not recipe_product_allowed(brand, name, "Flavor"):
        raise ValueError("Only flavour concentrates can be added to the flavour catalogue.")

    duplicate = db.execute(
        """
        SELECT 1 FROM flavours
        WHERE tvw_catalogue_key(brand, canonical_name, category) = ?
        UNION ALL
        SELECT 1 FROM flavor_reference
        WHERE tvw_catalogue_key(brand, name, category) = ?
           OR (brand = ? COLLATE NOCASE AND name = ? COLLATE NOCASE)
        LIMIT 1
        """,
        (key, key, brand, name),
    ).fetchone()
    if duplicate:
        raise ValueError("This identity is already in the catalogue. Match it to the existing entry or review it in Flavour Moderation.")

    reference_id = inventory_repo.insert_reference(brand=brand, name=name, category="Flavor")
    return db.execute(
        """
        INSERT INTO flavours (
            brand, canonical_name, category, source_flavor_ref_id,
            canonical_brand, normalized_name, source, created_at, updated_at
        )
        VALUES (?, ?, 'Flavor', ?, ?, ?, 'Manual', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (brand, name, reference_id, brand, normalize_flavor_identity_name(name)),
    ).lastrowid


def review(request_id, actor_id, actor_role, action, flavour_id):
    statuses = {"add": "added", "match": "matched", "decline": "declined"}
    if action not in statuses:
        raise ValueError("Choose a review action.")
    with recipes_repo.transaction(immediate=True) as db:
        request = fetch(request_id)
        if not request or request["status"] != "pending":
            raise ValueError("This request is no longer pending.")
        if action == "match":
            match = db.execute(
                f"SELECT 1 FROM flavours f WHERE f.id = ? AND {visible_flavour_sql('f')}",
                (flavour_id,),
            ).fetchone()
            if not match:
                raise ValueError("Choose an existing visible flavour to match.")
        else:
            flavour_id = _create_flavour(db, request) if action == "add" else None
        status = statuses[action]
        db.execute(
            """
            UPDATE flavour_requests
            SET status = ?, flavour_id = ?, reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, flavour_id, actor_id, request_id),
        )
        tvw_admin_repo.insert_admin_audit_log(
            actor_user_id=actor_id, actor_role=actor_role, action=f"flavour_request_{status}",
            target_type="flavour_request", target_id=request_id, details={"flavour_id": flavour_id},
            commit=False,
        )
