from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import re
import shutil
import stat
import subprocess  # nosec B404
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path, PureWindowsPath

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
    ".bin",
    ".bmp",
    ".class",
    ".db",
    ".dll",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".sqlite",
    ".tar",
    ".ttf",
    ".wasm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}

SCAN_SOURCE_GIT = "git-ls-files"
SCAN_SOURCE_FALLBACK = "filesystem-fallback"


@dataclass(frozen=True)
class FileContext:
    path: Path
    relative_path: str
    estimated_tokens: int
    bytes: int
    rank_score: int = 0
    selected: bool = False


@dataclass(frozen=True)
class ScanStats:
    """Metadata explaining how a repository scan selected and excluded paths."""

    source: str
    fallback_reason: str | None = None
    exclusions: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def exclusion_total(self) -> int:
        return sum(self.exclusions.values())


class ScanResult(list[FileContext]):
    """List-compatible scan result with repository and exclusion metadata."""

    def __init__(self, files: Iterable[FileContext] = (), stats: ScanStats | None = None) -> None:
        super().__init__(files)
        self.stats = stats or ScanStats(source=SCAN_SOURCE_FALLBACK)

    @property
    def files(self) -> list[FileContext]:
        return list(self)


@dataclass
class _ScanState:
    source: str
    fallback_reason: str | None = None
    exclusions: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def exclude(self, reason: str) -> None:
        self.exclusions[reason] = self.exclusions.get(reason, 0) + 1

    def freeze(self) -> ScanStats:
        return ScanStats(
            source=self.source,
            fallback_reason=self.fallback_reason,
            exclusions=dict(sorted(self.exclusions.items())),
            notes=tuple(self.notes),
        )


class _GitCommandError(RuntimeError):
    pass


def _terms(text: str) -> list[str]:
    return [term for term in re.findall(r"[a-z0-9_]+", text.lower()) if len(term) > 1]


def _is_reparse_or_symlink(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and getattr(info, "st_file_attributes", 0) & reparse_flag)


