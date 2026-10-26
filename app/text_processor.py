"""
Modul pentru curățarea avansată și normalizarea textului extras prin OCR.
Elimină automat antetele și subsolurile repetitive (nume autor, titlu carte pe fiecare pagină),
numerele de pagină izolate, artefactele de scanare și liniile frânte,
fără a afecta narațiunea reală sau titlurile de capitole.
"""

from collections import Counter
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Dict, List, Optional, Set, Tuple


class TextProcessor:
    """
    Procesator de text OCR avansat:
    - Elimină caractere nule (\\x00), caractere de control și artefacte de scanare.
    - Elimină automat antetele repetitive (Running Headers cu numele autorului/titlului).
    - Elimină subsolurile repetitive și numerele de pagină izolate.
    - Rezolvă despărțirile în silabe la capăt de rând (dehyphenation).
    - Reasamblează rândurile frânte artificial într-un flux reflowable natural.
    - Păstrează structura paragrafelor reale și titlurile de capitole.
    """

    # Despărțiri în silabe la sfârșit de rând: cuvânt + cratimă + newline + continuare
    HYPHENATION_REGEX = re.compile(
        r"(\b[\w\u00C0-\u024F]+)[-\u00AD\u2010\u2011]\s*\r?\n\s*([\w\u00C0-\u024F]+\b)",
        re.UNICODE,
    )

    # Numere de pagină izolate: "12", "- 12 -", "Pagina 12", "XII", "~ 45 ~"
    PAGE_NUMBER_REGEX = re.compile(
        r"^\s*(?:[-—~•*\[(]?\s*(?:\d+|[ivxlcdmIVXLCDM]+)\s*[-—~•*\])]?|(?:pag(?:ina|\.)?\s*\d+))\s*$",
        re.IGNORECASE,
    )

    # Artefacte de scanare (margini de pagină, pete, simboluri izolate)
    NOISE_LINE_REGEX = re.compile(
        r"^[^\w\s]{1,4}$|^\s*[_~=\-\.\:\;\'\"\|\/\\]{1,6}\s*$|^\s*[a-zA-Z]\s*$"
    )

    # Indicatori de progres Kindle / e-reader (ex: "10 pagini rămase în capitol 4 minute", "152 pagini rămase", "Locația 142 din 5000", "2 ore rămase")
    READING_PROGRESS_REGEX = re.compile(
        r"""(?ix)
        ^\s*(?:
            # X pagini rămase în capitol Y minute / ore
            \d+\s*(?:pagini|pagină|pag|pags?|pages?)\s*r[aăâ\?]?mase.*
            |
            # X ore / minute rămase în capitol / carte
            \d+\s*(?:ore|or[aă]|minute|min|hours?|mins?)\s*(?:și|\+)?\s*\d*\s*(?:minute|min|mins?)?\s*r[aăâ\?]?mase.*
            |
            # timp / pagini rămase în capitol / carte
            (?:timp|pagini)\s*r[aăâ\?]?mas[eă]?\s*[iî\?]?n\s+(?:capitol|carte).*
            |
            # English left in chapter / book
            \d+\s*(?:pages?|mins?|hours?)\s+left\s+(?:in|of)\s+.*
            |
            # Locație / Location
            (?:loca[tț\?]+ia|location|loc\.)\s*\d+.*
            |
            # Procentaj progres
            \d+\s*%\s*(?:finalizat|completat|[iî\?]?n\s*carte|in\s*book)?$
            |
            # Variantă dacă conține "rămase în capitol" sau "rămase în carte"
            .*r[aăâ\?]?mase\s+[iî\?]?n\s+(?:capitol|carte).*
            |
            # Doar ore/minute/pagini rămase
            \d+\s*(?:ore|or[aă]|minute|min|pagini|pag)\s*r[aăâ\?]?mase\s*$
        )\s*$
        """,
        re.VERBOSE,
    )

    # Tipare de titluri de capitol care NU trebuie șterse niciodată
    CHAPTER_PATTERNS = [
        r"^(?:capitolul|capitol|chapter)\b",
        r"^(?:actul|scena)\b",
        r"^(?:prolog|epilog|introducere|prefață|cuvânt înainte|postfață)\b",
        r"^(?:prologue|epilogue|introduction|preface|foreword|afterword)\b",
    ]

    @classmethod
    def is_likely_chapter_heading(cls, paragraph: str) -> bool:
        """
        Detectează dacă un rând/paragraf este un titlu legitim de capitol,
        o etichetă de parte, o secțiune de personaj (ex: Lena, Louise) sau o dată.
        """
        p = paragraph.strip()
        if not p or len(p) > 65:
            return False

        # Nu este titlu dacă începe cu linie de dialog sau ghilimele
        if p.startswith(("—", "-", "–", '"', "«", "”")):
            return False

        # Nu este titlu dacă se termină cu punctuație narativă
        if p.endswith((".", ",", ";", "!", "?", "—", '"', "»", ":")):
            return False

        # 1. Tipare explicite de capitol (Capitolul 1, Chapter 2, Prolog, etc.)
        for pat in cls.CHAPTER_PATTERNS:
            if re.match(pat, p, re.IGNORECASE):
                return True

        # 2. Tipar strict pentru "Partea X" (nu confunda cu propoziții narative de genul "Partea stângă...")
        if re.match(
            r"^(?:partea|part)\s+(?:\d+|[IVXLCDM]+|întâi|prima|a\s+[a-zăîșțâ]+|unu|doi|trei|patru|cinci|șase|șapte|opt|nouă|zece|one|two|three|four|five)\s*$",
            p,
            re.IGNORECASE,
        ):
            return True

        # 3. Zile și date calendaristice (ex: "Luni, 10 august", "Marti, 11 august", "August 2015", "Septembrie")
        month_or_day_pat = (
            r"^(?:\d{4}\s+)?(?:luni|mar[țt]i|miercuri|joi|vineri|s[âa]mb[ăa]t[ăa]|duminic[ăa]|[0-9]{1,2})?"
            r"(?:[,\s]+)?(?:ianuarie|februarie|martie|aprilie|mai|iunie|iulie|august|septembrie|octombrie|noiembrie|decembrie|[0-9]{1,2}\s+[a-zăîșțâ]+)\b"
        )
        if re.search(month_or_day_pat, p, re.IGNORECASE) and len(p.split()) <= 6:
            return True

        # 4. Titluri scurte scrise doar cu majuscule (lungime 3-35 caractere, fără caractere speciale OCR)
        clean_letters = [c for c in p if c.isalpha()]
        if 3 <= len(clean_letters) <= 35:
            if all(c.isupper() for c in clean_letters) and not any(ch in p for ch in "¢$€@#%^&*~_+="):
                return True

        # 5. Nume scurte de personaje ca titlu de capitol / secțiune (ex: "Lena", "Louise", "Jules", "Sean", "Mark")
        # 1-2 cuvinte, Capitalized, doar litere (fără punctuație), lungime 3-25 caractere
        words = p.split()
        if 1 <= len(words) <= 2 and 3 <= len(p) <= 25:
            if all(w[0].isupper() and (len(w) == 1 or w[1:].islower()) and w.isalpha() for w in words):
                return True

        return False

    @classmethod
    def _is_similar(cls, a: str, b: str, threshold: float = 0.75) -> bool:
        """Verifică dacă două șiruri de caractere sunt similare (fuzzy match)."""
        if not a or not b:
            return False
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold

    @classmethod
    def normalize_for_header_match(cls, line: str) -> str:
        """Normalizează un rând eliminând cifrele și caracterele speciale de la margini."""
        l = line.strip()
        l = re.sub(r"^[\dIVXLCDM\.\-\—~•*\[\]\(\)\s]+", "", l)
        l = re.sub(r"[\dIVXLCDM\.\-\—~•*\[\]\(\)\s]+$", "", l)
        l = re.sub(r"\s+", " ", l).strip().lower()
        return l

    @classmethod
    def is_running_header_or_footer(
        cls,
        line: str,
        author: str = "",
        title: str = "",
        recurring_headers: Optional[Set[str]] = None,
    ) -> bool:
        """
        Determină dacă un rând din marginea de sus/jos este antet/subsol nedorit:
        - Numele autorului cărții
        - Titlul cărții
        - Numere de pagină izolate
        - Indicatori de progres Kindle (pagini rămase / minute rămase)
        Tot restul conținutului (inclusiv nume personaje și capitole) este păstrat!
        """
        stripped = line.strip()
        if not stripped:
            return False

        # 1. Număr de pagină izolat (ex: "124", "- 124 -", "pag. 45", "XII")
        if cls.PAGE_NUMBER_REGEX.match(stripped):
            return True

        # 2. Indicator de progres Kindle / e-reader scanat
        if cls.READING_PROGRESS_REGEX.search(stripped):
            return True

        # 3. Artefact scurt de scanare (linii de delimitare, pete OCR, caractere solitare)
        if cls.NOISE_LINE_REGEX.match(stripped) and len(stripped) <= 3:
            return True

        norm_line = cls.normalize_for_header_match(stripped)
        if not norm_line or len(norm_line) < 2:
            return True

        # Nu filtrăm propoziții de narațiune (care conțin punct final sau dialog)
        if stripped.endswith((".", "!", "?", "—", '"', "»", ":", ";")):
            return False

        # 4. Potrivire cu autorul cărții (antet autor)
        if author:
            norm_author = cls.normalize_for_header_match(author)
            if norm_author and len(norm_author) >= 3:
                if (
                    norm_author in norm_line
                    or norm_line in norm_author
                    or cls._is_similar(norm_line, norm_author, threshold=0.75)
                ):
                    return True

        # 5. Potrivire cu titlul cărții (antet titlu)
        if title:
            norm_title = cls.normalize_for_header_match(title)
            if norm_title and len(norm_title) >= 3:
                if (
                    norm_title in norm_line
                    or norm_line in norm_title
                    or cls._is_similar(norm_line, norm_title, threshold=0.75)
                ):
                    return True

        # 6. Verificare recurență peste volum (antet repetat pe multe pagini)
        if recurring_headers and norm_line in recurring_headers:
            return True

        # 7. Dacă este titlu de capitol sau personaj recunoscut, NU îl ștergem niciodată!
        if cls.is_likely_chapter_heading(stripped):
            return False

        return False

    @classmethod
    def detect_recurring_headers(cls, pages_raw: List[str]) -> Set[str]:
        """
        Detectează antetele strict repetitive care apar pe multe pagini
        (ex: titlul sau autorul cărții alternat pe zeci/sute de pagini).
        Nu include titluri de capitole sau nume de personaje.
        """
        top_candidates: List[str] = []
        bottom_candidates: List[str] = []
        total_pages = len(pages_raw)
        if total_pages <= 3:
            min_threshold = 2
        else:
            min_threshold = max(3, int(total_pages * 0.05))

        for p_text in pages_raw:
            lines = [l.strip() for l in p_text.split("\n") if l.strip()]
            if len(lines) < 2:
                continue

            for l in lines[:2]:
                if re.match(r"^(?:capitolul|capitol|chapter|partea|part)\s+", l, re.IGNORECASE):
                    continue
                norm = cls.normalize_for_header_match(l)
                if 3 <= len(norm) <= 60 and not l.endswith((".", "!", "?")):
                    top_candidates.append(norm)

            for last_l in lines[-2:]:
                if re.match(r"^(?:capitolul|capitol|chapter|partea|part)\s+", last_l, re.IGNORECASE):
                    continue
                norm_bot = cls.normalize_for_header_match(last_l)
                if 3 <= len(norm_bot) <= 60 and not last_l.endswith((".", "!", "?")):
                    bottom_candidates.append(norm_bot)

        counts_top = Counter(top_candidates)
        counts_bot = Counter(bottom_candidates)

        recurring = {line for line, count in counts_top.items() if count >= min_threshold}
        recurring.update({line for line, count in counts_bot.items() if count >= min_threshold})
        return recurring

    @classmethod
    def clean_single_page(
        cls,
        raw_text: str,
        author: str = "",
        title: str = "",
        recurring_headers: Optional[Set[str]] = None,
    ) -> str:
        """
        Curăță o singură pagină de text:
        - Elimină caractere nule și artefacte
        - Elimină antetele de la începutul paginii (primele 2 rânduri)
        - Elimină subsolurile de la sfârșitul paginii (ultimele 2 rânduri)
        - Rezolvă despărțirile în silabe la capăt de rând
        """
        if not raw_text:
            return ""

        # 1. Eliminare caractere nule \x00 și caractere de control ciudate
        text = raw_text.replace("\x00", "")
        text = "".join(
            ch for ch in text
            if ch in ("\n", "\r", "\t") or unicodedata.category(ch)[0] != "C"
        )
        text = text.replace("\u00A0", " ").replace("\u200B", "").replace("\uFEFF", "")
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        raw_lines = [l.strip() for l in text.split("\n")]
        # Păstrăm liniile goale interne pentru paragrafe, dar eliminăm zgomotul
        non_empty_indices = [i for i, l in enumerate(raw_lines) if l]

        if not non_empty_indices:
            return ""

        lines_to_exclude = set()

        # Verificăm primele 3 rânduri non-goale (antete, autor, titlu, numere de pagină)
        for idx in non_empty_indices[:3]:
            line_str = raw_lines[idx]
            if cls.is_running_header_or_footer(line_str, author=author, title=title, recurring_headers=recurring_headers):
                lines_to_exclude.add(idx)

        # Verificăm ultimele 2 rânduri non-goale (subsoluri / numere de pagină)
        for idx in non_empty_indices[-2:]:
            line_str = raw_lines[idx]
            if cls.is_running_header_or_footer(line_str, author=author, title=title, recurring_headers=recurring_headers):
                lines_to_exclude.add(idx)

        # Filtrare linii corp pagină
        filtered_lines = []
        for i, line in enumerate(raw_lines):
            if i in lines_to_exclude:
                continue
            # Eliminare directă a indicatorilor de progres Kindle de oriunde din pagină
            if cls.READING_PROGRESS_REGEX.search(line):
                continue
            # Verificare rapidă dacă o linie din interior este doar zgomot pur
            if cls.NOISE_LINE_REGEX.match(line) and len(line) <= 2:
                continue
            filtered_lines.append(line)

        text = "\n".join(filtered_lines)

        # Rezolvare despărțiri în silabe (dehyphenation)
        text = cls.HYPHENATION_REGEX.sub(r"\1\2", text)
        text = cls.HYPHENATION_REGEX.sub(r"\1\2", text)

        # Corectare automată a erorilor tipice OCR (ex: ']' -> '1', '1' -> 'i', 'să 1 se' -> 'să i se', '4' -> 'ă')
        text = cls.fix_ocr_glitches(text)

        return text

    @classmethod
    def fix_ocr_glitches(cls, text: str) -> str:
        """
        Corectează erorile frecvente de recunoaștere optică a caracterelor (OCR):
        - Caracterul ']' scanat în loc de cifra '1' (ex: 'MARTI, 1] AUGUST' -> 'MARTI, 11 AUGUST', 'pagina 1]' -> '11')
        - Cifra '1' scanată în loc de litera 'i' (ex: 'sa 1 se' -> 'sa i se', 'nu-1' -> 'nu-i', '1-a' -> 'i-a')
        - Cifra '1' în interiorul cuvintelor (ex: 'f1indca' -> 'fiindca', 'v1ata' -> 'viata', 'och1' -> 'ochi')
        - Cifra '1' la început de cuvânt românesc (ex: '1n' -> 'în', '1ar' -> 'iar', '1l' -> 'îl', '1i' -> 'îi')
        - Cifra '4' scanată în loc de diacritica 'ă' în interior de cuvinte (ex: 's4' -> 'să', 'fiindc4' -> 'fiindcă')
        """
        if not text:
            return text

        # 1. Caracterul ']' scanat în loc de cifra 1 în numere și date
        # ex: 1] -> 11, 2] -> 21, ]5 -> 15, 1]5 -> 115
        text = re.sub(r"(\b\d+)\]", r"\g<1>1", text)
        text = re.sub(r"(^|[\s.,;:!?\(\-])\](\d+)", r"\g<1>1\g<2>", text)
        text = re.sub(r"(?<=\d)\](?=\d)", "1", text)
        months = r"(?:ianuarie|februarie|martie|aprilie|mai|iunie|iulie|august|septembrie|octombrie|noiembrie|decembrie)"
        text = re.sub(rf"\b(\d+)\s*\]\s*(?={months}\b)", r"\g<1>1 ", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b\]\s*(?={months}\b)", r"1 ", text, flags=re.IGNORECASE)

        # 2. Cifra '1' în forme clitice cu cratimă (ex: 1-a -> i-a, 1-au -> i-au, 1-ar -> i-ar, s-1 -> s-i, nu-1 -> nu-i)
        text = re.sub(r"\b1-([a-zA-ZăîșțâĂÎȘȚÂ]+)\b", r"i-\1", text)
        text = re.sub(r"\b([a-zA-ZăîșțâĂÎȘȚÂ]+)-1\b", r"\1-i", text)

        # 3. Cifra '1' izolată reprezentând pronumele 'i' (ex: 'să 1 se' -> 'să i se', 'nu 1 place' -> 'nu i place')
        pronouns_and_verbs = (
            r"(?:se|s-[a-zA-ZăîșțâĂÎȘȚÂ]+|a|au|ar|am|ai|va|vor|fi|"
            r"place|pl[ăa]cea|trebui|trebuie|zic|zice|zis|spus|spun|spune|"
            r"dau|da|dat|dea|vine|venea|venit|pare|p[ăa]rea|p[ăa]rut|fost|ia|lua|ar[ăa]tat|arat[ăa])"
        )
        prep_pat = (
            rf"\b([sS][ăa]|nu|ca|dac[ăa]|[șsȘS]i|ce|pe|la|f[ăa]r[ăa]|pentru|de)\s+1\s+({pronouns_and_verbs})\b"
        )
        text = re.sub(prep_pat, r"\1 i \2", text, flags=re.IGNORECASE)

        # 4. Cifra '1' în interiorul sau la finalul cuvintelor (ex: f1indca -> fiindca, d1n -> din, och1 -> ochi)
        text = re.sub(r"([a-zA-ZăîșțâĂÎȘȚÂ])1([a-zA-ZăîșțâĂÎȘȚÂ])", r"\g<1>i\g<2>", text)
        text = re.sub(r"([a-zA-ZăîșțâĂÎȘȚÂ]{2,})1\b", r"\g<1>i", text)

        # 5. Cifra '1' la început de cuvânt urmată de litere (ex: 1n -> în, 1ar -> iar, 1l -> îl, 1i -> îi, 1si -> își, 1ti -> îți)
        text = re.sub(r"\b1([nN][a-zA-ZăîșțâĂÎȘȚÂ]*)\b", r"î\1", text)
        text = re.sub(r"\b1([mM][a-zA-ZăîșțâĂÎȘȚÂ]*)\b", r"î\1", text)
        text = re.sub(r"\b1([șȘsS][a-zA-ZăîșțâĂÎȘȚÂ]*)\b", r"î\1", text)
        text = re.sub(r"\b1([țȚtT][a-zA-ZăîșțâĂÎȘȚÂ]*)\b", r"î\1", text)
        text = re.sub(r"\b1([iI]\b)", r"î\1", text)
        text = re.sub(r"\b1([lL]\b)", r"î\1", text)
        text = re.sub(r"\b1([a-zA-ZăîșțâĂÎȘȚÂ]{2,})\b", r"i\1", text)

        # 6. Cifra '4' scanată în loc de 'ă' în interiorul cuvintelor (ex: s4 -> să, fiindc4 -> fiindcă)
        text = re.sub(r"([a-zA-ZăîșțâĂÎȘȚÂ])4([a-zA-ZăîșțâĂÎȘȚÂ])", r"\g<1>ă\g<2>", text)
        text = re.sub(r"\b([scSC])4\b", r"\g<1>ă", text)
        text = re.sub(r"([a-zA-ZăîșțâĂÎȘȚÂ]{2,})4\b", r"\g<1>ă", text)

        return text

    # Alias pentru compatibilitate inversă
    clean_raw_ocr_page = clean_single_page

    @classmethod
    def is_front_matter_or_junk_page(
        cls,
        raw_text: str,
        page_idx: int,
        author: str = "",
        title: str = "",
    ) -> bool:
        """
        Detectează dacă o pagină din debutul cărții (primele 1-6 pagini) este conținut editorial inutil:
        - Liste de cărți ale autorului / colecției (ex: "1. Titlu 2. Titlu 3. Titlu...")
        - Pagina CIP / ISBN / Drepturi de autor / Bun de tipar / Redactor
        - Cuprins static imprimat (care nu are sens în format EPUB reflowable)
        - Pagină de gardă / falsă copertă (text sub 80 caractere)
        """
        if page_idx > 6 or not raw_text:
            return False

        stripped = raw_text.strip()
        lines = [l.strip() for l in stripped.split("\n") if l.strip()]
        if not lines:
            return True

        first_line = lines[0]
        # Capitolele reale încep cu etichete explicite (Capitolul 1, Prolog, etc.)
        if re.search(r"^(?:capitolul|capitol|chapter|partea|part|prolog|epilog)\b", first_line, re.I):
            return False

        # 1. Pagini scurte de gardă / falsă copertă (sub 80 caractere, fără narațiune)
        if len(stripped) < 80:
            return True

        lower = stripped.lower()

        # 2. Date editoriale, CIP, drepturi de autor, ISBN
        cip_keywords = [
            "descrierea cip", "biblioteca națională", "bibliotecii naționale",
            "toate drepturile rezervate", "all rights reserved", "bun de tipar",
            "isbn", "tipărit la", "imprimeria", "redactor:", "tehnoredactor:",
            "coperta:", "corectură:", "copyright ©", "apărut la editura", "casa editorială"
        ]
        cip_matches = sum(1 for kw in cip_keywords if kw in lower)
        if "descrierea cip" in lower or "isbn" in lower or cip_matches >= 2:
            return True

        # 3. Liste de cărți / catalog al editurii (ex: "1. Titlu 2. Titlu 3. Titlu...")
        catalog_keywords = [
            "în aceeași colecție", "din aceeași serie", "alte cărți", "de același autor",
            "au mai apărut", "seria de autor", "opere de", "opere alese", "colecția",
            "cărți publicate", "also by", "other books by"
        ]
        has_catalog_kw = any(kw in lower for kw in catalog_keywords)
        numbered_lines = sum(1 for l in lines if re.match(r"^\s*(?:\d+|[ivxlcdm]+)[\.\)\-]\s+", l, re.I))

        # Dacă are cuvinte cheie de catalog și cel puțin 2 elemente numerotate, sau cel puțin 3 rânduri numerotate
        if (has_catalog_kw and numbered_lines >= 2) or (numbered_lines >= 3 and len(lines) <= 25):
            return True

        # 4. Cuprins static imprimat (Table of Contents)
        if re.search(r"^\s*(?:cuprins|table of contents|contents|sommaire|índice)\s*$", first_line, re.I):
            return True
        if sum(1 for l in lines if re.search(r"\.\.\.+\s*\d+", l)) >= 2:
            return True

        # Detecție listă de capitole numerotate în debutul cărții (ex: 1. Capitolul 1, 2. Luni...)
        chapter_list_count = sum(
            1 for l in lines
            if re.match(
                r"^\s*(?:\d+|[ivxlcdm]+)[\.\)\-]\s*(?:capitol|partea|part|luni|mar[țt]i|miercuri|joi|vineri|s[âa]mb[ăa]t[ăa]|duminic[ăa]|[A-ZĂÎȘȚÂ][a-zăîșțâ]+)",
                l,
                re.I
            )
        )
        if chapter_list_count >= 3 and not any(len(l) > 120 and l.endswith((".", "!", "?")) for l in lines):
            return True

        # 5. Pagină de titlu interior scurtă (Titlu / Autor / Editură sub 150 caractere)
        if author and cls.normalize_for_header_match(author) in lower and len(stripped) < 150:
            return True
        if title and cls.normalize_for_header_match(title) in lower and len(stripped) < 150:
            return True

        # 6. Dacă are narațiune reală (paragraf lung cu punctuație finală)
        has_narrative = any(len(l) > 100 and l.endswith((".", "!", "?", "—", '"', "»")) for l in lines)
        if has_narrative:
            return False

        return False

    @classmethod
    def clean_entire_book_pages(
        cls,
        pages_raw: List[Tuple[int, str]],
        author: str = "",
        title: str = "",
        filter_front_matter: bool = True,
    ) -> List[Tuple[int, str]]:
        """
        Curăță toate paginile din carte:
        1. Ignoră pagina 0 de text dacă este vorba de o carte multi-pagină (pagina 0 este coperta grafică).
        2. Filtrează paginile de introducere inutilă (liste de cărți 1. 2. 3., CIP, Cuprins static).
        3. Detectează recurențele globale (nume autor / titlu alternat pe fiecare pagină).
        4. Curăță fiecare pagină individual (antete, subsoluri, numere de pagină izolate).
        """
        if not pages_raw:
            return []

        # Pas 1: colectare mostre de pagini pentru detectarea antetelor repetitive
        sample_texts = [p[1] for p in pages_raw if len(p[1].strip()) > 30]
        recurring_headers = cls.detect_recurring_headers(sample_texts)

        # Pas 2: filtrare pagini de debut (copertă, catalog 1. 2. 3., CIP) și curățare
        cleaned_pages: List[Tuple[int, str]] = []
        is_multi_page = len(pages_raw) > 1
        story_started = False

        for page_idx, page_text in pages_raw:
            # Dacă cartea are mai multe pagini, pagina 0 este coperta grafică oficială
            if is_multi_page and page_idx == 0:
                continue

            # Filtrare conținut editorial/front-matter înainte de începerea narațiunii reale
            if filter_front_matter and not story_started and page_idx <= 6:
                if cls.is_front_matter_or_junk_page(page_text, page_idx, author=author, title=title):
                    continue
                else:
                    story_started = True

            cleaned = cls.clean_single_page(
                raw_text=page_text,
                author=author,
                title=title,
                recurring_headers=recurring_headers,
            )
            if cleaned.strip():
                cleaned_pages.append((page_idx, cleaned))

        return cleaned_pages

    @classmethod
    def reflow_to_paragraphs(cls, full_text: str) -> List[str]:
        """
        Transformă textul unei pagini sau al unui capitol într-o listă de paragrafe fluide reflowable.
        """
        if not full_text:
            return []

        raw_paragraphs = re.split(r"\n\s*\n+", full_text)
        cleaned_paragraphs: List[str] = []

        for block in raw_paragraphs:
            lines = [l.strip() for l in block.split("\n") if l.strip()]
            if not lines:
                continue

            merged = " ".join(lines)
            merged = re.sub(r"[ \t]+", " ", merged).strip()

            # Evităm adăugarea paragrafelor care conțin doar caractere speciale sau prea scurte
            if len(merged) < 2 and not merged.isalnum():
                continue

            cleaned_paragraphs.append(merged)

        return cleaned_paragraphs
