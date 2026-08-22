import sqlite3
import os
from pathlib import Path
import subprocess

def export_dashboard_db():
    project_root = Path(__file__).resolve().parent.parent
    src = project_root / 'data' / 'economic_analysis.db'
    dst = project_root / 'data' / 'dashboard_release.db'

    if not src.exists():
        print(f"Source DB {src} not found.")
        return

    if dst.exists():
        dst.unlink()

    print("Copying required tables into release DB...")
    conn = sqlite3.connect(str(dst))
    conn.execute(f"ATTACH DATABASE '{src}' AS src")

    # The dashboard only needs these tables (omitting the 6M+ row turn_macro_snapshots)
    tables = [
        'teams', 
        'matches', 
        'games', 
        'game_economic_summaries', 
        'diagnostic_events', 
        'titanium_events'
    ]
    
    for t in tables:
        # Check if table exists in source
        try:
            conn.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
        except sqlite3.OperationalError as e:
            print(f"Warning: Failed to copy {t}: {e}")

    conn.commit()
    conn.close()

    size_mb = dst.stat().st_size / (1024 * 1024)
    print(f"Created {dst.name}: {size_mb:.1f} MB (vs original {src.stat().st_size / (1024 * 1024):.1f} MB)")

    print("Compressing...")
    gz_dst = dst.with_suffix('.db.gz')
    subprocess.run(['gzip', '-c', str(dst)], stdout=open(gz_dst, 'wb'))
    
    gz_size_mb = gz_dst.stat().st_size / (1024 * 1024)
    print(f"Compression complete: {gz_dst.name} is {gz_size_mb:.1f} MB")

if __name__ == "__main__":
    export_dashboard_db()
