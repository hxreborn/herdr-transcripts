#!/usr/bin/env python3

import fcntl
import glob
import json
import marshal
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import termios
import time
import tty
import unicodedata
from collections import deque

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
SETTINGS = os.path.join(HOME, ".claude", "settings.json")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
CODEX_SESSIONS = os.path.join(CODEX_HOME, "sessions")
CODEX_NAMES = os.path.join(CODEX_HOME, "session_index.jsonl")
OPENCODE_HOME = os.path.join(HOME, ".local", "share", "opencode")
DROID_SESSIONS = os.path.join(HOME, ".factory", "sessions")
COPILOT_STATE = os.path.join(HOME, ".copilot", "session-state")
CACHE_DIR = os.path.join(HOME, ".cache", "herdr-transcripts")
INDEX = os.path.join(CACHE_DIR, "index.bin")
SNAPSHOT_FILE = os.path.join(CACHE_DIR, "snapshot.json")
LOCK = os.path.join(CACHE_DIR, "lock")
TURNS_DIR = os.path.join(CACHE_DIR, "turns")
CONFIG_DIR = os.environ.get("HERDR_PLUGIN_CONFIG_DIR",
                            os.path.join(HOME, ".config", "herdr", "plugins", "config", "transcripts"))
CONFIG = os.path.join(CONFIG_DIR, "config.toml")
HERDR_CONFIG = os.environ.get("HERDR_CONFIG_PATH") or os.path.join(HOME, ".config", "herdr", "config.toml")
SELF = os.path.realpath(__file__)
Q = shlex.quote(SELF)
MIN_FZF = (0, 66, 0)
RETENTION_TARGET = 3650

CAP_TEXT = 120000
CAP_TURNS = 200
CAP_TURN_CHARS = 1000
CAP_FILES = 100
INDEX_VERSION = "v7"
POOL_MIN_BYTES = 24 << 20

R = "\033[0m"
B = "\033[1m"

DEFAULTS = {
    "scope": "conversation",
    "sort": "recent",
    "time": "relative",
    "cwd_only": False,
    "agent": "all",
    "skip_permissions": False,
    "chrome": False,
}

SCOPES = [("conversation", "1,2,3"), ("titles", "1"), ("prompts", "2"),
          ("replies", "3"), ("tools", "4"), ("everything", "1,2,3,4")]
SORTS = ["recent", "oldest", "size", "cwd"]
TIMES = ["relative", "absolute"]
FLAGS = [("skip_permissions", "skip-permissions", "--dangerously-skip-permissions", True),
         ("chrome", "chrome", "--chrome", False)]

STATUS = {"working": ("working", "accent"), "blocked": ("blocked", "warn"),
          "idle": ("idle", "dim"), "done": ("done", "dim")}


def read_settings():
    out = dict(DEFAULTS)
    try:
        with open(CONFIG) as fh:
            for line in fh:
                m = re.match(r"\s*([A-Za-z_]\w*)\s*=\s*(.+?)\s*$", line)
                if not m or m.group(1) not in DEFAULTS:
                    continue
                key, raw = m.group(1), m.group(2)
                out[key] = raw == "true" if raw in ("true", "false") else raw.strip('"')
    except OSError:
        pass
    for key, allowed in (("scope", [s for s, _ in SCOPES]), ("sort", SORTS), ("time", TIMES),
                         ("agent", ["all", *PROVIDERS])):
        if out[key] not in allowed:
            out[key] = DEFAULTS[key]
    return out


def write_settings(settings):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    body = "".join(f"{k} = {json.dumps(settings[k])}\n" for k in sorted(DEFAULTS))
    tmp = f"{CONFIG}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, CONFIG)


def theme():
    colors, section = {}, None
    try:
        with open(HERDR_CONFIG) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("["):
                    section = line
                elif section == "[theme.custom]":
                    m = re.match(r'(\w+)\s*=\s*"([^"]*)"', line)
                    if m:
                        colors[m.group(1)] = m.group(2)
    except OSError:
        pass

    def pick(name, fallback):
        value = colors.get(name, fallback)
        return fallback if value == "reset" else value

    return {"accent": pick("accent", "#bfc2ff"), "text": pick("text", "#e2e2e2"),
            "dim": pick("overlay0", "#919191"), "row": pick("active_row_bg", "#1e1e1e"),
            "bg": "-1" if colors.get("panel_bg", "-1") == "reset" else colors.get("panel_bg", "-1"),
            "red": pick("red", "#ffb4ab"), "warn": pick("peach", "#ffdeab"),
            "inverse_text": pick("surface_dim", "#111111")}


