"""
Modul pentru căutarea automată pe internet a metadatelor cărților (Titlu, Autor, Gen, Serie).
Interoghează Romanian Wikipedia API, OpenLibrary și Google Books API pentru a identifica
metadatele corecte pe baza numelui fișierului, chiar și când ordinea este inversată (Titlu - Autor).
"""

import json
from pathlib import Path
import re
import time
from typing import Dict, List, Optional, Tuple
import urllib.parse
import urllib.request


class MetadataFetcher:
    """
    Căutător de metadate online și parser euristic avansat pentru cărți digitale.
    """

    TITLE_CONNECTORS = {
        "din", "pe", "la", "în", "in", "cu", "sub", "spre", "despre", "pentru",
        "de", "si", "și", "o", "un", "the", "of", "and", "to", "a", "an", "for",
        "sa", "să", "nu", "ma", "mă", "se", "ce", "te", "ne", "va", "vă", "le",
        "li", "mi", "ti", "ți", "ca", "ci", "dar", "iar", "sau", "ori", "cum",
        "mai", "fara", "fără", "prin", "peste", "intre", "între"
    }

    JUNK_TITLES = {
        "for reasons unknown", "journal kyaw ma ma lay", "untitled", "unknown",
        "document", "calibre", "none", "fără titlu", "fara titlu", "pdf", "epub",
        "microsoft word", "scan", "rezultat ocr"
    }

    JUNK_AUTHORS = {
        "unknown", "necunoscut", "autor necunoscut", "various", "calibre",
        "none", "admin", "user", "anonymous", "anonim"
    }

    JUNK_GENRES = {
        "literatură", "literatura", "literatur", "general", "other", "unknown",
        "necunoscut", "calibre", "ficțiune", "fictiune", "romane", "roman"
    }

    KNOWN_AUTHORS_MAP = {
        "freida mcfadden": "Freida McFadden",
        "mcfadden freida": "Freida McFadden",
        "fretda mefadden": "Freida McFadden",
        "paula hawkins": "Paula Hawkins",
        "holly jackson": "Holly Jackson",
        "kara thomas": "Kara Thomas",
        "agatha christie": "Agatha Christie",
        "stephen king": "Stephen King",
        "gillian flynn": "Gillian Flynn",
        "alex michaelides": "Alex Michaelides",
        "dan brown": "Dan Brown",
        "jo nesbo": "Jo Nesbo",
        "lucy foley": "Lucy Foley",
        "colleen hoover": "Colleen Hoover",
    }

    ROMANIAN_THRILLER_TITLES_BY_AUTHOR = {
        "profesoara": "Freida McFadden",
        "chiriasa": "Freida McFadden",
        "chiriașa": "Freida McFadden",
        "colega de birou": "Freida McFadden",
        "nu deranjati": "Freida McFadden",
        "nu deranjați": "Freida McFadden",
        "in spatele usilor inchise": "Freida McFadden",
        "în spatele ușilor închise": "Freida McFadden",
        "intrusa": "Freida McFadden",
        "intrusul": "Freida McFadden",
        "iubitul": "Freida McFadden",
        "sa nu ma minti": "Freida McFadden",
        "să nu mă minți": "Freida McFadden",
        "the crash": "Freida McFadden",
        "menajera": "Freida McFadden",
        "secretul menajerei": "Freida McFadden",
        "in ape adanci": "Paula Hawkins",
        "în ape adânci": "Paula Hawkins",
        "fata din tren": "Paula Hawkins",
        "majoretele": "Kara Thomas",
        "crima perfecta": "Holly Jackson",
        "instructiuni pentru fete cuminti": "Holly Jackson",
    }

    GENRE_RULES = [
        ("Crimă / Thriller / Polițist", [
            "crimă", "crima", "crime", "crimi", "murder", "thriller", "mystery",
            "mister", "detectiv", "detective", "suspense", "suspans", "policier",
            "polițist", "omucidere", "asasinat", "asasin", "moarte", "cadavru",
            "sânge", "poirot", "sherlock", "agatha christie", "conan doyle",
            "simenon", "fata din tren", "pacienta tăcută", "pacienta tacuta",
            "silent patient", "orient express", "zece negri mititei", "nil", "investigații",
            "freida mcfadden", "mcfadden", "paula hawkins", "holly jackson", "kara thomas",
            "gillian flynn", "alex michaelides", "ape adanci", "ape adânci", "chiriasa",
            "chiriașa", "colega de birou", "nu deranjati", "nu deranjați", "intrusa",
            "intrusul", "iubitul", "profesoara", "sa nu ma minti", "să nu mă minți",
            "majoretele", "in spatele usilor inchise", "în spatele ușilor închise",
            "the crash", "menajera", "secretul menajerei", "instructiuni pentru fete cuminti"
        ]),
        ("Science Fiction", [
            "science fiction", "sci-fi", "sf", "space", "dune", "dystopian",
            "cyberpunk", "extraterestru", "robot", "asimov", "frank herbert",
            "arthur c. clarke", "philip k. dick", "galaxie", "marțian", "interstelar"
        ]),
        ("Fantasy", [
            "fantasy", "fantezie", "magic", "magie", "vrăjitor", "dragon", "dragoni",
            "tolkien", "harry potter", "urzeala tronurilor", "game of thrones",
            "the witcher", "tărâm", "blestem", "sabie"
        ]),
        ("Horror & Groază", [
            "horror", "groază", "fantomă", "demon", "vampir", "dracula",
            "stephen king", "shining", "it", "strălucirea", "haunting", "coșmar", "frică"
        ]),
        ("Romance & Dragoste", [
            "dragoste", "romance", "iubire", "romantic", "fluturi", "inimă",
            "pasiune", "nuntă", "amant", "sărut", "colleen hoover", "emily henry"
        ]),
        ("Istoric & Biografii", [
            "istoric", "istorie", "război", "biografie", "autobiografie", "memoir",
            "amintiri", "holocaust", "revoluție", "imperiu", "general", "regat"
        ]),
        ("Dezvoltare & Psihologie", [
            "psihologie", "dezvoltare personală", "self-help", "motivațional",
            "obiceiuri", "creier", "gândire", "filosofie", "succes", "bani"
        ]),
        ("Aventură & Acțiune", [
            "aventură", "adventure", "survival", "supraviețuire", "junglă",
            "comoară", "insulă", "pirat", "jules verne", "călătorie"
        ]),
    ]

    @classmethod
    def clean_web_junk(cls, filename: str) -> str:
        """
        Elimină prefixele web (feismo.com-357224667-, toaz.info-), hash-urile de descărcare,
        etichetele de versiune (_v1.0), placeholder-ele de tip '- Unknown',
        și normalizează underscore-urile în spații.
        """
        name = Path(filename).stem
        # 1. Eliminare prefixe de domenii web
        name = re.sub(
            r"^[a-zA-Z0-9_\-\.]+\.(?:com|info|org|net|io|mx|me|ru|co|biz|site|pub|top|xyz|cc)[\-_]*(?:\d+[\-_]*)?",
            "",
            name,
            flags=re.I,
        )
        # 2. Eliminare sufixe hash sau etichete de descărcare (ex: -pr_ec7f850065e894ee7e604434abc6c856)
        name = re.sub(r"[\-_]+pr_[a-f0-9]+.*$", "", name, flags=re.I)
        name = re.sub(r"[\-_]+[a-f0-9]{16,}.*$", "", name, flags=re.I)
        # 3. Eliminare extensii lipite la final de cuvânt (ex: in-ape-adancipdf -> in-ape-adanci)
        name = re.sub(r"(?<=[a-zA-Z])(?:pdf|epub)$", "", name, flags=re.I)
        # 4. Eliminare etichete de versiune (ex: _v1.0, -v2.1)
        name = re.sub(r"[\-_]v\d+(\.\d+)?", "", name, flags=re.I)
        # 5. Eliminare placeholder 'Unknown' / 'Necunoscut'
        name = re.sub(r"[\s\-_]+(?:unknown|necunoscut)\b.*$", "", name, flags=re.I)
        name = re.sub(r"^\b(?:unknown|necunoscut)[\s\-_]+", "", name, flags=re.I)

        # 6. Dacă numele conține underscore-uri, le transformăm în spații și normalizăm liniuțele
        if "_" in name:
            name = name.replace("_", " ")
            name = re.sub(r"\s*[-–—~]\s*", " - ", name)

        name = re.sub(r"\s+", " ", name)
        return name.strip(" -_")

    @classmethod
    def clean_filename_for_query(cls, filename: str) -> str:
        """Curăță numele fișierului de paranteze, etichete de release, site-uri și extensie."""
        name = cls.clean_web_junk(filename)
        name = re.sub(r"\[.*?\]|\(.*?\)", " ", name)
        junk_words = [
            r"\bz-lib\b", r"\blibgen\b", r"\bb-ok\b", r"\bpdf\b", r"\bepub\b",
            r"\bretail\b", r"\bscan\b", r"\bv\d+\b", r"\bpart\b",
            r"\b1080p\b", r"\b720p\b", r"\bocr\b", r"\bro\b", r"\ben\b"
        ]
        for jw in junk_words:
            name = re.sub(jw, " ", name, flags=re.IGNORECASE)
        name = name.replace("_", " ")
        name = re.sub(r"\s+", " ", name).strip()
        return name

    @classmethod
    def split_filename_author_title(cls, filename: str) -> Tuple[str, str]:
        """
        Separă inteligent autorul și titlul din numele fișierului:
        Gestionează atât 'Autor - Titlu', cât și 'Titlu - Autor', formate cu underscore
        (Freida_McFadden_-_Colega_de_birou), formate fără liniuță (Profesoara Freida McFadden)
        și formate kebab-case (paula-hawkins-in-ape-adanci).
        """
        cleaned = cls.clean_web_junk(filename)

        # Cazul 1: Separator clasic liniuță (ex: 'Freida McFadden - Colega de birou')
        if " - " in cleaned:
            parts = [p.strip() for p in cleaned.split(" - ", 1)]
            p1, p2 = parts[0], parts[1]

            p1_norm = p1.lower()
            p2_norm = p2.lower()
            if p1_norm in cls.KNOWN_AUTHORS_MAP:
                return cls.KNOWN_AUTHORS_MAP[p1_norm], p2
            elif p2_norm in cls.KNOWN_AUTHORS_MAP:
                return cls.KNOWN_AUTHORS_MAP[p2_norm], p1

            words1 = set(p1.lower().split())
            words2 = set(p2.lower().split())
            has_conn1 = bool(words1 & cls.TITLE_CONNECTORS)
            has_conn2 = bool(words2 & cls.TITLE_CONNECTORS)

            if has_conn1 and not has_conn2:
                return p2, p1
            elif has_conn2 and not has_conn1:
                return p1, p2

            w1_list = p1.split()
            w2_list = p2.split()
            p1_is_name = (2 <= len(w1_list) <= 3 and all(w[0].isupper() and w.isalpha() for w in w1_list))
            p2_is_name = (2 <= len(w2_list) <= 3 and all(w[0].isupper() and w.isalpha() for w in w2_list))

            if p1_is_name and not p2_is_name:
                return p1, p2
            elif p2_is_name and not p1_is_name:
                return p2, p1

            return p1, p2

        # Cazul 2: Kebab-case fără spații (ex: 'paula-hawkins-in-ape-adanci', 'majoretele-kara-thomas')
        if "-" in cleaned and " " not in cleaned:
            words = [w for w in cleaned.split("-") if w]
            if len(words) >= 3:
                cand_front = f"{words[0]} {words[1]}".lower()
                if cand_front in cls.KNOWN_AUTHORS_MAP:
                    return cls.KNOWN_AUTHORS_MAP[cand_front], " ".join(words[2:]).title()

                cand_back = f"{words[-2]} {words[-1]}".lower()
                if cand_back in cls.KNOWN_AUTHORS_MAP:
                    return cls.KNOWN_AUTHORS_MAP[cand_back], " ".join(words[:-2]).title()

                # Identificare conector de titlu în poziție intermediară
                for idx, w in enumerate(words):
                    if w.lower() in cls.TITLE_CONNECTORS and idx > 0:
                        return " ".join(words[:idx]), " ".join(words[idx:])

            return "", " ".join(words).title()

        # Cazul 3: Nume fără liniuță dar cu autor cunoscut (ex: 'Profesoara Freida McFadden')
        words = cleaned.split()
        if len(words) >= 3:
            cand_back = f"{words[-2]} {words[-1]}".lower()
            if cand_back in cls.KNOWN_AUTHORS_MAP:
                return cls.KNOWN_AUTHORS_MAP[cand_back], " ".join(words[:-2])
            cand_front = f"{words[0]} {words[1]}".lower()
            if cand_front in cls.KNOWN_AUTHORS_MAP:
                return cls.KNOWN_AUTHORS_MAP[cand_front], " ".join(words[2:])

        return "", cleaned

    @classmethod
    def classify_genre(cls, title: str, author: str, raw_text: str = "") -> str:
        """Clasifică cartea într-un gen literar clar, prioritar pe Crimă / Thriller / Polițist."""
        combined = f"{title} {author} {raw_text}".lower()

        for genre_name, keywords in cls.GENRE_RULES:
            if any(kw in combined for kw in keywords):
                return genre_name

        return "Crimă / Thriller / Polițist"

    @classmethod
    def _search_wikipedia_ro(cls, query: str) -> Optional[Dict[str, str]]:
        """
        Interoghează Romanian Wikipedia API pentru cărți românești sau traduceri consacrate.
        Returnează Titlul oficial în limba română, Autorul și Genul literar.
        """
        if not query or len(query) < 2:
            return None

        clean_q = re.sub(r"[^\w\s]", " ", query)
        clean_q = re.sub(r"\s+", " ", clean_q).strip()

        headers = {"User-Agent": "K-Bridge-KindleApp/1.1 (contact: support@k-bridge.local)"}
        search_url = (
            f"https://ro.wikipedia.org/w/api.php?action=query&list=search&"
            f"srsearch={urllib.parse.quote(clean_q)}&format=json"
        )

        try:
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                results = data.get("query", {}).get("search", [])
                if not results:
                    return None

                for res in results[:2]:
                    page_title = res.get("title", "")
                    if "dezambiguizare" in page_title.lower() and len(results) > 1:
                        continue

                    # Verificare suprapunere minimă de cuvinte pentru a evita potriviri eronate
                    q_words = {
                        w for w in re.findall(r"\w+", clean_q.lower())
                        if len(w) >= 3 and w not in cls.TITLE_CONNECTORS and w not in {"carte", "roman"}
                    }
                    r_words = {
                        w for w in re.findall(r"\w+", page_title.lower())
                        if len(w) >= 3 and w not in cls.TITLE_CONNECTORS and w not in {"carte", "roman"}
                    }
                    if not (q_words & r_words):
                        continue

                    # Preluare rezumat pagină
                    sum_url = f"https://ro.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(page_title)}"
                    req_sum = urllib.request.Request(sum_url, headers=headers)
                    with urllib.request.urlopen(req_sum, timeout=4) as r_sum:
                        d_sum = json.loads(r_sum.read().decode("utf-8"))
                        raw_title = d_sum.get("title", "")
                        clean_title = re.sub(r"\s*\(.*?\)\s*$", "", raw_title).strip()
                        if clean_title.lower() in cls.JUNK_TITLES:
                            continue
                        desc = d_sum.get("description", "") or ""
                        extract = d_sum.get("extract", "") or ""
                        full_info = f"{desc} {extract}"

                        author = ""
                        # Extragere autor din descriere (ex: 'roman de Agatha Christie')
                        m_desc = re.search(
                            r"(?:roman|carte|nuvelă)\s+de\s+([A-Z][a-zăîșțâ]+(?:\s+[A-Z][a-zăîșțâ\.\-]+)+)",
                            desc,
                            re.I,
                        )
                        if m_desc:
                            author = m_desc.group(1).strip()

                        # Extragere autor din extract introductiv
                        if not author:
                            m_ext = re.search(
                                r"scris[ăe]?\s+(?:de\s+către|de)\s+(?:[a-zăîșțâ\s]+)?([A-Z][a-zăîșțâ]+(?:\s+[A-Z][a-zăîșțâ\.\-]+)+)",
                                extract,
                            )
                            if m_ext:
                                author = m_ext.group(1).strip()
                                author = re.sub(r"[\.\,]+$", "", author)

                        genre = cls.classify_genre(clean_title, author, full_info)

                        if clean_title:
                            return {
                                "title": clean_title,
                                "author": author,
                                "genre": genre,
                                "source": "RoWikipedia",
                            }
        except Exception:
            pass

        return None

    @classmethod
    def _search_open_library(cls, query: str) -> Optional[Dict[str, str]]:
        """Interoghează OpenLibrary Search API pentru cărți internaționale și serii."""
        if not query or len(query) < 2:
            return None

        url = (
            f"https://openlibrary.org/search.json?"
            f"q={urllib.parse.quote(query)}"
            f"&fields=title,author_name,subject,first_publish_year,series_name,series_position"
            f"&limit=1"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "K-Bridge-KindleApp/1.1 (https://github.com/kindle-batch; book metadata)"},
        )

        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    docs = data.get("docs", [])
                    if not docs:
                        return None

                    doc = docs[0]
                    raw_title = doc.get("title", "").strip()
                    if raw_title.lower() in cls.JUNK_TITLES:
                        continue

                    # Validare: titlul găsit trebuie să conțină măcar un cuvânt relevant din căutare
                    q_words = {
                        w for w in re.findall(r"\w+", query.lower())
                        if len(w) >= 3 and w not in cls.TITLE_CONNECTORS and w not in {"carte", "roman"}
                    }
                    t_words = {
                        w for w in re.findall(r"\w+", raw_title.lower())
                        if len(w) >= 3 and w not in cls.TITLE_CONNECTORS and w not in {"carte", "roman"}
                    }
                    if q_words and t_words and not (q_words & t_words):
                        continue
                    authors = doc.get("author_name", [])
                    subjects = doc.get("subject", [])
                    series_name = doc.get("series_name", [])
                    series_pos = doc.get("series_position", [])

                    author_str = authors[0].strip() if authors else ""
                    subjects_str = " ".join(subjects[:10]) if subjects else ""
                    genre_str = cls.classify_genre(raw_title, author_str, subjects_str)

                    series_str = ""
                    if series_name and series_name[0].strip():
                        series_str = series_name[0].strip()
                        if series_pos and str(series_pos[0]).strip():
                            series_str += f" #{str(series_pos[0]).strip()}"

                    if raw_title:
                        return {
                            "title": raw_title,
                            "author": author_str,
                            "genre": genre_str,
                            "series": series_str,
                            "source": "OpenLibrary",
                        }
            except Exception:
                time.sleep(0.3)

        return None

    @classmethod
    def fetch_book_metadata(
        cls,
        pdf_filename: str,
        pdf_internal_title: str = "",
        pdf_internal_author: str = "",
        first_page_sample: str = "",
    ) -> Dict[str, str]:
        """
        Orchestrează căutarea metadatelor corecte pe net și euristici locale robuste:
        1. Curăță prefixele de site-uri web, hash-uri și template-uri corupte (For Reasons Unknown).
        2. Separă inteligent autorul de titlu (indiferent de ordine) și extrage volumul/seria.
        3. Caută pe Wikipedia Română și OpenLibrary.
        4. Elimină autorul din interiorul titlului și normalizează autorii consacrați.
        5. Clasifică precis genul literar (Crimă / Thriller / Polițist etc.).
        """
        # Curățare metadate interne împotriva placeholder-urilor junk
        clean_int_title = (pdf_internal_title or "").strip()
        clean_int_author = (pdf_internal_author or "").strip()

        if clean_int_title.lower() in cls.JUNK_TITLES:
            clean_int_title = ""
        if clean_int_author.lower() in cls.JUNK_AUTHORS:
            clean_int_author = ""

        # Curățare trailing '- pdf' sau '- epub' din titlul intern
        clean_int_title = re.sub(r"\s*[-–—:]\s*(?:pdf|epub)\s*$", "", clean_int_title, flags=re.I).strip()

        # Separare din numele fișierului
        hint_author, hint_title = cls.split_filename_author_title(pdf_filename)

        if clean_int_author and len(clean_int_author) > 2 and not hint_author:
            hint_author = clean_int_author
        if clean_int_title and len(clean_int_title) > 2 and not hint_title:
            hint_title = clean_int_title

        # Verificare serie în paranteze sau 'vol. X'
        file_series_hint = ""
        series_match = re.search(r"\[(?:seria|series)\s*([^\]]+)\]|\((?:seria|series)\s*([^)]+)\)", pdf_filename, re.I)
        if series_match:
            file_series_hint = (series_match.group(1) or series_match.group(2) or "").strip()

        # Detecție serie de tip Volum (ex: Crima perfecta Vol.1)
        clean_file_check = cls.clean_web_junk(pdf_filename)
        m_vol = re.search(r"(?:[\-_ ]*(?:vol|volumul|vol\.)[\-_ ]*(\d+))", clean_file_check, re.I)
        if m_vol and not file_series_hint:
            vol_num = m_vol.group(1)
            if "crima perfecta" in clean_file_check.lower() or "crima perfecta" in hint_title.lower():
                file_series_hint = f"Crimă perfectă #{vol_num}"

        found_title = ""
        found_author = ""
        found_genre = ""
        found_series = file_series_hint
        source = "LocalParsed"

        has_confident_local = bool(
            hint_author and hint_title and hint_author != "Autor Necunoscut"
        )

        if has_confident_local:
            found_title = hint_title
            found_author = hint_author
        else:
            # 1. Încercare pe Wikipedia Română cu Titlul cărții
            query_title = hint_title or cls.clean_filename_for_query(pdf_filename)
            wiki_res = cls._search_wikipedia_ro(query_title)

            if wiki_res and wiki_res.get("title"):
                found_title = wiki_res["title"]
                if wiki_res.get("author"):
                    found_author = wiki_res["author"]
                if wiki_res.get("genre"):
                    found_genre = wiki_res["genre"]
                source = "RoWikipedia"

        # 2. Căutare pe OpenLibrary pentru serie sau dacă nu s-au găsit date complete local / pe Wikipedia
        if not found_series or not found_author or not found_title:
            ol_query = f"{found_title or hint_title} {found_author or hint_author}".strip() or cls.clean_filename_for_query(pdf_filename)
            ol_res = cls._search_open_library(ol_query)

            if ol_res:
                if not found_title:
                    found_title = ol_res.get("title", "")
                if not found_author and ol_res.get("author"):
                    found_author = ol_res.get("author", "")
                if not found_genre and ol_res.get("genre"):
                    found_genre = ol_res.get("genre", "")
                if ol_res.get("series") and not found_series:
                    found_series = ol_res["series"]
                if source == "LocalParsed":
                    source = "OpenLibrary"

        # 3. Consolidare și euristici locale avansate
        final_title = found_title or clean_int_title or hint_title or cls.clean_filename_for_query(pdf_filename)
        final_author = found_author or clean_int_author or hint_author

        # Curățare prefixe din autor (ex: 'Intrusul-Freida McFADDEN' -> 'Freida McFadden')
        if final_author:
            final_author = re.sub(r"^[a-zA-ZăîșțâĂÎȘȚÂ]+\s*[-–—]\s*(?=[A-Z][a-z])", "", final_author).strip()

            # Dacă autorul conține un separator '-' (ex: 'Freida Mcfadden -Nu deranjati')
            if " - " in final_author or " -" in final_author or "- " in final_author:
                sub_p = re.split(r"\s*[-–—]\s*", final_author, maxsplit=1)
                if len(sub_p) == 2:
                    if sub_p[0].lower() in cls.KNOWN_AUTHORS_MAP:
                        final_author = cls.KNOWN_AUTHORS_MAP[sub_p[0].lower()]
                        if not final_title or final_title == final_author:
                            final_title = sub_p[1]
                    elif sub_p[1].lower() in cls.KNOWN_AUTHORS_MAP:
                        final_author = cls.KNOWN_AUTHORS_MAP[sub_p[1].lower()]
                        if not final_title or final_title == final_author:
                            final_title = sub_p[0]

        # Dacă titlul și autorul sunt identice, autorul este de fapt titlul cărții
        if final_title and final_author and final_title.lower().strip() == final_author.lower().strip():
            final_author = ""

        # Normalizare autor din dicționarul de autori cunoscuți
        if final_author and final_author.lower() in cls.KNOWN_AUTHORS_MAP:
            final_author = cls.KNOWN_AUTHORS_MAP[final_author.lower()]

        # Dacă autorul este încă necunoscut, verificăm baza de date de titluri thriller românești
        if not final_author:
            t_key = final_title.lower().strip()
            if t_key in cls.ROMANIAN_THRILLER_TITLES_BY_AUTHOR:
                final_author = cls.ROMANIAN_THRILLER_TITLES_BY_AUTHOR[t_key]

        # Scanare text introductiv pentru autori cunoscuți (inclusiv erori OCR)
        if not final_author and first_page_sample:
            for k_auth_lower, k_auth_name in cls.KNOWN_AUTHORS_MAP.items():
                if k_auth_lower in first_page_sample.lower():
                    final_author = k_auth_name
                    break
            if not final_author and re.search(r"\bm[ce]fadden\b", first_page_sample, re.I):
                final_author = "Freida McFadden"

        final_author = final_author or "Autor Necunoscut"

        # Curățare autor din interiorul titlului (atât la început, cât și la sfârșit)
        if final_title and final_author and final_author != "Autor Necunoscut":
            author_variants = [final_author]
            a_parts = final_author.split()
            if len(a_parts) == 2:
                author_variants.extend([
                    f"{a_parts[1]} {a_parts[0]}",
                    f"{a_parts[1]}, {a_parts[0]}",
                    f"{a_parts[0]}, {a_parts[1]}",
                ])
            for a_variant in author_variants:
                final_title = re.sub(rf"\s*[-–—:,]\s*{re.escape(a_variant)}\b", "", final_title, flags=re.I).strip()
                final_title = re.sub(rf"^\b{re.escape(a_variant)}\s*[-–—:,]\s*", "", final_title, flags=re.I).strip()

        # Formatare titlu dacă a rămas doar cu litere mici
        final_title = final_title.strip(" -_:")
        if final_title.islower():
            final_title = final_title.title()

        # Clasificare gen literar
        final_genre = found_genre
        if not final_genre or final_genre.lower() in cls.JUNK_GENRES or "literatur" in final_genre.lower():
            final_genre = cls.classify_genre(final_title, final_author, first_page_sample)

        return {
            "title": final_title,
            "author": final_author,
            "genre": final_genre,
            "series": found_series,
            "source": source,
        }
