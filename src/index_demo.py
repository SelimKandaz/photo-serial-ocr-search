#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path

DB = Path("serial_ocr_demo.db")
DATA = Path("examples/fake_ocr_records.json")

def normalize(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum() or ch.isspace())

def main():
    records = json.loads(DATA.read_text(encoding="utf-8"))
    conn = sqlite3.connect(DB)
    conn.execute("drop table if exists ocr_records")
    conn.execute("create table ocr_records (filename text, text text, normalized text)")
    for r in records:
        conn.execute(
            "insert into ocr_records values (?, ?, ?)",
            (r["filename"], r["text"], normalize(r["text"]))
        )
    conn.commit()
    print(f"Indexed {len(records)} fake OCR records into {DB}")

if __name__ == "__main__":
    main()
