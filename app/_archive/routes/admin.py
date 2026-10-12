from flask import Blueprint, render_template, flash, redirect, url_for, request, jsonify, session
from utils.auth import login_required, role_required
from db import get_db
from datetime import datetime
import requests
import re
import time

from scrapers.clyrolinx_scraper import run_clyrolinx_scraper

# Initialize the Blueprint
admin_bp = Blueprint("admin", __name__)

# ==========================================
# 0. ADMIN DASHBOARD
# ==========================================

@admin_bp.route("/admin")
@login_required
@role_required("Old Goat")
def index():
    """Main Admin Landing Page."""
    db = get_db()
    
    # Quick stats for the landing page
    user_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    log_count = db.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    
    # Get the timestamp of the last successful flavor sync
    last_sync_row = db.execute("""
        SELECT timestamp FROM audit_logs 
        WHERE action LIKE '%Sync%' 
        ORDER BY id DESC LIMIT 1
    """).fetchone()
    
    stats = {
        "user_count": user_count,
        "log_count": log_count,
        "last_sync": last_sync_row[0] if last_sync_row else "Never"
    }
    
    return render_template("admin_index.html", stats=stats)

# ==========================================
# 1. BLCK SYNC (SCRAPER & MANUAL OVERRIDES)
# ==========================================

@admin_bp.route("/admin/sync-blck", methods=["POST"])
@login_required
@role_required("Old Goat")
def sync_blck():
    """Triggers the BLCK Catalog Scraper with manual overrides."""
    db = get_db()
    added = 0
    updated = 0
    now = datetime.now()
    limit_pages = 15
    
    # --- B. MANUAL OVERRIDES (The Source of Truth) ---
    manual_items = [
        ("AuNic", "Freebase Nicotine VG - 1000ml", "Base", 1200, 1000),
        ("AuNic", "Freebase Nicotine VG - 5000ml", "Base", 6000, 5000),
        ("AuNic", "Freebase Nicotine PG - 1000ml", "Base", 1200, 1000),
        ("AuNic", "Freebase Nicotine PG - 5000ml", "Base", 6000, 5000),
        ("AuNic", "Salt Nicotine VG - 1000ml", "Base", 1250, 1000),
        ("AuNic", "Salt Nicotine VG - 5000ml", "Base", 6250, 5000),
        ("AuNic", "Salt Nicotine PG - 1000ml", "Base", 1250, 1000),
        ("AuNic", "Salt Nicotine PG - 5000ml", "Base", 6250, 5000),
        ("BLCK Flavour", "PG (Propylene Glycol) 100ml", "Base", 35, 100),
        ("BLCK Flavour", "PG (Propylene Glycol) 500ml", "Base", 65, 500),
        ("BLCK Flavour", "PG (Propylene Glycol) 1000ml", "Base", 119, 1000),
        ("BLCK Flavour", "PG (Propylene Glycol) 5000ml", "Base", 495, 5000),
        ("BLCK Flavour", "PG (Propylene Glycol) 24000ml", "Base", 2495, 24000),
        ("BLCK Flavour", "VG (Vegetable Glycerine) 500ml", "Base", 49, 500),
        ("BLCK Flavour", "VG (Vegetable Glycerine) 1000ml", "Base", 89, 1000),
        ("BLCK Flavour", "VG (Vegetable Glycerine) 2000ml", "Base", 175, 2000),
        ("BLCK Flavour", "VG (Vegetable Glycerine) 5000ml", "Base", 349, 5000),
        ("BLCK Flavour", "VG (Vegetable Glycerine) 24000ml", "Base", 1795, 24000),
        ("Gold-Nic", "Nicotine 36mg/ml VG 100ml", "Base", 99, 100),
        ("Gold-Nic", "Nicotine 100mg/ml PG 1L", "Base", 1590, 1000),
        ("Prime-Nic", "Nicotine Salts 100mg/ml PG 50ml", "Base", 160, 50),
        ("Scrawny Gecko", "(White Label) Nicotine VG 36mg 100ml", "Base", 110, 100)
    ]

    headers = {'User-Agent': 'Mozilla/5.0 Chrome/121.0.0.0'}
    
    try:
        # --- C. SCRAPE WEB DATA ---
        for page in range(1, limit_pages + 1):
            url = f"https://blck.co.za/products.json?limit=250&page={page}"
            res = requests.get(url, headers=headers, timeout=10)
            products = res.json().get('products', [])
            if not products: break

            for p in products:
                title = p.get('title', '')
                vendor = p.get('vendor', 'Unknown')
                p_type = p.get('product_type', '').lower()

                # Skip if item is in manual overrides
                if any(m[1] in title for m in manual_items): continue

                category = "Flavor"
                if any(x in title.lower() or x in p_type for x in ['bottle', 'cap', 'beaker', 'scale', 'syringe']):
                    category = "Hardware"
                elif any(x in title.lower() for x in ['vegetable glycerin', 'propylene glycol', 'nicotine', 'vg', 'pg']):
                    category = "Base"
                elif any(x in p_type for x in ['mod', 'kit', 'tank', 'coil', 'e-liquid', 'merchandise']):
                    continue

                handle = p.get('handle', '')
                full_url = f"https://blck.co.za/products/{handle}"
                price_total = float(p['variants'][0].get('price', 0)) if p.get('variants') else 0.0
                
                volume = 10.0 
                if category == "Base":
                    vol_match = re.search(r'(\d+)\s*(ml|l)', title.lower())
                    if vol_match:
                        num = float(vol_match.group(1))
                        unit = vol_match.group(2)
                        volume = num * 1000 if unit == 'l' else num

                price_per_ml = price_total / volume if volume > 0 else 0.0
                clean_name = title.replace(vendor, "").strip()

                existing = db.execute("SELECT id FROM flavor_reference WHERE name = ? AND brand = ?", (clean_name, vendor)).fetchone()
                if existing:
                    db.execute("UPDATE flavor_reference SET category=?, price_10ml=?, price_per_ml=?, url=?, last_scraped=? WHERE id=?",
                               (category, price_total, price_per_ml, full_url, now, existing[0]))
                    updated += 1
                else:
                    db.execute("INSERT INTO flavor_reference (name, brand, source, category, price_10ml, price_per_ml, url, last_scraped) VALUES (?, ?, 'BLCK', ?, ?, ?, ?, ?)",
                               (clean_name, vendor, category, price_total, price_per_ml, full_url, now))
                    added += 1
            db.commit()

        # --- D. INJECT MANUAL DATA ---
        for m_brand, m_name, m_cat, m_price, m_vol in manual_items:
            m_per_ml = m_price / m_vol
            existing = db.execute("SELECT id FROM flavor_reference WHERE name = ? AND brand = ?", (m_name, m_brand)).fetchone()
            if existing:
                db.execute("UPDATE flavor_reference SET category=?, price_10ml=?, price_per_ml=?, last_scraped=? WHERE id=?",
                           (m_cat, m_price, m_per_ml, now, existing[0]))
                updated += 1
            else:
                db.execute("INSERT INTO flavor_reference (name, brand, source, category, price_10ml, price_per_ml, last_scraped) VALUES (?, ?, 'MANUAL', ?, ?, ?, ?)",
                           (m_name, m_brand, m_cat, m_price, m_per_ml, now))
                added += 1
        
        db.commit()

        # Log completion to audit_logs
        db.execute("INSERT INTO audit_logs (actor_id, action, timestamp) VALUES (?, ?, ?)",
                  (session.get('user_id'), f"BLCK Sync: +{added} added / {updated} updated", now))
        db.commit()

        return jsonify({"status": "success", "added": added, "updated": updated})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_bp.route("/admin/sync-clyrolinx", methods=["POST"])