def ansi(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"\033[38;2;{r};{g};{b}m"


def ansi_bg(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"\033[48;2;{r};{g};{b}m"


def palette():
    t = theme()
    out = {name: ansi(t[name]) for name in ("accent", "text", "dim", "red", "warn")}
    out["on_accent"] = ansi_bg(t["accent"]) + ansi(t["inverse_text"])
    out["on_warn"] = ansi_bg(t["warn"]) + ansi(t["inverse_text"])
    out["on_muted"] = ansi_bg(t["row"]) + ansi(t["text"])
    return out


C = palette()


def chip(text, style=None):
    return f"{C['dim'] if style is None else C[style]} {text} {R}"


def cell_width(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def display_width(text):
    return sum(cell_width(ch) for ch in re.sub(r"\033\[[0-9;]*m", "", text))


def truncate(text, width):
    if display_width(text) <= width:
        return text
    out, seen = [], 0
    for chunk in re.split(r"(\033\[[0-9;]*m)", text):
        if chunk.startswith("\033"):
            out.append(chunk)
            continue
        for ch in chunk:
            if seen + cell_width(ch) > width - 1:
                return "".join(out) + "…" + R
            out.append(ch)
            seen += cell_width(ch)
    return "".join(out)


def plural(n, unit):
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def relative(ts):
    d = max(0, time.time() - ts)
    if d < 45:
        return "just now"
    if d < 3600:
        return plural(max(1, int(round(d / 60))), "minute") + " ago"
    if d < 86400:
        return plural(int(d // 3600), "hour") + " ago"
    if d < 172800:
        return "yesterday"
    if d < 1209600:
        return plural(int(d // 86400), "day") + " ago"
    if d < 5184000:
        return plural(int(d // 604800), "week") + " ago"
    return plural(int(d // 2592000), "month") + " ago"


def short_path(path):
    return "~" + path[len(HOME):] if path.startswith(HOME + "/") else path


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def herdr_bin():
    return os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr")


HERDR_ERROR = []


def herdr_call(*args):
    exe = herdr_bin()
    if not exe:
        return None
    try:
        done = subprocess.run([exe, *args], capture_output=True, text=True, timeout=10)
        if done.returncode != 0:
            first = (done.stderr or "").strip().split("\n")[0]
            HERDR_ERROR.append(first or f"exited {done.returncode}")
            return None
        return json.loads(done.stdout).get("result")
    except Exception as exc:
        HERDR_ERROR.append(f"{type(exc).__name__}: {exc}")
        return None


def run_herdr(exe, *args):
    try:
        return subprocess.run([exe, *args], capture_output=True, timeout=120).returncode == 0
    except Exception:
        return False


SNAPSHOT = []


def snapshot(cached=False):
    if not SNAPSHOT:
        data = None
        if cached and os.environ.get("TRANSCRIPTS_SNAPSHOT"):
            try:
                with open(SNAPSHOT_FILE) as fh:
                    data = json.load(fh)
            except Exception:
                data = None
        if data is None:
            data = (herdr_call("api", "snapshot") or {}).get("snapshot", {})
            if os.environ.get("TRANSCRIPTS_SNAPSHOT"):
                try:
                    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
                    write_json(SNAPSHOT_FILE, data)
                except OSError:
                    pass
        SNAPSHOT.append(data)
    return SNAPSHOT[0]


def live_agents(cached=False):
    out = {}
    for agent in snapshot(cached).get("agents", []):
        session = agent.get("agent_session") or {}
        provider = session.get("agent") or agent.get("agent")
        if provider and session.get("value"):
            out[f"{provider}:{session['value']}"] = agent
    return out


def context_cwd():
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "") or "{}")
    except ValueError:
        ctx = {}
    for key in ("focused_pane_cwd", "cwd", "pane_cwd"):
        if ctx.get(key):
            return ctx[key]
    snap = snapshot()
    for pane in snap.get("panes", []):
        if pane.get("pane_id") == snap.get("focused_pane_id"):
            return pane.get("foreground_cwd") or pane.get("cwd") or ""
    return os.environ.get("PWD", "")


IMAGE = re.compile(r"\[Image[^\]]*\]")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
METADATA = ("<system-reminder>", "<command-name>", "<command-message>", "<command-args>",
            "<local-command-", "<user-prompt-submit-hook>", "<bash-input>", "<bash-stdout>",
            "<bash-stderr>", "<ide_", "<caveat>", "<task-notification>", "Base directory for this skill:",
            "(Re-invocation of ")
MIDTURN = re.compile(r"new message while you were working:\n(.+?)\n\nThis is how Claude Code", re.S)
PWD_LINE = re.compile(r"^% pwd\n(.+)$", re.M)


FILE_KEYS = ("file_path", "filePath", "notebook_path", "absolute_path", "path")


def normalize(text, cap):
    clean = CONTROL.sub(" ", IMAGE.sub("[image]", text))
    return " ".join(clean.split())[:cap]


def tool_args(given, *lead):
    fields = given.items() if isinstance(given, dict) else []
    return " ".join([*lead, *(v for k, v in fields if isinstance(v, str) and k != "content")])


def tool_files(given):
    if isinstance(given, str):
        try:
            given = json.loads(given)
        except Exception:
            return []
    fields = given.items() if isinstance(given, dict) else []
    return [v for k, v in fields if k in FILE_KEYS and isinstance(v, str) and v]


def readonly_db(path):
    import sqlite3
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def parse_transcript(path, agent="claude", title=""):
    cwd = branch = ""
    prompts, replies, tools, files = [], [], [], []
    turns = deque(maxlen=CAP_TURNS)
    total = 0
    with open(path, "rb") as fh:
        for raw in fh:
            head = raw[:400]
            if b'"type":"ai-title"' in head:
                try:
                    title = json.loads(raw).get("aiTitle", "") or title
                except Exception:
                    pass
                continue
            if b'"type":"queue-operation"' in head and b'"operation":"enqueue"' in head:
                try:
                    text = (json.loads(raw).get("content") or "").strip()
                except Exception:
                    continue
                if text and not text.startswith(METADATA):
                    turns.append(["you", normalize(text, CAP_TURN_CHARS)])
                    if total < CAP_TEXT:
                        prompts.append(normalize(text, 1000))
                        total += min(len(text), 1000)
                continue
            is_user = b'"type":"user"' in head or b'"role":"user"' in head
            is_assistant = b'"role":"assistant"' in head or b'"type":"assistant"' in head
            if cwd and (b'"isSidechain":true' in head or (is_user and b'"type":"tool_result"' in raw[:1200])):
                continue
            if not cwd and not is_user and b'"cwd":"' in raw[:600]:
                try:
                    o = json.loads(raw)
                    cwd, branch = o.get("cwd") or "", o.get("gitBranch") or ""
                except Exception:
                    pass
            if not (is_user or is_assistant):
                continue
            if is_assistant and b'"type":"text"' not in raw and b'"type":"tool_use"' not in raw:
                continue
            try:
                o = json.loads(raw)
            except Exception:
                continue
            if is_user and not cwd:
                cwd, branch = o.get("cwd") or "", o.get("gitBranch") or ""
            if o.get("isSidechain"):
                continue
            content = o.get("message", {}).get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                kind = part.get("type")
                if kind == "image":
                    if is_user:
                        turns.append(["you", "[image]"])
                    continue
                if kind == "text":
                    text = (part.get("text") or "").strip()
                    if text.startswith("<system-reminder>"):
                        if not cwd:
                            found = PWD_LINE.search(text)
                            cwd = found.group(1).strip() if found else ""
                        m = MIDTURN.search(text)
                        text = m.group(1).strip() if m else ""
                    if not text or text.startswith(METADATA) or text.startswith("[Request interrupted"):
                        continue
                    turns.append(["you" if is_user else agent, normalize(text, CAP_TURN_CHARS)])
                    if total < CAP_TEXT:
                        (prompts if is_user else replies).append(normalize(text, 1000))
                        total += min(len(text), 1000)
                elif kind == "tool_use" and total < CAP_TEXT:
                    files += tool_files(part.get("input"))
                    text = tool_args(part.get("input"))
                    if text:
                        tools.append(normalize(text, 300))
                        total += min(len(text), 300)
    return make_entry(title, cwd, branch, prompts, replies, tools, turns, files)


def make_entry(title, cwd, branch, prompts, replies, tools, turns, files=()):
    entry = {"title": normalize(title, 200), "cwd": cwd, "branch": normalize(branch, 80),
             "prompts": " | ".join(prompts), "replies": " | ".join(replies), "tools": " | ".join(tools),
             "opening_prompt": next((text for _, text in turns if text != "[image]"), ""),
             "files": list(dict.fromkeys(files))[:CAP_FILES]}
    return entry, list(turns)


CODEX_NOISE = ("<environment_context>", "# AGENTS.md instructions", "<user_instructions>", "<turn_aborted>",
               "<permissions", "<INSTRUCTIONS>", "<collaboration_mode", "<user_shell", "<image", "</image>",
               "<skill>")
CODEX_CWD = re.compile(r"(?:<cwd>|Current working directory: )([^<\n]+)")
CODEX_THREADS = []


def codex_threads():
    if not CODEX_THREADS:
        threads = {}
        for db_path in sorted(glob.glob(os.path.join(CODEX_HOME, "state_*.sqlite"))):
            try:
                db = readonly_db(db_path)
                for sid, name, branch in db.execute("select id, name, git_branch from threads"):
                    threads[sid] = (name or "", branch or "")
                db.close()
            except Exception:
                pass
        try:
            with open(CODEX_NAMES, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                        name, branch = threads.get(o["id"], ("", ""))
                        threads[o["id"]] = (name or o.get("thread_name") or "", branch)
                    except Exception:
                        pass
        except OSError:
            pass
        CODEX_THREADS.append(threads)
    return CODEX_THREADS[0]


def codex_sid(path):
    return os.path.basename(path)[:-len(".jsonl")][-36:]


def parse_codex(path):
    cwd = ""
    prompts, replies, tools = [], [], []
    turns = deque(maxlen=CAP_TURNS)
    total = 0
    with open(path, "rb") as fh:
        for raw in fh:
            head = raw[:300]
            if b'"type":"session_meta"' in head:
                meta = json.loads(raw).get("payload") or {}
                if (meta.get("source") or "cli") != "cli" or meta.get("thread_source") == "subagent":
                    break
                cwd = meta.get("cwd") or ""
                continue
            if b'"type":"turn_context"' in head:
                if not cwd:
                    cwd = (json.loads(raw).get("payload") or {}).get("cwd") or ""
                continue
            is_call = b'"type":"function_call"' in head or b'"type":"custom_tool_call"' in head
            if not is_call and b'"type":"message"' not in head:
                continue
            try:
                o = json.loads(raw)
                p = o.get("payload") or o
            except Exception:
                continue
            if is_call:
                text = f"{p.get('name') or ''} {p.get('arguments') or p.get('input') or ''}".strip()
                if text and total < CAP_TEXT:
                    tools.append(normalize(text, 300))
                    total += min(len(text), 300)
                continue
            role = p.get("role")
            if role not in ("user", "assistant") or not isinstance(p.get("content"), list):
                continue
            texts = []
            for part in p["content"]:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "input_image":
                    turns.append(["you", "[image]"])
                elif part.get("type") in ("input_text", "output_text"):
                    text = (part.get("text") or "").strip()
                    if not cwd and role == "user":
                        m = CODEX_CWD.search(text)
                        cwd = m.group(1).strip() if m else ""
                    if text and not text.startswith(CODEX_NOISE):
                        texts.append(text)
            text = " ".join(texts)
            if not text:
                continue
            turns.append(["you" if role == "user" else "codex", normalize(text, CAP_TURN_CHARS)])
            if total < CAP_TEXT:
                (prompts if role == "user" else replies).append(normalize(text, 1000))
                total += min(len(text), 1000)
    title, branch = codex_threads().get(codex_sid(path), ("", ""))
    return make_entry(title, cwd, branch, prompts, replies, tools, turns)


def opencode_scan(provider, spec):
    path = os.path.join(spec["root"], "opencode.db")
    try:
        db = readonly_db(path)
        sizes = dict(db.execute("select session_id, sum(length(cast(data as blob)))"
                                " from part group by session_id"))
        sessions = db.execute("select id, time_updated from session where parent_id is null").fetchall()
        db.close()
    except Exception:
        return []
    found = []
    for sid, updated in sessions:
        size = sizes.get(sid) or 0
        found.append((updated / 1000, size, f"{provider}:{sid}", f"{path}/{sid}",
                      f"{INDEX_VERSION}:{updated}:{size}"))
    return found


def parse_opencode(path):
    db_path, _, sid = path.rpartition("/")
    prompts, replies, tools, files = [], [], [], []
    turns = deque(maxlen=CAP_TURNS)
    total = 0
    db = readonly_db(db_path)
    title, cwd = db.execute("select title, directory from session where id = ?", (sid,)).fetchone()
    for message, data in db.execute(
            "select m.data, p.data from part p join message m on m.id = p.message_id"
            " where p.session_id = ? order by m.time_created, p.time_created, p.id", (sid,)):
        if data.startswith('{"type":"tool"'):
            if total >= CAP_TEXT:
                continue
            part = json.loads(data)
            files += tool_files((part.get("state") or {}).get("input"))
            text = tool_args((part.get("state") or {}).get("input"), part.get("tool") or "").strip()
            if text:
                tools.append(normalize(text, 300))
                total += min(len(text), 300)
            continue
        if not data.startswith('{"type":"text"'):
            continue
        part = json.loads(data)
        text = (part.get("text") or "").strip()
        if part.get("synthetic") or not text or text.startswith(METADATA):
            continue
        is_user = '"role":"user"' in message
        turns.append(["you" if is_user else "opencode", normalize(text, CAP_TURN_CHARS)])
        if total < CAP_TEXT:
            (prompts if is_user else replies).append(normalize(text, 1000))
            total += min(len(text), 1000)
    db.close()
    return make_entry(title, cwd, "", prompts, replies, tools, turns, files)


def jsonl_sid(path):
    return os.path.basename(path)[:-len(".jsonl")]


def resume_command(spec, sid, settings):
    flags = [flag for key, given in spec.get("flags", {}).items() if settings[key] for flag in given]
    return [part.replace("{sid}", sid) for part in spec["resume"]] + flags


def parse_droid(path):
    try:
        with open(path, "rb") as fh:
            title = json.loads(fh.readline()).get("title") or ""
    except Exception:
        title = ""
    return parse_transcript(path, "droid", title)


COPILOT_WORKSPACE = re.compile(r"^(cwd|branch|summary):[ \t]*(\S.*?)\s*$", re.M)


def copilot_sid(path):
    return os.path.basename(os.path.dirname(path))


def copilot_workspace(path):
    try:
        with open(os.path.join(os.path.dirname(path), "workspace.yaml"),
                  encoding="utf-8", errors="replace") as fh:
            return dict(COPILOT_WORKSPACE.findall(fh.read(4000)))
    except OSError:
        return {}


def parse_copilot(path):
    cwd = branch = ""
    prompts, replies, tools, files = [], [], [], []
    turns = deque(maxlen=CAP_TURNS)
    total = 0
    with open(path, "rb") as fh:
        for raw in fh:
            head = raw[:64]
            if not cwd and b'"type":"session.start"' in head:
                try:
                    context = (json.loads(raw).get("data") or {}).get("context") or {}
                except Exception:
                    context = {}
                cwd, branch = context.get("cwd") or "", context.get("branch") or ""
                continue
            is_user = b'"type":"user.message"' in head
            if not is_user and b'"type":"assistant.message"' not in head:
                continue
            try:
                data = json.loads(raw).get("data") or {}
            except Exception:
                continue
            text = (data.get("content") or "").strip()
            if text:
                turns.append(["you" if is_user else "copilot", normalize(text, CAP_TURN_CHARS)])
                if total < CAP_TEXT:
                    (prompts if is_user else replies).append(normalize(text, 1000))
                    total += min(len(text), 1000)
            for call in data.get("toolRequests") or []:
                files += tool_files(call.get("arguments") if isinstance(call, dict) else None)
                text = tool_args(call.get("arguments") if isinstance(call, dict) else None)
                if text and total < CAP_TEXT:
                    tools.append(normalize(text, 300))
                    total += min(len(text), 300)
    workspace = copilot_workspace(path)
    return make_entry(workspace.get("summary", ""), cwd or workspace.get("cwd", ""),
                      branch or workspace.get("branch", ""), prompts, replies, tools, turns, files)


GEMINI_HOME = os.path.join(HOME, ".gemini")
GEMINI_CHATS = os.path.join(GEMINI_HOME, "tmp")
GEMINI_REGISTRY = os.path.join(GEMINI_HOME, "projects.json")
QWEN_PROJECTS = os.path.join(HOME, ".qwen", "projects")
KIMI_HOME = os.path.join(HOME, ".kimi")
KIMI_SESSIONS = os.path.join(KIMI_HOME, "sessions")
KIMI_REGISTRY = os.path.join(KIMI_HOME, "kimi.json")
GEMINI_NOISE = ("<session_context>", "System: ")
KIMI_NOISE = ("<system>",)
GEMINI_SESSION = re.compile(rb'"sessionId"\s*:\s*"([^"]+)"')
PROJECT_PATHS = []


def project_paths(cache=None):
    if not PROJECT_PATHS:
        import hashlib
        slugs, paths = {}, set()
        try:
            with open(GEMINI_REGISTRY) as fh:
                for path, slug in (json.load(fh).get("projects") or {}).items():
                    slugs[slug] = path
        except Exception:
            pass
        try:
            with open(KIMI_REGISTRY) as fh:
                paths.update(d["path"] for d in json.load(fh).get("work_dirs") or [])
        except Exception:
            pass
        try:
            paths.update(entry["cwd"] for entry in (cache or read_index()).values() if entry.get("cwd"))
        except Exception:
            pass
        lookup = dict(slugs)
        for path in paths | set(slugs.values()):
            raw = path.encode()
            lookup[hashlib.sha256(raw).hexdigest()] = path
            lookup[hashlib.md5(raw).hexdigest()] = path
        PROJECT_PATHS.append(lookup)
    return PROJECT_PATHS[0]


def project_cwd(path):
    return project_paths().get(os.path.basename(os.path.dirname(os.path.dirname(path))), "")


def hashed_entry(title, cwd, branch, prompts, replies, tools, turns, files=()):
    entry, turns = make_entry(title, cwd, branch, prompts, replies, tools, turns, files)
    if not cwd:
        entry["paths"] = len(project_paths())
    return entry, turns


def gemini_sid(path):
    with open(path, "rb") as fh:
        found = GEMINI_SESSION.search(fh.read(4096))
    return found.group(1).decode() if found else os.path.basename(path).partition(".")[0]


def gemini_records(path):
    if not path.endswith(".jsonl"):
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        return data, data.get("messages") or []
    meta, messages = {}, {}
    with open(path, "rb") as fh:
        for raw in fh:
            try:
                o = json.loads(raw)
            except Exception:
                continue
            if not isinstance(o, dict):
                continue
            patch = o.get("$set")
            if isinstance(patch, dict):
                if isinstance(patch.get("messages"), list):
                    messages = {m.get("id"): m for m in patch["messages"] if isinstance(m, dict)}
                meta.update(patch)
            elif o.get("type"):
                messages[o.get("id")] = o
            elif o.get("sessionId"):
                meta = o
    return meta, list(messages.values())


def parse_gemini(path):
    meta, messages = gemini_records(path)
    cwd = project_cwd(path) if (meta.get("kind") or "main") == "main" else ""
    prompts, replies, tools, files = [], [], [], []
    turns = deque(maxlen=CAP_TURNS)
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        kind = message.get("type")
        if kind not in ("user", "gemini"):
            continue
        content = message.get("content")
        texts = []
        for part in ([{"text": content}] if isinstance(content, str) else content) or []:
            if not isinstance(part, dict):
                continue
            if "inlineData" in part:
                if kind == "user":
                    turns.append(["you", "[image]"])
                continue
            text = (part.get("text") or "").strip()
            if text and not text.startswith(GEMINI_NOISE):
                texts.append(text)
        for call in message.get("toolCalls") or []:
            if not isinstance(call, dict) or total >= CAP_TEXT:
                continue
            files += tool_files(call.get("args"))
            text = tool_args(call.get("args"), str(call.get("name") or "")).strip()
            if text:
                tools.append(normalize(text, 300))
                total += min(len(text), 300)
        text = " ".join(texts)
        if not text:
            continue
        turns.append(["you" if kind == "user" else "gemini", normalize(text, CAP_TURN_CHARS)])
        if total < CAP_TEXT:
            (prompts if kind == "user" else replies).append(normalize(text, 1000))
            total += min(len(text), 1000)
    return hashed_entry(meta.get("summary") or "", cwd, "", prompts, replies, tools, turns, files)


def parse_qwen(path):
    cwd = branch = ""
    prompts, replies, tools, files = [], [], [], []
    turns = deque(maxlen=CAP_TURNS)
    total = 0
    with open(path, "rb") as fh:
        for raw in fh:
            head = raw[:400]
            is_user = b'"type":"user"' in head
            if not is_user and b'"type":"assistant"' not in head:
                continue
            try:
                o = json.loads(raw)
            except Exception:
                continue
            if o.get("isSidechain"):
                continue
            if not cwd:
                cwd, branch = o.get("cwd") or "", o.get("gitBranch") or ""
            texts = []
            for part in (o.get("message") or {}).get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("inlineData"):
                    if is_user:
                        turns.append(["you", "[image]"])
                    continue
                call = part.get("functionCall")
                if isinstance(call, dict):
                    files += tool_files(call.get("args"))
                    text = tool_args(call.get("args"), str(call.get("name") or "")).strip()
                    if text and total < CAP_TEXT:
                        tools.append(normalize(text, 300))
                        total += min(len(text), 300)
                    continue
                text = (part.get("text") or "").strip()
                if text:
                    texts.append(text)
            text = " ".join(texts)
            if not text:
                continue
            turns.append(["you" if is_user else "qwen", normalize(text, CAP_TURN_CHARS)])
            if total < CAP_TEXT:
                (prompts if is_user else replies).append(normalize(text, 1000))
                total += min(len(text), 1000)
    return make_entry("", cwd, branch, prompts, replies, tools, turns, files)


def kimi_sid(path):
    return os.path.basename(os.path.dirname(path))


def parse_kimi(path):
    cwd = project_cwd(path)
    prompts, replies, tools, files = [], [], [], []
    turns = deque(maxlen=CAP_TURNS)
    total = 0
    with open(path, "rb") as fh:
        for raw in fh:
            head = raw[:32]
            if b'"user"' not in head and b'"assistant"' not in head:
                continue
            try:
                o = json.loads(raw)
            except Exception:
                continue
            role = o.get("role") if isinstance(o, dict) else None
            if role not in ("user", "assistant"):
                continue
            content = o.get("content")
            texts = []
            for part in ([{"text": content}] if isinstance(content, str) else content) or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    if role == "user":
                        turns.append(["you", "[image]"])
                    continue
                text = (part.get("text") or "").strip()
                if text and not text.startswith(KIMI_NOISE):
                    texts.append(text)
            for call in o.get("tool_calls") or []:
                given = call.get("function") if isinstance(call, dict) else None
                if not isinstance(given, dict) or total >= CAP_TEXT:
                    continue
                files += tool_files(given.get("arguments"))
                text = f"{given.get('name') or ''} {given.get('arguments') or ''}".strip()
                if text:
                    tools.append(normalize(text, 300))
                    total += min(len(text), 300)
            text = " ".join(texts)
            if not text:
                continue
            turns.append(["you" if role == "user" else "kimi", normalize(text, CAP_TURN_CHARS)])
            if total < CAP_TEXT:
                (prompts if role == "user" else replies).append(normalize(text, 1000))
                total += min(len(text), 1000)
    return hashed_entry("", cwd, "", prompts, replies, tools, turns, files)


PROVIDERS = {
    "claude": {"root": PROJECTS, "files": os.path.join(PROJECTS, "*", "*.jsonl"), "sid": jsonl_sid,
               "parse": parse_transcript, "resume": ("claude", "--resume", "{sid}"),
               "flags": {key: (flag,) for key, _, flag, _ in FLAGS},
               "install": "npm install -g @anthropic-ai/claude-code"},
    "codex": {"root": CODEX_SESSIONS, "files": os.path.join(CODEX_HOME, "*sessions", "**", "*.jsonl"), "sid": codex_sid,
              "parse": parse_codex, "resume": ("codex", "resume", "{sid}"),
              "flags": {"skip_permissions": ("--yolo",)}, "install": "npm install -g @openai/codex"},
    "opencode": {"root": OPENCODE_HOME, "scan": opencode_scan, "parse": parse_opencode,
                 "resume": ("opencode", "--session", "{sid}"),
                 "install": "curl -fsSL https://opencode.ai/install | bash"},
    "droid": {"root": DROID_SESSIONS, "files": os.path.join(DROID_SESSIONS, "*.jsonl"), "sid": jsonl_sid,
              "parse": parse_droid, "resume": ("droid", "--resume", "{sid}"),
              "flags": {"skip_permissions": ("--auto", "high")},
              "install": "curl -fsSL https://app.factory.ai/cli | sh"},
    "copilot": {"root": COPILOT_STATE, "files": os.path.join(COPILOT_STATE, "*", "events.jsonl"),
                "sid": copilot_sid, "parse": parse_copilot, "resume": ("copilot", "--resume={sid}"),
                "flags": {"skip_permissions": ("--allow-all-tools",)},
                "install": "npm install -g @github/copilot"},
    "gemini": {"root": GEMINI_CHATS, "files": os.path.join(GEMINI_CHATS, "*", "chats", "*.json*"),
               "sid": gemini_sid, "parse": parse_gemini, "resume": ("gemini", "--resume", "{sid}"),
               "flags": {"skip_permissions": ("--yolo",)},
               "install": "npm install -g @google/gemini-cli"},
    "qwen": {"root": QWEN_PROJECTS, "files": os.path.join(QWEN_PROJECTS, "*", "chats", "*.jsonl"),
             "sid": jsonl_sid, "parse": parse_qwen, "resume": ("qwen", "--resume", "{sid}"),
             "flags": {"skip_permissions": ("--yolo",)},
             "install": "npm install -g @qwen-code/qwen-code"},
    "kimi": {"root": KIMI_SESSIONS, "files": os.path.join(KIMI_SESSIONS, "*", "*", "context.jsonl"),
             "sid": kimi_sid, "parse": parse_kimi, "resume": ("kimi", "--session", "{sid}"),
             "flags": {"skip_permissions": ("--yolo",)},
             "install": "uv tool install kimi-cli"},
}


def parse_job(job):
    uid, path = job
    try:
        return PROVIDERS[uid.partition(":")[0]]["parse"](path)
    except Exception:
        return None


def parse_jobs(jobs, nbytes):
    if len(jobs) < 2 or nbytes < POOL_MIN_BYTES:
        return map(parse_job, jobs)
    import concurrent.futures
    import multiprocessing
    context = multiprocessing.get_context("fork" if sys.platform == "linux" else None)
    return concurrent.futures.ProcessPoolExecutor(mp_context=context).map(parse_job, jobs)


def turns_path(uid):
    return os.path.join(TURNS_DIR, uid + ".json")


def write_json(path, data, indent=None):
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=indent)
        if indent:
            fh.write("\n")
    os.replace(tmp, path)


def write_index(data):
    tmp = f"{INDEX}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        marshal.dump(data, fh)
    os.replace(tmp, INDEX)


def read_index():
    with open(INDEX, "rb") as fh:
        return marshal.load(fh)


def glob_scan(provider, spec):
    for path in glob.glob(spec["files"], recursive=True):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_size:
            yield (st.st_mtime, st.st_size, f"{provider}:{spec['sid'](path)}", path,
                   f"{INDEX_VERSION}:{st.st_mtime_ns}:{st.st_size}")


def transcript_files():
    found = []
    for provider, spec in PROVIDERS.items():
        found.extend(spec.get("scan", glob_scan)(provider, spec))
    return found


def load_cache(files):
    try:
        cache = read_index()
    except Exception:
        cache = {}
    stale = [(row, uid, path, size) for row, (mtime, size, uid, path, key) in enumerate(files)
             if (cache.get(uid) or {}).get("key") != key or not os.path.exists(turns_path(uid))
             or "paths" in cache[uid] and cache[uid]["paths"] != len(project_paths(cache))]
    return cache, stale


def index_entries(files):
    os.makedirs(TURNS_DIR, mode=0o700, exist_ok=True)
    cache, stale = load_cache(files)
    if stale:
        lock = open(LOCK, "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache, stale = load_cache(files)
    parsed = parse_jobs([(uid, path) for _, uid, path, _ in stale], sum(size for _, _, _, size in stale))
    reparse = {row for row, _, _, _ in stale}
    fresh = {}
    for row, (mtime, size, uid, path, key) in enumerate(files):
        entry = cache.get(uid)
        if row in reparse:
            result = next(parsed)
            if result is None:
                continue
            entry, turns = result
            entry["key"] = key
            write_json(turns_path(uid), {"cwd": entry["cwd"], "branch": entry["branch"],
                                         "files": entry.pop("files"), "turns": turns})
        fresh[uid] = entry
        yield mtime, size, uid, entry
    if stale or set(fresh) != set(cache):
        write_index(fresh)
        for old in glob.glob(os.path.join(TURNS_DIR, "*.json")):
            if os.path.basename(old)[:-len(".json")] not in fresh:
                os.remove(old)


def status_chip(agent):
    label, color = STATUS.get(agent.get("agent_status"), ("open", "dim"))
    return f"{C[color]}●{R} {C[color]}{label}{R}"


HIDDEN_FIELD_GAP = " " * 500


def render_row(mtime, size, uid, entry, settings, live, current):
    title = entry.get("title") or ""
    fallback = entry.get("opening_prompt", "")[:90] or "untitled session"
    head = f"{B}{title}{R}" if title else f"{C['text']}{fallback}{R}"
    where = os.path.basename(entry["cwd"].rstrip("/")) or entry["cwd"]
    when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
            if settings["time"] == "absolute" else relative(mtime))
    where_color = C["accent"] if entry["cwd"] == current else C["dim"]
    parts = [when, uid.partition(":")[0], f"{where_color}{where}{C['dim']}"]
    if entry.get("branch"):
        parts.append(entry["branch"])
    parts.append(human_size(size))
    meta = C["dim"] + "  ·  ".join(parts) + R
    if uid in live:
        meta = status_chip(live[uid]) + f"{C['dim']}  ·  {R}" + meta
    tools = entry["tools"] if searches_tools(settings["scope"]) else ""
    return (f"{head}\n  {meta}{HIDDEN_FIELD_GAP}\t{entry['prompts']}\t{entry['replies']}"
            f"\t{tools}\t{entry['cwd']}\t{uid}")


def searches_tools(scope):
    return "4" in scope_nth(scope)


def list_rows():
    settings = read_settings()
    live = live_agents()
    current = context_cwd()
    here = current if settings["cwd_only"] else ""
    files = transcript_files()
    sort = settings["sort"]
    files.sort(key={"oldest": lambda f: f[0], "size": lambda f: -f[1]}.get(sort, lambda f: -f[0]))

    def keep(entry):
        return (entry.get("cwd") and (entry.get("title") or entry.get("opening_prompt"))
                and (not here or entry["cwd"] == here))

    rows = index_entries(files)
    if sort == "cwd":
        rows = sorted((r for r in rows if keep(r[3])), key=lambda r: (r[3]["cwd"], -r[0]))
    first = True
    agent = settings["agent"]
    for mtime, size, uid, entry in rows:
        if not keep(entry) or (agent != "all" and not uid.startswith(agent + ":")):
            continue
        if not first:
            sys.stdout.write("\0")
        sys.stdout.write(render_row(mtime, size, uid, entry, settings, live, current))
        sys.stdout.flush()
        first = False


def git_deletions(cwd, paths):
    try:
        done = subprocess.run(["git", "-C", cwd, "log", "--diff-filter=D", "--name-only", "--relative",
                               "--no-renames", "--format=", "-z", "--", *paths],
                              capture_output=True, text=True, timeout=2)
    except Exception:
        return None
    return {name for name in done.stdout.split("\0") if name} if not done.returncode else None


def touched_line(cwd, given):
    cwd = cwd.rstrip("/")
    if not cwd:
        return ""
    alive, gone = [], []
    for raw in given:
        path = os.path.normpath(os.path.join(cwd, raw))
        if not path.startswith(cwd + os.sep) or os.path.isdir(path):
            continue
        (alive if os.path.exists(path) else gone).append(os.path.relpath(path, cwd))
    if not alive and not gone:
        return ""
    parts = [plural(len(alive) + len(gone), "file") + " touched", f"{len(alive)} still there"]
    deleted = set()
    if gone:
        deleted = git_deletions(cwd, gone) if os.path.isdir(cwd) else None
    if deleted is None:
        parts.append(f"{len(gone)} gone")
    else:
        superseded = sum(1 for path in gone
                         if path in deleted or any(name.startswith(path + "/") for name in deleted))
        if superseded:
            parts.append(f"{superseded} superseded")
        if len(gone) > superseded:
            parts.append(f"{len(gone) - superseded} unexplained")
    return C["dim"] + "  ·  ".join(parts) + R


def preview(uid, query):
    terms = [t.lstrip("'^").rstrip("$") for t in query.split() if not t.startswith("!")]
    terms = [t for t in terms if t]
    pattern = re.compile("|".join(re.escape(t) for t in terms), re.I) if terms else None

    def highlight(text):
        return pattern.sub(lambda m: f"{C['on_accent']}{m.group(0)}{R}", text) if pattern else text

    try:
        with open(turns_path(uid)) as fh:
            entry = json.load(fh)
        turns = entry["turns"]
    except Exception:
        print(f"{C['dim']}no transcript indexed for {uid}{R}")
        return

    where = [short_path(entry.get("cwd", "")), entry.get("branch", "")]
    print(f"{C['text']}" + f"{C['dim']}  ·  {C['text']}".join(x for x in where if x) + R)
    print(f"{C['dim']}{uid.replace(':', '  ·  ')}{R}")
    agent = live_agents(cached=True).get(uid)
    if agent:
        tab = agent.get("terminal_title_stripped") or agent.get("pane_id", "")
        print(f"{status_chip(agent)} {C['dim']}in this Herdr session  ·  {tab}{R}")
    touched = touched_line(entry.get("cwd", ""), entry.get("files") or [])
    if touched:
        print(touched)
    print()
    if pattern:
        hits = [t for t in turns if pattern.search(t[1])]
        if hits:
            print(f"{C['dim']}{len(hits)} of {plural(len(turns), 'message')} match{R}\n")
            turns = hits
        else:
            print(f"{C['dim']}no match in the {plural(len(turns), 'indexed message')} below{R}\n")
    for who, text in turns:
        color = C["accent"] if who == "you" else C["warn"]
        print(f"{color}{B}{who}{R} {C['dim']}›{R} {highlight(text)}\n")


KEYS = [
    {"key": "↵", "help": "resume the session", "prio": 0},
    {"key": "tab", "help": "next search scope", "setting": "scope-next",
     "chip": lambda s: "scope", "prio": 1},
    {"key": "ctrl-o", "help": "next sort order", "setting": "sort-next",
     "chip": lambda s: "sort", "prio": 2},
    {"key": "ctrl-p", "help": "only sessions from the current directory", "setting": "cwd",
     "chip": lambda s: "all directories" if s["cwd_only"] else "this directory", "prio": 3},
    {"key": "ctrl-a", "help": "cycle through the agents, or all of them", "setting": "agent-next",
     "chip": lambda s: "all agents" if s["agent"] == "all" else s["agent"] + " only", "prio": 0.5},
    {"key": "ctrl-g", "alias": "f1", "help": "these keys", "raw": f"execute({Q} overlay help)",
     "chip": lambda s: "keys", "prio": 5},
    {"key": "ctrl-d", "help": "diagnostics: retention, index, tools",
     "raw": f"execute({Q} overlay diagnostics)", "chip": lambda s: "checks", "prio": 6},
    {"key": "ctrl-t", "help": "relative or absolute times", "setting": "time", "prio": 4},
    {"key": "ctrl-v", "alias": "ctrl-/", "help": "show or hide the transcript preview",
     "raw": "toggle-preview", "prio": 7},
    {"key": "ctrl-x", "help": "resume with permission prompts off, as the agent spells it",
     "setting": "flag:skip_permissions", "prio": 8},
    {"key": "ctrl-b", "help": "resume with --chrome, for agents that take it",
     "setting": "flag:chrome", "prio": 8},
    {"key": "esc", "help": "close the picker", "prio": 9},
]


def bindings():
    out = []
    for k in KEYS:
        action = k.get("raw") or (f"transform({Q} apply {k['setting']})" if k.get("setting") else "")
        if not action:
            continue
        for key in (k["key"], k.get("alias")):
            if key:
                out.append(f"{key}:{action}")
    return out


def term_size():
    try:
        with open("/dev/tty") as tty_out:
            size = os.get_terminal_size(tty_out.fileno())
            return size.columns, size.lines
    except Exception:
        return (int(os.environ.get("FZF_COLUMNS") or os.environ.get("COLUMNS") or 100),
                int(os.environ.get("FZF_LINES") or os.environ.get("LINES") or 30))


def hint(key, label, sep="\t"):
    return f"{C['accent']}{key}{R}{sep}{C['text']}{label}{R}"


def setting_chip(label, value):
    return f"{C['dim']}{label}{R}\t{C['accent']}{value}{R}"


def action_bar(hints, buttons, width, indent=2, sep="\t"):
    bar = sep.join(buttons)
    while hints and indent + display_width(sep.join(hints)) + display_width(bar) + 3 > width:
        hints.pop()
    left = " " * indent + sep.join(hints)
    gap = " " * max(1, width - display_width(left) - display_width(bar) - 2 * len(sep) - 1)
    return left + sep + gap + sep + bar


def preview_rows(height):
    usable = height - 8
    want = max(5, round(usable * 0.4))
    for delta in (0, -1, 1, -2, 2):
        rows = want + delta
        if rows >= 3 and (usable - rows) % 3 == 2:
            return rows
    return want


def footer(settings=None, width=None):
    settings = settings or read_settings()
    width = width or term_size()[0]
    hints = [hint(k["key"], k["chip"](settings))
             for k in sorted(KEYS, key=lambda k: k["prio"]) if k.get("chip")]
    return action_bar(hints, [chip("↵ resume", "on_accent"), chip("esc close", "on_muted")], width - 2)


def header(settings=None, width=None):
    settings = settings or read_settings()
    width = width or term_size()[0]
    compact = width < 76

    names = [name for name, _ in SCOPES]
    if compact:
        names = [settings["scope"]]
    scope = "\t".join(chip(n, "on_accent" if n == settings["scope"] else None) for n in names)
    flags = []
    for key, label, _, danger in FLAGS:
        if settings[key]:
            flags.append(chip(label + " ✓", "on_warn" if danger else "on_accent"))
        elif not compact:
            flags.append(chip(label))
    if not flags:
        flags = [chip("no flags set")]

    state = [setting_chip("sort", settings["sort"]), setting_chip("times", settings["time"])]
    left = f" {C['dim']}resume{R}\t" + "\t".join(flags)
    while state and display_width(left) + display_width("\t".join(state)) + 6 > width:
        state.pop()
    right = "\t".join(state)
    pad = " " * max(1, width - display_width(left) - display_width(right) - 5)
    return (f" {C['dim']}search{R}\t{scope}\n" + left + "\t" + pad + "\t" + right)


def refresh_bars():
    return f"transform-header({Q} header)+transform-footer({Q} footer)"


def scope_nth(name):
    return dict(SCOPES)[name]


def apply_setting(name):
    settings = read_settings()
    had_tools = searches_tools(settings["scope"])
    if name == "scope-next":
        names = [n for n, _ in SCOPES]
        settings["scope"] = names[(names.index(settings["scope"]) + 1) % len(names)]
    elif name.startswith("scope-set:"):
        settings["scope"] = name.split(":", 1)[1]
    elif name == "sort-next":
        settings["sort"] = SORTS[(SORTS.index(settings["sort"]) + 1) % len(SORTS)]
    elif name == "time":
        settings["time"] = TIMES[1 - TIMES.index(settings["time"])]
    elif name == "cwd":
        settings["cwd_only"] = not settings["cwd_only"]
    elif name == "agent-next":
        names = ["all", *PROVIDERS]
        settings["agent"] = names[(names.index(settings["agent"]) + 1) % len(names)]
    elif name.startswith("flag:"):
        key = name.split(":", 1)[1]
        settings[key] = not settings[key]
    else:
        return "ignore"
    write_settings(settings)
    if name.startswith("scope"):
        fetch = f"reload({Q} list)+" if searches_tools(settings["scope"]) != had_tools else ""
        return fetch + f"change-nth({scope_nth(settings['scope'])})+" + refresh_bars()
    if name.startswith("flag:"):
        return refresh_bars()
    return f"reload({Q} list)+" + refresh_bars()


def click_action(where):
    word = os.environ.get("FZF_CLICK_HEADER_WORD" if where == "header" else "FZF_CLICK_FOOTER_WORD", "")
    word = word.strip()
    if where == "header":
        word = word.replace("✓", "").strip()
        if word in dict(SCOPES):
            return apply_setting(f"scope-set:{word}")
        for key, label, _, _ in FLAGS:
            if word == label:
                return apply_setting(f"flag:{key}")
        if word in ("sort", *SORTS):
            return apply_setting("sort-next")
        if word in ("times", *TIMES):
            return apply_setting("time")
        return "ignore"
    words = word.split()
    if "resume" in words or word == "↵":
        return "accept"
    if "close" in words or word == "esc":
        return "abort"
    if "keys" in words or word == "f1":
        return f"execute({Q} overlay help)"
    if "checks" in words or word == "ctrl-d":
        return f"execute({Q} overlay diagnostics)"
    if "scope" in words or word == "tab":
        return apply_setting("scope-next")
    if "sort" in words or word == "ctrl-o" or word in SORTS:
        return apply_setting("sort-next")
    if "times" in words or word == "ctrl-t" or word in TIMES:
        return apply_setting("time")
    if "directory" in words or "directories" in words or word == "ctrl-p":
        return apply_setting("cwd")
    if "agent" in words or "agents" in words or word == "ctrl-a":
        return apply_setting("agent-next")
    return "ignore"


def read_key(stream):
    ch = stream.read(1)
    if ch == b"\033" and select.select([stream], [], [], 0.05)[0]:
        ch += stream.read(2)
    return ch


def overlay(title, lines, standalone=False, on_enter=None, any_key=False, buttons=()):
    try:
        tty_in = open("/dev/tty", "rb", buffering=0)
        tty_out = open("/dev/tty", "w", encoding="utf-8", errors="replace")
    except OSError:
        print("\n".join(lines))
        return
    top = 0
    tty_out.write("\033[2J" if standalone else "\033[?1049l")
    tty_out.write("\0337\033[?25l")
    tty_out.flush()
    old = termios.tcgetattr(tty_in.fileno())
    tty.setraw(tty_in.fileno())
    try:
        while True:
            cols, rows = os.get_terminal_size(tty_out.fileno())
            bar_width = sum(display_width(chip(text, style)) + 2 for text, style in buttons)
            widest = max([display_width(line) for line in lines] + [display_width(title), bar_width])
            width = max(24, min(cols - 4, widest + 4))
            body = max(1, min(len(lines), rows - 8))
            height = body + 6
            top = max(0, min(top, len(lines) - body))
            left, first = max(1, (cols - width) // 2 + 1), max(1, (rows - height) // 2 + 1)

            def row(content):
                return (f"{C['accent']}│{R} {content}{R}"
                        f"{' ' * max(0, width - 4 - display_width(content))} {C['accent']}│{R}")

            hints = []
            if len(lines) > body:
                hints.append(hint("↑↓", f"scroll  {min(top + body, len(lines))}/{len(lines)}", sep=" "))
            bar = action_bar(hints, [chip(text, style) for text, style in buttons],
                             width - 4, indent=0, sep=" ")
            drawn = [f"{C['accent']}╭{'─' * (width - 2)}╮{R}",
                     row(f"{C['text']}{B}{title}{R}"),
                     row(f"{C['dim']}{'─' * (width - 4)}{R}")]
            for i in range(body):
                drawn.append(row(truncate(lines[top + i], width - 4) if top + i < len(lines) else ""))
            drawn += [row(""), row(bar), f"{C['accent']}╰{'─' * (width - 2)}╯{R}"]
            buf = [f"\033[{first + n};{left}H{line}" for n, line in enumerate(drawn)]
            tty_out.write("".join(buf))
            tty_out.flush()
            key = read_key(tty_in)
            if any_key or key in (b"q", b"\033", b"\x04", b"\x03"):
                break
            if key in (b"\r", b"\n"):
                if on_enter:
                    replacement = on_enter()
                    if replacement is not None:
                        lines = replacement
                        continue
                break
            if key in (b"j", b"\033[B"):
                top += 1
            elif key in (b"k", b"\033[A"):
                top -= 1
            elif key in (b"G", b"\033[F"):
                top = len(lines)
            elif key in (b"g", b"\033[H"):
                top = 0
            elif key == b" ":
                top += body
    finally:
        termios.tcsetattr(tty_in.fileno(), termios.TCSADRAIN, old)
        tty_out.write("\0338\033[?25h")
        tty_out.write("\033[2J\033[H" if standalone else "\033[?1049h")
        tty_out.flush()


def help_lines():
    out = []
    for k in KEYS:
        out.append(f"{C['accent']}{k['key']:<8}{R}{C['text']}{k['help']}{R}")
    out += ["",
            f"{C['dim']}Typing filters on whole words. Prefix a word with ' to match it{R}",
            f"{C['dim']}fuzzily, ! to exclude it, ^ or $ to anchor it.{R}",
            "",
            f"{C['dim']}Every chip in the header and the footer is clickable, and the{R}",
            f"{C['dim']}scope, sort, time and resume flags are kept between runs.{R}"]
    return out


def fzf_version():
    try:
        out = subprocess.run(["fzf", "--version"], capture_output=True, text=True, timeout=5).stdout
        return tuple(int(x) for x in re.match(r"(\d+)\.(\d+)\.(\d+)", out.strip()).groups())
    except Exception:
        return None


def diagnose_lines():
    out = []

    def section(text):
        out.append(f"{C['accent']}{B}{text}{R}")

    def row(key, value, color=""):
        out.append(f"  {C['dim']}{key:<24}{R}{color}{value}{R}")

    cleanup = None
    try:
        with open(SETTINGS) as fh:
            cleanup = json.load(fh).get("cleanupPeriodDays")
    except Exception:
        pass
    effective = 30 if cleanup is None else cleanup

    now = time.time()
    every = transcript_files()
    ages = sorted((now - f[0]) / 86400 for f in every)
    claude_ages = [(now - f[0]) / 86400 for f in every if f[2].startswith("claude:")]
    at_risk = sum(1 for a in claude_ages if a > effective)
    soon = sum(1 for a in claude_ages if effective - 7 < a <= effective)

    entries, index_error = {}, None
    try:
        entries = read_index()
    except FileNotFoundError:
        index_error = ("not built yet", C["warn"] + B)
    except Exception as exc:
        index_error = (f"{short_path(INDEX)} is unreadable ({type(exc).__name__})", C["red"] + B)

    section("Claude Code retention")
    if cleanup is None:
        row("cleanupPeriodDays", "not set  →  Claude Code defaults to 30 days", C["red"] + B)
    else:
        row("cleanupPeriodDays", plural(cleanup, "day"), C["warn"] + B if cleanup <= 30 else "")
    row("settings file", short_path(SETTINGS))
    row("past the limit", f"{at_risk}  →  deleted on the next Claude Code start" if at_risk else "0",
        C["red"] + B if at_risk else "")
    if soon:
        row("expiring within 7 days", str(soon), C["warn"] + B)
    if cleanup is None or cleanup <= 30:
        out += ["", f"  {C['warn']}Claude Code deletes transcripts older than "
                    f"{plural(effective, 'day')} on start.{R}"]
    out.append("")

    section("Transcripts")
    for provider in PROVIDERS:
        row(f"{provider} sessions", str(sum(1 for f in every if f[2].startswith(provider + ":"))))
    row("directories", str(len({e["cwd"] for e in entries.values() if e.get("cwd")})) if entries else "not indexed yet")
    row("total size", human_size(sum(f[1] for f in every)))
    if ages:
        row("newest", relative(now - ages[0] * 86400))
        row("oldest", relative(now - ages[-1] * 86400))
    out.append("")

    section("Index")
    if index_error:
        row("cache", index_error[0], index_error[1])
    else:
        st = os.stat(INDEX)
        stale = sum(1 for f in every if (entries.get(f[2]) or {}).get("key") != f[4])
        row("cache", short_path(CACHE_DIR))
        row("indexed sessions", str(len(entries)))
        row("index size", human_size(st.st_size + sum(
            os.path.getsize(f) for f in glob.glob(os.path.join(TURNS_DIR, "*.json")))))
        row("last rebuilt", relative(st.st_mtime))
        row("stale entries", str(stale), C["warn"] + B if stale else "")
        skipped = [f[3] for f in every if f[2] not in entries and f[0] <= st.st_mtime]
        if skipped:
            row("skipped", f"{len(skipped)}  →  unreadable, left out of the list", C["red"] + B)
            for path in skipped[:3]:
                row("", os.path.basename(path), C["dim"])
            if len(skipped) > 3:
                row("", f"and {len(skipped) - 3} more", C["dim"])
    out.append("")

    section("Resume flags")
    settings = read_settings()
    row("config file", short_path(CONFIG))
    for key, label, _, danger in FLAGS:
        state = "on" if settings[key] else "off"
        row(label, state, (C["warn"] if danger else C["accent"]) + B if settings[key] else "")
    for provider, spec in PROVIDERS.items():
        row(f"{provider} resume", " ".join(resume_command(spec, "<id>", settings)))
    out.append("")

    section("Tools")
    for tool in (*PROVIDERS, "fzf", "python3"):
        path = shutil.which(tool)
        row(tool, path or "not found", "" if path else C["red"] + B)
    version = fzf_version()
    if version:
        ok = version >= MIN_FZF
        row("fzf version", ".".join(str(v) for v in version) +
            ("" if ok else f"  →  needs {'.'.join(str(v) for v in MIN_FZF)} or newer"),
            "" if ok else C["red"] + B)
    exe = herdr_bin()
    row("herdr", exe or "not found (transcripts resumes in place)", "" if exe else C["dim"])
    if exe:
        agents = live_agents()
        if HERDR_ERROR:
            row("herdr status", HERDR_ERROR[0], C["red"] + B)
        else:
            row("live agents", str(len(agents)))
    return out


def apply_retention():
    data = {}
    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS) as fh:
                data = json.load(fh)
        except Exception:
            return [f"{C['red']}{short_path(SETTINGS)} is not valid JSON, so it was left alone.{R}",
                    f"{C['dim']}Fix the file by hand and press enter again.{R}", ""] + diagnose_lines()
    if not isinstance(data, dict):
        return [f"{C['red']}{short_path(SETTINGS)} does not hold an object, so it was left alone.{R}",
                ""] + diagnose_lines()
    data["cleanupPeriodDays"] = RETENTION_TARGET
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    write_json(SETTINGS, data, indent=2)
    return diagnose_lines()


def retention_needed():
    try:
        with open(SETTINGS) as fh:
            value = json.load(fh).get("cleanupPeriodDays")
    except Exception:
        value = None
    return value is None or value <= 30


def resume(cwd, uid):
    if ":" not in uid:
        uid = "claude:" + uid
    provider, _, sid = uid.partition(":")
    settings = read_settings()
    command = resume_command(PROVIDERS[provider], sid, settings)
    SNAPSHOT.clear()
    live = live_agents()
    exe = herdr_bin()

    if os.environ.get("TRANSCRIPTS_DRY_RUN"):
        plan = "focus" if uid in live else ("tab" if exe and os.environ.get("HERDR_PLUGIN_ROOT") else "exec")
        print(f"{plan} {uid} {cwd} {' '.join(command)}".strip())
        return

    if exe and uid in live:
        agent = live[uid]
        where = agent.get("terminal_title_stripped") or agent.get("pane_id", "")
        show_status(f"already running in this Herdr session  ·  {where}")
        if not run_herdr(exe, "agent", "focus", agent["pane_id"]):
            show_message("Herdr would not switch to it", [
                f"{C['red']}That session runs in pane {agent['pane_id']}, and focusing it failed.{R}", "",
                f"{C['dim']}Switch to it yourself, or close it and resume from here again.{R}"])
        return

    if not shutil.which(command[0]):
        show_message(f"{command[0]} not found", [
            f"{C['red']}The {command[0]} command is not on PATH, so this session cannot be resumed.{R}", "",
            f"{C['dim']}Install it, then try again:{R}",
            f"  {C['text']}{PROVIDERS[provider]['install']}{R}"])
        return

    if not os.path.isdir(cwd):
        show_message("session directory missing", [
            f"{C['red']}The session was recorded in{R}",
            f"  {C['dim']}{short_path(cwd)}{R}",
            f"{C['red']}and that directory no longer exists.{R}", "",
            f"{C['dim']}{command[0]} resumes a session in its own directory, so restore or{R}",
            f"{C['dim']}recreate it — a git worktree usually explains a path that vanished.{R}"])
        return

    quoted = " ".join(shlex.quote(part) for part in command)
    if exe and os.environ.get("HERDR_PLUGIN_ROOT"):
        label = os.path.basename(cwd.rstrip("/")) or command[0]
        created = herdr_call("tab", "create", "--cwd", cwd, "--label", label, "--no-focus")
        pane = ((created or {}).get("root_pane") or {}).get("pane_id")
        tab = ((created or {}).get("tab") or {}).get("tab_id")
        if pane:
            name = re.sub(r"[^a-z0-9_-]", "", f"{provider}-{sid.lower()}")[:26] + f"-{os.getpid() % 100000}"
            try:
                subprocess.Popen([exe, "agent", "start", name, "--kind", provider, "--pane", pane,
                                  "--", *command[1:]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                run_herdr(exe, "pane", "send-text", pane, quoted + "\n")
            if not (tab and run_herdr(exe, "tab", "focus", tab)):
                run_herdr(exe, "agent", "focus", pane)
            return
        show_message("Herdr would not open a tab", [
            f"{C['red']}Creating a tab for {short_path(cwd)} failed.{R}", "",
            f"{C['dim']}Resume it yourself with:{R}",
            f"  {C['text']}cd {shlex.quote(cwd)} && {quoted}{R}"])
        return

    os.chdir(cwd)
    os.execvp(command[0], command)


def show_status(text):
    try:
        with open("/dev/tty", "w", encoding="utf-8", errors="replace") as tty_out:
            tty_out.write(f"\033[2J\033[H\n  {C['accent']}{text}{R}\n")
    except OSError:
        pass


def show_message(title, lines):
    overlay(title, lines, standalone=True, any_key=True, buttons=[("press any key", "on_muted")])


def check_requirements():
    version = fzf_version()
    if version is None:
        show_message("fzf is missing", [
            f"{C['red']}The picker needs fzf {'.'.join(str(v) for v in MIN_FZF)} or newer.{R}", "",
            f"{C['dim']}Install it with your package manager:{R}",
            f"  {C['text']}brew install fzf{R}",
            f"  {C['text']}pacman -S fzf{R}", "",
            f"{C['dim']}Distribution packages are often older than {'.'.join(str(v) for v in MIN_FZF)}.{R}",
            f"{C['dim']}Releases: https://github.com/junegunn/fzf/releases{R}"])
        return False
    if version < MIN_FZF:
        have = ".".join(str(v) for v in version)
        need = ".".join(str(v) for v in MIN_FZF)
        show_message("fzf is too old", [
            f"{C['red']}fzf {have} is installed, the picker needs {need} or newer.{R}", "",
            f"{C['dim']}The picker draws its list with --gutter, which arrived in fzf {need}.{R}", "",
            f"{C['dim']}Upgrade: https://github.com/junegunn/fzf/releases{R}"])
        return False
    if not transcript_files():
        show_message("No sessions yet", [
            f"{C['text']}No transcripts were found in{R}",
            *[f"  {C['dim']}{short_path(spec['root'])}{R}" for spec in PROVIDERS.values()], "",
            f"{C['dim']}Each tool writes one file per session there. Start a session{R}",
            f"{C['dim']}with {' or '.join(f'`{p}`' for p in PROVIDERS)}, and it will show up here.{R}", "",
            f"{C['dim']}Claude Code deletes transcripts older than cleanupPeriodDays{R}",
            f"{C['dim']}(30 by default) on every start. Set it in {short_path(SETTINGS)}{R}",
            f"{C['dim']}to keep them for longer.{R}"])
        return False
    return True


def picker():
    if not check_requirements():
        return
    settings = read_settings()
    if settings["cwd_only"]:
        settings["cwd_only"] = False
        write_settings(settings)
    width, height = term_size()
    os.environ["TRANSCRIPTS_SNAPSHOT"] = "1"
    sys.stdout.write("\033]0;transcripts\007")
    sys.stdout.flush()

    cmd = ["fzf", "--read0", "--ansi", "--exact", "--no-sort", "--height=-1",
           "--delimiter=\t", f"--nth={scope_nth(settings['scope'])}", "--accept-nth=5..",
           "--no-hscroll", "--ellipsis= ", "--layout=reverse", "--highlight-line",
           "--gap=1", "--gap-line=", "--pointer= ", "--marker= ", "--gutter= ",
           "--tabstop=1", "--info=inline-right", "--prompt=Search  ", "--ghost=type to search",
           "--header-first", f"--header=  {C['dim']}reading transcripts…{R}\n ",
           "--footer=" + footer(settings, width),
           "--preview", f"{Q} preview {{-1}} {{q}}",
           f"--preview-window=down:{preview_rows(height)}:wrap:border-top"
           + (":hidden" if height < 18 else ""),
           "--preview-label= transcript ", "--preview-label-pos=3",
           "--color=" + fzf_colors(),
           "--bind", f"start:reload({Q} list)",
           "--bind", f"load:{refresh_bars()}",
           "--bind", f"click-header:transform({Q} click header)",
           "--bind", f"click-footer:transform({Q} click footer)"]
    for bind in bindings():
        cmd += ["--bind", bind]

    done = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    selected = done.stdout.strip("\n")
    if not selected:
        return
    cwd, _, uid = selected.rpartition("\t")
    resume(cwd, uid)


def fzf_colors():
    t = theme()
    return ",".join([
        f"fg:{t['text']}", f"bg:{t['bg']}", f"fg+:{t['accent']}", f"bg+:{t['row']}",
        f"hl:{t['accent']}", f"hl+:{t['accent']}", f"pointer:{t['accent']}", f"prompt:{t['accent']}",
        f"header:{t['dim']}", f"footer:{t['dim']}", f"info:{t['dim']}", f"border:{t['dim']}",
        f"separator:{t['dim']}", f"label:{t['dim']}", f"ghost:{t['dim']}",
        f"spinner:{t['accent']}", f"marker:{t['accent']}", "gutter:-1", f"preview-fg:{t['text']}",
    ])


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if not argv:
        picker()
        return
    command, rest = argv[0], argv[1:]
    if command == "list":
        list_rows()
    elif command == "warm":
        for _ in index_entries(transcript_files()):
            pass
    elif command == "preview":
        preview(rest[0], rest[1] if len(rest) > 1 else "")
    elif command == "header":
        print(header())
    elif command == "footer":
        print(footer())
    elif command == "apply":
        print(apply_setting(rest[0]))
    elif command == "click":
        print(click_action(rest[0]))
    elif command == "overlay" and rest[:1] == ["help"]:
        overlay("keys", help_lines(), buttons=[("esc close", "on_muted")])
    elif command == "overlay" and rest[:1] == ["diagnostics"]:
        fix = [(f"↵ keep {RETENTION_TARGET} days", "on_accent")] if retention_needed() else []
        overlay("diagnostics", diagnose_lines(), buttons=fix + [("esc close", "on_muted")],
                on_enter=lambda: apply_retention() if retention_needed() else None)
    elif command == "resume":
        resume(rest[0], rest[1])
    else:
        print(f"usage: {os.path.basename(SELF)} [list|warm|preview|header|footer|apply|click|overlay|resume]",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    owns_the_screen = not sys.argv[1:] or sys.argv[1] in ("resume", "overlay")
    try:
        sys.exit(main(sys.argv[1:]) or 0)
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
    except Exception as exc:
        if owns_the_screen:
            show_message("the picker failed", [
                f"{C['red']}{type(exc).__name__}: {exc}{R}", "",
                f"{C['dim']}Run {os.path.basename(SELF)} from a shell for the full trace.{R}"])
        raise
