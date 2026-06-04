#!/usr/bin/env python3
import argparse
import json
import sqlite3
from pathlib import Path

DB_DEFAULT = Path("serial_ocr_demo.db")

def normalize(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum() or ch.isspace())

def connect(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        create table if not exists ocr_records (
            id integer primary key autoincrement,
            filename text not null,
            text text not null,
            normalized text not null
        )
    """)
    return conn

def index_records(json_file: Path, db_path: Path):
    records = json.loads(json_file.read_text(encoding="utf-8"))
    conn = connect(db_path)
    conn.execute("delete from ocr_records")
    for record in records:
        conn.execute(
            "insert into ocr_records (filename, text, normalized) values (?, ?, ?)",
            (record["filename"], record["text"], normalize(record["text"]))
        )
    conn.commit()
    return len(records)

def search_records(query: str, db_path: Path):
    conn = connect(db_path)
    normalized_query = normalize(query)
    return conn.execute(
        "select filename, text from ocr_records where normalized like ? order by filename",
        (f"%{normalized_query}%",)
    ).fetchall()

def main():
    parser = argparse.ArgumentParser(description="Local serial-number OCR index demo.")
    sub = parser.add_subparsers(dest="command", required=True)

    index_cmd = sub.add_parser("index", help="Index fake OCR JSON records.")
    index_cmd.add_argument("json_file", type=Path)
    index_cmd.add_argument("--db", type=Path, default=DB_DEFAULT)

    search_cmd = sub.add_parser("search", help="Search indexed OCR records.")
    search_cmd.add_argument("query")
    search_cmd.add_argument("--db", type=Path, default=DB_DEFAULT)

    args = parser.parse_args()
    if args.command == "index":
        print(f"Indexed {index_records(args.json_file, args.db)} records into {args.db}")
    elif args.command == "search":
        rows = search_records(args.query, args.db)
        for filename, text in rows:
            print(f"{filename}: {text}")
        if not rows:
            print("No matches found.")

if __name__ == "__main__":
    main()