@login_required
@role_required("Old Goat")
def sync_clyrolinx():
    """Triggers the Clyrolinx scraper and logs results."""
    db = get_db()
    now = datetime.now()

    try:
        added_rows, skipped = run_clyrolinx_scraper(db, max_pages=200, delay_s=1.0)

        db.execute(
            "INSERT INTO audit_logs (actor_id, action, timestamp) VALUES (?, ?, ?)",
            (
                session.get("user_id"),
                f"CLYROLINX Sync: +{len(added_rows)} added / {skipped} duplicates skipped",
                now,
            ),
        )
        db.commit()

        return jsonify({"status": "success", "added": len(added_rows), "skipped": skipped})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================================
# 2. VC SYNC (LAB INTELLIGENCE)
# ==========================================

@admin_bp.route("/admin/sync-vc", methods=["POST"])
@login_required
@role_required("Old Goat")
def sync_vc():
    """Triggers the batched VC Scraper and logs results."""
    from vc_scraper import scrape_vc_flavors
    db = get_db()
    
    data = request.get_json(silent=True) or {}
    is_deep = data.get('deep_sync', False)
    pages_to_scrape = 200 if is_deep else 20
    
    try:
        results = scrape_vc_flavors(db, limit_pages=pages_to_scrape)
        added = results.get('added', 0)
        updated = results.get('updated', 0)
        ts = results.get('timestamp', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        log_type = "DEEP VC Sync" if is_deep else "VC Sync"
        log_msg = f"{log_type}: +{added} new / ~{updated} updated"
        
        db.execute(
            "INSERT INTO audit_logs (actor_id, action, timestamp) VALUES (?, ?, ?)",
            (session.get('user_id'), log_msg, ts)
        )
        db.commit()
        
        return jsonify(results)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================================
# 3. BACKUP MANAGEMENT & USERS (Keep as is)
# ==========================================
# ... [Include manage_backups, manual_backup, run_restore, manage_users from your original file]