# herdr-transcripts

Find a past Claude Code, Codex, or other coding-agent session from a phrase
you remember. Read the matching message, then press Enter to resume in the
project directory or focus the session if it is already running in Herdr.

Install and open the picker from a Herdr terminal with Python 3 and fzf 0.66 or newer:

```sh
herdr plugin install hxreborn/herdr-transcripts
herdr plugin pane open --plugin transcripts --entrypoint picker
```

From a checkout, run `./transcripts` to use the same picker in your terminal.

[![Find a session by a word you remember, reorder, switch to dates, narrow with a second word, and resume it in a new Herdr tab](assets/overview.gif)](assets/overview.mp4)

## Features

- Search words across titles, prompts, replies and tool calls
- Highlighted matches in previews
- Scores a session against your tree: what it touched, what survived
- Mouse-first like Herdr, with a key for everything
- Filters by agent and cwd
- The install and startup hooks warm the local index. Later runs parse changed sessions.
- Python standard library and `fzf`

## Supported agents

[Claude Code](https://docs.anthropic.com/en/docs/claude-code),
[Codex](https://github.com/openai/codex),
[OpenCode](https://opencode.ai),
[Gemini CLI](https://github.com/google-gemini/gemini-cli),
[Droid](https://factory.ai),
[Copilot CLI](https://github.com/github/copilot-cli),
[Kimi Code](https://github.com/MoonshotAI/kimi-cli),
[Qwen Code](https://github.com/QwenLM/qwen-code),
[Devin CLI](https://devin.ai/cli) and
[Antigravity CLI](https://antigravity.google/docs/cli).

`Ctrl+X` toggles each agent's own skip-permissions flag, `--yolo`,
`--allow-all-tools` or `--auto high` as the agent spells it. `Ctrl+D` shows
where each agent's transcripts are read from and the exact resume command.

A session already running in Herdr shows a status chip, and `Enter` focuses it
instead of starting it again.

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
- Herdr ≥ 0.7 for the popup
- `fzf` ≥ 0.66
- Python ≥ 3.8
- At least one supported agent, to resume sessions

> [!NOTE]
> Debian and Ubuntu package older `fzf` versions. Install a current
> [fzf release](https://github.com/junegunn/fzf/releases) if the picker reports
> an unsupported version.

## Bind a shortcut

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

Type a project and a phrase, such as `checkout retry`, or paste a session ID.
Results start with the most recent session. Press `Enter` to open one.
If a saved filter hides the session you want, press `Ctrl+R` to search
conversations across all agents and directories with the same query.

`Tab` cycles the search scope:

| Scope          | Searches                                    |
| -------------- | ------------------------------------------- |
| `conversation` | titles, paths, IDs, prompts, and replies    |
| `titles`       | title, full directory path, branch, and ID  |
| `prompts`      | your messages                               |
| `replies`      | the assistant's messages                    |
| `tools`        | commands, paths, and patterns in tool calls |
| `everything`   | every indexed field                         |

Search terms match substrings, in any order. Prefix a word with `'` for fuzzy matching
or `!` to exclude it. `^` and `$` anchor a word.

`Ctrl+A` narrows to one agent, `Ctrl+P` to the current directory, and `Ctrl+O`
changes the sort order.

For Claude and Codex the index keeps up to 4,096 normalized characters per
message, with separate one-million-character limits for prompts and replies per
session. The preview normally shows recent turns. A search also retrieves
matching text from earlier indexed turns.

## What a session left behind

The preview checks the files a session touched against your tree:

```
9 files touched  ·  7 still there  ·  1 superseded  ·  1 unexplained
```

*Superseded* means a commit recorded the deletion. *Unexplained* means it is gone
and git never recorded it going. One batched `git log` per preview, never per row,
and it degrades to there-and-gone outside a repository. Codex is read from the
paths its own patches report, so its line misses anything written by a plain
shell command.

## Keys

| Key                | Action                                   |
| ------------------ | ---------------------------------------- |
| `Enter`            | resume                                   |
| `Esc`              | close                                    |
| `Tab`              | next scope                               |
| `Ctrl+R`           | reset scope, agent, and directory filters |
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

Set `CLAUDE_CONFIG_DIR` or `CODEX_HOME` in the environment that launches the
picker to search a custom agent profile. Each combination of profile paths
has its own index.

Colours come from `[theme.custom]` in the Herdr config, at `HERDR_CONFIG_PATH`
when Herdr sets it and `~/.config/herdr/config.toml` otherwise.

## Diagnostics

`Ctrl+D` reports dependency versions, index state, session counts, active
flags, and Claude Code transcript retention. If `cleanupPeriodDays` is 30 or
lower, diagnostics offers to set it to 3,650 in `~/.claude/settings.json`.

The index lives in `~/.cache/herdr-transcripts`. Run `./transcripts warm` for
an incremental refresh. The first run after upgrading rebuilds the search data.

## Development

```sh
tests/tui.py
herdr plugin link .
```

## Licence

[GPL-3.0-or-later](LICENSE)
