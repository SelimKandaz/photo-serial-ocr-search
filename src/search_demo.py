#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path

DB = Path("serial_ocr_demo.db")

def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python src/search_demo.py SEARCH_TEXT")
    query = "".join(ch for ch in sys.argv[1].upper() if ch.isalnum() or ch.isspace())
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "select filename, text from ocr_records where normalized like ?",
        (f"%{query}%",)
    ).fetchall()
    for filename, text in rows:
        print(f"{filename}: {text}")

if __name__ == "__main__":
    main()
