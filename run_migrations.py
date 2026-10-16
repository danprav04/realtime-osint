#!/usr/bin/env python3
"""
run_migrations.py - Convenience root runner for Realtime OSINT Database Migrations.

Usage:
  python run_migrations.py                # Apply pending migrations (upgrade head)
  python run_migrations.py status         # Check current migration revision
  python run_migrations.py downgrade      # Roll back 1 revision
  python run_migrations.py stamp head     # Stamp database to head without applying
"""

import sys
import os

# Ensure current directory is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from backend.app.db.migrator import main

if __name__ == "__main__":
    main()
