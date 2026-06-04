"""Extract text from a PDF file and save as a same-name markdown file.

Usage: python3 extract_pdf.py --pdf-path /path/to/file.pdf
"""
import argparse
import sys
from pathlib import Path

from PyPDF2 import PdfReader


def main():
    parser = argparse.ArgumentParser(description="Extract PDF text to Markdown")
    parser.add_argument("--pdf-path", required=True, help="Path to the PDF file")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"Error: File not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)
    if pdf_path.suffix.lower() != ".pdf":
        print(f"Error: Not a PDF file: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    reader = PdfReader(str(pdf_path))
    pages_text = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)

    full_text = "\n\n".join(pages_text)

    md_path = pdf_path.with_suffix(".md")
    md_path.write_text(full_text, encoding="utf-8")

    print(f"Extracted {len(reader.pages)} pages ({len(full_text)} characters)")
    print(f"Saved to: {md_path}")


if __name__ == "__main__":
    main()
