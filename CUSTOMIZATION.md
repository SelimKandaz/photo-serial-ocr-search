# Customization Guide

This project can be adapted for local OCR indexing and serial-number search workflows.

## Common customization points

- OCR engine
- Image folder path
- Supported file types
- Text normalization rules
- Search ranking
- SQLite database path
- GUI labels and workflow text

## Start here

- `src/photo_serial_search.py` for the lightweight SQLite demo
- `src/photo_ocr_strict_search.py` for the full desktop OCR app

## Keep local

Keep private images, extracted OCR text and generated databases outside the repository.
