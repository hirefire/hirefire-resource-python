import re
import sys
from pathlib import Path

HEADING = re.compile(r"^### (P-[a-z][a-z0-9-]+):", re.M)


def main():
    directory = Path(sys.argv[1]).resolve()
    slugs = []
    for name in ("findings.md", "findings-design.md"):
        slugs += HEADING.findall((directory / name).read_text())
    if not slugs:
        sys.exit("no finding with a working name left")
    numbers = {slug: f"P-{index:02d}" for index, slug in enumerate(slugs, start=1)}
    pattern = re.compile(
        "|".join(re.escape(slug) for slug in sorted(slugs, key=len, reverse=True))
        + r"(?![a-z0-9-])"
    )
    pattern = re.compile(
        "("
        + "|".join(re.escape(slug) for slug in sorted(slugs, key=len, reverse=True))
        + r")(?![a-z0-9-])"
    )
    for path in sorted(directory.rglob("*.md")):
        text = path.read_text()
        replaced = pattern.sub(lambda match: numbers[match.group(1)], text)
        if replaced != text:
            path.write_text(replaced)
            print(f"numbered {path.relative_to(directory)}")
    for slug, number in numbers.items():
        print(f"{number} {slug}")


if __name__ == "__main__":
    main()
