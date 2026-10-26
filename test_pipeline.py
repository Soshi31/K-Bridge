import os
import tempfile
from app.config import AppConfig, load_config, save_config
from app.text_processor import TextProcessor
from app.epub_builder import EPUBBuilder

def test_text_processor():
    raw = "Aces-\nta este un trans-\nfer de text cu \x00 caractere nule.\n\n14\n\nCapitolul 1\nAcesta este un nou paragraf cu linie\nfrântă artificial."
    cleaned = TextProcessor.clean_raw_ocr_page(raw)
    assert "\x00" not in cleaned, "Caracterele nule nu au fost eliminate!"
    assert "Acesta este un transfer" in cleaned, f"Despărțirea în silabe nu a fost rezolvată! Rezultat: {cleaned}"
    
    paras = TextProcessor.reflow_to_paragraphs(cleaned)
    assert len(paras) >= 2, f"Paragrafe insuficiente: {paras}"
    assert TextProcessor.is_likely_chapter_heading("Capitolul 1") is True

    # Test eliminare antete repetitive cu nume autor / titlu
    pages = [
        (0, "AGATHA CHRISTIE\n124\n\nEra o dimineață răcoroasă la Istanbul.\n\n- 124 -"),
        (1, "CRIMA DIN ORIENT EXPRESS\n\nLocomotiva pufăia aburi pe peronul gării.\n\n125"),
        (2, "AGATHA CHRISTIE\n\nHercule Poirot își aranja mustața cu grijă.\n\n126")
    ]
    cleaned_pages = TextProcessor.clean_entire_book_pages(pages, author="Agatha Christie", title="Crima din Orient Express")
    for idx, c_text in cleaned_pages:
        assert "AGATHA CHRISTIE" not in c_text, f"Numele autoarei a rămas în pagina {idx}: {c_text}"
        assert "CRIMA DIN ORIENT EXPRESS" not in c_text, f"Titlul cărții a rămas în pagina {idx}: {c_text}"
        assert "124" not in c_text
        assert "125" not in c_text

    # Test eliminare recurențe chiar și fără metadata setată (pur statistic peste pagini)
    pages_no_meta = [
        (1, "STEPHEN KING\n\nÎnceput de poveste în Maine."),
        (2, "STEPHEN KING\n\nContinuare poveste în Maine.")
    ]
    cleaned_no_meta = TextProcessor.clean_entire_book_pages(pages_no_meta, author="", title="", filter_front_matter=False)
    for idx, c_text in cleaned_no_meta:
        assert "STEPHEN KING" not in c_text, f"Recurența nu a fost eliminată statistic: {c_text}"

    # Test filtrare conținut introductiv inutil (Front Matter / Catalog 1. 2. 3. / CIP / Cuprins static)
    raw_book_pages = [
        (0, "COPERTA GRAFICA A CARTII CU PRET SI LOGO"),
        (1, "CRIMA DIN ORIENT EXPRESS"),  # Pagină scurtă de gardă
        (2, "În aceeași colecție au mai apărut:\n1. Zece negri mititei\n2. Moarte pe Nil\n3. Misterul trenului albastru\n4. Tragedie în trei acte"),  # Catalog listă
        (3, "Descrierea CIP a Bibliotecii Naționale a României\nISBN 978-606-533-289-1\nToate drepturile rezervate editurii."),  # Pagina CIP / Drepturi
        (4, "CUPRINS\nCapitolul 1 ................... 9\nCapitolul 2 ................... 25"),  # Cuprins static
        (5, "CAPITOLUL 1\nPasagerul din Taur Express\n\nEra ora cinci a unei dimineți geroase de iarnă în Siria."),  # Povestea reală
        (6, "Trenul se urni încet pe calea ferată către Istanbul.")
    ]
    filtered_pages = TextProcessor.clean_entire_book_pages(raw_book_pages, author="Agatha Christie", title="Crima din Orient Express")
    kept_page_indices = [p[0] for p in filtered_pages]
    assert 0 not in kept_page_indices, "Pagina 0 (coperta) nu a fost omisă din corpul textului!"
    assert 1 not in kept_page_indices, "Pagina de gardă nu a fost omisă!"
    assert 2 not in kept_page_indices, "Lista de cărți din catalog (1. 2. 3.) nu a fost omisă!"
    assert 3 not in kept_page_indices, "Pagina CIP nu a fost omisă!"
    assert 4 not in kept_page_indices, "Cuprinsul static imprimat nu a fost omis!"
    assert 5 in kept_page_indices, "Capitolul 1 legitim a fost omis greșit!"
    assert 6 in kept_page_indices, "Pagina 6 legitimă a fost omisă greșit!"

    # Test eliminare indicatori de progres Kindle (pagini rămase / ore rămase / locație)
    pages_with_kindle_progress = [
        (0, "Paula Hawkins\n\nÎnceput de capitol într-o noapte întunecată.\n\n10 pagini rămase în capitol 4 minute"),
        (1, "152 pagini rămase în capitol 4 ore 15 minute\n\nNarațiunea continuă lin pe râul înghețat.\n\nLocația 1420 din 5000")
    ]
    cleaned_progress = TextProcessor.clean_entire_book_pages(pages_with_kindle_progress, author="Paula Hawkins", title="În ape adânci")
    for idx, c_text in cleaned_progress:
        assert "pagini rămase" not in c_text, f"Indicatorul de pagini rămase nu a fost eliminat: {c_text}"
        assert "ore" not in c_text or "rămase" not in c_text
        assert "Locația" not in c_text, f"Locația Kindle nu a fost eliminată: {c_text}"

    # Test corectare erori OCR tipice (1 -> i, ] -> 1, 4 -> ă, 'sa 1 se' -> 'sa i se')
    ocr_glitch_sample = "MARTI, 1] AUGUST\nsa 1 se intample ceva si nu-1 pasa\nf1indc4 era 1ntuneric si s4 iasd"
    fixed_sample = TextProcessor.fix_ocr_glitches(ocr_glitch_sample)
    assert "11 AUGUST" in fixed_sample, f"1] nu a fost corectat în 11: {fixed_sample}"
    assert "sa i se" in fixed_sample, f"'sa 1 se' nu a fost corectat: {fixed_sample}"
    assert "nu-i pasa" in fixed_sample, f"'nu-1' nu a fost corectat: {fixed_sample}"
    assert "fiindcă" in fixed_sample or "fiindca" in fixed_sample, f"'f1indc4' nu a fost corectat: {fixed_sample}"
    assert "întuneric" in fixed_sample or "intuneric" in fixed_sample, f"'1ntuneric' nu a fost corectat: {fixed_sample}"
    assert "să iasă" in fixed_sample or "sa iasa" in fixed_sample or "să iasd" in fixed_sample, f"'s4' nu a fost corectat: {fixed_sample}"

    print("[TEST] TextProcessor & Header/Footer Cleaner & Front Matter & Kindle Progress & OCR Glitch Fixer OK!")

