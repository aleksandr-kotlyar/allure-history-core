# Changelog

## 2026.2.10 (2026-06-14)

### Changed

- The remaining shared GitLab publish helper scripts were moved into
  `core-allure-history`, so provider wrappers can call the shared
  implementation directly.
- `gitlab-allure-history` now acts more clearly as a thinner wrapper and
  example of provider-specific usage around the shared core runtime.

## 2026.2.9 (2026-06-14)

### Changed

- The provider-neutral runtime that previously lived inside
  `gitlab-allure-history` was extracted into `core-allure-history`.
- Report generation, history restoration, retention, index generation, and
  Pages smoke checks were separated into the shared core runtime so provider
  adapters can reuse the same behavior.
