# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- OpenCode, Gemini CLI, Droid, Copilot CLI, Kimi Code and Qwen Code sessions,
  indexed, searched and resumed next to Claude Code and Codex.
- `Ctrl+X` spells the skip-permissions flag of each new agent: `--yolo` for
  Gemini, Kimi and Qwen, `--allow-all-tools` for Copilot, `--auto high` for
  Droid.
- Codex sessions under `~/.codex/archived_sessions`.
- Gemini CLI and Kimi Code sessions stored under a hash of their directory
  recover it from the tools' registries and from every other indexed session,
  with a second index pass picking up directories learned on the first.

### Changed

- The Herdr snapshot is read again right before a resume, so a session that
  went live while the picker was open is focused instead of started twice.
- Live agents are keyed by the agent name Herdr reports for the session, not
  for the pane.
- The theme is read from `HERDR_CONFIG_PATH` when Herdr sets it.
- Tool calls that carry a `content` argument index the call, not the file
  body, for every agent.

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