def _path_safety_reason(path: Path, root: Path, root_real: Path) -> str | None:
    """Return a safety exclusion before any file content is opened."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            if not path.resolve(strict=False).is_relative_to(root_real):
                return "outside-root"
        except OSError:
            return "unreadable"
        return "missing"
    except OSError:
        return "unreadable"

    if _is_reparse_or_symlink(info):
        try:
            target = path.resolve(strict=False)
        except OSError:
            return "symlink"
        return "symlink" if target.is_relative_to(root_real) else "outside-root"

    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return "unreadable"
    return None if resolved.is_relative_to(root_real) else "outside-root"


def _policy_reason(
    relative_path: str,
    extra_ignores: set[str],
    gitignore_patterns: list[str],
    *,
    apply_gitignore: bool,
) -> str | None:
    parts = Path(relative_path).parts
    if any(part in DEFAULT_IGNORES or part.endswith(".egg-info") for part in parts):
        return "default-ignore"
    if any(part in extra_ignores for part in parts):
        return "extra-ignore"
    if apply_gitignore and _matches_gitignore(relative_path, gitignore_patterns):
        return "gitignore"
    return None


def _is_ignored(path: Path, root: Path, ignore_names: set[str]) -> bool:
    """Compatibility helper retained for callers of the previous private API."""

    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return True
    return _policy_reason(relative, ignore_names, [], apply_gitignore=False) is not None


def _load_gitignore_patterns(root: Path) -> list[str]:
    gitignore = root / ".gitignore"
    try:
        content = gitignore.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    patterns: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        patterns.append(stripped.replace("\\", "/").lstrip("/"))
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


def _run_git(args: list[str]) -> bytes:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise _GitCommandError("git executable unavailable")
    try:
        completed = subprocess.run(  # nosec B603
            [git_executable, *args],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise _GitCommandError("git executable unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise _GitCommandError("git command timed out") from exc
    except OSError as exc:
        raise _GitCommandError("git command could not be started") from exc
    if completed.returncode != 0:
        raise _GitCommandError(f"git command failed with exit code {completed.returncode}")
    return completed.stdout


def _find_git_context(root: Path) -> tuple[Path, Path] | tuple[None, str]:
    try:
        output = _run_git(["-C", str(root), "rev-parse", "--show-toplevel"])
    except _GitCommandError as exc:
        return None, str(exc)
    raw_root = os.fsdecode(output).strip()
    if not raw_root:
        return None, "Git returned an empty repository root"
    repo_root = Path(raw_root).expanduser().absolute().resolve(strict=False)
    root_real = root.resolve(strict=False)
    try:
        scope = root_real.relative_to(repo_root)
    except ValueError:
        return None, "Git repository root does not contain the scan root"
    return repo_root, scope


def _parse_git_paths(output: bytes) -> list[str]:
    return [os.fsdecode(raw) for raw in output.split(b"\0") if raw]


def _git_paths(repo_root: Path, scope: Path, *, ignored: bool) -> list[str]:
    args = ["-C", str(repo_root), "ls-files"]
    if ignored:
        args.extend(["--others", "--ignored", "--exclude-standard"])
    else:
        args.extend(["--cached", "--others", "--exclude-standard"])
    args.extend(["--full-name", "-z"])
    if scope != Path("."):
        args.extend(["--", scope.as_posix()])
    return _parse_git_paths(_run_git(args))


def _git_candidate(repo_root: Path, root: Path, raw_path: str) -> tuple[Path | None, str | None]:
    normalized = raw_path.replace("\\", "/")
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or Path(normalized).is_absolute()
        or PureWindowsPath(normalized).is_absolute()
        or any(part == ".." for part in parts)
    ):
        return None, "outside-root"
    candidate = repo_root.joinpath(*(part for part in parts if part))
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, "outside-root"
    return candidate, None


def _read_candidate_bytes(path: Path, byte_limit: int) -> bytes:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    file_descriptor = os.open(path, flags)
    with os.fdopen(file_descriptor, "rb", closefd=True) as stream:
        return stream.read(byte_limit + 1)


def _read_rank_text(path: Path, byte_limit: int) -> str:
    try:
        info = path.lstat()
    except OSError:
        return ""
    if _is_reparse_or_symlink(info):
        return ""
    try:
        resolved = path.resolve(strict=False)
        lexical = path.absolute()
    except OSError:
        return ""
    if os.path.normcase(os.path.normpath(str(resolved))) != os.path.normcase(
        os.path.normpath(str(lexical))
    ):
        return ""
    try:
        data = _read_candidate_bytes(path, max(0, byte_limit))
    except OSError:
        return ""
    if len(data) > byte_limit:
        return ""
    try:
        return data.decode("utf-8").lower()
    except UnicodeDecodeError:
        return ""


def _looks_binary(path: Path, data: bytes) -> bool:
    if path.suffix.lower() in BINARY_SUFFIXES or b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _process_file(
    candidate: Path,
    root: Path,
    root_real: Path,
    extra_ignores: set[str],
    gitignore_patterns: list[str],
    byte_limit: int,
    state: _ScanState,
    files: list[FileContext],
    *,
    apply_gitignore: bool,
) -> None:
    safety_reason = _path_safety_reason(candidate, root, root_real)
    if safety_reason:
        state.exclude(safety_reason)
        return
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError:
        state.exclude("outside-root")
        return
    policy_reason = _policy_reason(
        relative,
        extra_ignores,
        gitignore_patterns,
        apply_gitignore=apply_gitignore,
    )
    if policy_reason:
        state.exclude(policy_reason)
        return

    try:
        info = candidate.lstat()
    except FileNotFoundError:
        state.exclude("missing")
        return
    except OSError:
        state.exclude("unreadable")
        return
    if _is_reparse_or_symlink(info):
        state.exclude("symlink")
        return
    if not stat.S_ISREG(info.st_mode):
        state.exclude("not-file")
        return
    if candidate.suffix.lower() in BINARY_SUFFIXES:
        state.exclude("binary")
        return
    if info.st_size > byte_limit:
        state.exclude("too-large")
        return
    try:
        data = _read_candidate_bytes(candidate, byte_limit)
    except OSError:
        safety_reason = _path_safety_reason(candidate, root, root_real)
        state.exclude(safety_reason or "unreadable")
        return
    if len(data) > byte_limit:
        state.exclude("too-large")
        return
    if _looks_binary(candidate, data):
        state.exclude("binary")
        return
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        state.exclude("binary")
        return
    files.append(
        FileContext(
            path=candidate,
            relative_path=relative,
            estimated_tokens=_estimate_tokens(text),
            bytes=len(data),
        )
    )


def _iter_fallback_files(
    root: Path,
    root_real: Path,
    extra_ignores: set[str],
    gitignore_patterns: list[str],
    state: _ScanState,
) -> Iterable[Path]:
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in sorted(directory_names, key=lambda value: (value.casefold(), value)):
            candidate = current_path / name
            safety_reason = _path_safety_reason(candidate, root, root_real)
            if safety_reason:
                state.exclude(safety_reason)
                continue
            relative = candidate.relative_to(root).as_posix()
            policy_reason = _policy_reason(
                relative,
                extra_ignores,
                gitignore_patterns,
                apply_gitignore=True,
            )
            if policy_reason:
                state.exclude(policy_reason)
                continue
            kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(file_names, key=lambda value: (value.casefold(), value)):
            candidate = current_path / name
            yield candidate


def _process_git_ignored_entry(
    candidate: Path,
    root: Path,
    root_real: Path,
    extra_ignores: set[str],
    gitignore_patterns: list[str],
    state: _ScanState,
) -> None:
    safety_reason = _path_safety_reason(candidate, root, root_real)
    if safety_reason:
        state.exclude(safety_reason)
        return
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError:
        state.exclude("outside-root")
        return
    policy_reason = _policy_reason(
        relative,
        extra_ignores,
        gitignore_patterns,
        apply_gitignore=True,
    )
    state.exclude(policy_reason or "gitignore")


def scan_repository(
    path: str | Path,
    extra_ignores: list[str] | None = None,
    max_file_bytes: int = 1_000_000,
) -> ScanResult:
    """Scan text files without traversing links or leaving the requested root.

    Git worktrees use ``git ls-files --cached --others --exclude-standard`` so
    Git's complete ignore behavior decides the candidate set. A non-Git path,
    unavailable Git executable, or failed Git command uses a conservative
    filesystem fallback and the root ``.gitignore`` matcher.
    """

    root = Path(path).expanduser().absolute()
    extra_ignores_set = set(extra_ignores or [])
    byte_limit = max(0, max_file_bytes)
    state = _ScanState(source=SCAN_SOURCE_FALLBACK)
    files: list[FileContext] = []

    try:
        root_info = root.lstat()
    except FileNotFoundError:
        state.fallback_reason = "scan root does not exist"
        state.exclude("missing")
        return ScanResult(files, state.freeze())
    except OSError:
        state.fallback_reason = "scan root is not readable"
        state.exclude("unreadable")
        return ScanResult(files, state.freeze())
    if _is_reparse_or_symlink(root_info):
        state.fallback_reason = "scan root is a symlink or reparse point"
        state.exclude("symlink")
        return ScanResult(files, state.freeze())
    if not stat.S_ISDIR(root_info.st_mode):
        state.fallback_reason = "scan root is not a directory"
        state.exclude("not-directory")
        return ScanResult(files, state.freeze())

    root_real = root.resolve(strict=False)
    git_context = _find_git_context(root)
    if git_context[0] is not None:
        repo_root, scope = git_context
        try:
            git_paths = _git_paths(repo_root, scope, ignored=False)
        except _GitCommandError as exc:
            state.fallback_reason = str(exc)
        else:
            state.source = SCAN_SOURCE_GIT
            try:
                ignored_paths = _git_paths(repo_root, scope, ignored=True)
            except _GitCommandError as exc:
                state.notes.append(f"Git ignored-entry counts unavailable: {exc}")
                ignored_paths = []
            gitignore_patterns: list[str] = []
            for raw_path in sorted(set(ignored_paths), key=lambda value: (value.casefold(), value)):
                candidate, rejection = _git_candidate(repo_root, root, raw_path)
                if rejection:
                    state.exclude(rejection)
                    continue
                if candidate is None:
                    state.exclude("outside-root")
                    continue
                _process_git_ignored_entry(
                    candidate,
                    root,
                    root_real,
                    extra_ignores_set,
                    gitignore_patterns,
                    state,
                )
            for raw_path in sorted(set(git_paths), key=lambda value: (value.casefold(), value)):
                candidate, rejection = _git_candidate(repo_root, root, raw_path)
                if rejection:
                    state.exclude(rejection)
                    continue
                if candidate is None:
                    state.exclude("outside-root")
                    continue
                _process_file(
                    candidate,
                    root,
                    root_real,
                    extra_ignores_set,
                    gitignore_patterns,
                    byte_limit,
                    state,
                    files,
                    apply_gitignore=False,
                )
            return ScanResult(files, state.freeze())
    else:
        state.fallback_reason = git_context[1]

    gitignore_patterns = _load_gitignore_patterns(root)
    for candidate in _iter_fallback_files(
        root,
        root_real,
        extra_ignores_set,
        gitignore_patterns,
        state,
    ):
        _process_file(
            candidate,
            root,
            root_real,
            extra_ignores_set,
            gitignore_patterns,
            byte_limit,
            state,
            files,
            apply_gitignore=True,
        )
    return ScanResult(files, state.freeze())


def rank_files(files: list[FileContext], task_query: str) -> list[FileContext]:
    query_terms = _terms(task_query)
    ranked: list[FileContext] = []
    for file_context in files:
        path_text = file_context.relative_path.lower()
        name_text = Path(file_context.relative_path).name.lower()
        content = _read_rank_text(file_context.path, file_context.bytes)
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


def _select_files(
    ranked_files: list[FileContext], token_budget: int
) -> tuple[list[FileContext], list[FileContext], int]:
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
    return selected, skipped, used


def _report_stats(scan_stats: ScanStats | None) -> ScanStats:
    return scan_stats or ScanStats(source="provided-file-list")


def _file_json(item: FileContext) -> dict[str, object]:
    return {
        "path": item.relative_path,
        "bytes": item.bytes,
        "estimated_tokens": item.estimated_tokens,
        "rank_score": item.rank_score,
        "selected": item.selected,
    }


def _build_json_report(
    root: str | Path,
    ranked_files: list[FileContext],
    task_query: str,
    token_budget: int,
    include_ignore_recommendations: bool,
    scan_stats: ScanStats | None,
) -> str:
    stats = _report_stats(scan_stats)
    selected, skipped, used = _select_files(ranked_files, token_budget)
    payload: dict[str, object] = {
        "repository": str(Path(root).resolve()),
        "task_query": task_query,
        "token_budget": max(0, token_budget),
        "estimated_tokens_selected": used,
        "files_scanned": len(ranked_files),
        "selected_files": [_file_json(item) for item in selected],
        "highest_ranked_skipped_files": [_file_json(item) for item in skipped[:10]],
        "scan": {
            "source": stats.source,
            "fallback_reason": stats.fallback_reason,
            "notes": list(stats.notes),
        },
        "exclusions": {
            "total": stats.exclusion_total,
            "by_reason": stats.exclusions,
        },
    }
    if include_ignore_recommendations:
        payload["ignore_recommendations"] = _recommend_ignores(Path(root).resolve())
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build_report(
    root: str | Path,
    ranked_files: list[FileContext],
    task_query: str,
    token_budget: int,
    include_ignore_recommendations: bool = False,
    scan_stats: ScanStats | None = None,
    output_format: str = "text",
) -> str:
    if output_format == "json":
        return _build_json_report(
            root,
            ranked_files,
            task_query,
            token_budget,
            include_ignore_recommendations,
            scan_stats,
        )
    if output_format != "text":
        raise ValueError(f"Unsupported report format: {output_format}")

    stats = _report_stats(scan_stats)
    selected, skipped, used = _select_files(ranked_files, token_budget)
    budget = max(0, token_budget)
    lines = [
        "# Context Budget Report",
        "",
        f"- Repository: `{Path(root).resolve()}`",
        f"- Task query: `{task_query}`",
        f"- Token budget: {budget}",
        f"- Estimated tokens selected: {used}",
        f"- Files scanned: {len(ranked_files)}",
        f"- Scan source: `{stats.source}`",
    ]
    if stats.fallback_reason:
        lines.append(f"- Fallback reason: {stats.fallback_reason}")
    lines.extend(["", "## Exclusions", "", f"- Total excluded: {stats.exclusion_total}", "- Counts by reason:"])
    if stats.exclusions:
        lines.extend(f"  - `{reason}`: {count}" for reason, count in stats.exclusions.items())
    else:
        lines.append("  - None.")
    if stats.notes:
        lines.extend(["", "- Notes:"])
        lines.extend(f"  - {note}" for note in stats.notes)
    lines.extend(["", "## Selected Files", ""])
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
    parser.add_argument("--output", help="Write the report to this path in the selected format.")
    parser.add_argument("--ignore", action="append", default=[], help="Additional directory or file name to ignore.")
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=1_000_000,
        help="Skip individual text files larger than this many bytes.",
    )
    parser.add_argument("--recommend-ignore", action="store_true", help="Include ignore recommendations.")
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
        help="Report format (default: text).",
    )
    parser.add_argument(
        "--json",
        dest="output_format",
        action="store_const",
        const="json",
        help="Alias for --format json.",
    )
    args = parser.parse_args(argv)

    root = Path(args.path).expanduser().absolute()
    scan = scan_repository(root, args.ignore, args.max_file_bytes)
    ranked = rank_files(scan, args.query)
    report = build_report(
        root,
        ranked,
        args.query,
        args.budget,
        args.recommend_ignore,
        scan.stats,
        args.output_format,
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        print(f"Wrote context budget report to {output}")
    else:
        print(report, end="")
    return 0
