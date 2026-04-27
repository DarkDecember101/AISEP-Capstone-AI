import fitz


def test_extract_text_and_images_limits_pitch_deck_images(tmp_path):
    from src.modules.evaluation.infrastructure.parsers.pdf_parser import PDFParser

    pdf_path = tmp_path / "deck.pdf"
    doc = fitz.open()

    page = doc.new_page()
    page.insert_text((72, 72), "This slide has enough extracted text to skip image rendering.")

    page = doc.new_page()
    page.insert_text((72, 72), "Short")

    doc.new_page()

    doc.save(pdf_path)
    doc.close()

    pages = PDFParser.extract_text_and_images(
        str(pdf_path),
        extract_images=True,
        image_only_for_low_text=True,
        low_text_char_threshold=20,
        max_images=1,
    )

    assert pages[0]["image_path"] is None
    rendered = [p["image_path"] for p in pages if p["image_path"]]
    assert len(rendered) == 1
