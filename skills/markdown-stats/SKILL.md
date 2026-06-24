---
name: markdown-stats
description: Analyze a Markdown file — count lines, headers, links, todos. Use when the user asks about document structure, completion status, or file statistics.
license: MIT
---
# Markdown Stats

Analyze the structure of a Markdown file.

## Workflow
1. Read the file
2. Count: total lines, headers by level (h1/h2/h3), links, checkboxes (done vs pending)
3. Report summary

## Input
- `file_path`: Path to the Markdown file to analyze

## Output
- File name and size
- Line count
- Header count by level
- Checkbox summary (done/pending/total)
