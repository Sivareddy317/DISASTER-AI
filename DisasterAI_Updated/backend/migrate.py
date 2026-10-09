import sqlite3
from pathlib import Path


def run_migration(db_path: str = "disaster.db"):
    db_file = Path(db_path)
    if not db_file.exists():
        print(f"Database file not found at {db_path}, skipping migration.")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Check columns in reports
    cur.execute("PRAGMA table_info(reports)")
    columns = [row[1] for row in cur.fetchall()]
    print(f"Current 'reports' columns: {columns}")

    modified = False

    if "incident_id" not in columns:
        print("Adding 'incident_id' column to 'reports'...")
        cur.execute("ALTER TABLE reports ADD COLUMN incident_id INTEGER REFERENCES incidents(id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_reports_incident_id ON reports (incident_id)")
        modified = True

    if "source_id" not in columns:
        print("Adding 'source_id' column to 'reports'...")
        cur.execute("ALTER TABLE reports ADD COLUMN source_id VARCHAR(100)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_reports_source_id ON reports (source_id)")
        modified = True

    if "raw_metadata" not in columns:
        print("Adding 'raw_metadata' column to 'reports'...")
        cur.execute("ALTER TABLE reports ADD COLUMN raw_metadata TEXT")
        modified = True

    if "stage" not in columns:
        print("Adding 'stage' column to 'reports'...")
        cur.execute("ALTER TABLE reports ADD COLUMN stage VARCHAR(20)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_reports_stage ON reports (stage)")
        modified = True

    # Check incidents table columns
    cur.execute("PRAGMA table_info(incidents)")
    inc_columns = [row[1] for row in cur.fetchall()]
    print(f"Current 'incidents' columns: {inc_columns}")

    conn.commit()
    conn.close()

    if modified:
        print("Migration applied successfully.")
    else:
        print("Database schema already up to date.")


if __name__ == "__main__":
    run_migration()
