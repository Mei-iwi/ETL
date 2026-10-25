"""Create a handoff archive from an explicit source allowlist, never local data."""

import argparse
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT_FILES = {
    "pyproject.toml",
    "README.md",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    "docker-compose.yml",
    "Dockerfile",
    "Dockerfile.qa",
    "alembic.ini",
    "alembic_source.ini",
}
TREES = {
    "src": {".py"},
    "tests": {".py", ".json"},
    "scripts": {".py", ".sql"},
    "alembic": {".py", ".mako"},
    "alembic_source": {".py", ".sql"},
    "fixtures": {".json"},
}
LOCAL_DIRECTORIES = {"venv", "node_modules", "build", "dist", "volumes", "local_data", "secrets", "credentials", "keys"}


def private_file(relative):
    # The root placeholder is the only env file admitted by the allowlist.
    if relative.as_posix() == ".env.example":
        return False
    name = relative.name.casefold()
    return name.startswith(".env") or bool(re.search(
        r"(?:^|[_.-])(secrets?|credentials?|tokens?|private[_-]?keys?)(?:$|[_.-])", name
    ))


def source_files(root):
    root = Path(root).resolve()
    candidates = [root / name for name in ROOT_FILES if (root / name).is_file()]
    for tree, suffixes in TREES.items():
        candidates.extend(
            p for p in (root / tree).rglob("*") if p.is_file() and p.suffix in suffixes
        )
    for path in sorted(candidates):
        relative = path.relative_to(root)
        if private_file(relative):
            continue
        if any(
            part.startswith(".") or part == "__pycache__" or part.endswith(".egg-info")
            or part.casefold() in LOCAL_DIRECTORIES
            for part in relative.parts[:-1]
        ):
            continue
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root):
            raise ValueError("Source archive refuses symbolic links")
        if not path.resolve().is_relative_to(root):
            raise ValueError("Source archive path escapes project")
        yield path


def package(root, output):
    root = Path(root).resolve()
    files = list(source_files(root))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in files:
            name = path.relative_to(root).as_posix()
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/data-sync-etl-source.zip")
    args = parser.parse_args()
    count = package(Path(__file__).resolve().parents[1], args.output)
    print(f"Source archive created: {count} files; local environment and storage excluded")


if __name__ == "__main__":
    main()