def test_epub_builder():
    sample_pages = [
        (0, "Capitolul 1\n\nAcesta este primul paragraf de test pentru KOReader pe Kindle Colorsoft."),
        (1, "Acesta este al doilea paragraf pe o nouă pagină.")
    ]
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_epub = os.path.join(tmp_dir, "test_book.epub")
        res = EPUBBuilder.build_epub("Dune", sample_pages, out_epub, author="Frank Herbert", genre="Science Fiction", series="Dune #1")
        assert os.path.isfile(res), "Fișierul EPUB nu a fost generat!"
        assert os.path.getsize(res) > 500, "Fișierul EPUB este prea mic sau corupt!"

        # Verificare citire metadate din EPUB
        meta = EPUBBuilder.read_epub_metadata(out_epub)
        assert meta.get("title") == "Dune", f"Titlu incorect: {meta}"
        assert meta.get("author") == "Frank Herbert", f"Autor incorect: {meta}"
        assert "Dune" in meta.get("series", ""), f"Serie incorectă: {meta}"

        # Verificare actualizare metadate EPUB direct
        updated = EPUBBuilder.update_epub_metadata(
            out_epub,
            title="Dune (Ediție Revizuită)",
            author="Frank Herbert",
            genre="Sci-Fi Clasic",
            series="Dune #1",
        )
        assert updated is True, "Actualizarea metadatelor EPUB a eșuat!"
        meta_updated = EPUBBuilder.read_epub_metadata(out_epub)
        assert meta_updated.get("title") == "Dune (Ediție Revizuită)", f"Titlu neactualizat: {meta_updated}"
        assert meta_updated.get("genre") == "Sci-Fi Clasic", f"Gen neactualizat: {meta_updated}"

    print("[TEST] EPUBBuilder & Series Metadata & Live Update OK!")

def test_config():
    cfg = load_config()
    assert cfg.kindle_port == 2222
    assert cfg.remote_dir == "/mnt/us/Carti/Biblioteca"
    print("[TEST] Config OK!")

if __name__ == "__main__":
    test_text_processor()
    test_epub_builder()
    test_config()
    print("ALL TESTS PASSED SUCCESSFULLY!")

