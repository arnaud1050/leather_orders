"""Turn email.src.html into a self-contained email.html.

    python embed_images.py docs/releases/1.2.0

Reads <folder>/email.src.html, replaces every src="name.png" that points at a
file in the folder with a base64 data URI, and writes <folder>/email.html.
Gmail can't read images linked from a local path when you paste, but it does
accept embedded ones.
"""
import base64
import re
import sys
from pathlib import Path

folder = Path(sys.argv[1])
source = (folder / "email.src.html").read_text(encoding="utf-8")

missing = []


def embed(match):
    name = match.group(1)
    path = folder / name
    if not path.is_file():
        missing.append(name)
        return match.group(0)
    data = base64.b64encode(path.read_bytes()).decode()
    return f'src="data:image/png;base64,{data}"'


built = re.sub(r'src="([^":/]+\.png)"', embed, source)
leftovers = re.findall(r"\{\{[^}]*\}\}", built)

if missing:
    sys.exit(f"Missing image files in {folder}: {', '.join(missing)}")
if leftovers:
    sys.exit(f"Unfilled placeholders left in email.src.html: {len(leftovers)}, first: {leftovers[0][:60]}")

(folder / "email.html").write_text(built, encoding="utf-8")
print(f"Wrote {folder / 'email.html'} ({len(built) // 1024} KB)")
