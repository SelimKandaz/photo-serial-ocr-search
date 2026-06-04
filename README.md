# Photo Serial OCR Search

A local-first OCR indexing and search toolkit for finding serial numbers inside images, PDFs and scanned documents.

This repository now includes two layers:

1. `src/photo_serial_search.py`  
   A lightweight fake-data SQLite demo for understanding the search/index structure.

2. `src/photo_ocr_strict_search.py`  
   The original full desktop OCR search application, sanitized for open-source use.

## Full desktop OCR application

```bash
pip install -r requirements.txt
python src/photo_ocr_strict_search.py
```

Optional OCR backends:

- Tesseract through `pytesseract`
- PaddleOCR when installed
- PDF text extraction through `pypdf`
- PDF image rendering through PyMuPDF
- HEIC/HEIF support through `pillow-heif`

## Lightweight demo

```bash
python src/photo_serial_search.py index examples/fake_ocr_records.json
python src/photo_serial_search.py search DEMO
```

## Privacy note

Do not commit real photos, extracted private text, real serial-number batches, internal inventory records or generated local databases.

## License

MIT
