# context-budgeter

`context-budgeter` scans a repository, ignores common generated directories, estimates tokens cheaply, ranks files for a task query, and writes a Markdown or JSON context budget report.

The token estimate is intentionally cheap and deterministic: roughly one token per four characters.

## Unreleased Highlights

- Repository scans now skip individual text files above a configurable byte limit.
- `--max-file-bytes` keeps logs, generated reports, and vendored text dumps out of context packs.
- Valid Git worktrees use `git ls-files --cached --others --exclude-standard`, so Git's ignore rules decide which untracked files are candidates.
- Symlinks and Windows reparse points are never scanned; resolved paths outside the requested root are rejected.
- Binary files are skipped deterministically by known suffix, NUL bytes, or invalid UTF-8.
- Text and JSON reports include exclusion totals and counts by reason.

## Usage

```bash
python -m context_budgeter /path/to/repo --query "fix oauth login" --budget 8000
python -m context_budgeter /path/to/repo --query "fix oauth login" --output context-report.md --recommend-ignore
python -m context_budgeter /path/to/repo --query "auth bug" --max-file-bytes 200000
python -m context_budgeter /path/to/repo --query "auth bug" --format json --output context-report.json
```

Installed script:

```bash
context-budgeter /path/to/repo --query "billing retry bug" --budget 4000
```

## Selection and Fallbacks

When the path is inside a valid Git worktree, the candidate list comes from `git ls-files --cached --others --exclude-standard`. This includes tracked files even when a `.gitignore` rule also matches them, which is Git's normal behavior, and excludes ignored untracked files. The report records the source as `git-ls-files`.

If Git is unavailable, the path is not in a worktree, or `git ls-files` fails, the scanner uses a deterministic filesystem walk with `followlinks=False`. The fallback applies built-in ignores and a conservative matcher for the root `.gitignore`; it intentionally does not interpret negated rules or nested `.gitignore` files. The report records `filesystem-fallback` and the reason for using it.

The JSON report exposes this metadata under `scan` and `exclusions`, for example:

```json
{
  "scan": {"source": "git-ls-files", "fallback_reason": null},
  "exclusions": {
    "total": 3,
    "by_reason": {"binary": 1, "gitignore": 2}
  }
}
```

Text reports contain the same information in the `Exclusions` section. Exclusion counts describe filesystem entries or Git-reported ignored entries; an ignored directory may therefore count as one entry rather than once per descendant.

Reports can contain source code and secrets. Review output paths and report files before sharing them. See [SECURITY.md](SECURITY.md) and [docs/SECURITY_REVIEW_2026-07.md](docs/SECURITY_REVIEW_2026-07.md).

## Development

```bash
python -m unittest discover -s tests
```

No network calls are required by the test suite.
