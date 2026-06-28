# Changelog

## Unreleased

### Changed

- The GitLab publish helper flow now lives in `core-allure-history`, so the
  GitLab component can delegate publishing to `allure-history-gitlab
  publish-job`.

### Notes

- Core release history starts from the behavior extracted out of
  `gitlab-allure-history`. Earlier GitLab component history remains in the
  [`gitlab-allure-history` changelog][gitlab-allure-history-changelog] and is
  not duplicated here.

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
