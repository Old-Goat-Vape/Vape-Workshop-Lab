"""BLCK variant normalization + canonical costing fixture tests."""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from scrapers.blck_variant_normalize import (
    normalize_variant_fields,
    parse_pack_volume_ml,
    variant_matches_criteria,
)
from scrapers.blck_scraper import _upsert_variant
from services import blck_canonical_costing_service as canon


VG_FIXTURE = {
    "id": 65639972878,
    "title": "VG (Vegetable Glycerine)",
    "handle": "vg-vegetable-glycerine",
    "vendor": "BLCK Flavour",
    "product_type": "Base",
    "variants": [
        {"id": 1, "title": "500ml", "option1": "500ml", "price": "49.00", "available": True},
        {"id": 2, "title": "1 Litre", "option1": "1 Litre", "price": "89.00", "available": True},
        {"id": 365231013902, "title": "5 Litre", "option1": "5 Litre", "price": "369.00", "available": True},
        {"id": 9, "title": "30kg (24 Litre)", "option1": "30kg (24 Litre)", "price": "1795.00", "available": True},
    ],
}

PG_FIXTURE = {
    "id": 65596653582,
    "title": "PG (Propylene Glycol)",
    "handle": "pg-propylene-glycol",
    "vendor": "BLCK Flavour",
    "product_type": "Base",
    "variants": [
        {"id": 11, "title": "500ml", "option1": "500ml", "price": "65.00", "available": True},
        {"id": 365087391758, "title": "5 Litre", "option1": "5 Litre", "price": "495.00", "available": True},
    ],
}

GOLD_FIXTURE = {
    "id": 10698750222,
    "title": "Gold Nic (Nicotine)",
    "handle": "nicotine-blck-flavour",
    "vendor": "Gold-Nic",
    "product_type": "Base",
    "variants": [
        {"id": 100, "title": "100mg/ml VG 1 Litre", "option1": "100mg/ml VG 1 Litre", "price": "1790.00", "available": True},
        {"id": 188698296334, "title": "100mg/ml PG 1 Litre", "option1": "100mg/ml PG 1 Litre", "price": "1790.00", "available": True},
        {"id": 101, "title": "36mg/ml PG 100ml", "option1": "36mg/ml PG 100ml", "price": "110.00", "available": True},
    ],
}

SALT_FIXTURE = {
    "id": 7264583614525,
    "title": "AuNic - Salt Nicotine",
    "handle": "aunic-professional-series-salt-nicotine-1000ml",
    "vendor": "AuNic",
    "product_type": "Base",
    "variants": [
        {
            "id": 41133687078973,
            "title": "100mg/ml VG / 1 Litre",
            "option1": "100mg/ml VG",
            "option2": "1 Litre",
            "price": "1450.00",
            "available": True,
        },
        {
            "id": 41133687144509,
            "title": "100mg/ml PG / 1 Litre",
            "option1": "100mg/ml PG",
            "option2": "1 Litre",
            "price": "1450.00",
            "available": True,
        },
    ],
}


