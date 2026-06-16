#!/usr/bin/env python3
"""Hello world skill — echoes input text with a greeting."""
import sys
text = sys.argv[1] if len(sys.argv) > 1 else "World"
print(f"Hello, {text}!")

