# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-09-21

### Added

- Codex sessions get the touched-files line too. Codex records each applied
  patch in an `event_msg` record whose payload type is `patch_apply_end`,
  carrying the paths it changed, and the parser now reads those instead of
  giving up on `apply_patch` shell text. A rename keeps both its source and
  its destination, so the source scores as gone and the destination as alive.
  Nothing written by a plain shell command is visible this way, so the line
  is a floor rather than an inventory.

### Fixed

- The diagnostics overlay no longer crashes when `cleanupPeriodDays` in
  `settings.json` holds something other than a number. A quoted `"30"` raised
  a `TypeError` and took `Ctrl+D` down with it; a non-number now reads as
  unset, which is also what the retention fix writes over.

### Changed

- `INDEX_VERSION` moved to v9, so the first run after upgrading reparses every
  session once. That costs a couple of seconds for a few GiB of transcripts.

## [1.2.0] - 2026-09-19

### Added

- The preview scores the files a session touched against the working tree:
  files that are still there, files whose deletion a commit recorded, and
  files that are gone with nothing in git explaining them. One batched
  `git log` per preview, never per row, and the line degrades to a plain
  alive-and-gone split when the directory is not a repository or git cannot
  answer within two seconds.

### Changed

- Preview turns keep 1000 characters instead of 400, matching what the search
  index holds, so a session can no longer match on a word the preview refuses
  to show.

## [1.1.0] - 2026-09-19

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

- Resumes start the agent through `herdr agent start`, so Herdr tracks the
  pane as that agent from the first prompt. The picker doesn't wait for it.
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

[1.2.0]: https://github.com/hxreborn/herdr-transcripts/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/hxreborn/herdr-transcripts/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/hxreborn/herdr-transcripts/releases/tag/v1.0.0
