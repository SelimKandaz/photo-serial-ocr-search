# Photo Serial OCR Search

A local-first OCR indexing concept for searching serial numbers inside images and scanned documents.

This repository is a sanitized public portfolio version. It includes architecture, fake sample data and a lightweight searchable-index prototype without real photos, real serial numbers or company records.

## Features

- Local SQLite index
- Store image filename, extracted text and normalized search tokens
- Search for partial serial numbers
- Keep private images out of Git
- Provide fake sample data only
- Keep generated `.db` files out of Git

## Quick start

```bash
python src/photo_serial_search.py index examples/fake_ocr_records.json
python src/photo_serial_search.py search DEMO
```

Legacy two-step demo scripts are also included:

```bash
python src/index_demo.py
python src/search_demo.py DEMO
```

## Privacy note

Do not publish real photos, extracted text from private documents, real serial numbers or internal inventory records.
