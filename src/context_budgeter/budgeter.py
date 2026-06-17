from __future__ import annotations

import argparse
import fnmatch
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path

from ._version import __version__

DEFAULT_IGNORES = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "coverage",
    ".idea",
    ".vscode",
}

BINARY_SUFFIXES = {
    ".7z",
    ".bmp",
    ".class",
    ".dll",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpg",
    ".jpeg",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".zip",
}


@dataclass(frozen=True)
class FileContext:
    path: Path
    relative_path: str
    estimated_tokens: int
    bytes: int
    rank_score: int = 0
    selected: bool = False


def _terms(text: str) -> list[str]:
    return [term for term in re.findall(r"[a-z0-9_]+", text.lower()) if len(term) > 1]


def _is_ignored(path: Path, root: Path, ignore_names: set[str]) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in ignore_names or part.endswith(".egg-info") for part in relative.parts)


def _load_gitignore_patterns(root: Path) -> list[str]:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []
    patterns: list[str] = []
    for line in gitignore.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        patterns.append(stripped.lstrip("/"))
    return patterns


def _matches_gitignore(relative_path: str, patterns: list[str]) -> bool:
    parts = relative_path.split("/")
    for pattern in patterns:
        if not pattern:
            continue
        directory_only = pattern.endswith("/")
        normalized = pattern.rstrip("/")
        if directory_only and (relative_path == normalized or relative_path.startswith(normalized + "/")):
            return True
        if "/" in normalized:
            if fnmatch.fnmatch(relative_path, normalized) or relative_path.startswith(normalized + "/"):
                return True
        elif any(fnmatch.fnmatch(part, normalized) for part in parts):
            return True
    return False


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def scan_repository(path: str | Path, extra_ignores: list[str] | None = None) -> list[FileContext]:
    root = Path(path).resolve()
    ignore_names = DEFAULT_IGNORES | set(extra_ignores or [])
    gitignore_patterns = _load_gitignore_patterns(root)
    files: list[FileContext] = []
    for item in sorted(root.rglob("*")):
        if not item.is_file() or _is_ignored(item, root, ignore_names):
            continue
        relative = item.relative_to(root).as_posix()
        if _matches_gitignore(relative, gitignore_patterns):
            continue
        if item.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = item.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append(
            FileContext(
                path=item,
                relative_path=relative,
                estimated_tokens=_estimate_tokens(text),
                bytes=item.stat().st_size,
            )
        )
    return files


def rank_files(files: list[FileContext], task_query: str) -> list[FileContext]:
    query_terms = _terms(task_query)
    ranked: list[FileContext] = []
    for file_context in files:
        path_text = file_context.relative_path.lower()
        name_text = Path(file_context.relative_path).name.lower()
        try:
            content = file_context.path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError):
            content = ""
        score = 0
        for term in query_terms:
            if term in path_text:
                score += 8
            if term in name_text:
                score += 6
            score += min(content.count(term), 6)
        ranked.append(replace(file_context, rank_score=score))
    return sorted(ranked, key=lambda item: (-item.rank_score, item.estimated_tokens, item.relative_path))


def _recommend_ignores(root: Path) -> list[str]:
    recommendations: list[str] = []
    for name in sorted(DEFAULT_IGNORES):
        candidate = root / name
        if candidate.exists():
            recommendations.append(f"- `{name}/` is commonly generated or high-volume; keep it out of context scans.")
    return recommendations


def build_report(
    root: str | Path,
    ranked_files: list[FileContext],
    task_query: str,
    token_budget: int,
    include_ignore_recommendations: bool = False,
) -> str:
    budget = max(0, token_budget)
    used = 0
    selected: list[FileContext] = []
    skipped: list[FileContext] = []
    for file_context in ranked_files:
        if used + file_context.estimated_tokens <= budget:
            selected.append(replace(file_context, selected=True))
            used += file_context.estimated_tokens
        else:
            skipped.append(file_context)

    lines = [
        "# Context Budget Report",
        "",
        f"- Repository: `{Path(root).resolve()}`",
        f"- Task query: `{task_query}`",
        f"- Token budget: {budget}",
        f"- Estimated tokens selected: {used}",
        f"- Files scanned: {len(ranked_files)}",
        "",
        "## Selected Files",
        "",
    ]
    if selected:
        for item in selected:
            lines.append(f"- `{item.relative_path}` - {item.estimated_tokens} tokens, rank {item.rank_score}")
    else:
        lines.append("- No files fit within the current budget.")
    lines.extend(["", "## Highest-Ranked Skipped Files", ""])
    if skipped:
        for item in skipped[:10]:
            lines.append(f"- `{item.relative_path}` - {item.estimated_tokens} tokens, rank {item.rank_score}")
    else:
        lines.append("- None.")
    if include_ignore_recommendations:
        lines.extend(["", "## Ignore Recommendations", ""])
        recommendations = _recommend_ignores(Path(root).resolve())
        lines.extend(recommendations or ["- No common generated directories were detected."])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank repository files for a task within a cheap token budget.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("path", nargs="?", default=".", help="Repository path to scan.")
    parser.add_argument("--query", default="", help="Task query used for ranking.")
    parser.add_argument("--budget", type=int, default=8000, help="Estimated token budget.")
    parser.add_argument("--output", help="Write the Markdown report to this path.")
    parser.add_argument("--ignore", action="append", default=[], help="Additional directory or file name to ignore.")
    parser.add_argument("--recommend-ignore", action="store_true", help="Include ignore recommendations.")
    args = parser.parse_args(argv)

    root = Path(args.path).resolve()
    files = scan_repository(root, args.ignore)
    ranked = rank_files(files, args.query)
    report = build_report(root, ranked, args.query, args.budget, args.recommend_ignore)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        print(f"Wrote context budget report to {output}")
    else:
        print(report, end="")
    return 0
