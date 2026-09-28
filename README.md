# Photo Serial OCR Search

Find serial numbers inside photos, PDFs, and scanned documents. Everything is indexed and searched locally.

## Desktop app

```bash
pip install -r requirements.txt
python src/photo_ocr_strict_search.py
```

Supports Tesseract and PaddleOCR, PDF text and page rendering, and HEIC photos.

## Small demo

```bash
python src/photo_serial_search.py index examples/fake_ocr_records.json
python src/photo_serial_search.py search DEMO
```

## License

MIT
