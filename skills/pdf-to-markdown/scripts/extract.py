from __future__ import annotations

import os
from pathlib import Path


async def pdf_extract(pdf_path: str) -> dict:
    """Extract text from a PDF file and save as a same-name markdown file.

    Returns {"pdf_path": str, "md_path": str, "pages": int, "chars": int}
    """
    from PyPDF2 import PdfReader

    path = Path(pdf_path)
    if not path.exists():
        return {"error": f"File not found: {pdf_path}"}
    if not path.suffix.lower() == ".pdf":
        return {"error": f"Not a PDF file: {pdf_path}"}

    reader = PdfReader(str(path))
    pages_text = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text:
            pages_text.append(text)

    full_text = "\n\n".join(pages_text)

    md_path = path.with_suffix(".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    return {
        "pdf_path": str(path),
        "md_path": str(md_path),
        "pages": len(reader.pages),
        "chars": len(full_text),
    }
