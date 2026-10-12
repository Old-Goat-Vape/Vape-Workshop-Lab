from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from db import get_db
from utils.auth import login_required, role_required
from backup import create_backup  # Import the persistence manager

ingredients_bp = Blueprint("ingredients", __name__)

# ==========================================
# 0. MAIN INVENTORY VIEW
# ==========================================

@ingredients_bp.route("/ingredients")
@login_required
def ingredients():
    """Master view showing all active library items."""
    db = get_db()
    search_query = request.args.get('search', '')
    
    # Query for Flavors
    query_flavors = """
        SELECT i.*, f.brand, f.typical_usage_min, f.typical_usage_max
        FROM ingredients i
        JOIN flavor_library f ON i.id = f.ingredient_id 
        WHERE i.active=1
    """
    params = []
    if search_query:
        query_flavors += " AND (i.name LIKE ? OR f.brand LIKE ?)"
        params = [f'%{search_query}%', f'%{search_query}%']
    
    query_flavors += " ORDER BY f.brand ASC, i.name ASC"
    flavors = db.execute(query_flavors, params).fetchall()
    
    # Query for Bases (VG/PG/NIC)
    base_ingredients = db.execute("SELECT * FROM base_liquids WHERE active=1 ORDER BY name ASC").fetchall()
    
    # Query for Hardware (Bottles/Caps)
    hardware = db.execute("SELECT * FROM stock_library WHERE active=1 ORDER BY item_name ASC").fetchall()
    
    return render_template("ingredients.html", 
                           flavors=flavors, 
                           base_ingredients=base_ingredients, 
                           hardware=hardware,
                           search_query=search_query)

@ingredients_bp.route("/library/add")
@login_required
def add_stock_page():
    return render_template("add_stock.html")

# ==========================================
# 1. SMART ADD & DUPLICATE PREVENTION
# ==========================================

@ingredients_bp.route("/library/flavors/add", methods=["POST"])
@login_required
@role_required("Manager", "Master")
def add_flavor():
    db = get_db()
    name = request.form['name'].strip()
    brand = request.form['brand'].strip()
    new_stock = float(request.form.get('stock', 0) or 0)
    new_cost = float(request.form.get('cost', 0) or 0)

    try:
        # Check for existing Item
        existing = db.execute("""
            SELECT i.id, i.stock_ml, i.cost_per_ml 
            FROM ingredients i 
            JOIN flavor_library f ON i.id = f.ingredient_id
            WHERE LOWER(i.name) = LOWER(?) AND LOWER(f.brand) = LOWER(?) AND i.active=1
        """, (name, brand)).fetchone()

        if existing:
            # Weighted Average Cost Calculation
            total_stock = existing['stock_ml'] + new_stock
            if total_stock > 0:
                avg_cost = ((existing['stock_ml'] * existing['cost_per_ml']) + (new_stock * new_cost)) / total_stock
            else:
                avg_cost = new_cost
            
            db.execute("UPDATE ingredients SET stock_ml = ?, cost_per_ml = ? WHERE id = ?", 
                       (total_stock, avg_cost, existing['id']))
            msg = f"Updated existing {brand} {name}. New avg cost: R{avg_cost:.4f}/ml"
        else:
            # Create New Entry
            cur = db.execute("""INSERT INTO ingredients (name, category, cost_per_ml, stock_ml, low_stock_ml) 
                               VALUES (?, ?, ?, ?, ?)""", 
                            (name, request.form['category'], new_cost, new_stock, 10.0))
            ing_id = cur.lastrowid
            
            db.execute("""INSERT INTO flavor_library (ingredient_id, brand, typical_usage_min, typical_usage_max, notes) 
                          VALUES (?, ?, ?, ?, ?)""",
                       (ing_id, brand, 
                        float(request.form.get('usage_min', 0.5) or 0.5), 
                        float(request.form.get('usage_max', 10.0) or 10.0), 
                        request.form.get('notes', '')))
            msg = "Flavor added successfully."
        
        db.commit()
        create_backup(trigger_reason="stock_change") # PERSISTENCE TRIGGER
        flash(msg)
    except Exception as e:
        db.rollback()
        flash(f"Error: {e}")
    return redirect(url_for("ingredients.ingredients"))

# ==========================================
# 2. THE ADJUST & EDIT ROUTES
# ==========================================

