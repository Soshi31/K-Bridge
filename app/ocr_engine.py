"""
Modul pentru randarea PDF-urilor și recunoașterea optică a caracterelor (OCR).
Folosește PyMuPDF (fitz) pentru randare la 300 DPI și pytesseract pentru OCR,
paralelizat pe nucleele CPU cu ThreadPoolExecutor.
"""

import concurrent.futures
import io
import os
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

try:
    import pymupdf as fitz
except ImportError:
    import fitz
from PIL import Image
import pytesseract

from app.text_processor import TextProcessor


class OCREngine:
    """
    Motor de procesare PDF -> Imagini 300 DPI -> Text OCR curățat.
    """

    def __init__(
        self,
        tesseract_cmd: Optional[str] = None,
        ocr_lang: str = "ron+eng",
        dpi: int = 300,
        max_workers: int = 4,
    ):
        self.ocr_lang = ocr_lang
        self.dpi = dpi
        self.max_workers = max(1, max_workers)

        if tesseract_cmd and os.path.isfile(tesseract_cmd):
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        elif os.path.isfile(r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
            pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

        # Verificare folder tessdata local pentru limba română (ron) și altele
        base_dir = Path(__file__).resolve().parent.parent
        local_tessdata = base_dir / "tessdata"
        if local_tessdata.is_dir() and any(local_tessdata.glob("*.traineddata")):
            os.environ["TESSDATA_PREFIX"] = str(local_tessdata)

    def set_tesseract_path(self, path: str):
        """Configurează calea către executabilul Tesseract."""
        if path and os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path

    def test_tesseract(self) -> Tuple[bool, str]:
        """
        Verifică dacă executabilul Tesseract funcționează și returnează versiunea
        și lista de limbi instalate.
        """
        try:
            version = pytesseract.get_tesseract_version()
            langs = pytesseract.get_languages(config="")
            return True, f"Tesseract v{version} funcțional. Limbi detectate: {', '.join(langs)}"
        except Exception as e:
            return False, f"Eroare Tesseract: {str(e)}"

    @staticmethod
    def _render_page_pixmap(pdf_path: str, page_number: int, dpi: int) -> Image.Image:
        """
        Deschide PDF-ul și randează pagina specificată ca imagine PIL la DPI-ul dorit.
        Fiecare fir deschide propriul handle PyMuPDF pentru thread-safety deplin.
        """
        doc = fitz.open(pdf_path)
        try:
            page = doc.load_page(page_number)
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            return img
        finally:
            doc.close()

    def _ocr_single_page(
        self,
        pdf_path: str,
        page_index: int,
        ocr_lang: str,
        dpi: int,
        stop_event: Optional[threading.Event] = None,
    ) -> Tuple[int, str]:
        """
        Execută randarea paginii și rularea Tesseract OCR pentru o singură pagină.
        Returnează (page_index, cleaned_text).
        """
        if stop_event and stop_event.is_set():
            return page_index, ""

        # 1. Randează pagina ca imagine la 300 DPI
        img = self._render_page_pixmap(pdf_path, page_index, dpi)

        if stop_event and stop_event.is_set():
            return page_index, ""

        # 2. Execută OCR cu pytesseract
        # PSM 1: Automatic page segmentation with OSD
        # PSM 3: Fully automatic page segmentation (standard book text)
        custom_config = r"--oem 1 --psm 3"
        try:
            raw_text = pytesseract.image_to_string(img, lang=ocr_lang, config=custom_config)
        except Exception as ocr_err:
            # Fallback dacă limba nu e instalată (ex: încearcă fără limba specificată)
            err_msg = str(ocr_err).lower()
            if "language" in err_msg or "traineddata" in err_msg:
                try:
                    raw_text = pytesseract.image_to_string(img, lang="eng", config=custom_config)
                except Exception:
                    raw_text = pytesseract.image_to_string(img, config=custom_config)
            else:
                raise ocr_err

        # 3. Curăță textul rezultat
        cleaned_text = TextProcessor.clean_raw_ocr_page(raw_text)
        return page_index, cleaned_text

    def process_pdf(
        self,
        pdf_path: str,
        page_callback: Optional[Callable[[int, int, str], None]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> List[Tuple[int, str]]:
        """
        Procesează toate paginile din PDF în mod paralel:
        - Randează fiecare pagină la 300 DPI
        - Rulează OCR
        - Curăță artefactele
        - Păstrează ordinea corectă a paginilor

        Returnează o listă de tupluri sortate: [(0, text_pagina_0), (1, text_pagina_1), ...]
        """
        if not os.path.isfile(pdf_path):
            raise FileNotFoundError(f"Fișierul PDF nu a fost găsit: {pdf_path}")

        # Aflăm numărul total de pagini
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        doc.close()

        if total_pages == 0:
            return []

        if log_callback:
            log_callback(
                f"Inițializare randare & OCR pentru {os.path.basename(pdf_path)} "
                f"({total_pages} pagini la {self.dpi} DPI, {self.max_workers} thread-uri)..."
            )

        results: List[Tuple[int, str]] = []
        completed_pages = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Planificăm toate paginile
            future_to_page = {
                executor.submit(
                    self._ocr_single_page,
                    pdf_path,
                    p_idx,
                    self.ocr_lang,
                    self.dpi,
                    stop_event,
                ): p_idx
                for p_idx in range(total_pages)
            }

            for future in concurrent.futures.as_completed(future_to_page):
                if stop_event and stop_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    if log_callback:
                        log_callback("Procesarea PDF a fost oprită de utilizator.")
                    break

                try:
                    p_idx, cleaned_text = future.result()
                    results.append((p_idx, cleaned_text))
                    completed_pages += 1

                    if page_callback:
                        page_callback(completed_pages, total_pages, cleaned_text)

                except Exception as exc:
                    page_num = future_to_page[future]
                    if log_callback:
                        log_callback(f"[Avertisment] Eroare la procesarea paginii {page_num + 1}: {exc}")
                    results.append((page_num, f"[Eroare OCR pagina {page_num + 1}]"))

        # Sortăm rezultatele după indexul paginii pentru a garanta ordinea cronologică
        results.sort(key=lambda x: x[0])
        return results
