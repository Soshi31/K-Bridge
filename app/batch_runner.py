"""
Modul de coordonare a procesării în lot (Batch Runner).
Rulează într-un fir de execuție separat pentru a menține interfața grafică complet reactivă.
Coordonează motorul OCR, constructorul EPUB și clientul SFTP.
"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from app.config import AppConfig
from app.epub_builder import EPUBBuilder
from app.metadata_fetcher import MetadataFetcher
from app.ocr_engine import OCREngine
from app.sftp_client import KindleSFTPClient
from app.text_processor import TextProcessor


@dataclass
class QueueItem:
    item_id: str
    pdf_path: str
    file_name: str
    title: str = ""
    author: str = ""
    genre: str = ""
    series: str = ""  # Serie (ex: "Dune #1", "Harry Potter #2")
    file_type: str = "pdf"  # "pdf" sau "epub"
    total_pages: int = 0
    status: str = "În așteptare"  # "În așteptare", "Randare/OCR...", "Generat EPUB", "EPUB Încărcat", "Trimis SSH", "Eroare"
    output_epub_path: str = ""
    target_format: str = "EPUB"  # "EPUB" or "PDF"
    error_message: str = ""


class BatchRunner:
    """
    Coordonator pentru procesarea loturilor de fișiere PDF.
    """

    def __init__(
        self,
        config: AppConfig,
        on_batch_progress: Optional[Callable[[int, int], None]] = None,
        on_book_progress: Optional[Callable[[int, int], None]] = None,
        on_item_status: Optional[Callable[[QueueItem], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,  # (mesaj, level)
        on_finished: Optional[Callable[[int, int], None]] = None,
    ):
        self.config = config
        self.on_batch_progress = on_batch_progress
        self.on_book_progress = on_book_progress
        self.on_item_status = on_item_status
        self.on_log = on_log
        self.on_finished = on_finished

        self.stop_event = threading.Event()
        self.is_running = False
        self._thread: Optional[threading.Thread] = None

    def log(self, message: str, level: str = "INFO"):
        if self.on_log:
            self.on_log(message, level)
        else:
            print(f"[{level}] {message}")

    def start(self, items: List[QueueItem]):
        """Pornește procesarea lotului într-un fir de execuție de fundal."""
        if self.is_running:
            return

        self.stop_event.clear()
        self.is_running = True
        self._thread = threading.Thread(
            target=self._run_batch,
            args=(items,),
            daemon=True,
            name="BatchRunnerThread",
        )
        self._thread.start()

    def stop(self):
        """Oprește procesarea în curs."""
        if self.is_running:
            self.stop_event.set()
            self.log("Comandă de oprire primită. Se anulează procesarea...", "WARN")

    def _run_batch(self, items: List[QueueItem]):
        total_items = len(items)
        success_count = 0
        fail_count = 0

        self.log(f"=== Început procesare lot: {total_items} fișiere PDF ===", "INFO")

        # Asigurăm existența folderului de ieșire
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Verificăm dacă avem fișiere PDF care necesită motor OCR
        has_pdfs = any(item.file_type == "pdf" and not item.pdf_path.lower().endswith(".epub") for item in items)
        ocr_engine = None

        if has_pdfs:
            ocr_engine = OCREngine(
                tesseract_cmd=self.config.tesseract_cmd,
                ocr_lang=self.config.ocr_lang,
                dpi=self.config.dpi,
                max_workers=self.config.max_workers,
            )
            tess_ok, tess_msg = ocr_engine.test_tesseract()
            if not tess_ok:
                self.log(f"[Eroare Critică Tesseract] {tess_msg}", "ERROR")
                self.log("Vă rugăm să configurați calea corectă către tesseract.exe în panoul de Setări.", "ERROR")
                for item in items:
                    if item.file_type == "pdf" and not item.pdf_path.lower().endswith(".epub"):
                        item.status = "Eroare"
                        item.error_message = tess_msg
                        if self.on_item_status:
                            self.on_item_status(item)
                self.is_running = False
                if self.on_finished:
                    self.on_finished(0, total_items)
                return
            self.log(f"[OCR] {tess_msg}", "INFO")

        for idx, item in enumerate(items):
            if self.stop_event.is_set():
                item.status = "Oprit"
                if self.on_item_status:
                    self.on_item_status(item)
                continue

            self.log(f"\n--- [{idx + 1}/{total_items}] Procesare: {item.file_name} ---", "INFO")

            try:
                is_epub = item.file_type == "epub" or item.pdf_path.lower().endswith(".epub")

                if is_epub:
                    # PROCESARE DIRECTĂ FIȘIER EPUB (FĂRĂ OCR)
                    item.status = "Actualizare EPUB..."
                    if self.on_item_status:
                        self.on_item_status(item)

                    # 1. Citire metadate existente din EPUB
                    existing_meta = EPUBBuilder.read_epub_metadata(item.pdf_path)
                    if not item.title and existing_meta.get("title"):
                        item.title = existing_meta["title"]
                    if not item.author and existing_meta.get("author"):
                        item.author = existing_meta["author"]
                    if not item.genre and existing_meta.get("genre"):
                        item.genre = existing_meta["genre"]
                    if not item.series and existing_meta.get("series"):
                        item.series = existing_meta["series"]

                    # 2. Căutare online metadate dacă este activată
                    need_meta = (
                        not item.title
                        or not item.author
                        or item.author == "Autor Necunoscut"
                        or not item.genre
                        or "literatur" in item.genre.lower()
                        or not item.series
                    )
                    if self.config.fetch_metadata and need_meta:
                        self.log(f"  Căutare / optimizare metadate pentru EPUB...", "INFO")
                        meta = MetadataFetcher.fetch_book_metadata(
                            pdf_filename=item.file_name,
                            pdf_internal_title=item.title,
                            pdf_internal_author=item.author,
                        )
                        if meta.get("title"):
                            item.title = meta["title"]
                        if meta.get("author") and meta["author"] != "Autor Necunoscut":
                            item.author = meta["author"]
                        elif not item.author:
                            item.author = meta.get("author", "Autor Necunoscut")
                        if meta.get("genre"):
                            item.genre = meta["genre"]
                        if meta.get("series") and not item.series:
                            item.series = meta["series"]

                        source = meta.get("source", "")
                        self.log(f"  [Metadate EPUB - {source}] Titlu: '{item.title}' | Autor: '{item.author}' | Gen: '{item.genre}' | Serie: '{item.series}'", "SUCCESS")
                        if self.on_item_status:
                            self.on_item_status(item)

                    # 3. Actualizare metadate în fișierul EPUB
                    final_title = item.title if item.title else Path(item.pdf_path).stem
                    final_author = item.author if item.author else "Autor Necunoscut"
                    final_genre = item.genre if item.genre else ""
                    final_series = item.series if item.series else ""

                    EPUBBuilder.update_epub_metadata(
                        epub_path=item.pdf_path,
                        title=final_title,
                        author=final_author,
                        genre=final_genre,
                        series=final_series,
                    )

                    target_epub_path = item.pdf_path
                    item.output_epub_path = target_epub_path
                    item.status = "EPUB Pregătit"
                    if self.on_item_status:
                        self.on_item_status(item)
                    self.log(f"EPUB pregătit cu succes: {item.file_name}", "SUCCESS")

                else:
                    # PROCESARE FIȘIER PDF (Extragere copertă, OCR, filtrare zgomot și generare EPUB)
                    # 1. Extragere metadate din PDF și copertă din prima pagină
                    doc = fitz.open(item.pdf_path)
                    item.total_pages = len(doc)
                    pdf_meta = doc.metadata or {}
                    internal_title = pdf_meta.get("title", "")
                    internal_author = pdf_meta.get("author", "")

                    cover_bytes = None
                    if item.total_pages > 0:
                        try:
                            first_page = doc.load_page(0)
                            # Randează prima pagină la 250 DPI pentru o copertă clară pe Kindle Colorsoft
                            mat = fitz.Matrix(250 / 72.0, 250 / 72.0)
                            pix = first_page.get_pixmap(matrix=mat, alpha=False)
                            cover_bytes = pix.tobytes("jpeg", jpg_quality=92)
                            self.log(f"  Copertă color generată din prima pagină a PDF-ului.", "INFO")
                        except Exception as cov_err:
                            self.log(f"  [Avertisment copertă] {cov_err}", "WARN")
                    first_sample = ""
                    try:
                        for p in doc[:3]:
                            first_sample += " " + p.get_text()
                    except Exception:
                        pass
                    doc.close()

                    # Căutare online Titlu, Autor, Gen, Serie dacă nu sunt deja complet setate
                    need_meta = (
                        not item.title
                        or not item.author
                        or item.author == "Autor Necunoscut"
                        or not item.genre
                        or "literatur" in item.genre.lower()
                        or not item.series
                    )
                    if self.config.fetch_metadata and need_meta:
                        self.log(f"  Căutare / optimizare metadate pentru PDF...", "INFO")
                        meta = MetadataFetcher.fetch_book_metadata(
                            pdf_filename=item.file_name,
                            pdf_internal_title=internal_title,
                            pdf_internal_author=internal_author,
                            first_page_sample=first_sample,
                        )
                        if meta.get("title"):
                            item.title = meta["title"]
                        if meta.get("author") and meta["author"] != "Autor Necunoscut":
                            item.author = meta["author"]
                        elif not item.author:
                            item.author = meta.get("author", "Autor Necunoscut")
                        if meta.get("genre"):
                            item.genre = meta["genre"]
                        if meta.get("series") and not item.series:
                            item.series = meta["series"]

                        source = meta.get("source", "")
                        self.log(f"  [Metadate găsite - {source}] Titlu: '{item.title}' | Autor: '{item.author}' | Gen: '{item.genre}' | Serie: '{item.series}'", "SUCCESS")
                        if self.on_item_status:
                            self.on_item_status(item)

                    final_title = item.title if item.title else Path(item.pdf_path).stem
                    final_author = item.author if item.author else "Autor Necunoscut"
                    final_genre = item.genre if item.genre else ""
                    final_series = item.series if item.series else ""

                    # Nume curat pentru fișierul EPUB
                    clean_name = "".join(c for c in final_title if c.isalnum() or c in (" ", ".", "_", "-")).strip()
                    if not clean_name:
                        clean_name = Path(item.pdf_path).stem
                    epub_filename = f"{clean_name}.epub"
                    target_epub_path = str(output_dir / epub_filename)

                    # Dacă fișierul EPUB există deja pe disc, nu mai refacem OCR
                    if os.path.isfile(target_epub_path) and os.path.getsize(target_epub_path) > 1000:
                        self.log(f"  EPUB deja existent pe disc: {clean_name}. Se sare peste OCR.", "INFO")
                        item.output_epub_path = target_epub_path
                        item.status = "Generat EPUB"
                        if self.on_item_status:
                            self.on_item_status(item)
                    else:
                        item.status = "Randare/OCR..."
                        if self.on_item_status:
                            self.on_item_status(item)

                        if self.on_book_progress:
                            self.on_book_progress(0, item.total_pages)

                        # Callback pentru progresul paginilor
                        def page_progress_cb(current_page: int, total_p: int, text_chunk: str):
                            if self.on_book_progress:
                                self.on_book_progress(current_page, total_p)

                        # 2. Rulare OCR Optic
                        pages_data = ocr_engine.process_pdf(
                            pdf_path=item.pdf_path,
                            page_callback=page_progress_cb,
                            log_callback=lambda msg: self.log(f"  {msg}", "INFO"),
                            stop_event=self.stop_event,
                        )

                        if self.stop_event.is_set():
                            item.status = "Oprit"
                            if self.on_item_status:
                                self.on_item_status(item)
                            break

                        # 3. Curățare avansată: eliminare pagini de debut (liste de cărți 1. 2. 3., CIP, Cuprins static) și antete repetitive
                        self.log(f"  Filtrare automată conținut de debut (liste cărți 1. 2. 3., CIP) și antete repetitive...", "INFO")
                        pages_data = TextProcessor.clean_entire_book_pages(
                            pages_raw=pages_data,
                            author=final_author,
                            title=final_title,
                            filter_front_matter=True,
                        )

                        # 4. Generare EPUB
                        self.log(f"Generare fișier EPUB cu metadate: {epub_filename}...", "INFO")
                        EPUBBuilder.build_epub(
                            title=final_title,
                            pages_text=pages_data,
                            output_file_path=target_epub_path,
                            author=final_author,
                            language="ro" if "ron" in self.config.ocr_lang else "en",
                            genre=final_genre,
                            series=final_series,
                            cover_image_bytes=cover_bytes,
                        )

                        item.output_epub_path = target_epub_path
                        item.status = "Generat EPUB"
                        if self.on_item_status:
                            self.on_item_status(item)

                        self.log(f"EPUB salvat cu succes în: {target_epub_path}", "SUCCESS")

                # 4. Trimitere automată prin SSH / SFTP (dacă este bifat)
                if self.config.auto_send and self.config.kindle_ip:
                    self.log(f"Trimitere pe Kindle ({self.config.kindle_ip}:{self.config.kindle_port})...", "INFO")
                    sftp_client = KindleSFTPClient(
                        host=self.config.kindle_ip,
                        port=self.config.kindle_port,
                        username=self.config.kindle_user,
                        password=self.config.kindle_password,
                        remote_dir=self.config.remote_dir,
                    )
                    success, sftp_msg = sftp_client.upload_epub(
                        local_epub_path=target_epub_path,
                        log_callback=lambda msg: self.log(f"  [SFTP] {msg}", "INFO"),
                    )
                    if success:
                        item.status = "Trimis SSH"
                        self.log(f"[SFTP] {sftp_msg}", "SUCCESS")
                    else:
                        item.status = "Generat EPUB (Eroare SSH)"
                        self.log(f"[SFTP Avertisment] {sftp_msg}", "WARN")

                    if self.on_item_status:
                        self.on_item_status(item)

                success_count += 1

            except Exception as e:
                fail_count += 1
                item.status = "Eroare"
                item.error_message = str(e)
                if self.on_item_status:
                    self.on_item_status(item)
                self.log(f"[Eroare la conversia {item.file_name}]: {e}", "ERROR")

            # Actualizare progres global lot
            if self.on_batch_progress:
                self.on_batch_progress(idx + 1, total_items)

        self.is_running = False
        self.log(
            f"\n=== Lot finalizat! Reușite: {success_count}, Eșecuri: {fail_count} ===",
            "SUCCESS" if fail_count == 0 else "WARN",
        )
        if self.on_finished:
            self.on_finished(success_count, fail_count)