@ingredients_bp.route("/adjust", methods=["POST"])
@login_required
def adjust():
    db = get_db()
    ing_id = request.form.get("ingredient_id")
    change = float(request.form.get("change") or 0)
    ing_type = request.form.get("type") 

    if ing_type == 'base':
        db.execute("UPDATE base_liquids SET quantity_ml = quantity_ml + ? WHERE id = ?", (change, ing_id))
    elif ing_type == 'hardware':
        db.execute("UPDATE stock_library SET quantity = quantity + ? WHERE id = ?", (change, ing_id))
    else:
        db.execute("UPDATE ingredients SET stock_ml = stock_ml + ? WHERE id = ?", (change, ing_id))
    
    db.commit()
    create_backup(trigger_reason="quick_adjust") # PERSISTENCE TRIGGER
    flash("Stock adjusted and backed up.")
    return redirect(url_for("ingredients.ingredients"))

@ingredients_bp.route("/library/edit/<string:item_type>/<int:id>", methods=["GET", "POST"])
@login_required
@role_required("Manager", "Master")
def edit_item(item_type, id):
    db = get_db()
    
    if request.method == "POST":
        if item_type == 'flavor':
            db.execute("""UPDATE ingredients SET name=?, category=?, cost_per_ml=?, stock_ml=?, low_stock_ml=? WHERE id=?""",
                       (request.form['name'], request.form['category'], float(request.form['cost']), 
                        float(request.form['stock']), float(request.form['threshold']), id))
            db.execute("UPDATE flavor_library SET brand=?, notes=? WHERE ingredient_id=?", 
                       (request.form['brand'], request.form['notes'], id))
        
        elif item_type == 'base':
            db.execute("""UPDATE base_liquids SET name=?, type=?, strength=?, quantity_ml=?, cost_per_ml=?, low_stock_threshold=? WHERE id=?""",
                       (request.form['name'], request.form['type'], float(request.form['strength']), 
                        float(request.form['quantity']), float(request.form['cost']), float(request.form['threshold']), id))
        
        elif item_type == 'hardware':
            db.execute("""UPDATE stock_library SET item_name=?, type=?, quantity=?, cost_per_item=?, low_stock_threshold=?, size=?, notes=? WHERE id=?""",
                       (request.form['name'], request.form['type'], int(request.form['quantity']), 
                        float(request.form['cost']), int(request.form['threshold']), request.form['size'], request.form['notes'], id))
        
        db.commit()
        create_backup(trigger_reason="manual_edit") # PERSISTENCE TRIGGER
        flash("Item updated successfully.")
        return redirect(url_for("ingredients.ingredients"))

    item = None
    if item_type == 'flavor':
        item = db.execute("SELECT i.*, f.brand, f.notes FROM ingredients i JOIN flavor_library f ON i.id=f.ingredient_id WHERE i.id=?", (id,)).fetchone()
    elif item_type == 'base':
        item = db.execute("SELECT * FROM base_liquids WHERE id=?", (id,)).fetchone()
    elif item_type == 'hardware':
        item = db.execute("SELECT * FROM stock_library WHERE id=?", (id,)).fetchone()
        
    return render_template("edit_item.html", item=item, type=item_type)

# ==========================================
# 3. UTILITIES & DELETES
# ==========================================

@ingredients_bp.route("/library/bases/add", methods=["POST"])
@login_required
@role_required("Manager", "Master")
def add_base():
    db = get_db()
    db.execute("""INSERT INTO base_liquids (name, type, strength, quantity_ml, cost_per_ml, notes) 
                  VALUES (?, ?, ?, ?, ?, ?)""",
               (request.form['name'], request.form['type'], float(request.form['strength']), 
                float(request.form['quantity']), float(request.form.get('cost', 0)), request.form.get('notes', '')))
    db.commit()
    create_backup(trigger_reason="base_added") # PERSISTENCE TRIGGER
    return redirect(url_for("ingredients.ingredients"))

@ingredients_bp.route("/library/stock/add", methods=["POST"])
@login_required
@role_required("Manager", "Master")
def add_stock_item():
    db = get_db()
    db.execute("""INSERT INTO stock_library (item_name, type, quantity, cost_per_item, size, notes) 
                  VALUES (?, ?, ?, ?, ?, ?)""",
               (request.form['item_name'], request.form['type'], int(request.form['quantity']), 
                float(request.form.get('cost', 0)), request.form['size'], request.form.get('notes', '')))
    db.commit()
    create_backup(trigger_reason="hardware_added") # PERSISTENCE TRIGGER
    return redirect(url_for("ingredients.ingredients"))

@ingredients_bp.route("/library/delete/<string:item_type>/<int:id>")
@login_required
@role_required("Master")
def delete_item(item_type, id):
    db = get_db()
    table = "ingredients" if item_type == 'flavor' else ("base_liquids" if item_type == 'base' else "stock_library")
    db.execute(f"UPDATE {table} SET active=0 WHERE id=?", (id,))
    db.commit()
    create_backup(trigger_reason="item_archived") # PERSISTENCE TRIGGER
    flash("Item archived.")
    return redirect(url_for("ingredients.ingredients"))