# Photo Serial OCR Search

A local-first OCR indexing concept for searching serial numbers inside images and scanned documents.

This repository is a sanitized public portfolio version. It includes architecture, fake sample data and a lightweight searchable-index prototype without real photos, real serial numbers or company records.

## Why this project exists

Serial numbers are often trapped inside photos, labels and scanned documents. A local OCR index can make those records searchable without uploading sensitive images to a cloud service.

## Features

- Local SQLite index concept
- Store image filename, extracted text and normalized tokens
- Search for partial serial numbers
- Keep private images out of Git
- Provide fake sample data only

## Quick start

```bash
python src/index_demo.py
python src/search_demo.py DEMO
```

## Technology focus

- Python
- SQLite
- OCR pipeline design
- Local search
- Operations documentation
- Privacy-conscious indexing
