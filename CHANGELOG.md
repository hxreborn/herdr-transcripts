# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-09-18

### Added

- `fzf` picker over Claude Code and Codex transcripts, in one list, with the
  agent, directory, branch, age, size and live Herdr status on every row.
- Search scopes over titles, prompts, replies and tool calls, cycled with `Tab`.
- Preview of the conversation that shows only the messages a query matched.
- Resume into the session's own directory in a new Herdr tab, or focus the pane
  when the session is already running.
- Filters for one agent (`Ctrl+A`) and the current directory (`Ctrl+P`), sort
  orders, absolute or relative times, and resume flags, all persisted.
- Incremental index with parallel parsing, built at install and refreshed on
  every Herdr start.
- Diagnostics overlay (`Ctrl+D`) covering dependency versions, index state,
  session counts and Claude Code's `cleanupPeriodDays`, with a one-key fix.

[1.0.0]: https://github.com/hxreborn/herdr-transcripts/releases/tag/v1.0.0
