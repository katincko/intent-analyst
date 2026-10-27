"""Fails when a tracked text file holds an em dash (U+2014) or an en dash (U+2013).

Usage: python scripts/check_dashes.py [path ...]
Without paths it checks every tracked file plus every untracked file that .gitignore does not
ignore. A file with a NUL byte is binary and skipped; any other file must decode as UTF-8, or the
check fails with a message.
"""

import subprocess
import sys
from pathlib import Path

# Written as code points so that this file does not contain the characters it forbids.
FORBIDDEN = {chr(0x2014): "em dash (U+2014)", chr(0x2013): "en dash (U+2013)"}


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return [root / name for name in out.decode("utf-8").split("\0") if name]


def find_dashes(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError:
        # Listed by git but gone from disk (deleted, not yet staged): nothing to check.
        return []
    if bytes([0]) in data:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        return [f"{path}: not valid UTF-8 ({error.reason} at byte {error.start})"]
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for char, name in FORBIDDEN.items():
            if char in line:
                found.append(f"{path}:{number}: {name}")
    return found


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    paths = [Path(a) for a in argv] if argv else tracked_files(root)
    problems = [p for path in paths for p in find_dashes(path)]
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} problem(s). Use a comma, a colon or a hyphen; save text as UTF-8.")
        return 1
    print(f"no em or en dash in {len(paths)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
