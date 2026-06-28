# Changelog

## 2026.2.11 (2026-06-28)

### Changed

- Released the post-2026.2.10 GitLab adapter changes for the shared publish
  job runtime flow in `core-allure-history`.
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
  [`gitlab-allure-history` changelog][gitlab-allure-history-changelog].

## 2026.2.10 (2026-06-14)

### Changed

- Prepared the extracted core runtime line for use by provider-specific
  adapters.

## 2026.2.9 (2026-06-14)

### Changed

- The provider-neutral runtime that previously lived inside
  `gitlab-allure-history` was extracted into `core-allure-history`.
- Report generation, history restoration, retention, index generation, and
  Pages smoke checks were separated into the shared core runtime so provider
  adapters can reuse the same behavior.

[gitlab-allure-history-changelog]: https://github.com/aleksandr-kotlyar/gitlab-allure-history/blob/master/CHANGELOG.md
