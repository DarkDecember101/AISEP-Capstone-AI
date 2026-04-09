import fitz  # PyMuPDF
import os
from typing import List, Dict, Any
from src.shared.config.settings import settings


class PDFParser:
    @staticmethod
    def extract_text_and_images(file_path: str, extract_images: bool = False) -> List[Dict[str, Any]]:
        """
        Parses a PDF. Extracts text per page. Optionally extracts images.
        Returns a list of dicts: {"page_num": int, "text": str, "image_path": str}
        """
        doc = fitz.open(file_path)
        pages_data = []

        # Create dir for images if needed
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        img_dir = os.path.join(settings.ARTIFACTS_DIR, "images", base_name)
        if extract_images:
            os.makedirs(img_dir, exist_ok=True)

        for page_num, page in enumerate(doc):
            text = page.get_text("text").strip()
            if not text:
                blocks = page.get_text("blocks") or []
                text = "\n".join(
                    [str(block[4]).strip() for block in blocks if len(
                        block) > 4 and str(block[4]).strip()]
                ).strip()

            image_path = None
            if extract_images or (not text and settings.ENABLE_PSEUDO_OCR_FALLBACK):
                # Always extract image if text is empty and fallback is enabled
                pix = page.get_pixmap()
                os.makedirs(img_dir, exist_ok=True)
                image_path = os.path.join(img_dir, f"page_{page_num}.png")
                pix.save(image_path)

            pages_data.append({
                "page_num": page_num,
                "text": text,
                "image_path": image_path
            })

        return pages_data
