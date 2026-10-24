#!/usr/bin/env python3
"""Regenerate the test PDFs in tests/fixtures/ from the HTML in tests/fixtures/src/.

Developer tool, not run in CI: the generated PDFs are committed so the tests
don't need a browser. Needs Google Chrome or Chromium.

Usage: python tests/make_fixtures.py [path/to/chrome]
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent / "fixtures"
CANDIDATES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium",
              "chromium-browser"]


def chrome() -> str:
    for c in ([sys.argv[1]] if len(sys.argv) > 1 else []) + CANDIDATES:
        if Path(c).exists() or shutil.which(c):
            return c
    sys.exit("Chrome not found; pass its path as the first argument")


def main() -> None:
    exe = chrome()
    for src in sorted((HERE / "src").glob("*.html")):
        out = HERE / (src.stem + ".pdf")
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--generate-pdf-document-outline=false", f"--print-to-pdf={out}", src.as_uri()],
                       check=True, capture_output=True)
        print(f"{out.name}: {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
