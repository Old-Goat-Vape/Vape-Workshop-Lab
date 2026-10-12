import sqlite3
import os

# This finds the directory where THIS script is saved
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# This points to the instance folder in that same directory
db_path = os.path.join(BASE_DIR, 'instance', 'goat_lab.db')

def repair():
    print(f"🔍 Looking for database at: {db_path}")
    
    if not os.path.exists(os.path.dirname(db_path)):
        print("❌ 'instance' folder not found. Creating it...")
        os.makedirs(os.path.dirname(db_path))

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Ensure the table itself exists first
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS flavor_reference (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                brand TEXT
            )
        """)

        # NOTE:
        # Schema changes have been moved into the application's startup migrations.
        # This legacy script now only verifies that the database file exists and
        # that the minimal tables/indexes are present.

        # 3. Create the Unique Index for the scraper's ON CONFLICT
        try:
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_name_brand ON flavor_reference(name, brand)")
            print("✅ Unique index verified.")
        except Exception as e:
            print(f"ℹ️ Index info: {e}")

        conn.commit()
        conn.close()
        print("\n🚀 SUCCESS! Database is repaired. Restart your app and run the Sync.")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    repair()