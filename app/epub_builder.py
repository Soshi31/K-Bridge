"""
Modul pentru generarea cărților EPUB optimizate pentru Kindle Colorsoft și KOReader.
Folosește ebooklib și bs4 pentru o structură XHTML curată, validă și conformă standardelor.
"""

import html
import os
from pathlib import Path
import re
import shutil
from typing import Dict, List, Optional, Tuple
import uuid
import xml.etree.ElementTree as ET
import zipfile

from bs4 import BeautifulSoup
from ebooklib import epub

from app.text_processor import TextProcessor


class EPUBBuilder:
    """
    Generator de fișiere EPUB reflowable, cu CSS optimizat pentru e-ink (Kindle / KOReader).
    """

    # Stil CSS conform specificațiilor KOReader / Kindle Colorsoft
    KO_READER_CSS = """/* Stil optimizat pentru Kindle Colorsoft & KOReader */
@namespace h "http://www.w3.org/1999/xhtml";

body {
    font-family: serif, "Bookerly", "Georgia", "Palatino", "Times New Roman";
    line-height: 1.4;
    margin: 0;
    padding: 0 0.5em;
    color: #111111;
    background-color: #ffffff;
}

h1, h2, h3 {
    text-align: center;
    margin-top: 1.6em;
    margin-bottom: 0.8em;
    font-weight: bold;
    page-break-before: always;
    line-height: 1.2;
}

h1 {
    font-size: 1.6em;
}

h2 {
    font-size: 1.3em;
}

p {
    text-align: justify;
    text-indent: 1.2em;
    margin-top: 0;
    margin-bottom: 0.2em;
    line-height: 1.4;
    word-break: normal;
    hyphens: auto;
    -webkit-hyphens: auto;
}

/* Primul paragraf de după un titlu sau separator nu are indentare */
h1 + p, h2 + p, h3 + p, hr + p, .no-indent {
    text-indent: 0;
}

hr.chapter-sep {
    border: 0;
    height: 1px;
    background: #cccccc;
    margin: 1.5em auto;
    width: 30%;
}

.title-page {
    text-align: center;
    padding-top: 25%;
    page-break-after: always;
}

.book-title {
    font-size: 2em;
    font-weight: bold;
    margin-bottom: 0.5em;
}

.book-subtitle {
    font-size: 1.1em;
    color: #555555;
    margin-top: 1em;
}
"""

    @classmethod
    def build_epub(
        cls,
        title: str,
        pages_text: List[Tuple[int, str]],
        output_file_path: str,
        author: str = "Autor Necunoscut",
        language: str = "ro",
        genre: str = "",
        series: str = "",
        cover_image_bytes: Optional[bytes] = None,
    ) -> str:
        """
        Construiește fișierul EPUB din lista de pagini procesate.
        Dacă cover_image_bytes este furnizat, prima pagină este setată drept copertă oficială a cărții.
        Adaugă metadatele standard (Titlu, Autor, Gen/Subiect, Serie) pentru indexare optimă în KOReader / Kindle.
        """
        output_path = Path(output_file_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        book = epub.EpubBook()

        # 1. Identificator unic și metadate
        book_id = f"urn:uuid:{uuid.uuid4()}"
        book.set_identifier(book_id)
        book.set_title(title)
        book.set_language(language)
        book.add_author(author)
        if genre:
            book.add_metadata("DC", "subject", genre)

        # Adăugare metadate de Serie (Calibre + EPUB3 pentru compatibilitate maximă KOReader)
        if series:
            s_name = series.strip()
            s_idx = "1"
            if "#" in series:
                parts = series.split("#", 1)
                s_name = parts[0].strip()
                s_idx = parts[1].strip()
            if s_name:
                book.add_metadata(None, "meta", "", {"name": "calibre:series", "content": s_name})
                if s_idx:
                    book.add_metadata(None, "meta", "", {"name": "calibre:series_index", "content": s_idx})
                book.add_metadata(None, "meta", s_name, {"property": "belongs-to-collection", "id": "series_coll"})
                book.add_metadata(None, "meta", "series", {"refines": "#series_coll", "property": "collection-type"})
                if s_idx:
                    book.add_metadata(None, "meta", s_idx, {"refines": "#series_coll", "property": "group-position"})

        # 2. Adăugare fișier CSS comun
        style_item = epub.EpubItem(
            uid="style_koreader",
            file_name="style/koreader.css",
            media_type="text/css",
            content=cls.KO_READER_CSS.encode("utf-8"),
        )
        book.add_item(style_item)

        # 3. Copertă (dacă este furnizată imaginea primei pagini)
        has_cover = False
        if cover_image_bytes:
            try:
                book.set_cover("cover.jpg", cover_image_bytes, create_page=True)
                cover_page = book.get_item_with_id("cover")
                if cover_page:
                    # Stilizare responsive pentru e-reader Kindle Colorsoft
                    cover_html = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{language}">
<head>
    <title>Copertă - {html.escape(title)}</title>
    <style type="text/css">
        @page {{ margin: 0; padding: 0; }}
        body {{ margin: 0; padding: 0; text-align: center; background-color: #ffffff; }}
        div.cover-box {{ height: 100vh; width: 100%; display: flex; align-items: center; justify-content: center; }}
        img {{ max-width: 100%; max-height: 100%; height: auto; width: auto; object-fit: contain; margin: 0 auto; display: block; }}
    </style>
</head>
<body>
    <div class="cover-box">
        <img src="cover.jpg" alt="{html.escape(title)}"/>
    </div>
</body>
</html>"""
                    cover_page.set_content(cover_html.encode("utf-8"))
                    has_cover = True
            except Exception as e:
                print(f"[Avertisment] Eroare la adăugarea copertei în EPUB: {e}")

        # Pagină de titlu (Title Page) ca fallback dacă nu există copertă grafică
        title_item = None
        if not has_cover:
            title_page_content = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{language}">
<head>
    <title>{html.escape(title)}</title>
    <link rel="stylesheet" href="style/koreader.css" type="text/css"/>
</head>
<body>
    <div class="title-page">
        <div class="book-title">{html.escape(title)}</div>
        <p class="book-subtitle">Ediție digitală reflowable optimizată pentru Kindle Colorsoft / KOReader</p>
    </div>
</body>
</html>"""
            title_item = epub.EpubHtml(
                title="Copertă / Titlu",
                file_name="title.xhtml",
                lang=language,
            )
            title_item.set_content(title_page_content.encode("utf-8"))
            title_item.add_item(style_item)
            book.add_item(title_item)

        # 4. Segmentare inteligentă în capitole și îmbinare continuă a paragrafelor
        chapters_items: List[epub.EpubHtml] = []
        toc_entries = []

        current_chapter_title = "Capitolul 1"
        current_paragraphs: List[str] = []
        chapter_counter = 1
        has_detected_any_heading = False

        def commit_chapter():
            nonlocal chapter_counter, current_chapter_title, current_paragraphs
            if not current_paragraphs:
                return

            c_file_name = f"chapter_{chapter_counter:03d}.xhtml"
            c_item = epub.EpubHtml(
                title=current_chapter_title,
                file_name=c_file_name,
                lang=language,
            )

            # Construire conținut XHTML
            p_html_list = []
            if has_detected_any_heading or chapter_counter == 1:
                p_html_list.append(f"<h1>{html.escape(current_chapter_title)}</h1>")

            for idx, p_text in enumerate(current_paragraphs):
                css_class = ' class="no-indent"' if idx == 0 else ""
                escaped = html.escape(p_text)
                p_html_list.append(f"<p{css_class}>{escaped}</p>")

            full_html = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{language}">
<head>
    <title>{html.escape(current_chapter_title)}</title>
    <link rel="stylesheet" href="style/koreader.css" type="text/css"/>
</head>
<body>
    {''.join(p_html_list)}
</body>
</html>"""
            # Verificare / Curățare cu BeautifulSoup pentru conformitate XHTML strictă
            soup = BeautifulSoup(full_html, "html.parser")
            c_item.set_content(str(soup).encode("utf-8"))
            c_item.add_item(style_item)
            book.add_item(c_item)

            chapters_items.append(c_item)
            toc_entries.append(c_item)

            chapter_counter += 1
            current_paragraphs = []

        # Punctuație terminală de propoziție
        terminal_punct = (".", "!", "?", "”", '"', "»", ":", "…")

        # Iterăm prin paginile sortate și reconstituim fluxul natural al cărții
        for page_idx, raw_page_text in pages_text:
            if not raw_page_text.strip():
                continue

            raw_blocks = re.split(r"\n\s*\n+", raw_page_text.strip())
            for block in raw_blocks:
                lines = [l.strip() for l in block.split("\n") if l.strip()]
                if not lines:
                    continue

                # Reconstituim rândurile din bloc (rezolvând cratimele de la final de rând)
                b_text = ""
                for l in lines:
                    if not b_text:
                        b_text = l
                    elif b_text.endswith(("-", "—")):
                        b_text = b_text[:-1] + l
                    else:
                        b_text = b_text + " " + l

                b_text = re.sub(r"\s+", " ", b_text).strip()
                if not b_text:
                    continue

                # Verificăm dacă blocul marchează un titlu de capitol legitim (ex: Capitolul 1, Luni 10 august, Louise, Lena)
                if TextProcessor.is_likely_chapter_heading(b_text):
                    if current_paragraphs:
                        commit_chapter()
                    current_chapter_title = b_text
                    has_detected_any_heading = True
                    continue

                # Dacă paragraful anterior nu se termina cu punctuație de final de propoziție,
                # înseamnă că propoziția continuă peste pagină sau peste rând
                if current_paragraphs and not current_paragraphs[-1].endswith(terminal_punct):
                    prev = current_paragraphs[-1]
                    if prev.endswith(("-", "—")):
                        current_paragraphs[-1] = prev[:-1] + b_text
                    else:
                        current_paragraphs[-1] = prev + " " + b_text
                else:
                    current_paragraphs.append(b_text)

        # Salvăm ultimul capitol rămas
        commit_chapter()

        # În cazul în care nu s-a generat niciun capitol (text foarte scurt sau gol)
        if not chapters_items:
            empty_item = epub.EpubHtml(title="Conținut", file_name="content.xhtml", lang=language)
            empty_item.set_content("<p>Nu a putut fi extras text din document.</p>".encode("utf-8"))
            empty_item.add_item(style_item)
            book.add_item(empty_item)
            chapters_items.append(empty_item)
            toc_entries.append(empty_item)

        # 5. Configurare Spine, Cuprins (TOC), NCX și Navigatie
        book.toc = tuple(toc_entries)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        # Spine-ul definește ordinea secvențială de citire a cărții.
        # Nu adăugăm 'nav' în spine pentru a nu genera pagini cu liste de capitole (1. Capitolul 1, 2. ...)
        # la deschiderea cărții. Cuprinsul rămâne accesibil prin meniul nativ Kindle / KOReader.
        if has_cover:
            book.spine = ["cover"] + chapters_items
        elif title_item:
            book.spine = [title_item] + chapters_items
        else:
            book.spine = chapters_items

        # 6. Salvare fișier EPUB pe disc
        epub.write_epub(str(output_path), book, {})
        return str(output_path)

    JUNK_TITLES = {
        "for reasons unknown", "journal kyaw ma ma lay", "untitled", "unknown",
        "document", "calibre", "none", "fără titlu", "fara titlu", "pdf", "epub",
        "microsoft word", "scan"
    }
    JUNK_AUTHORS = {
        "unknown", "necunoscut", "autor necunoscut", "various", "calibre",
        "none", "admin", "user", "anonymous", "anonim"
    }
    JUNK_GENRES = {
        "literatură", "literatura", "literatur", "general", "other", "unknown",
        "necunoscut", "calibre", "ficțiune", "fictiune", "romane", "roman"
    }

    @classmethod
    def read_epub_metadata(cls, epub_path: str) -> Dict[str, str]:
        """
        Extrage metadatele de bază (Titlu, Autor, Gen, Serie) dintr-un fișier EPUB existent
        fără a dezarhiva tot conținutul și fără a bloca fișierul.
        Filtrează automat template-urile corupte (For Reasons Unknown, Unknown, etc.).
        """
        import re
        meta = {"title": "", "author": "", "genre": "", "series": "", "series_index": ""}
        if not os.path.isfile(epub_path):
            return meta

        try:
            with zipfile.ZipFile(epub_path, "r") as z:
                opf_path = None
                if "META-INF/container.xml" in z.namelist():
                    c_tree = ET.fromstring(z.read("META-INF/container.xml"))
                    for rf in c_tree.iter("{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"):
                        opf_path = rf.attrib.get("full-path")
                        break
                if not opf_path:
                    for name in z.namelist():
                        if name.endswith(".opf"):
                            opf_path = name
                            break
                if not opf_path:
                    return meta

                opf_data = z.read(opf_path)
                tree = ET.fromstring(opf_data)

                for elem in tree.iter():
                    tag = elem.tag.split("}")[-1].lower()
                    if tag == "title" and not meta["title"]:
                        meta["title"] = (elem.text or "").strip()
                    elif tag in ("creator", "author") and not meta["author"]:
                        meta["author"] = (elem.text or "").strip()
                    elif tag == "subject" and not meta["genre"]:
                        meta["genre"] = (elem.text or "").strip()
                    elif tag == "meta":
                        name = elem.attrib.get("name", "")
                        prop = elem.attrib.get("property", "")
                        if name == "calibre:series":
                            meta["series"] = elem.attrib.get("content", "").strip()
                        elif name == "calibre:series_index":
                            meta["series_index"] = elem.attrib.get("content", "").strip()
                        elif prop == "belongs-to-collection":
                            meta["series"] = (elem.text or "").strip()
                        elif prop == "group-position":
                            meta["series_index"] = (elem.text or "").strip()

            if meta["series_index"] and meta["series"] and f"#{meta['series_index']}" not in meta["series"]:
                meta["series"] = f"{meta['series']} #{meta['series_index']}"

            # Filtrare și curățare metadate corupte
            raw_title = meta["title"].strip()
            raw_author = meta["author"].strip()
            raw_genre = meta["genre"].strip()

            if raw_title.lower() in cls.JUNK_TITLES:
                meta["title"] = ""
            if raw_author.lower() in cls.JUNK_AUTHORS:
                meta["author"] = ""
            if raw_genre.lower() in cls.JUNK_GENRES or "literatur" in raw_genre.lower():
                meta["genre"] = ""

            # Curățare prefixe lipite de autor (ex: 'Intrusul-Freida McFADDEN' -> 'Freida McFadden')
            if meta["author"]:
                meta["author"] = re.sub(r'^[a-zA-ZăîșțâĂÎȘȚÂ]+\s*[-–—]\s*(?=[A-Z][a-z])', '', meta["author"]).strip()

            # Dacă autorul conține un separator '-' și titlul e gol (ex: 'Freida Mcfadden -Nu deranjati')
            if meta["author"] and (" - " in meta["author"] or " -" in meta["author"] or "- " in meta["author"]):
                parts = re.split(r'\s*[-–—]\s*', meta["author"], maxsplit=1)
                if len(parts) == 2:
                    meta["author"] = parts[0].strip()
                    if not meta["title"]:
                        meta["title"] = parts[1].strip()

            # Curățare autor din interiorul titlului (ex: 'The Crash - McFadden, Freida')
            if meta["title"] and meta["author"]:
                a_clean = meta["author"]
                meta["title"] = re.sub(rf'\s*[-–—:,]\s*{re.escape(a_clean)}\b', '', meta["title"], flags=re.I).strip()
                a_parts = a_clean.split()
                if len(a_parts) == 2:
                    rev_a = f"{a_parts[1]} {a_parts[0]}"
                    rev_comma = f"{a_parts[1]}, {a_parts[0]}"
                    meta["title"] = re.sub(rf'\s*[-–—:,]\s*{re.escape(rev_a)}\b', '', meta["title"], flags=re.I).strip()
                    meta["title"] = re.sub(rf'\s*[-–—:,]\s*{re.escape(rev_comma)}\b', '', meta["title"], flags=re.I).strip()

            # Dacă titlul și autorul sunt identice, autorul este greșit setat cu titlul cărții
            if meta["title"] and meta["author"] and meta["title"].lower() == meta["author"].lower():
                meta["author"] = ""

        except Exception as e:
            print(f"[Avertisment] Eroare la citirea metadatelor EPUB: {e}")

        return meta

    @classmethod
    def update_epub_metadata(
        cls,
        epub_path: str,
        title: str = "",
        author: str = "",
        genre: str = "",
        series: str = "",
    ) -> bool:
        """
        Actualizează metadatele (Titlu, Autor, Gen, Serie) direct în fișierul EPUB existent,
        fără a altera capitolele, imaginile sau structura interioară.
        """
        if not os.path.isfile(epub_path):
            return False

        try:
            tmp_epub = epub_path + ".tmp"
            with zipfile.ZipFile(epub_path, "r") as z_in, zipfile.ZipFile(tmp_epub, "w") as z_out:
                opf_path = None
                if "META-INF/container.xml" in z_in.namelist():
                    c_tree = ET.fromstring(z_in.read("META-INF/container.xml"))
                    for rf in c_tree.iter("{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"):
                        opf_path = rf.attrib.get("full-path")
                        break
                if not opf_path:
                    for name in z_in.namelist():
                        if name.endswith(".opf"):
                            opf_path = name
                            break
                if not opf_path:
                    return False

                # 1. Păstrăm fișierul mimetype primul și necomprimat conform standardului EPUB
                if "mimetype" in z_in.namelist():
                    z_out.writestr("mimetype", z_in.read("mimetype"), compress_type=zipfile.ZIP_STORED)

                # 2. Copiem restul fișierelor nemodificate
                for item in z_in.infolist():
                    if item.filename in ("mimetype", opf_path):
                        continue
                    z_out.writestr(item, z_in.read(item.filename))

                # 3. Parsăm și modificăm OPF
                opf_bytes = z_in.read(opf_path)
                ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
                ET.register_namespace("opf", "http://www.idpf.org/2007/opf")
                tree = ET.fromstring(opf_bytes)

                metadata_elem = None
                for elem in tree.iter():
                    if elem.tag.endswith("metadata"):
                        metadata_elem = elem
                        break

                if metadata_elem is not None:
                    # Titlu
                    found_title = False
                    for el in metadata_elem:
                        if el.tag.endswith("title"):
                            if title:
                                el.text = title
                            found_title = True
                            break
                    if not found_title and title:
                        t_el = ET.SubElement(metadata_elem, "{http://purl.org/dc/elements/1.1/}title")
                        t_el.text = title

                    # Autor
                    found_author = False
                    for el in metadata_elem:
                        if el.tag.endswith("creator") or el.tag.endswith("author"):
                            if author:
                                el.text = author
                            found_author = True
                            break
                    if not found_author and author:
                        a_el = ET.SubElement(metadata_elem, "{http://purl.org/dc/elements/1.1/}creator")
                        a_el.text = author

                    # Gen / Subiect
                    found_genre = False
                    for el in metadata_elem:
                        if el.tag.endswith("subject"):
                            if genre:
                                el.text = genre
                            found_genre = True
                            break
                    if not found_genre and genre:
                        g_el = ET.SubElement(metadata_elem, "{http://purl.org/dc/elements/1.1/}subject")
                        g_el.text = genre

                    # Serie (Calibre + EPUB3)
                    if series:
                        s_name = series.strip()
                        s_idx = "1"
                        if "#" in series:
                            parts = series.split("#", 1)
                            s_name = parts[0].strip()
                            s_idx = parts[1].strip()

                        found_series = False
                        for el in metadata_elem:
                            if el.tag.endswith("meta") and el.attrib.get("name") == "calibre:series":
                                el.attrib["content"] = s_name
                                found_series = True
                            elif el.tag.endswith("meta") and el.attrib.get("name") == "calibre:series_index":
                                el.attrib["content"] = s_idx

                        if not found_series and s_name:
                            m1 = ET.SubElement(metadata_elem, "{http://www.idpf.org/2007/opf}meta")
                            m1.attrib["name"] = "calibre:series"
                            m1.attrib["content"] = s_name
                            m2 = ET.SubElement(metadata_elem, "{http://www.idpf.org/2007/opf}meta")
                            m2.attrib["name"] = "calibre:series_index"
                            m2.attrib["content"] = s_idx

                new_opf = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
                z_out.writestr(opf_path, new_opf, compress_type=zipfile.ZIP_DEFLATED)

            # Înlocuim fișierul original
            shutil.move(tmp_epub, epub_path)
            return True
        except Exception as e:
            print(f"[Eroare] Actualizare metadate EPUB: {e}")
            if os.path.exists(epub_path + ".tmp"):
                try:
                    os.remove(epub_path + ".tmp")
                except Exception:
                    pass
            return False

