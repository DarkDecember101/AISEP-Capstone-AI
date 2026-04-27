import fitz  # PyMuPDF
import os
from typing import List, Dict, Any
from src.shared.config.settings import settings


class PDFParser:
    @staticmethod
    def extract_text_and_images(
        file_path: str,
        extract_images: bool = False,
        image_only_for_low_text: bool = False,
        low_text_char_threshold: int = 160,
        max_images: int | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Parses a PDF. Extracts text per page. Optionally extracts images.
        Returns a list of dicts: {"page_num": int, "text": str, "image_path": str}
        """
        doc = fitz.open(file_path)
        pages_data = []
        rendered_images = 0

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
            should_render_image = False
            if not text and settings.ENABLE_PSEUDO_OCR_FALLBACK:
                should_render_image = True
            elif extract_images:
                should_render_image = (
                    len(text) < low_text_char_threshold
                    if image_only_for_low_text
                    else True
                )

            if should_render_image and (max_images is None or rendered_images < max_images):
                pix = page.get_pixmap()
                os.makedirs(img_dir, exist_ok=True)
                image_path = os.path.join(img_dir, f"page_{page_num}.png")
                pix.save(image_path)
                rendered_images += 1

            pages_data.append({
                "page_num": page_num,
                "text": text,
                "image_path": image_path
            })

        doc.close()
        return pages_data
