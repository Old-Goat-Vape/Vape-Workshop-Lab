import os
import shutil
from datetime import datetime
import glob

# Configuration - Fixed path to match your root folder
DB_PATH = "old_goat_lab.db" 
BACKUP_DIR = "backups/"
MAX_BACKUPS = 30

def ensure_backup_dir():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

def create_backup(trigger_reason="auto"):
    ensure_backup_dir()
    if not os.path.exists(DB_PATH):
        print(f"Backup failed: {DB_PATH} not found.")
        return False

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"backup_{timestamp}_{trigger_reason}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)

    try:
        shutil.copy2(DB_PATH, backup_path)
        _enforce_retention()
        return True
    except Exception as e:
        print(f"Backup failed: {e}")
        return False

def _enforce_retention():
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "*.db")), key=os.path.getmtime)
    while len(backups) > MAX_BACKUPS:
        oldest_backup = backups.pop(0)
        os.remove(oldest_backup)

def restore_backup(backup_name):
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    if os.path.exists(backup_path):
        create_backup(trigger_reason="pre_restore_safety")
        shutil.copy2(backup_path, DB_PATH)
        return True
    return False

def list_backups():
    if not os.path.exists(BACKUP_DIR):
        return []
    backups = []
    for file in glob.glob(os.path.join(BACKUP_DIR, "*.db")):
        stats = os.stat(file)
        backups.append({
            'name': os.path.basename(file),
            'date': datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            'size': f"{stats.st_size / 1024:.1f} KB"
        })
    return sorted(backups, key=lambda x: x['date'], reverse=True)