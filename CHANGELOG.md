# Changelog

All notable changes to `context-budgeter` will be documented in this file.

The format is based on Keep a Changelog, and this project uses semantic versioning.

## [Unreleased]

- No unreleased changes yet.

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
