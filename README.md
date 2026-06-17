# context-budgeter

`context-budgeter` scans a repository, ignores common generated directories, estimates tokens cheaply, ranks files for a task query, and writes a Markdown context budget report.

The token estimate is intentionally cheap and deterministic: roughly one token per four characters.

## 0.1.1 Highlights

- Repository scans now respect root `.gitignore` patterns before estimating token budgets.
- Ignored local files, generated reports, and scratch globs stay out of context reports by default.

## Usage

```bash
python -m context_budgeter /path/to/repo --query "fix oauth login" --budget 8000
python -m context_budgeter /path/to/repo --query "fix oauth login" --output context-report.md --recommend-ignore
```

Installed script:

```bash
context-budgeter /path/to/repo --query "billing retry bug" --budget 4000
```

## Development

```bash
python -m unittest discover -s tests
```

No network calls are required by the test suite.
