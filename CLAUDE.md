# Launchpad Claude Guidance

## Prose

- Never use Unicode em dashes (U+2014) or en dashes (U+2013) in code, comments, UI copy, docs, commits, PR text, or replies.
- Prefer commas, colons, parentheses, or an ASCII hyphen with spaces (` - `).
- For empty/placeholder display values, use ASCII `-` or `N/A`.

## Best practices and reusable code

- Prefer production-grade, typed, complete implementations. No stubs, placeholders, or copy-paste forks of existing UI/logic.
- Before adding a feature, search the repo for an existing component, composable, util, or service and reuse or extend it.
- Frontend GitHub flows must use:
  - `GithubInstallationPicker` for personal vs organization App installs
  - `GithubRepoPicker` for searchable repository selection
  - `GitlabRepoPicker` for searchable GitLab project selection
  - `GithubConnectCard` for connection/status surfaces
  - `~/utils/githubAccount` for account-type labels and clone URLs
- Backend: reuse `app/services/` and `pkg/` helpers (tokens, installs, clone, detect). Do not reimplement the same path in a second router or worker.
- If two call sites need the same UI or logic, extract a shared module before shipping a third copy.
- Follow `.cursor/rules/` (especially `idp-core`, `reusable-code`, `nuxt-frontend`, `fastapi-backend`, `research`).
