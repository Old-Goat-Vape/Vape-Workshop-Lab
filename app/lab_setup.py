import sqlite3
import os
from werkzeug.security import generate_password_hash

# CONFIGURATION
DB_PATH = 'instance/goat_labs.db'
SCHEMA_PATH = 'schema.sql'

FLV_LIST = [
    "Coolcumber", "Fresh Watermelon", "Fruit Fusion", "Garden Mint", "Huckleberry",
    "Lemon Lime Soda", "Ligero Leaf", "Mangotang", "Menthol Mist", "Nat. Mint",
    "Rainbow Sherbet", "Red Grapefruit", "Acai", "Alpine Strawberry", "Amaretto Sour",
    "Apple", "Apple Cider", "Apple Cranberry", "Apple Filling", "Apple Pop",
    "Apple Razz", "Apricot", "Arabian T", "Avocado", "Back Bar Bitters", "Banana",
    "Basil", "Beer Nuts", "Berry Blend", "Berry Cheesecake", "Biscotti", "Black Cherry",
    "Black Currant", "Black Tea", "Blackberry", "Blackberry Blossom", "Blood Orange",
    "Blue Raz", "Blueberry", "Blueberry Muffin", "Bourbon", "Boysenberry", "Brie Cheese",
    "Brulee", "Brut Bubble Wine", "Bubble Gum", "Butterscotch", "Cactus Aloe",
    "Cactus Apple", "Candied Blueberry", "Candy Roll", "Cantaloupe", "Caramel",
    "Carrot", "Cavendish", "Chai Spice", "Cheesecake", "Cherimoya", "Cherry Blossom",
    "Cherry Filling", "Chocolate Deutch", "Cinnamon Crunch", "Cinnamon Roll",
    "Citrus Soda", "Classic C", "Clove", "Coconut", "Coffee", "Cola", "Commercial C",
    "Connecticut Shade", "Cookie", "Cookie Dough", "Cool Menthol", "Cotton Candy",
    "Cranberry", "Cream", "Cream and Cookies", "Creamsicle", "Creme de Menthe",
    "Crispy Donut", "Crunch Cereal", "Cucumber", "Cupcake Batter", "Cured T",
    "Custard", "Donuts", "Dragon Fruit", "Dulce De Leche", "Eggnog", "Eisai Tea",
    "Elderflower", "Energy Drink", "Fig", "Fire Cinnamon", "Fried Dough", "Frosting",
    "Ginger", "Ginger Peach", "Ginger Snap", "Golden Kiwi", "Graham Cracker",
    "Granny Smith", "Granola", "Grape", "Grapefruit Blend", "Greek Yogurt",
    "Green Apple", "Green Tea", "Guanabana", "Guava Dark Berry", "Hazelnut", "Heat",
    "Hibiscus Sweet", "Honey Bee", "Honeydew", "Horchata", "Ice", "Irish Cream",
    "Jackfruit", "Jammy Berry", "Jammy Grape", "Juniper Gin", "Kentucky Blend",
    "Kinako Soy", "Kiwi", "Lembas Bread", "Lemon", "Lemon Grass", "Lemon Tea",
    "Lemonade", "Licorice", "Lime", "Lime Wedge", "Lovage Root", "Lychee", "Macaroon",
    "Mango", "Mango Pineapple Passion", "Maple Bar", "Marshmallow", "Marshmallow Treat",
    "Marshmallow Vanilla", "Melon Rind", "Mild T", "Milk", "Milk & Honey",
    "Milk Chocolate", "Mint Candy", "Mocha", "Morning Mimosa", "Moscato", "Native T",
    "Nectarine", "Oak Barrel", "Oatmeal Raisin", "Orange Citrus", "Oriental T",
    "Papaya Punch", "Passion Fruit", "Pastry Zest", "PB Cream", "Peach", "Peach Gummy",
    "Peanut Butter", "Pear", "Pearesto", "Peppermint", "Persimmon", "Persimmon Spice",
    "Pineapple", "Pink Fruit", "Pink Guava", "Pink Lemonade", "Pistachio", "Pomegranate",
    "Popcorn", "Pound Cake", "Pralines", "Pucker", "Pumpkin Bread", "Pumpkin Spice",
    "Rainier Cherry", "Raisin Rum", "Raspberry", "Red Apple", "Red Burley",
    "Red Cinnamon", "Red Raspberry", "Red Tea", "Red Vanilla Soda", "Red Velvet",
    "Rich Cinnamon", "Ripe Mango", "Rock & Road", "Root Beer", "Rose Essence",
    "RWB Popsicle Blast", "RY4", "Salmiakki", "Sangria", "Saturn Peach",
    "Smoked Butterscotch", "Smooth Vanilla", "Smoothie Base", "Smore", "Snow Cream",
    "Sour Apple", "Soursop", "Spearmint", "Starch Base", "Strawberry", "Strawberry Cream",
    "Strawberry Filling", "Strawberry Lime", "Strawberry Smash", "Sugar Orchid",
    "Sumo Citrus", "Sun Cookie", "Sweet & Smokey", "Sweet C", "Sweet Coconut",
    "Sweet Cream", "Sweet Crunch", "Sweet Dough", "Sweet Fig", "Sweet Mango",
    "Sweetness", "Tangerine", "Tart Blast", "Tatanka T", "Tequila Agave", "Thai Chai",
    "Toffee", "Torrone", "Tricks Cereal", "Tropical Citrus", "Tropical Punch",
    "Turkish T", "Vanilla Bean", "Vanilla Custard", "Vanilla Pudding", "Vanilla T",
    "Virginia T", "Waffle", "Watermelon", "Wheat", "Whipped Cream", "White Chocolate",
    "Wild Melon", "Wintergreen", "Wood Spice", "Yakima Hops", "Yam", "Yuzu"
]

def run_setup():
    print("🐐 OLD GOAT LABS - NUCLEAR RESET 🐐")
    
    # 1. Clear Database
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            print("🗑️  Previous database removed.")
        except PermissionError:
            print("❌ Error: Close app.py or your DB browser before running setup!")
            return

    if not os.path.exists('instance'):
        os.makedirs('instance')

    # 2. Rebuild Tables
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, 'r') as f:
        conn.executescript(f.read())
    print("🏗️  Tables rebuilt successfully.")

    # 3. Create Admin
    hashed_pw = generate_password_hash("2580")
    conn.execute("""
        INSERT INTO users (username, password_hash, role, status) 
        VALUES (?, ?, ?, ?)
    """, ("Old Goat", hashed_pw, "Old Goat", "Active"))
    
    # 4. Process Flavor List
    print(f"💉 Inducting {len(FLV_LIST)} flavors at 15ml (Weight calc: 15.54g)...")
    
    cursor = conn.cursor()
    for flavor_name in FLV_LIST:
        # 4a. Add to Master Catalog
        cursor.execute("""
            INSERT INTO flavor_reference (brand, name, density, avg_usage, source)
            VALUES (?, ?, ?, ?, ?)
        """, ("FLV", flavor_name, 1.036, 0.0, "BLCK"))
        
        ref_id = cursor.lastrowid
        
        # 4b. Add to Physical Inventory (15ml * 1.036 density = 15.54g)
        cursor.execute("""
            INSERT INTO library_items (reference_id, current_stock, unit, low_stock_threshold)
            VALUES (?, ?, 'g', 2.0)
        """, (ref_id, 15.54))

    conn.commit()
    conn.close()
    print("\n✅ SETUP COMPLETE.")
    print("🚀 Run 'python app.py' to begin mixing.")

if __name__ == "__main__":
    run_setup()