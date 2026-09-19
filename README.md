# herdr-transcripts

Search the transcripts of your coding agent sessions, in a
[Herdr](https://herdr.dev) popup. Search by title, prompt, reply or tool call.

[![Find a session by a word you remember, reorder, switch to dates, narrow with a second word, and resume it in a new Herdr tab](assets/overview.gif)](assets/overview.mp4)

## Features

- Search words across titles, prompts, replies and tool calls
- Highlighted matches in previews
- Mouse-first like Herdr, with a key for everything
- Filters by agent and cwd
- Incremental index, pre-warmed, parallel parsing (~110 ms on 3 GiB)
- Python standard library and `fzf`

## Supported agents

| Agent                                                         | Transcripts          | Resume            | `Ctrl+X`                         | `Ctrl+B`   |
| ------------------------------------------------------------- | -------------------- | ----------------- | -------------------------------- | ---------- |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `~/.claude/projects` | `claude --resume` | `--dangerously-skip-permissions` | `--chrome` |
| [Codex](https://github.com/openai/codex)                      | `~/.codex/sessions`  | `codex resume`    | `--yolo`                         |            |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli)     | `~/.gemini/tmp`      | `gemini --resume` | `--yolo`                         |            |
| [Qwen Code](https://github.com/QwenLM/qwen-code)              | `~/.qwen/projects`   | `qwen --resume`   | `--yolo`                         |            |

## Screenshots

**Searched by word across titles, prompts and replies**

![A one-word query, the sessions that mention it, and the preview with the matching message highlighted](assets/shot-search.png)

**Scoped to replies, matching what the agent said**

![The replies scope: sessions where the agent said the word, with the reply highlighted in the preview](assets/shot-replies.png)

**Scoped by agent with absolute dates**

![The list limited to codex sessions with absolute dates](assets/shot-filters.png)

**Diagnostics with retention, index state and flags**

![The diagnostics overlay: retention, transcript counts, index state, resume flags](assets/shot-diagnostics.png)

## Requirements

- Linux or macOS
- Herdr ≥ 0.7
- `fzf` ≥ 0.66
- Python ≥ 3.8
- At least one supported agent, to resume sessions

> [!NOTE]
> Debian and Ubuntu package older `fzf` versions. Install a current
> [fzf release](https://github.com/junegunn/fzf/releases) if the picker reports
> an unsupported version.

## Install

```sh
herdr plugin install hxreborn/herdr-transcripts
```

Bind a key in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "ctrl+alt+f"
type = "shell"
command = "\"$HERDR_BIN_PATH\" plugin pane open --plugin transcripts --entrypoint picker"
description = "Search session transcripts"
```

Reload Herdr:

```sh
herdr server reload-config
```

## Usage

Type a word. Matches are newest first. `Enter` resumes the session in its own
directory, or focuses its pane if it is already running.

`Tab` cycles the search scope:

| Scope          | Searches                                    |
| -------------- | ------------------------------------------- |
| `conversation` | titles, prompts, and replies                |
| `titles`       | session title, directory, and branch        |
| `prompts`      | your messages                               |
| `replies`      | the assistant's messages                    |
| `tools`        | commands, paths, and patterns in tool calls |
| `everything`   | every indexed field                         |

Words match exactly, in any order. Prefix a word with `'` for fuzzy matching
or `!` to exclude it. `^` and `$` anchor a word.

`Ctrl+A` narrows to one agent, `Ctrl+P` to the current directory, and `Ctrl+O`
changes the sort order.

`./transcripts` outside Herdr runs the picker in the current terminal.

## Keys

| Key                | Action                                   |
| ------------------ | ---------------------------------------- |
| `Enter`            | resume                                   |
| `Esc`              | close                                    |
| `Tab`              | next scope                               |
| `Ctrl+O`           | next sort order                          |
| `Ctrl+P`           | only this directory                      |
| `Ctrl+A`           | one agent, or all of them                |
| `Ctrl+T`           | relative or absolute time                |
| `Ctrl+V`, `Ctrl+/` | toggle the preview                       |
| `Ctrl+X`           | toggle the agent's skip-permissions flag |
| `Ctrl+B`           | toggle `--chrome` (Claude Code only)     |
| `Ctrl+G`           | keys overlay                             |
| `Ctrl+D`           | diagnostics                              |

## Configuration

The picker saves scope, sort, time format, agent filter, and resume flags in
`~/.config/herdr/plugins/config/transcripts/config.toml`.

Colours come from `[theme.custom]` in `~/.config/herdr/config.toml`.

## Diagnostics

`Ctrl+D` reports dependency versions, index state, session counts, active
flags, and Claude Code transcript retention. If `cleanupPeriodDays` is 30 or
lower, diagnostics offers to set it to 3,650 in `~/.claude/settings.json`.

The index lives in `~/.cache/herdr-transcripts`. Delete that directory, or run
`./transcripts warm`, to rebuild it.

## Development

```sh
tests/tui.py
herdr plugin link .
```

## Licence

[GPL-3.0-or-later](LICENSE)
