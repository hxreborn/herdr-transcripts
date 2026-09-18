#!/usr/bin/env python3

import codecs
import fcntl
import json
import os
import pty
import select
import shutil
import struct
import subprocess
import termios
import time

import fixtures

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)

THEME = """onboarding = false

[theme]
name = "dracula"

[theme.custom]
accent = "#ffb2ba"
panel_bg = "reset"
active_row_bg = "#1e1e1e"
overlay0 = "#8b8b8b"
text = "#e2e2e2"
red = "#ffb4ab"
peach = "#ffdeab"
"""

FAKE_HERDR = '''#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
with open(os.environ["FAKE_HERDR_LOG"], "a") as fh:
    fh.write(" ".join(args) + "\\n")
if args[:2] == ["api", "snapshot"]:
    with open(os.environ["FAKE_HERDR_SNAPSHOT"]) as fh:
        print(json.dumps({"result": {"snapshot": json.load(fh)}}))
elif args[:2] == ["tab", "create"]:
    print(json.dumps({"result": {"root_pane": {"pane_id": "w1:p9", "tab_id": "w1:t9"},
                                 "tab": {"tab_id": "w1:t9"}}}))
else:
    print(json.dumps({"result": {"type": "ok"}}))
'''


class Sandbox:
    def __init__(self, root, live=(), context_cwd=None):
        self.root = root
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root)
        self.sessions = fixtures.build(root)
        self.codex = fixtures.build_codex(root)
        os.makedirs(os.path.join(root, ".config", "herdr"), exist_ok=True)
        with open(os.path.join(root, ".config", "herdr", "config.toml"), "w") as fh:
            fh.write(THEME)
        os.makedirs(os.path.join(root, "bin"), exist_ok=True)
        self.herdr = os.path.join(root, "bin", "herdr")
        with open(self.herdr, "w") as fh:
            fh.write(FAKE_HERDR)
        os.chmod(self.herdr, 0o755)
        for provider in ("claude", "codex"):
            stub = os.path.join(root, "bin", provider)
            with open(stub, "w") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(stub, 0o755)
        self.log = os.path.join(root, "herdr.log")
        self.snapshot_file = os.path.join(root, "snapshot.json")
        self.context_cwd = context_cwd or self.sessions[0][1]
        self.set_live(live)

    def set_live(self, live):
        agents = []
        for index, status, *provider in live:
            provider = provider[0] if provider else "claude"
            sid, cwd = (self.codex if provider == "codex" else self.sessions)[index]
            agents.append({"agent": provider, "agent_session": {"value": sid}, "agent_status": status,
                           "cwd": cwd, "pane_id": f"w1:p{index}", "tab_id": f"w1:t{index}",
                           "terminal_title_stripped": f"tab {index + 1}"})
        with open(self.snapshot_file, "w") as fh:
            json.dump({"agents": agents, "focused_pane_id": "w1:p0",
                       "panes": [{"pane_id": "w1:p0", "cwd": self.context_cwd}]}, fh)

    def env(self, plugin=True, herdr=True, **extra):
        env = dict(os.environ)
        env.update({
            "HOME": self.root,
            "TERM": "xterm-256color",
            "PATH": os.path.join(self.root, "bin") + ":" + os.environ["PATH"],
            "FAKE_HERDR_LOG": self.log,
            "FAKE_HERDR_SNAPSHOT": self.snapshot_file,
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"focused_pane_cwd": self.context_cwd}),
            "LINES": "", "COLUMNS": "",
        })
        env.pop("HERDR_BIN_PATH", None)
        env.pop("HERDR_PLUGIN_CONFIG_DIR", None)
        env.pop("NO_COLOR", None)
        if plugin:
            env["HERDR_PLUGIN_ROOT"] = PLUGIN
        if herdr:
            env["HERDR_BIN_PATH"] = self.herdr
        env.update(extra)
        return env

    def calls(self):
        try:
            with open(self.log) as fh:
                return [line.strip() for line in fh if line.strip()]
        except OSError:
            return []

    def clear_calls(self):
        try:
            os.remove(self.log)
        except OSError:
            pass


class Term:
    def __init__(self, argv, env, cols=100, rows=30):
        self.cols, self.rows = cols, rows
        import pyte

        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        def attach():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                     env=env, cwd=env["HOME"], preexec_fn=attach)
        os.close(slave)

    def wait_idle(self, quiet=1.0, limit=40.0):
        deadline = time.time() + limit
        last, seen = time.time(), False
        while time.time() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                try:
                    data = os.read(self.master, 65536)
                except OSError:
                    break
                if not data:
                    break
                self.feed(data)
                last, seen = time.time(), True
            elif seen and time.time() - last > quiet:
                break
            elif self.proc.poll() is not None and time.time() - last > quiet:
                break
        return self

    def feed(self, data):
        text = self.decoder.decode(data)
        self.stream.feed(text)
        for query, answer in (("\033[6n", f"\033[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R"),
                              ("\033[?2004$p", "\033[?2004;2$y")):
            for _ in range(text.count(query)):
                os.write(self.master, answer.encode())

    def send(self, keys, wait=True):
        os.write(self.master, keys.encode())
        if wait:
            self.wait_idle()
        return self

    def click_at(self, col, row):
        os.write(self.master, f"\033[<0;{col + 1};{row + 1}M".encode())
        time.sleep(0.05)
        os.write(self.master, f"\033[<0;{col + 1};{row + 1}m".encode())
        return self.wait_idle()

    def find(self, needle, after=0):
        for y, line in enumerate(self.screen.display):
            x = line.find(needle, after)
            if x >= 0:
                return x, y
        raise AssertionError(f"{needle!r} is not on screen")

    def click(self, needle, into=1, after=0):
        x, y = self.find(needle, after)
        return self.click_at(x + into, y)

    def cell(self, needle):
        x, y = self.find(needle)
        return self.screen.buffer[y][x]

    def text(self):
        return "\n".join(line.rstrip() for line in self.screen.display)

    def show(self, label=""):
        bar = f"── {label} ({self.cols}x{self.rows}) " if label else f"── ({self.cols}x{self.rows}) "
        print(bar + "─" * max(0, 100 - len(bar)))
        for line in self.screen.display:
            print("|" + line.rstrip())
        print("─" * 100)
        return self

    def close(self):
        try:
            self.proc.kill()
            self.proc.wait(timeout=3)
        except Exception:
            pass
        try:
            os.close(self.master)
        except OSError:
            pass

    def wait(self, timeout=10):
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None


def run(sandbox, cols=100, rows=30, args=(), **env_extra):
    return Term([os.path.join(PLUGIN, "transcripts"), *args],
                sandbox.env(**env_extra), cols=cols, rows=rows)


def sandbox(name="box", **kw):
    root = os.path.join(os.environ.get("TRANSCRIPTS_TEST_DIR", "/tmp/transcripts-tests"), name)
    return Sandbox(root, **kw)
