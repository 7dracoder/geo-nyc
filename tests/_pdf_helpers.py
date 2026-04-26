"""Tiny synthetic PDFs for tests.

We use PyMuPDF itself to build the fixtures so the test suite doesn't
depend on any pre-baked binaries on disk.
"""

from __future__ import annotations

from pathlib import Path

import fitz


def make_pdf(path: Path, pages: list[str]) -> Path:
    """Write a PDF where each entry in ``pages`` becomes one page of text."""

    doc = fitz.open()
    try:
        for body in pages:
            page = doc.new_page()
            page.insert_text((50, 72), body)
        doc.save(path)
    finally:
        doc.close()
    return path


def make_pdf_bytes(pages: list[str]) -> bytes:
    """Return the binary contents of a freshly-built PDF."""

    doc = fitz.open()
    try:
        for body in pages:
            page = doc.new_page()
            page.insert_text((50, 72), body)
        return bytes(doc.tobytes())
    finally:
        doc.close()
