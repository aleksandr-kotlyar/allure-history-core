# Changelog

## Unreleased

### Added

- Read-only navigation previews for existing report trees, timestamp snapshots,
  explicit navigation roots, and an optional project intro.
- S3 metadata-only indexing, conditional publication plans, backups, and rollback.
- GitHub adapter and CLI for persistent report history, pull request comments,
  fork-aware Pages URLs, and GitHub Action outputs.
- GitHub runtime build, real Allure report smoke check, and GHCR publishing.

### Changed

- GitLab release pipelines mirror the tested GHCR image into GitLab Container
  Registry instead of building a second runtime.

## 2026.4.1 (2026-10-26)

### Changed

- Renamed the project to `allure-history-core` and aligned adapter names,
  generated report metadata, and the runtime installation path.

### Added

- The runtime image now includes checksum-verified `s5cmd` 2.3.0 binaries for
  AMD64 and ARM64 S3-compatible storage workflows.

## 2026.2.11 (2026-06-28)

### Changed

- Released the post-2026.2.10 GitLab adapter changes for the shared publish
  job runtime flow in `allure-history-core`.
- Updated generated indexes with branch-level latest-report navigation,
  dedicated history links, generator version links, and related layout fixes.
- Improved merge request comments so existing notes are updated when possible
  and GitLab API auth, permission, and network failures are easier to diagnose.
- Hardened the runtime helper with required command checks, image tag
  validation, configurable paths, and fallback collection of `allure-results`
  from dependency artifacts.

### Notes

- This release contains the GitLab, index, MR-comment, and runtime-helper
  changes that landed after `2026.2.10`.
- Earlier GitLab component history remains in the
  [`allure-history-gitlab` changelog][allure-history-gitlab-changelog].

## 2026.2.10 (2026-06-14)

### Changed

- Prepared the extracted core runtime line for use by provider-specific
  adapters.

## 2026.2.9 (2026-06-14)

### Changed

- The provider-neutral runtime that previously lived inside
  `allure-history-gitlab` was extracted into `allure-history-core`.
- Report generation, history restoration, retention, index generation, and
  Pages smoke checks were separated into the shared core runtime so provider
  adapters can reuse the same behavior.

[allure-history-gitlab-changelog]: https://gitlab.com/aleksandr-kotlyar/allure-history-gitlab/-/blob/master/CHANGELOG.md
