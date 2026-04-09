from typing import List, Dict, Any


class BasicChunker:
    @staticmethod
    def chunk_text(text: str, max_chunk_size: int = 1000, overlap: int = 100) -> List[str]:
        """
        Split text into overlapping chunks based on word count
        """
        words = text.split()
        chunks = []
        start = 0
        while start < len(words):
            end = start + max_chunk_size
            chunk = " ".join(words[start:end])
            chunks.append(chunk)
            start += max_chunk_size - overlap
        return chunks


class PitchDeckExtractor:
    @staticmethod
    def group_pages_simple(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Simple grouping heuristic. For Phase 1, just group small text slides together.
        Real version: use LLM or keywords to classify slides into Intro, Problem, etc.
        Returns groups of slides.
        """
        # Right now, returning batches of 5 pages as "sections"
        batches = []
        batch_size = 5
        for i in range(0, len(pages), batch_size):
            batch = pages[i:i+batch_size]
            section_text = "\n\n".join(
                [f"--- Slide {p['page_num']} ---\n{p['text']}" for p in batch])
            batches.append({
                "section": f"Section {i//batch_size + 1}",
                "text": section_text,
                "pages": [p['page_num'] for p in batch]
            })
        return batches


class BusinessPlanExtractor:
    @staticmethod
    def extract_chunks(full_text: str) -> List[str]:
        """
        Fallback chunk by window since heading detection might be complex for raw PDFs.
        """
        return BasicChunker.chunk_text(full_text)