def _schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE blck_product_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL DEFAULT 'BLCK',
            source_product_id TEXT NOT NULL,
            source_variant_id TEXT NOT NULL,
            product_title TEXT,
            variant_title TEXT,
            handle TEXT,
            vendor TEXT,
            product_type TEXT,
            option1 TEXT,
            option2 TEXT,
            option3 TEXT,
            sku TEXT,
            available INTEGER DEFAULT 1,
            pack_volume_ml REAL,
            nicotine_strength_mg_ml REAL,
            carrier TEXT,
            nicotine_type TEXT,
            pack_price REAL NOT NULL DEFAULT 0,
            price_per_ml REAL NOT NULL DEFAULT 0,
            product_url TEXT,
            last_scraped TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, source_variant_id)
        );
        CREATE TABLE blck_variant_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL DEFAULT 'BLCK',
            source_variant_id TEXT NOT NULL,
            pack_price REAL NOT NULL,
            price_per_ml REAL NOT NULL,
            available INTEGER DEFAULT 1,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE costing_canonical_variants (
            role TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'BLCK',
            source_product_id TEXT,
            source_variant_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            unavailable_reason TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


class TestVariantNormalize(unittest.TestCase):
    def test_volumes(self):
        self.assertEqual(parse_pack_volume_ml("100ml"), 100.0)
        self.assertEqual(parse_pack_volume_ml("500ml"), 500.0)
        self.assertEqual(parse_pack_volume_ml("1 Litre"), 1000.0)
        self.assertEqual(parse_pack_volume_ml("5 Litre"), 5000.0)
        self.assertEqual(parse_pack_volume_ml("30kg (24 Litre)"), 24000.0)

    def test_gold_nic_parse(self):
        f = normalize_variant_fields(
            product_title="Gold Nic (Nicotine)",
            vendor="Gold-Nic",
            variant_title="100mg/ml PG 1 Litre",
            option1="100mg/ml PG 1 Litre",
        )
        self.assertEqual(f["pack_volume_ml"], 1000.0)
        self.assertEqual(f["nicotine_strength_mg_ml"], 100.0)
        self.assertEqual(f["carrier"], "PG")
        self.assertEqual(f["nicotine_type"], "freebase")

    def test_aunic_salt_option_combo(self):
        f = normalize_variant_fields(
            product_title="AuNic - Salt Nicotine",
            vendor="AuNic",
            variant_title="100mg/ml VG / 1 Litre",
            option1="100mg/ml VG",
            option2="1 Litre",
        )
        self.assertEqual(f["pack_volume_ml"], 1000.0)
        self.assertEqual(f["nicotine_strength_mg_ml"], 100.0)
        self.assertEqual(f["carrier"], "VG")
        self.assertEqual(f["nicotine_type"], "salt")
        self.assertTrue(
            variant_matches_criteria(
                f,
                pack_volume_ml=1000.0,
                nicotine_strength_mg_ml=100.0,
                carrier="VG",
                nicotine_type="salt",
            )
        )


class TestBlckVariantPersistence(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        _schema(self.db)

    def tearDown(self):
        self.db.close()

    def _load(self, product):
        for v in product["variants"]:
            _upsert_variant(self.db, product=product, variant=v, now="t1")
        self.db.commit()

    def test_vg_sizes_distinct_and_5l_normalized(self):
        self._load(VG_FIXTURE)
        rows = self.db.execute("SELECT source_variant_id, pack_volume_ml, pack_price, price_per_ml FROM blck_product_variants").fetchall()
        self.assertEqual(len(rows), 4)
        five = self.db.execute(
            "SELECT * FROM blck_product_variants WHERE source_variant_id='365231013902'"
        ).fetchone()
        self.assertEqual(float(five["pack_volume_ml"]), 5000.0)
        self.assertAlmostEqual(float(five["pack_price"]), 369.0)
        self.assertAlmostEqual(float(five["price_per_ml"]), 369.0 / 5000.0, places=6)

    def test_pg_5l_distinct(self):
        self._load(PG_FIXTURE)
        five = self.db.execute(
            "SELECT * FROM blck_product_variants WHERE source_variant_id='365087391758'"
        ).fetchone()
        self.assertEqual(float(five["pack_volume_ml"]), 5000.0)
        self.assertAlmostEqual(float(five["price_per_ml"]), 495.0 / 5000.0, places=6)

    def test_gold_nic_exact_selection(self):
        self._load(GOLD_FIXTURE)
        with mock.patch.object(canon, "get_db", return_value=self.db):
            canon.sync_canonical_bindings(self.db)
            snap = canon.get_canonical_costing_snapshot(self.db)
        entry = snap["roles"]["freebase_nic"]
        self.assertEqual(entry["status"], "active")
        self.assertEqual(str(entry["source_variant_id"]), "188698296334")
        self.assertEqual(entry["carrier"], "PG")
        self.assertAlmostEqual(float(entry["price_per_ml"]), 1.79, places=4)

    def test_aunic_salt_exact_selection(self):
        self._load(SALT_FIXTURE)
        with mock.patch.object(canon, "get_db", return_value=self.db):
            canon.sync_canonical_bindings(self.db)
            snap = canon.get_canonical_costing_snapshot(self.db)
        entry = snap["roles"]["salt_nic"]
        self.assertEqual(entry["status"], "active")
        self.assertEqual(str(entry["source_variant_id"]), "41133687078973")
        self.assertEqual(entry["carrier"], "VG")
        self.assertAlmostEqual(float(entry["price_per_ml"]), 1.45, places=4)

    def test_price_change_same_variant_id(self):
        self._load(VG_FIXTURE)
        with mock.patch.object(canon, "get_db", return_value=self.db):
            canon.sync_canonical_bindings(self.db)
            before = canon.get_canonical_costing_snapshot(self.db)["roles"]["vg"]
            self.assertAlmostEqual(float(before["pack_price"]), 369.0)
            # Second import: same variant id, new price
            updated = dict(VG_FIXTURE)
            updated["variants"] = [
                {"id": 365231013902, "title": "5 Litre", "option1": "5 Litre", "price": "399.00", "available": True}
            ]
            _upsert_variant(self.db, product=updated, variant=updated["variants"][0], now="t2")
            self.db.commit()
            canon.sync_canonical_bindings(self.db)
            after = canon.get_canonical_costing_snapshot(self.db)["roles"]["vg"]
        self.assertEqual(after["source_variant_id"], before["source_variant_id"])
        self.assertAlmostEqual(float(after["pack_price"]), 399.0)
        self.assertAlmostEqual(float(after["price_per_ml"]), 399.0 / 5000.0, places=6)
        hist = self.db.execute(
            "SELECT COUNT(1) c FROM blck_variant_price_history WHERE source_variant_id='365231013902'"
        ).fetchone()["c"]
        self.assertGreaterEqual(int(hist), 2)

    def test_missing_variant_flags_unavailable_no_silent_substitute(self):
        # Only 500ml VG present — must NOT bind 5L role to 500ml.
        slim = dict(VG_FIXTURE)
        slim["variants"] = [{"id": 1, "title": "500ml", "option1": "500ml", "price": "49.00", "available": True}]
        self._load(slim)
        with mock.patch.object(canon, "get_db", return_value=self.db):
            canon.sync_canonical_bindings(self.db)
            snap = canon.get_canonical_costing_snapshot(self.db)
        vg = snap["roles"]["vg"]
        self.assertEqual(vg["status"], "unavailable")
        self.assertNotEqual(str(vg.get("source_variant_id") or ""), "1")
        self.assertIn("unavailable", (vg.get("unavailable_reason") or "").lower())


if __name__ == "__main__":
    unittest.main()
