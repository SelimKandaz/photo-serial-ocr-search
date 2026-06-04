import os
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from paddleocr import PaddleOCR  # type: ignore
except Exception:
    PaddleOCR = None

try:
    import pytesseract  # type: ignore
    from PIL import Image, ImageOps, ImageFilter  # type: ignore
except Exception:
    pytesseract = None
    Image = None
    ImageOps = None
    ImageFilter = None

try:
    from pillow_heif import register_heif_opener  # type: ignore
except Exception:
    register_heif_opener = None

try:
    from pypdf import PdfReader  # type: ignore
except Exception:
    PdfReader = None

try:
    import fitz  # type: ignore
except Exception:
    fitz = None


APP_TITLE = "Photo OCR Strict Search"

if register_heif_opener is not None:
    register_heif_opener()

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif"
}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS
DEFAULT_DB = Path("photo_index.db")


@dataclass
class SearchResult:
    path: str
    match_mode: str
    matched_text: str
    score: int


class OCRBackendError(RuntimeError):
    pass


class OCRIndexer:
    def __init__(self, db_path: Path, backend: str = "auto") -> None:
        self.db_path = db_path
        self.backend = backend.lower().strip()
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._ocr_model = None
        self._init_db()

    def _init_db(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                file_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                modified_time REAL NOT NULL,
                raw_text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                ocr_backend TEXT NOT NULL,
                indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
            CREATE INDEX IF NOT EXISTS idx_files_hash ON files(file_hash);
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def discover_files(self, root: Path) -> Iterable[Path]:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                yield path

    def file_fingerprint(self, path: Path) -> Tuple[str, int, float]:
        stat = path.stat()
        file_size = stat.st_size
        modified_time = stat.st_mtime

        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest(), file_size, modified_time

    def is_already_indexed(
        self,
        path: Path,
        file_hash: str,
        file_size: int,
        modified_time: float,
    ) -> bool:
        row = self.conn.execute(
            """
            SELECT 1
            FROM files
            WHERE path = ? AND file_hash = ? AND file_size = ? AND modified_time = ?
            """,
            (str(path), file_hash, file_size, modified_time),
        ).fetchone()
        return row is not None

    def normalize_text(self, text: str) -> str:
        text = text.upper()
        text = text.replace("\u2013", "-").replace("\u2014", "-")
        text = re.sub(r"[^A-Z0-9]+", "", text)
        return text

    def smart_variants(self, text: str) -> List[str]:
        groups = {
            "0": {"0", "O"},
            "O": {"0", "O"},
            "1": {"1", "I", "L"},
            "I": {"1", "I", "L"},
            "L": {"1", "I", "L"},
            "5": {"5", "S"},
            "S": {"5", "S"},
            "8": {"8", "B"},
            "B": {"8", "B"},
        }

        variants = {text}
        candidate_positions = [i for i, ch in enumerate(text) if ch in groups][:6]

        for pos in candidate_positions:
            next_variants = set()
            for current in variants:
                for alt in groups[current[pos]]:
                    chars = list(current)
                    chars[pos] = alt
                    next_variants.add("".join(chars))
            variants |= next_variants

        return sorted(variants)

    def _prepare_image_for_tesseract(self, image_path: Path):
        if Image is None or ImageOps is None:
            raise OCRBackendError("Pillow is not installed. Run: pip install pillow")

        if image_path.suffix.lower() in {".heic", ".heif"} and register_heif_opener is None:
            raise OCRBackendError(
                "HEIC/HEIF support needs pillow-heif. Run: pip install pillow-heif"
            )

        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)
        img = img.convert("L")
        img = img.filter(ImageFilter.SHARPEN)
        return img

    def _get_paddle_model(self):
        if PaddleOCR is None:
            raise OCRBackendError("PaddleOCR is not installed. Run: pip install paddleocr")

        if self._ocr_model is None:
            try:
                self._ocr_model = PaddleOCR(
                    lang="en",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            except Exception as exc:
                raise OCRBackendError(f"PaddleOCR init failed: {exc}") from exc

        return self._ocr_model

    def _convert_image_for_paddle(self, image_path: Path) -> str:
        suffix = image_path.suffix.lower()

        if suffix in {".jpg", ".jpeg", ".png", ".bmp"}:
            return str(image_path)

        if Image is None:
            raise OCRBackendError("Pillow is required to convert unsupported image formats.")

        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        temp_path = Path(temp.name)
        temp.close()
        img.save(temp_path, format="PNG")
        return str(temp_path)

    def extract_text_paddle(self, image_path: Path) -> str:
        model = self._get_paddle_model()
        temp_path_to_cleanup = None

        try:
            input_path = self._convert_image_for_paddle(image_path)
            if input_path != str(image_path):
                temp_path_to_cleanup = Path(input_path)

            result = model.predict(input_path)
        except Exception as exc:
            raise OCRBackendError(f"PaddleOCR predict failed for {image_path.name}: {exc}") from exc
        finally:
            if temp_path_to_cleanup and temp_path_to_cleanup.exists():
                try:
                    temp_path_to_cleanup.unlink()
                except Exception:
                    pass

        parts: List[str] = []
        if not result:
            return ""

        for res in result:
            data = getattr(res, "json", None)
            if not data:
                try:
                    data = res.json
                except Exception:
                    data = None

            if not data:
                continue

            rec_texts = data.get("rec_texts", [])
            if rec_texts:
                parts.extend([str(x) for x in rec_texts if x])

        return "\n".join(parts)

    def extract_text_tesseract(self, image_path: Path) -> str:
        if pytesseract is None:
            raise OCRBackendError(
                "pytesseract is not installed. Run: pip install pytesseract pillow"
            )

        img = self._prepare_image_for_tesseract(image_path)
        config = "--oem 3 --psm 6"
        try:
            return pytesseract.image_to_string(img, config=config)
        except Exception as exc:
            raise OCRBackendError(f"Tesseract OCR failed for {image_path.name}: {exc}") from exc

    def extract_text_from_image(self, image_path: Path) -> Tuple[str, str]:
        backend = self.backend
        errors: List[str] = []

        if backend in {"auto", "paddle"}:
            try:
                text = self.extract_text_paddle(image_path)
                return text, "paddle"
            except Exception as exc:
                errors.append(f"paddle: {exc}")
                if backend == "paddle":
                    try:
                        text = self.extract_text_tesseract(image_path)
                        return text, "tesseract-fallback"
                    except Exception as t_exc:
                        errors.append(f"tesseract fallback: {t_exc}")
                        raise OCRBackendError(" | ".join(errors)) from t_exc

        if backend in {"auto", "tesseract"}:
            try:
                text = self.extract_text_tesseract(image_path)
                return text, "tesseract"
            except Exception as exc:
                errors.append(f"tesseract: {exc}")
                raise OCRBackendError("No OCR backend available. " + " | ".join(errors)) from exc

        raise OCRBackendError("No OCR backend available. " + " | ".join(errors))

    def extract_text_from_pdf(self, pdf_path: Path) -> Tuple[str, str]:
        parts: List[str] = []

        if PdfReader is not None:
            try:
                reader = PdfReader(str(pdf_path))
                for page in reader.pages:
                    text = page.extract_text() or ""
                    if text.strip():
                        parts.append(text)
                if parts:
                    return "\n".join(parts), "pdf-text"
            except Exception:
                pass

        if fitz is not None:
            try:
                doc = fitz.open(str(pdf_path))
                try:
                    for page in doc:
                        text = page.get_text("text") or ""
                        if text.strip():
                            parts.append(text)
                    if parts:
                        return "\n".join(parts), "pdf-text"
                finally:
                    doc.close()
            except Exception:
                pass

        if fitz is None:
            raise OCRBackendError(
                "Scanned PDF fallback needs PyMuPDF. Run: pip install pymupdf"
            )

        page_texts: List[str] = []
        doc = fitz.open(str(pdf_path))
        try:
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                temp_base = pdf_path.with_suffix("")
                temp_png = temp_base.parent / f"{temp_base.name}__temp_page_{page.number + 1}.png"
                pix.save(str(temp_png))
                try:
                    text, _ = self.extract_text_from_image(temp_png)
                    if text.strip():
                        page_texts.append(text)
                finally:
                    try:
                        temp_png.unlink(missing_ok=True)
                    except Exception:
                        pass
        finally:
            doc.close()

        return "\n".join(page_texts), "pdf-ocr"

    def extract_text(self, file_path: Path) -> Tuple[str, str]:
        suffix = file_path.suffix.lower()
        if suffix in PDF_EXTENSIONS:
            return self.extract_text_from_pdf(file_path)
        return self.extract_text_from_image(file_path)

    def index_directory(
        self,
        root: Path,
        force: bool = False,
        progress_callback=None,
        error_callback=None,
    ) -> Tuple[int, int, int]:
        indexed = 0
        skipped = 0
        errors = 0

        files = list(self.discover_files(root))
        total = len(files)

        for idx, file_path in enumerate(files, start=1):
            try:
                if progress_callback:
                    progress_callback(f"Scanning {idx}/{total}: {file_path.name}")

                file_hash, file_size, modified_time = self.file_fingerprint(file_path)

                if not force and self.is_already_indexed(
                    file_path, file_hash, file_size, modified_time
                ):
                    skipped += 1
                    continue

                raw_text, backend_used = self.extract_text(file_path)
                normalized_text = self.normalize_text(raw_text)

                self.conn.execute(
                    """
                    INSERT INTO files (
                        path, file_hash, file_size, modified_time,
                        raw_text, normalized_text, ocr_backend
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        file_hash = excluded.file_hash,
                        file_size = excluded.file_size,
                        modified_time = excluded.modified_time,
                        raw_text = excluded.raw_text,
                        normalized_text = excluded.normalized_text,
                        ocr_backend = excluded.ocr_backend,
                        indexed_at = CURRENT_TIMESTAMP
                    """,
                    (
                        str(file_path),
                        file_hash,
                        file_size,
                        modified_time,
                        raw_text,
                        normalized_text,
                        backend_used,
                    ),
                )
                indexed += 1

            except Exception as exc:
                errors += 1
                if error_callback:
                    error_callback(file_path, exc, traceback.format_exc())

        self.conn.commit()
        return indexed, skipped, errors

    def search(self, query: str, mode: str = "strict") -> List[SearchResult]:
        mode = mode.lower().strip()
        normalized_query = self.normalize_text(query)
        if not normalized_query:
            return []

        rows = self.conn.execute(
            "SELECT path, raw_text, normalized_text FROM files"
        ).fetchall()

        results: List[SearchResult] = []
        variants = [normalized_query]

        if mode == "smart-strict":
            variants = self.smart_variants(normalized_query)
        elif mode != "strict":
            raise ValueError("mode must be 'strict' or 'smart-strict'")

        for row in rows:
            normalized_text = row["normalized_text"] or ""
            raw_text = row["raw_text"] or ""
            best_variant = None

            for variant in variants:
                if variant in normalized_text:
                    best_variant = variant
                    break

            if best_variant is not None:
                results.append(
                    SearchResult(
                        path=row["path"],
                        match_mode=mode,
                        matched_text=self._best_snippet(raw_text, query),
                        score=len(best_variant),
                    )
                )

        results.sort(key=lambda x: (-x.score, x.path))
        return results

    def _best_snippet(self, raw_text: str, original_query: str, width: int = 160) -> str:
        if not raw_text:
            return ""

        collapsed = re.sub(r"\s+", " ", raw_text).strip()
        if not collapsed:
            return ""

        idx = collapsed.upper().find(original_query.upper())
        if idx == -1:
            return collapsed[:width]

        start = max(0, idx - width // 2)
        end = min(len(collapsed), idx + len(original_query) + width // 2)
        return collapsed[start:end]

    def export_results_csv(self, results: Sequence[SearchResult], output_path: Path) -> None:
        with output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["path", "match_mode", "matched_text", "score"])
            for item in results:
                writer.writerow([item.path, item.match_mode, item.matched_text, item.score])

    def stats(self) -> dict:
        row = self.conn.execute("SELECT COUNT(*) AS count FROM files").fetchone()
        return {"indexed_files": int(row["count"]) if row else 0}


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x780")
        self.minsize(1000, 650)

        self.db_path = tk.StringVar(value=str(DEFAULT_DB.resolve()))
        self.folder_path = tk.StringVar(value="")
        self.backend = tk.StringVar(value="paddle")
        self.search_mode = tk.StringVar(value="strict")
        self.query = tk.StringVar(value="")
        self.status = tk.StringVar(value="Ready.")
        self.results_cache: List[SearchResult] = []
        self.busy = False

        self._build_ui()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        top = ttk.LabelFrame(root, text="Index Settings", padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Source Folder").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(top, textvariable=self.folder_path).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(top, text="Browse", command=self.choose_folder).grid(row=0, column=2, padx=(8, 0), pady=4)

        ttk.Label(top, text="Database File").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(top, textvariable=self.db_path).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(top, text="Save As", command=self.choose_db_file).grid(row=1, column=2, padx=(8, 0), pady=4)

        ttk.Label(top, text="OCR Backend").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Combobox(
            top,
            textvariable=self.backend,
            values=["paddle", "tesseract", "auto"],
            state="readonly",
            width=18,
        ).grid(row=2, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(top)
        buttons.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Index Folder", command=self.start_index).pack(side="left")
        ttk.Button(buttons, text="Reindex Folder", command=lambda: self.start_index(force=True)).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Show Stats", command=self.show_stats).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Clear Debug Log", command=self.clear_debug_log).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Save Debug Log", command=self.save_debug_log).pack(side="left", padx=(8, 0))

        top.columnconfigure(1, weight=1)

        mid = ttk.LabelFrame(root, text="Search", padding=10)
        mid.pack(fill="x", pady=(12, 0))

        ttk.Label(mid, text="Search Text").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        query_entry = ttk.Entry(mid, textvariable=self.query)
        query_entry.grid(row=0, column=1, sticky="ew", pady=4)
        query_entry.bind("<Return>", lambda event: self.run_search())

        ttk.Label(mid, text="Mode").grid(row=0, column=2, sticky="w", padx=(12, 8), pady=4)
        ttk.Combobox(
            mid,
            textvariable=self.search_mode,
            values=["strict", "smart-strict"],
            state="readonly",
            width=18,
        ).grid(row=0, column=3, sticky="w", pady=4)

        ttk.Button(mid, text="Search", command=self.run_search).grid(row=0, column=4, padx=(12, 0), pady=4)
        ttk.Button(mid, text="Export CSV", command=self.export_csv).grid(row=0, column=5, padx=(8, 0), pady=4)

        mid.columnconfigure(1, weight=1)

        center = ttk.PanedWindow(root, orient="vertical")
        center.pack(fill="both", expand=True, pady=(12, 0))

        results_frame = ttk.LabelFrame(center, text="Results", padding=10)
        debug_frame = ttk.LabelFrame(center, text="Debug Log", padding=10)
        center.add(results_frame, weight=3)
        center.add(debug_frame, weight=2)

        columns = ("path", "mode", "score", "snippet")
        self.tree = ttk.Treeview(results_frame, columns=columns, show="headings", height=14)
        self.tree.heading("path", text="File Path")
        self.tree.heading("mode", text="Mode")
        self.tree.heading("score", text="Score")
        self.tree.heading("snippet", text="OCR Snippet")
        self.tree.column("path", width=470, anchor="w")
        self.tree.column("mode", width=110, anchor="center")
        self.tree.column("score", width=70, anchor="center")
        self.tree.column("snippet", width=480, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self.open_selected_file)

        results_scroll = ttk.Scrollbar(results_frame, orient="vertical", command=self.tree.yview)
        results_scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=results_scroll.set)

        self.debug_text = tk.Text(debug_frame, wrap="word", height=12)
        self.debug_text.pack(side="left", fill="both", expand=True)
        self.debug_text.configure(state="disabled")

        debug_scroll = ttk.Scrollbar(debug_frame, orient="vertical", command=self.debug_text.yview)
        debug_scroll.pack(side="right", fill="y")
        self.debug_text.configure(yscrollcommand=debug_scroll.set)

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Label(bottom, textvariable=self.status).pack(side="left")
        ttk.Label(bottom, text="Double click a result to open the file").pack(side="right")

    def set_busy(self, value: bool) -> None:
        self.busy = value
        self.config(cursor="watch" if value else "")
        self.update_idletasks()

    def append_debug(self, message: str) -> None:
        self.debug_text.configure(state="normal")
        self.debug_text.insert("end", message + "\n")
        self.debug_text.see("end")
        self.debug_text.configure(state="disabled")

    def clear_debug_log(self) -> None:
        self.debug_text.configure(state="normal")
        self.debug_text.delete("1.0", "end")
        self.debug_text.configure(state="disabled")
        self.status.set("Debug log cleared.")

    def save_debug_log(self) -> None:
        output = filedialog.asksaveasfilename(
            title="Save debug log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile="photo_ocr_debug_log.txt",
        )
        if not output:
            return

        content = self.debug_text.get("1.0", "end").strip()
        Path(output).write_text(content, encoding="utf-8")
        self.status.set(f"Debug log saved to {output}")
        messagebox.showinfo(APP_TITLE, f"Debug log saved to:\n{output}")

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select image/PDF folder")
        if folder:
            self.folder_path.set(folder)

    def choose_db_file(self) -> None:
        filename = filedialog.asksaveasfilename(
            title="Choose database file",
            defaultextension=".db",
            filetypes=[("SQLite DB", "*.db"), ("All files", "*.*")],
            initialfile="photo_index.db",
        )
        if filename:
            self.db_path.set(filename)

    def start_index(self, force: bool = False) -> None:
        if self.busy:
            return

        folder = self.folder_path.get().strip()
        if not folder:
            messagebox.showwarning(APP_TITLE, "Choose a source folder first.")
            return

        folder_path = Path(folder)
        if not folder_path.exists():
            messagebox.showerror(APP_TITLE, "Selected folder does not exist.")
            return

        self.set_busy(True)
        self.status.set("Indexing started...")
        self.append_debug("=" * 80)
        self.append_debug(f"INDEX STARTED | force={force} | backend={self.backend.get()} | folder={folder_path}")

        def worker() -> None:
            try:
                indexer = OCRIndexer(Path(self.db_path.get()), backend=self.backend.get())
                try:
                    indexed, skipped, errors = indexer.index_directory(
                        folder_path,
                        force=force,
                        progress_callback=lambda msg: self.after(0, lambda m=msg: self.status.set(m)),
                        error_callback=lambda file_path, exc, tb: self.after(
                            0,
                            lambda p=file_path, e=exc, t=tb: self._log_index_error(p, e, t),
                        ),
                    )
                finally:
                    indexer.close()

                self.after(0, lambda: self._finish_index(indexed, skipped, errors))
            except Exception as exc:
                tb = traceback.format_exc()
                self.after(0, lambda: self._show_error(f"Indexing failed: {exc}\n\n{tb}"))

        threading.Thread(target=worker, daemon=True).start()

    def _log_index_error(self, file_path: Path, exc: Exception, tb: str) -> None:
        self.append_debug(f"[ERROR] {file_path}")
        self.append_debug(f"Type: {type(exc).__name__}")
        self.append_debug(f"Message: {exc}")
        self.append_debug(tb.rstrip())
        self.append_debug("-" * 80)

    def _finish_index(self, indexed: int, skipped: int, errors: int) -> None:
        self.set_busy(False)
        self.status.set(f"Done. Indexed: {indexed} | Skipped: {skipped} | Errors: {errors}")
        self.append_debug(f"INDEX FINISHED | indexed={indexed} | skipped={skipped} | errors={errors}")
        messagebox.showinfo(
            APP_TITLE,
            f"Index finished.\n\nIndexed: {indexed}\nSkipped: {skipped}\nErrors: {errors}",
        )

    def run_search(self) -> None:
        if self.busy:
            return

        query = self.query.get().strip()
        if not query:
            messagebox.showwarning(APP_TITLE, "Type something to search.")
            return

        db = Path(self.db_path.get())
        if not db.exists():
            messagebox.showerror(APP_TITLE, "Database file not found. Index a folder first.")
            return

        try:
            indexer = OCRIndexer(db)
            try:
                results = indexer.search(query, mode=self.search_mode.get())
            finally:
                indexer.close()
        except Exception as exc:
            self._show_error(f"Search failed: {exc}")
            return

        self.results_cache = results

        for row in self.tree.get_children():
            self.tree.delete(row)

        for item in results:
            self.tree.insert("", "end", values=(item.path, item.match_mode, item.score, item.matched_text))

        self.status.set(f"Search complete. {len(results)} result(s) found.")
        self.append_debug(
            f"SEARCH | query={query} | mode={self.search_mode.get()} | results={len(results)}"
        )

    def export_csv(self) -> None:
        if not self.results_cache:
            messagebox.showwarning(APP_TITLE, "No search results to export.")
            return

        output = filedialog.asksaveasfilename(
            title="Export results to CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="matches.csv",
        )
        if not output:
            return

        try:
            indexer = OCRIndexer(Path(self.db_path.get()))
            try:
                indexer.export_results_csv(self.results_cache, Path(output))
            finally:
                indexer.close()

            self.status.set(f"CSV exported to {output}")
            self.append_debug(f"CSV EXPORTED | {output}")
            messagebox.showinfo(APP_TITLE, f"CSV saved to:\n{output}")
        except Exception as exc:
            self._show_error(f"CSV export failed: {exc}")

    def show_stats(self) -> None:
        db = Path(self.db_path.get())
        if not db.exists():
            messagebox.showerror(APP_TITLE, "Database file not found.")
            return

        try:
            indexer = OCRIndexer(db)
            try:
                stats = indexer.stats()
            finally:
                indexer.close()

            self.status.set(f"Stats loaded. Indexed files: {stats.get('indexed_files', 0)}")
            self.append_debug(f"STATS | {json.dumps(stats)}")
            messagebox.showinfo(APP_TITLE, json.dumps(stats, indent=2))
        except Exception as exc:
            self._show_error(f"Could not load stats: {exc}")

    def open_selected_file(self, event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return

        values = self.tree.item(selected[0], "values")
        if not values:
            return

        path = Path(values[0])
        if not path.exists():
            self._show_error("Selected file no longer exists.")
            return

        try:
            if sys.platform.startswith("win"):
                path_str = str(path).replace("/", "\\")
                subprocess.Popen(["explorer", path_str])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            self._show_error(f"Could not open file: {exc}")

    def _show_error(self, message: str) -> None:
        self.set_busy(False)
        self.status.set("Error occurred.")
        self.append_debug("[FATAL ERROR]")
        self.append_debug(message)
        self.append_debug("-" * 80)
        messagebox.showerror(APP_TITLE, message)


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())