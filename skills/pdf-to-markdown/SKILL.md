---
name: pdf-to-markdown
description: Extract text content from PDF files and save as same-name Markdown documents. Use when you need to convert PDF documents to editable Markdown format.
license: MIT
metadata:
  author: skill-engine
  version: "1.0"
---

Extract text content from a PDF file and convert it to a Markdown document saved in the same directory.

## Workflow
1. First, read the PDF file and extract text from all pages
2. Then, save the extracted text as a `.md` file with the same name in the same directory

## Input
- `pdf_path`: Absolute path to the PDF file

## Output
- Markdown file saved alongside the PDF with `.md` extension
- Returns the path to the created markdown file and the number of pages extracted
