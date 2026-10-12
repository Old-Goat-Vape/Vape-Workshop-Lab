"""Fixture-based scraper parser regression tests (no live network)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FIX = APP_ROOT / "tests" / "fixtures" / "scrapers"


def _schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE flavor_reference (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT,
            name TEXT,
            source TEXT,
            category TEXT,
            price_per_ml REAL DEFAULT 0,
            url TEXT,
            last_scraped TEXT,
            active INTEGER DEFAULT 1,
            usage_notes TEXT,
            product_url TEXT,
            price_raw REAL,
            unit_size_ml REAL,
            UNIQUE(brand, name)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            action TEXT,
            target_table TEXT,
            target_id INTEGER,
            details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
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
            option1 TEXT, option2 TEXT, option3 TEXT,
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
    db.commit()


class TestClyrolinxParser(unittest.TestCase):
    def test_parse_store_api_price_and_ml(self):
        from scrapers.clyrolinx_scraper import parse_flavor_data, _parse_price, _parse_unit_size_ml

        synthetic = {
            "name": "Banana Cream Concentrate 10ml",
            "permalink": "https://clyrolinx.co.za/product/banana-cream-10ml/",
            "short_description": "<p>Lab flavour</p>",
            "categories": [{"slug": "flavours", "name": "Flavours"}],
            "prices": {"price": "14900", "currency_minor_unit": 2},
        }
        flav = parse_flavor_data(synthetic)
        self.assertIsNotNone(flav)
        assert flav is not None
        self.assertEqual(flav.name, "Banana Cream Concentrate 10ml")
        self.assertEqual(flav.price_raw, 149.0)
        self.assertEqual(flav.unit_size_ml, 10.0)
        self.assertAlmostEqual(flav.price_raw / flav.unit_size_ml, 14.9, places=4)

        sample = json.loads((FIX / "clyrolinx_store_api_sample.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(sample), 1)
        parsed_any = [parse_flavor_data(row) for row in sample]
        self.assertTrue(any(p is not None for p in parsed_any))

        self.assertEqual(_parse_price("R 149.00"), 149.0)
        self.assertEqual(_parse_price("R149"), 149.0)
        self.assertEqual(_parse_price("R 149,00"), 149.0)
        self.assertEqual(_parse_price("From R149"), 149.0)
        self.assertEqual(_parse_unit_size_ml("Banana 10ml"), 10.0)
        self.assertEqual(_parse_unit_size_ml("Base 1L"), 1000.0)
        self.assertIsNone(_parse_unit_size_ml("No size"))

    def test_perfume_skipped(self):
        from scrapers.clyrolinx_scraper import parse_flavor_data

        row = {
            "name": "Test Perfume",
            "permalink": "https://clyrolinx.co.za/product/test/",
            "categories": [{"slug": "perfume", "name": "Perfume"}],
            "prices": {"price": "1000", "currency_minor_unit": 2},
            "short_description": "",
        }
        self.assertIsNone(parse_flavor_data(row))

    def test_upsert_updates_price_without_duplicate(self):
        from scrapers.clyrolinx_scraper import ScrapeFlavor, upsert_flavor

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        _schema(db)
        flav = ScrapeFlavor(
            brand="Clyrolinx",
            name="Test Flavour 10ml",
            category="Flavor",
            usage_notes="",
            product_url="https://clyrolinx.co.za/product/test/",
            price_raw=100.0,
            unit_size_ml=10.0,
        )
        existing: dict = {}
        self.assertEqual(upsert_flavor(db, flav, existing_by_norm=existing, now="t1"), "added")
        flav2 = ScrapeFlavor(
            brand="Clyrolinx",
            name="Test Flavour 10ml",
            category="Flavor",
            usage_notes="",
            product_url="https://clyrolinx.co.za/product/test/",
            price_raw=120.0,
            unit_size_ml=10.0,
        )
        self.assertEqual(upsert_flavor(db, flav2, existing_by_norm=existing, now="t2"), "updated")
        row = db.execute("SELECT COUNT(1) c, price_per_ml FROM flavor_reference").fetchone()
        self.assertEqual(row["c"], 1)
        self.assertAlmostEqual(float(row["price_per_ml"]), 12.0, places=4)


class TestBlckParser(unittest.TestCase):
    def test_scrape_writes_from_fixture(self):
        from scrapers import blck_scraper

        sample = json.loads((FIX / "blck_products_sample.json").read_text(encoding="utf-8"))

        class _Resp:
            def json(self):
                return sample

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        _schema(db)

        with mock.patch.object(blck_scraper.requests, "get", return_value=_Resp()):
            # Second page empty to stop pagination.
            def _get(url, **kwargs):
                if "page=1" in url:
                    return _Resp()

                class _Empty:
                    def json(self):
                        return {"products": []}

                return _Empty()

            with mock.patch.object(blck_scraper.requests, "get", side_effect=_get):
                result = blck_scraper.scrape_vc_flavors(db, limit_pages=2)

        self.assertIn("added", result)
        self.assertGreater(result["added"] + result["updated"], 0)
        count = db.execute("SELECT COUNT(1) c FROM flavor_reference WHERE source='BLCK'").fetchone()["c"]
        self.assertGreater(count, 0)
        priced = db.execute(
            "SELECT COUNT(1) c FROM flavor_reference WHERE source='BLCK' AND COALESCE(price_per_ml,0) > 0"
        ).fetchone()["c"]
        self.assertGreater(priced, 0)


class TestVcFlavourParser(unittest.TestCase):
    def test_parse_row_list_and_missing_price_fields(self):
        from scrapers.vc_flavours_scraper import parse_vc_flavour_row

        row = [
            "1",
            "Banana Cream",
            "TFA",
            '<a href="https://vceliquidrecipes.com/flavour/1">link</a>',
            "Desc",
            "10",
            "20",
            "2020-01-01",
            "4.5",
            "5%",
            "",
        ]
        parsed = parse_vc_flavour_row(row)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["name"], "Banana Cream")
        self.assertEqual(parsed["brand"], "TFA")
        self.assertEqual(parsed["avg_usage"], 5.0)
        self.assertIn("vceliquidrecipes.com", parsed["vc_url"])

        bad = parse_vc_flavour_row(["1", "", "TFA"])
        self.assertIsNone(bad)

    def test_identity_index_avoids_rescan(self):
        from scrapers.vc_flavours_scraper import build_identity_index, find_existing_reference

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        _schema(db)
        db.execute(
            "INSERT INTO flavor_reference (brand, name, source, category, price_per_ml, active) VALUES (?,?,?,?,?,1)",
            ("TFA", "Banana Cream", "VC", "Flavor", 0),
        )
        db.commit()
        index = build_identity_index(db)
        hit = find_existing_reference(db, brand="TFA", flavor_name="Banana Cream", identity_index=index)
        self.assertIsNotNone(hit)
        miss = find_existing_reference(db, brand="TFA", flavor_name="Missing", identity_index=index)
        self.assertIsNone(miss)


class TestSafePrint(unittest.TestCase):
    def test_safe_print_survives_cp1252(self):
        from scrapers.safe_print import safe_print
        import io

        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        # Should not raise even with emoji.
        safe_print("🚀 test", file=buf)


@unittest.skipUnless(os.environ.get("TVW_LIVE_SCRAPER_SMOKE") == "1", "live smoke disabled")
class TestLiveScraperSmoke(unittest.TestCase):
    def test_blck_live_one_page(self):
        from scrapers.blck_scraper import scrape_vc_flavors

        path = os.path.join(tempfile.mkdtemp(), "live.db")
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        _schema(db)
        result = scrape_vc_flavors(db, limit_pages=1)
        self.assertGreater(result["added"] + result["updated"], 0)

    def test_clyrolinx_live_one_page(self):
        from scrapers.clyrolinx_scraper import run_clyrolinx_scraper

        path = os.path.join(tempfile.mkdtemp(), "live.db")
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        _schema(db)
        added, skipped, updated = run_clyrolinx_scraper(db, max_pages=1, delay_s=0.0)
        self.assertGreater(len(added) + skipped + updated, 0)


if __name__ == "__main__":
    unittest.main()
