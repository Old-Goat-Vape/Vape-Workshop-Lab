import sqlite3
import os

db_path = os.path.join('instance', 'goat_lab.db')

def migrate():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print(f"🔧 Checking database at {db_path}...")
    
    # NOTE:
    # Schema changes have been moved into the application's startup migrations.
    # This legacy script is kept only to verify the presence of minimal tables
    # for older deployments.

    # Ensure flavor_reference table exists for the search
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS flavor_reference (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, 
            brand TEXT, 
            avg_usage REAL DEFAULT 0.0, 
            source TEXT, 
            last_scraped DATETIME,
            UNIQUE(name, brand)
        )
    """)
    print("✅ Verified 'flavor_reference' table exists.")

    conn.commit()
    conn.close()
    print("🚀 Database migration complete!")

if __name__ == "__main__":
    migrate()