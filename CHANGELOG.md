# Changelog

All notable changes to `context-budgeter` will be documented in this file.

The format is based on Keep a Changelog, and this project uses semantic versioning.

## [Unreleased]

- Made selection repository-aware with `git ls-files --cached --others --exclude-standard` inside Git worktrees.
- Added a documented filesystem fallback when Git is unavailable, the path is not a worktree, or Git enumeration fails.
- Rejected symlinks, Windows reparse points, and paths resolving outside the requested root.
- Added deterministic binary-file detection and exclusion counts with reasons.
- Added JSON reports and exposed scan metadata in text reports.
- Added security-focused tests and `docs/SECURITY_REVIEW_2026-07.md`.

## [0.1.2] - 2026-07-06

- Updated GitHub Actions workflow dependencies to current major versions.
- Modernized package license metadata to avoid current Setuptools deprecation warnings.
- Added a configurable per-file byte limit to skip oversized text files before token estimation.
- Added CLI support for `--max-file-bytes` to keep generated logs and reports out of context packs.

## [0.1.1] - 2026-06-17

- Added root `.gitignore` pattern support to repository scans.
- Skipped ignored file, directory, and glob patterns before token estimation.
- Fixed GitHub Actions workflow pins to supported action versions.

## [0.1.0] - 2026-06-03

- Initial open-source release with CLI, examples, tests, GitHub workflows, security policy, and contributor docs.
