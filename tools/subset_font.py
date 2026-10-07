"""Rebuild the report's embedded code font from a Cascadia Code release.

The HTML report carries its code font inside the page, so the font has to be
small: the full Cascadia Mono is ~145 KB a weight, and every comparison page
the watcher writes would carry it. Cut down to the characters source code and
the report's own chrome use — Latin, punctuation, arrows, box drawing — it is
~14 KB a weight.

Cascadia *Mono* rather than Cascadia Code: they are the same typeface, and
Mono is the cut without programming ligatures. The report turns ligatures off
anyway (a font that fuses ``==`` into one glyph shows a character the file
does not contain), so Mono is the same picture with less to carry.

Needs fontTools with brotli, which are deliberately not dependencies::

    pip install fonttools brotli
    python tools/subset_font.py path/to/CascadiaCode-2407.24.zip

The release zip is the one on https://github.com/microsoft/cascadia-code/releases.
The font is licensed under the SIL Open Font License; ``OFL.txt`` beside the
output is that licence and must stay with the files.
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "lambda_watcher" / "diffing" / "fonts"

#: The two weights the report uses: text, and the semibold of names and headings.
WEIGHTS = ("Regular", "SemiBold")

#: Basic Latin and Latin-1/Extended-A; dashes, quotes and the ellipsis; arrows;
#: the minus sign the report writes for removed lines; bullets, box drawing and
#: check marks, which turn up in comments and in the report's own chrome.
UNICODES = ("U+0020-007E,U+00A0-017F,U+2010-2027,U+2030-203A,U+2190-2193,U+21D2,"
            "U+2212,U+2022,U+20AC,U+2122,U+2500-257F,U+25A0-25A1,U+2713-2717")


def main(argv: list[str] | None = None) -> int:
    """Subset both weights out of the release zip into the package's ``fonts`` folder."""
    from fontTools import subset

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("release", type=Path, help="CascadiaCode-<version>.zip")
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.release) as release:
        for weight in WEIGHTS:
            source = release.read(f"woff2/static/CascadiaMono-{weight}.woff2")
            options = subset.Options()
            options.flavor = "woff2"
            options.unicodes = subset.parse_unicodes(UNICODES)
            options.layout_features = ["ccmp", "locl", "mark", "mkmk"]
            options.hinting = False
            options.desubroutinize = True
            font = subset.load_font(io.BytesIO(source), options)
            subsetter = subset.Subsetter(options)
            subsetter.populate(unicodes=options.unicodes)
            subsetter.subset(font)
            target = OUT / f"CascadiaMono-{weight}.woff2"
            subset.save_font(font, str(target), options)
            print(f"{target.relative_to(OUT.parent.parent.parent.parent)}  {target.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
