# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `demo/` — a throwaway OJS 3.5 stack (Docker: OJS 3.5.0-5 + MariaDB)
  with fictional seed content, plus `demo/check_api.py`, an MCP client
  that launches the server over stdio and calls every read tool. Used to
  exercise the server against a live instance for the first time.
- A regression test for `python -m ojs_mcp.server`.

### Fixed

- `python -m ojs_mcp.server` silently did nothing: the module had no
  `__main__` block, so it defined `main()` and exited. An MCP client
  launched that way saw only the stdio pipe close, and reported a bare
  connection error.

### Documentation

- Authentication: a web server that does not forward the `Authorization`
  header to PHP (Apache's default, including in the official
  `pkpofficial/ojs` Docker images) makes OJS refuse every token with a
  401 that is byte-for-byte identical to an anonymous refusal. Documented
  the symptom, how to tell it apart from a missing journal role, and the
  one-line fix.
- Status: the read tools are no longer unverified against live OJS; the
  write tools and login/password authentication still are.

## [0.1.0] — 2026-09-09

First release.

### Added

- An MCP server for the authenticated Open Journal Systems REST API
  (OJS 3.5/3.6) — sixteen read tools (submissions, publications, issues,
  sections, users, reviewers, statistics, DOI, identity, and journal
  catalog) plus the `ojs_request` escape hatch below (seventeen always
  registered in total), and five write tools (editorial decisions,
  publication metadata editing, publish/unpublish, announcements)
  registered only when `OJS_ALLOW_WRITES=1` is explicitly set.
- The `ojs_request` escape hatch for calling any OJS REST API endpoint
  outside the curated tool list, with path validation and (without
  writes enabled) restriction to read requests.
- Two authentication strategies: an API token (`OJS_API_TOKEN`) and
  login/password form authentication (`OJS_USERNAME`/`OJS_PASSWORD`)
  with CAPTCHA/ALTCHA and forced-password-change detection, session CSRF
  token management, and automatic retry after session expiry.
- Multi-instance support: a single binary serves any OJS deployment via
  `OJS_BASE_URL`, with support for multiple journals on one instance
  (the `journal` parameter, the `list_journals` tool).
- Network mode `OJS_MCP_TRANSPORT=http` (streamable HTTP) to host a
  single process for many users at once, each with their own OJS token
  passed in the request header — the server stores no client
  credentials at all; with `Origin` validation
  (`OJS_MCP_ALLOWED_ORIGINS`) protecting against DNS rebinding.
- An MCPB bundle (`.mcpb`) for one-click installation in desktop MCP
  clients, and a PyPI release via OIDC.
- Full documentation (installation, configuration, authentication, tool
  list, multi-tenant hosting) published with MkDocs Material.

[0.1.0]: https://github.com/mpasternak/ojs-mcp/releases/tag/v0.1.0
