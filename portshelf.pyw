# SPDX-License-Identifier: GPL-3.0-or-later
# PortShelf - local dev servers at a glance, in the Windows tray.
# Copyright (C) 2026 Charlie Pearson
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program (LICENSE). If not, see <https://www.gnu.org/licenses/>.
"""PortShelf: a tiny Windows tray app + desktop widget listing local dev servers.

Left-click the tray icon to open the panel. Click a row to open it in the
browser, right-click for more. "History" lists projects that ran before;
click one to restart it on a free port.

    pythonw portshelf.pyw          run the tray app
    python  portshelf.pyw --list   print what it detects and exit (add --all for system ports)
"""
import ctypes
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from ctypes import wintypes
from pathlib import Path

import psutil

APP = "PortShelf"
VERSION = "1.0.0"
COPYRIGHT = "Copyright (C) 2026 Charlie Pearson"
HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP
HISTORY_FILE = DATA_DIR / "history.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
SHOW_FLAG = DATA_DIR / "show.flag"
SCAN_SECONDS = 3
START_TIMEOUT = 90
HISTORY_LIMIT = 60

DEFAULT_SETTINGS = {
    "pinned": False,
    "on_top": False,
    "opacity": 0.9,
    "show_all": False,
    "history_open": False,
    "pin_xy": None,
}

# Executables that are dev servers even though they live in Program Files / AppData.
RUNTIMES = {
    "node", "python", "pythonw", "py", "php", "php-cgi", "bun", "deno", "ruby", "java", "javaw",
    "dotnet", "go", "hugo", "caddy", "nginx", "httpd", "uvicorn", "gunicorn", "flask", "streamlit",
    "jupyter", "jupyter-lab", "jekyll", "rails", "puma", "air", "workerd", "esbuild", "miniserve",
}
GENERIC_LEAVES = {"demo", "dist", "build", "public", "site", "www", "src", "docs", "out", "static", "html"}
GENERIC_PKG_NAMES = {"my-app", "vite-project", "app", "project", "frontend", "web", "client", "server", "website", "site"}


def _nm(name):
    """Regex for a node CLI, either from node_modules or as a bare command."""
    return rf"node_modules[\\/](\.bin[\\/])?{name}\b|(^|\s){name}(\.cmd)?(\s|$)"


# Checked in order against the lower-cased command lines of the process and its launchers.
KINDS = [
    (r"-m\s+http\.server", "Python server"),
    (r"\bphp(\.exe)?\"?\s.*-s\s", "PHP server"),
    (r"manage\.py\"?\s+runserver", "Django"),
    (r"\buvicorn\b", "Uvicorn"),
    (r"\bgunicorn\b", "Gunicorn"),
    (r"\bflask\b", "Flask"),
    (r"\bstreamlit\b", "Streamlit"),
    (r"\bjupyter", "Jupyter"),
    (_nm("vite"), "Vite"),
    (_nm("next"), "Next.js"),
    (_nm("astro"), "Astro"),
    (_nm("nuxi?"), "Nuxt"),
    (_nm("react-scripts"), "Create React App"),
    (r"node_modules[\\/]@angular[\\/]cli|(^|\s)ng\s+serve", "Angular"),
    (_nm("vue-cli-service"), "Vue CLI"),
    (_nm("webpack(-dev-server|-cli)?"), "Webpack"),
    (_nm("gatsby"), "Gatsby"),
    (_nm("docusaurus"), "Docusaurus"),
    (r"@11ty[\\/]eleventy|" + _nm("eleventy"), "Eleventy"),
    (_nm("parcel"), "Parcel"),
    (_nm("remix"), "Remix"),
    (_nm("wrangler") + r"|\bworkerd\b", "Wrangler"),
    (_nm("live-server"), "live-server"),
    (_nm("http-server"), "http-server"),
    (_nm("serve"), "serve"),
    (r"\bhugo\b", "Hugo"),
    (r"\bjekyll\b", "Jekyll"),
    (r"\brails\s+s(erver)?\b", "Rails"),
]
# Tools that accept --port=N, so a restart can be pointed at a free port.
FLAG_KINDS = {"Vite", "Next.js", "Astro", "Nuxt", "Angular", "Vue CLI", "Webpack", "Gatsby", "Docusaurus",
              "Eleventy", "Parcel", "Remix", "live-server", "http-server"}
FLAG_TOOLS = re.compile(r"\b(vite|astro|next|nuxi?|webpack(-dev-server)?|http-server|live-server|parcel|ng|"
                        r"vue-cli-service|gatsby|docusaurus|eleventy|remix)\b")


def _env_dirs(*names):
    return [os.path.normcase(os.environ[n]) for n in names if os.environ.get(n)]


CWD_SKIP = _env_dirs("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
EXE_SKIP = CWD_SKIP + _env_dirs("LOCALAPPDATA", "APPDATA")


def under(path, roots):
    p = os.path.normcase(path or "")
    return any(p == r or p.startswith(r + os.sep) for r in roots)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- detection

def stem(name):
    return Path(name or "").stem.lower()


def read_package(folder):
    return load_json(Path(folder) / "package.json", {})


def manifest_name(folder):
    folder = Path(folder)
    name = read_package(folder).get("name")
    if not name:
        try:
            m = re.search(r'^name\s*=\s*"([^"]+)"', (folder / "pyproject.toml").read_text(encoding="utf-8"), re.M)
            name = m and m.group(1)
        except OSError:
            pass
    if not name:
        name = load_json(folder / "composer.json", {}).get("name")
    if name and name.lower() not in GENERIC_PKG_NAMES:
        return name
    return None


def project_name(folder):
    """Readable name for a folder: its manifest name, else the folder name."""
    p = Path(folder)
    suffix = ""
    if p.name.lower() in GENERIC_LEAVES and p.parent != p:
        suffix, p = " / " + p.name, p.parent
    worktree = False
    if p.parent.name.lower() == "worktrees" and p.parent.parent.name.lower() == ".claude":
        worktree = True
        base = p.parent.parent.parent
        name = manifest_name(p) or base.name
    else:
        name = manifest_name(p) or p.name
    return name + suffix, worktree


def served_dir(cwd, cmd):
    """Folder a static server is serving, when it's given explicitly."""
    joined = " ".join(cmd).lower()
    if "http.server" in joined:
        flags = ("--directory", "-d")
    elif cmd and stem(cmd[0]).startswith("php"):
        flags = ("-t",)
    else:
        return None
    for i, a in enumerate(cmd):
        if a in flags and i + 1 < len(cmd):
            return os.path.normpath(os.path.join(cwd, cmd[i + 1]))
        if a.startswith("--directory="):
            return os.path.normpath(os.path.join(cwd, a.split("=", 1)[1]))
    return None


def find_launcher(proc, cwd):
    """Walk up from the listening process to the command the user actually ran
    (e.g. `npm run dev` rather than node_modules/vite/bin/vite.js)."""
    chain = [proc]
    cur = proc
    for _ in range(6):
        try:
            par = cur.parent()
            if par is None:
                break
            pstem = stem(par.name())
            if os.path.normcase(par.cwd()) != os.path.normcase(cwd):
                break
            pcmd = par.cmdline()
        except (psutil.Error, OSError):
            break
        if "claude" in " ".join(pcmd).lower():
            break
        if pstem == "cmd":
            if "/c" not in (a.lower() for a in pcmd):
                break  # an interactive shell, not a script runner
        elif pstem not in RUNTIMES:
            break
        chain.append(par)
        cur = par
    root = next((p for p in reversed(chain) if safe_stem(p) != "cmd"), proc)
    return root, chain


def safe_stem(p):
    try:
        return stem(p.name())
    except psutil.Error:
        return ""


def safe_cmdline(p):
    try:
        return p.cmdline()
    except (psutil.Error, OSError):
        return []


def tidy_cmd(cmd):
    """`node .../npm-cli.js run dev` -> `npm run dev`."""
    if len(cmd) > 1 and stem(cmd[0]) == "node":
        script = cmd[1].replace("\\", "/").lower()
        for pat, tool in (("npm-cli.js", "npm"), ("npx-cli.js", "npx"), ("/yarn", "yarn"), ("/pnpm", "pnpm")):
            if pat in script:
                return [tool] + cmd[2:]
    return list(cmd)


def detect_kind(joined, cmd):
    for pat, label in KINDS:
        if re.search(pat, joined):
            return label
    exe = stem(cmd[0]) if cmd else "?"
    script = next((Path(a).name for a in cmd[1:] if re.search(r"\.(m?js|cjs|ts|py|php|rb)$", a, re.I)), None)
    return f"{exe} · {script}" if script else exe


_cache = {}


def describe(pid, show_all):
    try:
        p = psutil.Process(pid)
        ck = (pid, p.create_time())
    except psutil.Error:
        return None
    if ck not in _cache:
        _cache[ck] = _describe(p)
    d = _cache[ck]
    if d is None or (not d["dev"] and not show_all):
        return None
    return d


def _describe(p):
    try:
        exe_name = p.name()
    except psutil.Error:
        return None
    try:
        exe = p.exe()
    except (psutil.Error, OSError):
        exe = ""
    try:
        cwd = p.cwd()
    except (psutil.Error, OSError):
        cwd = ""
    cmd = safe_cmdline(p)
    s = stem(exe_name)
    dev = bool(cwd) and not under(cwd, CWD_SKIP) and (s in RUNTIMES or (exe and not under(exe, EXE_SKIP)))
    d = {"pid": p.pid, "cwd": cwd, "cmd": cmd, "dev": dev}
    if not dev:
        d.update(name=Path(exe_name).stem, kind="system", folder=exe or "", worktree=False,
                 root_pid=p.pid, root_ctime=p.create_time(), launch_base=None)
        return d
    root, chain = find_launcher(p, cwd)
    try:
        root_ctime = root.create_time()
    except psutil.Error:
        root, root_ctime = p, p.create_time()
    joined = " || ".join(" ".join(safe_cmdline(q)) for q in chain).lower()
    folder = served_dir(cwd, cmd) or cwd
    name, worktree = project_name(folder)
    d.update(name=name, kind=detect_kind(joined, cmd), folder=folder, worktree=worktree,
             root_pid=root.pid, root_ctime=root_ctime, launch_base=tidy_cmd(safe_cmdline(root) or cmd))
    return d


# ---------------------------------------------------------------- relaunch commands

def templatize(cmd, port):
    """Swap the port number in a command for a {PORT} placeholder."""
    p = str(port)
    out, hit = [], False
    for a in cmd:
        if a == p:
            out.append("{PORT}")
            hit = True
        elif re.search(rf"(?<=[:=]){p}$", a):
            out.append(re.sub(rf"(?<=[:=]){p}$", "{PORT}", a))
            hit = True
        else:
            out.append(a)
    return out, hit


def port_args(cmd, cwd, kind):
    """Extra args that make a restart listen on {PORT}, or [] to rely on the PORT env var."""
    tool = stem(cmd[0])
    if tool in ("npm", "yarn", "pnpm", "bun") and len(cmd) >= 2:
        script_name = cmd[2] if cmd[1] == "run" and len(cmd) > 2 else cmd[1]
        script = (read_package(cwd).get("scripts") or {}).get(script_name, "")
        if not script or re.search(r"concurrently|npm-run-all|run-[ps]\b|&&|\|\||;|--port|\s-p\s", script):
            return []
        if FLAG_TOOLS.search(script.lower()) and "react-scripts" not in script:
            return (["--"] if tool == "npm" else []) + ["--port={PORT}"]
        return []
    joined = " ".join(cmd).lower()
    if "http.server" in joined or re.search(r"manage\.py\"?\s+runserver", joined):
        return ["{PORT}"]
    if kind in ("Uvicorn", "Flask"):
        return ["--port", "{PORT}"]
    if kind == "Streamlit":
        return ["--server.port={PORT}"]
    if kind in FLAG_KINDS:
        return ["--port={PORT}"]
    return []


def make_launch(base, port, cwd, kind):
    tmpl, hit = templatize(base, port)
    if hit:
        return tmpl, "args"
    extra = port_args(base, cwd, kind)
    return (tmpl + extra, "args") if extra else (tmpl, "env")


def key_of(cwd, launch):
    """Identity of a project in history: folder + command without its port bits."""
    toks = []
    for t in launch:
        if "{PORT}" in t:
            if toks and toks[-1] in ("--port", "-p", "--listen", "-l"):
                toks.pop()
            continue
        toks.append(t)
    while toks and toks[-1] == "--":
        toks.pop()
    if toks:
        toks[0] = stem(toks[0])
    return os.path.normcase(cwd) + "|" + " ".join(toks).lower()


def join_cmd(args):
    return " ".join(f'"{a}"' if re.search(r'[\s&|<>^()]', a) else a for a in args)


def port_free(port):
    for fam, addr in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::")):
        try:
            s = socket.socket(fam, socket.SOCK_STREAM)
        except OSError:
            continue
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind((addr, port))
        except OSError:
            return False
        finally:
            s.close()
    return True


def free_port(preferred, busy):
    start = preferred if 1024 <= (preferred or 0) < 65000 else 3000
    for p in range(start, min(start + 200, 65535)):
        if p not in busy and port_free(p):
            return p
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def url_host(ip):
    if ip in ("", "0.0.0.0", "::", "127.0.0.1", "::1"):
        return "localhost"
    return f"[{ip}]" if ":" in ip else ip


# ---------------------------------------------------------------- scanning

def scan(show_all):
    try:
        conns = psutil.net_connections("tcp")
    except psutil.Error:
        return [], set()
    listen = [c for c in conns if c.status == psutil.CONN_LISTEN and c.laddr and c.pid]
    busy = {c.laddr.port for c in listen}
    groups = {}
    for c in listen:
        d = describe(c.pid, show_all)
        if not d:
            continue
        gk = d["root_pid"] if d["dev"] else ("sys", c.pid)
        g = groups.setdefault(gk, {"d": d, "ports": {}})
        g["ports"].setdefault(c.laddr.port, c.laddr.ip)

    out = []
    for g in groups.values():
        d, ports = g["d"], sorted(g["ports"])
        named = [p for p in ports if templatize(d["launch_base"] or d["cmd"], p)[1]]
        normal = [p for p in ports if p < 49152]
        primary = (named or normal or ports)[0]
        item = {
            "name": d["name"], "kind": d["kind"], "folder": d["folder"], "cwd": d["cwd"],
            "worktree": d["worktree"], "dev": d["dev"], "root_pid": d["root_pid"],
            "root_ctime": d["root_ctime"], "pid": d["pid"],
            "port": primary, "extra": [p for p in ports if p != primary],
            "url": f"http://{url_host(g['ports'][primary])}:{primary}/", "key": None,
        }
        if d["dev"] and d["launch_base"]:
            launch, mode = make_launch(d["launch_base"], primary, d["cwd"], d["kind"])
            item.update(launch=launch, mode=mode, key=key_of(d["cwd"], mask_args(launch)))
        out.append(item)

    live = {(c.pid) for c in listen}
    for ck in [k for k in _cache if k[0] not in live]:
        del _cache[ck]
    out.sort(key=lambda i: (not i["dev"], i["port"]))
    return out, busy


# ---------------------------------------------------------------- windows bits

user32 = ctypes.windll.user32
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def work_area():
    r = RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r


def autostart_command():
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    return f'"{exe}" "{Path(__file__).resolve()}"'


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


def kill_tree(pid, created):
    """Stop a process and its children. Does nothing (returns False) if the pid now
    belongs to a different process than the one we saw, i.e. Windows reused it."""
    try:
        p = psutil.Process(pid)
        if abs(p.create_time() - created) > 0.01:
            return False
        procs = p.children(recursive=True) + [p]
    except psutil.Error:
        return False
    for q in procs:
        try:
            q.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=3)
    for q in alive:
        try:
            q.kill()
        except psutil.Error:
            pass
    return True


# ---------------------------------------------------------------- secrets in saved commands
# Arguments that look like passwords or tokens are never written to history.json in
# plain text. They are encrypted with Windows DPAPI, which only the same Windows user
# account on the same machine can decrypt, and shown as ••• in the UI.

SECRET_NAME = re.compile(r"token|secret|passw(or)?d|pwd|api[-_]?key|auth|credential|private[-_]?key|access[-_]?key",
                         re.I)
SECRET_ASSIGN = re.compile(r"^\w*(token|secret|passw(or)?d|api[-_]?key)\w*=.", re.I)  # API_KEY=abc
URL_USERINFO = re.compile(r"^[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.I)  # https://user:pass@host
SECRET_PREFIX = "dpapi:"
MASK = "•••"


def secret_spans(args):
    """{index: offset} for args holding a secret; the secret is args[index][offset:]."""
    spans = {}
    for i, a in enumerate(args):
        if i == 0 or "{PORT}" in a or a.startswith(SECRET_PREFIX):
            continue
        if a.startswith("-") and "=" in a:
            name = a.split("=", 1)[0]
            if SECRET_NAME.search(name):
                spans[i] = len(name) + 1
        elif a.startswith("-") and SECRET_NAME.search(a):
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                spans[i + 1] = 0
        elif URL_USERINFO.search(a) or SECRET_ASSIGN.search(a):
            spans[i] = a.index("=") + 1 if SECRET_ASSIGN.search(a) else 0
    return spans


def mask_args(args):
    spans = secret_spans(args)
    out = []
    for i, a in enumerate(args):
        if a.startswith(SECRET_PREFIX):
            out.append(MASK)
        elif i in spans:
            out.append(a[:spans[i]] + MASK)
        else:
            out.append(a)
    return out


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, encrypt):
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):  # 0x1: no UI
        raise OSError(ctypes.get_last_error(), "Windows couldn't encrypt/decrypt a saved secret")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


_protected = {}


def protect_args(args):
    """Encrypt secret-looking args (the whole arg) for storage."""
    import base64
    spans = secret_spans(args)
    out = []
    for i, a in enumerate(args):
        if i in spans:
            if a not in _protected:  # cache so history.json doesn't churn every scan
                _protected[a] = SECRET_PREFIX + base64.b64encode(_dpapi(a.encode("utf-8"), True)).decode("ascii")
            a = _protected[a]
        out.append(a)
    return out


def reveal_args(args):
    import base64
    return [_dpapi(base64.b64decode(a[len(SECRET_PREFIX):]), False).decode("utf-8")
            if a.startswith(SECRET_PREFIX) else a for a in args]


def ago(ts):
    s = max(0, time.time() - ts)
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if s >= size:
            return f"{int(s // size)}{unit} ago"
    return "just now"


def short_path(p):
    home = str(Path.home())
    if p.lower().startswith(home.lower()):
        p = "~" + p[len(home):]
    return p


# ---------------------------------------------------------------- UI

BG = "#ffffff"
PANEL = "#ffffff"
HOVER = "#f5f6f8"
BORDER = "#e3e5e9"
CHIP = "#f1f2f4"
FG = "#1d1f23"
SOFT = "#4a505c"
MUTED = "#7a808c"
DIM = "#b3b8c1"
ACCENT = "#2563eb"
GREEN = "#22a35a"
AMBER = "#b7791f"
RED = "#d93a3a"

GLYPHS = {  # Segoe Fluent Icons / MDL2 code point, plain-text fallback
    "refresh": ("", "⟳"), "pin": ("", "pin"), "unpin": ("", "unpin"),
    "close": ("", "✕"), "right": ("", "▸"), "down": ("", "▾"), "play": ("", "▶"),
}


def fit(text, font, px):
    """Middle-ellipsize text to fit px, keeping more of the end (the folder name)."""
    if font.measure(text) <= px:
        return text
    n = len(text)
    while n > 6:
        n -= max(1, n // 12)
        head = n // 3
        t = text[:head] + "…" + text[len(text) - (n - head):]
        if font.measure(t) <= px:
            return t
    return text[:6] + "…"


class App:
    def __init__(self):
        import tkinter as tk
        from tkinter import font as tkfont, messagebox
        self.tk, self.messagebox = tk, messagebox

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.settings = {**DEFAULT_SETTINGS, **load_json(SETTINGS_FILE, {})}
        self.history = load_json(HISTORY_FILE, {})
        self.q = queue.Queue()
        self.wake = threading.Event()
        self.live, self.live_keys, self.busy, self.pending = [], set(), set(), {}
        self.visible = self.had_focus = False
        self.last_autohide = 0.0
        self.sig = None
        self.history_dirty = False
        self.last_save = time.time()
        self.tray_count = None
        self.rounded = False
        self.drag = None

        r = self.root = tk.Tk()
        r.withdraw()
        r.title(APP)
        r.overrideredirect(True)
        r.configure(bg=BORDER)
        self.scale = r.winfo_fpixels("1i") / 96
        self.W = int(440 * self.scale)
        fams = set(tkfont.families(r))
        icon_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), None)
        self.use_glyphs = icon_family is not None
        mono = "Cascadia Mono" if "Cascadia Mono" in fams else "Consolas"
        self.f_title = tkfont.Font(family="Segoe UI Semibold", size=11)
        self.f_bold = tkfont.Font(family="Segoe UI Semibold", size=10)
        self.f_text = tkfont.Font(family="Segoe UI", size=9)
        self.f_small = tkfont.Font(family="Segoe UI", size=8)
        self.f_mono = tkfont.Font(family=mono, size=10)
        self.f_icon = tkfont.Font(family=icon_family, size=10) if icon_family else self.f_text
        self.build()

        r.bind("<Escape>", lambda e: self.hide())
        r.bind_all("<MouseWheel>", self.on_wheel)
        self.make_tray()
        threading.Thread(target=self.scan_loop, daemon=True).start()
        if self.settings["pinned"]:
            self.root.after(300, self.show)
        self.root.after(200, self.pump)

    # ----- layout

    def glyph(self, name):
        g, fallback = GLYPHS[name]
        return g if self.use_glyphs else fallback

    def icon_button(self, parent, name, command, bg=BG):
        tk = self.tk
        b = tk.Label(parent, text=self.glyph(name), font=self.f_icon, fg=MUTED, bg=bg, padx=6, pady=2, cursor="hand2")
        b.bind("<Enter>", lambda e: b.config(fg=FG))
        b.bind("<Leave>", lambda e: b.config(fg=MUTED))
        b.bind("<Button-1>", lambda e: command())
        return b

    def build(self):
        tk = self.tk
        box = self.box = tk.Frame(self.root, bg=BG)
        box.pack(fill="both", expand=True, padx=1, pady=1)

        hdr = self.hdr = tk.Frame(box, bg=BG, cursor="fleur")
        hdr.pack(fill="x", padx=(14, 8), pady=(10, 6))
        self.title_lbl = tk.Label(hdr, text=APP, font=self.f_title, fg=FG, bg=BG)
        self.title_lbl.pack(side="left")
        self.count_lbl = tk.Label(hdr, text="", font=self.f_small, fg=MUTED, bg=BG)
        self.count_lbl.pack(side="left", padx=(8, 0), pady=(3, 0))
        self.icon_button(hdr, "close", self.close_clicked).pack(side="right")
        self.pin_btn = self.icon_button(hdr, "pin", self.toggle_pin)
        self.pin_btn.pack(side="right")
        self.icon_button(hdr, "refresh", self.wake.set).pack(side="right")
        for w in (hdr, self.title_lbl, self.count_lbl):
            w.bind("<ButtonPress-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)
            w.bind("<ButtonRelease-1>", self.drag_end)

        self.canvas = tk.Canvas(box, bg=PANEL, highlightthickness=0, bd=0, width=self.W)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=PANEL)
        self.canvas.create_window(0, 0, window=self.inner, anchor="nw", width=self.W)

        tk.Frame(box, bg=BORDER, height=1).pack(fill="x")
        foot = tk.Frame(box, bg=BG, cursor="hand2")
        foot.pack(fill="x")
        self.chev = tk.Label(foot, text="", font=self.f_icon, fg=MUTED, bg=BG)
        self.chev.pack(side="left", padx=(12, 4), pady=7)
        self.hist_lbl = tk.Label(foot, text="History", font=self.f_text, fg=MUTED, bg=BG)
        self.hist_lbl.pack(side="left", pady=7)
        self.hint_lbl = tk.Label(foot, text="", font=self.f_small, fg=DIM, bg=BG)
        self.hint_lbl.pack(side="right", padx=12)
        for w in (foot, self.chev, self.hist_lbl, self.hint_lbl):
            w.bind("<Button-1>", lambda e: self.toggle_history())
            w.bind("<Enter>", lambda e: self.hist_lbl.config(fg=FG))
            w.bind("<Leave>", lambda e: self.hist_lbl.config(fg=MUTED))

    def history_items(self):
        items = [dict(h, key=k) for k, h in self.history.items() if k not in self.live_keys]
        items.sort(key=lambda h: -h.get("last_seen", 0))
        return items[:25]

    def render(self, force=False):
        hist = self.history_items()
        sig = (
            tuple((g["root_pid"], g["port"], tuple(g["extra"]), g["name"], g["kind"]) for g in self.live),
            tuple(h["key"] for h in hist),
            tuple(sorted((k, bool(v.get("failed"))) for k, v in self.pending.items())),
            self.settings["history_open"], self.settings["pinned"], int(time.time() // 60),
        )
        if sig == self.sig and not force:
            return
        self.sig = sig
        for w in self.inner.winfo_children():
            w.destroy()

        n = len(self.live)
        self.count_lbl.config(text=f"{n} running" if n else "")
        if self.live:
            for g in self.live:
                self.running_row(g)
        else:
            self.message("Nothing running on localhost right now.")

        open_ = self.settings["history_open"]
        self.chev.config(text=self.glyph("down" if open_ else "right"))
        self.hist_lbl.config(text=f"Previously running  ({len(hist)})" if hist else "Previously running")
        self.hint_lbl.config(text="click to restart" if open_ and hist else "")
        if open_:
            tk = self.tk
            tk.Frame(self.inner, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(6, 2))
            if hist:
                for h in hist:
                    self.history_row(h)
            else:
                self.message("Servers you run will be remembered here.")
        self.pin_btn.config(text=self.glyph("unpin" if self.settings["pinned"] else "pin"))
        self.resize()

    def message(self, text):
        self.tk.Label(self.inner, text=text, font=self.f_text, fg=MUTED, bg=PANEL, pady=14).pack(fill="x")

    def row_frame(self, on_click, on_menu):
        tk = self.tk
        row = tk.Frame(self.inner, bg=PANEL, cursor="hand2")
        row.pack(fill="x", padx=6, pady=1)
        row.columnconfigure(1, weight=1)

        def wire():
            widgets = [row] + [w for w in all_children(row)]
            plain = [w for w in widgets if w.cget("bg") == PANEL]
            for w in widgets:
                w.bind("<Enter>", lambda e: [x.config(bg=HOVER) for x in plain])
                w.bind("<Leave>", lambda e: [x.config(bg=PANEL) for x in plain])
                w.bind("<Button-1>", lambda e: on_click())
                w.bind("<Button-3>", lambda e: on_menu(e))
        return row, wire

    def text_width(self, right_px):
        """Room left for the middle column once the dot and the right-hand column are placed."""
        return self.W - right_px - int(48 * self.scale)

    def title_line(self, row, name, kind, worktree, fg, avail):
        tk = self.tk
        s = self.scale
        line = tk.Frame(row, bg=PANEL)
        line.grid(row=0, column=1, sticky="w", pady=(7, 0))
        gap = int(17 * s)  # chip padding + spacing
        room = max(int(70 * s), avail - self.f_bold.measure(name) - gap)  # name gets priority
        if worktree and self.f_small.measure(kind + " · worktree") <= room:
            kind += " · worktree"
        kind = fit(kind, self.f_small, room)
        name = fit(name, self.f_bold, avail - self.f_small.measure(kind) - gap)
        tk.Label(line, text=name, font=self.f_bold, fg=fg, bg=PANEL).pack(side="left")
        tk.Label(line, text=kind, font=self.f_small, fg=MUTED, bg=CHIP, padx=5).pack(side="left", padx=(7, 0))

    def running_row(self, g):
        tk = self.tk
        row, wire = self.row_frame(lambda: webbrowser.open(g["url"]), lambda e: self.running_menu(e, g))
        tk.Label(row, text="●", font=self.f_small, fg=GREEN if g["dev"] else DIM, bg=PANEL).grid(
            row=0, column=0, rowspan=2, padx=(8, 8), sticky="n", pady=(9, 0))
        host = g["url"].split("//", 1)[1].rstrip("/")
        stop_w = self.f_small.measure("Stop") + int(20 * self.scale) if g["dev"] else 0
        avail = self.text_width(self.f_mono.measure(host) + int(16 * self.scale) + stop_w)
        self.title_line(row, g["name"], g["kind"], g["worktree"], FG, avail)
        sub = short_path(g["folder"]) or "—"
        tk.Label(row, text=fit(sub, self.f_small, avail), font=self.f_small, fg=MUTED, bg=PANEL,
                 anchor="w").grid(row=1, column=1, sticky="w", pady=(0, 7))
        right = tk.Frame(row, bg=PANEL)
        right.grid(row=0, column=2, rowspan=2, padx=(6, 2 if g["dev"] else 10), sticky="e")
        link = tk.Label(right, text=host, font=self.f_mono, fg=ACCENT, bg=PANEL, cursor="hand2")
        link.pack(anchor="e")
        if g["extra"]:
            more = ", ".join(f":{p}" for p in g["extra"][:3]) + ("…" if len(g["extra"]) > 3 else "")
            tk.Label(right, text="also " + more, font=self.f_small, fg=DIM, bg=PANEL).pack(anchor="e")
        stop = None
        if g["dev"]:
            stop = tk.Label(row, text="Stop", font=self.f_small, fg=MUTED, bg=PANEL, padx=6, cursor="hand2")
            stop.grid(row=0, column=3, rowspan=2, padx=(0, 6), sticky="e")
        wire()
        if stop:  # rebinding replaces the row's open-in-browser click for this button
            stop.bind("<Button-1>", lambda e: self.stop_server(g))
            stop.bind("<Enter>", lambda e: stop.config(fg=RED), add="+")
            stop.bind("<Leave>", lambda e: stop.config(fg=MUTED), add="+")
        link.bind("<Enter>", lambda e: link.config(font=self.f_mono_ul()), add="+")
        link.bind("<Leave>", lambda e: link.config(font=self.f_mono), add="+")

    def f_mono_ul(self):
        if not hasattr(self, "_mono_ul"):
            self._mono_ul = self.f_mono.copy()
            self._mono_ul.configure(underline=True)
        return self._mono_ul

    def history_row(self, h):
        tk = self.tk
        key = h["key"]
        row, wire = self.row_frame(lambda: self.start(key), lambda e: self.history_menu(e, h))
        tk.Label(row, text="○", font=self.f_small, fg=DIM, bg=PANEL).grid(
            row=0, column=0, rowspan=2, padx=(8, 8), sticky="n", pady=(9, 0))
        avail = self.text_width(int(36 * self.scale))
        self.title_line(row, h.get("name", "?"), h.get("kind", ""), h.get("worktree"), SOFT, avail)
        pend = self.pending.get(key)
        if pend and pend.get("failed"):
            sub, color = "Didn't come up. Check its console window.", RED
        elif pend:
            sub = f"Starting on :{pend['port']}…" if h.get("mode") == "args" else "Starting…"
            color = AMBER
        else:
            sub = f"{short_path(h.get('folder', ''))}  ·  :{h.get('last_port')}  ·  {ago(h.get('last_seen', 0))}"
            color = MUTED
        tk.Label(row, text=fit(sub, self.f_small, avail), font=self.f_small,
                 fg=color, bg=PANEL, anchor="w").grid(row=1, column=1, sticky="w", pady=(0, 7))
        play = tk.Label(row, text=self.glyph("play"), font=self.f_icon,
                        fg=MUTED if not pend else DIM, bg=PANEL, padx=10, cursor="hand2")
        play.grid(row=0, column=2, rowspan=2, padx=(6, 8), sticky="e")
        wire()
        play.bind("<Enter>", lambda e: play.config(fg=GREEN), add="+")
        play.bind("<Leave>", lambda e: play.config(fg=MUTED), add="+")

    def resize(self):
        self.root.update_idletasks()
        ih = self.inner.winfo_reqheight()
        wa = work_area()
        maxh = int(min(470 * self.scale, (wa.bottom - wa.top) * 0.7))
        self.scrollable = ih > maxh
        self.canvas.config(height=min(ih, maxh), scrollregion=(0, 0, self.W, ih))
        self.canvas.yview_moveto(0)
        self.root.update_idletasks()
        w, h = self.W + 2, self.root.winfo_reqheight()
        x, y = self.position(w, h, wa)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def position(self, w, h, wa):
        margin = int(12 * self.scale)
        xy = self.settings.get("pin_xy")
        if self.settings["pinned"] and xy:
            vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
            vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
            return max(vx, min(xy[0], vx + vw - w)), max(vy, min(xy[1], vy + vh - h))
        return wa.right - w - margin, wa.bottom - h - margin

    def on_wheel(self, e):
        if self.visible and getattr(self, "scrollable", False):
            self.canvas.yview_scroll(int(-e.delta / 120), "units")

    # ----- menus

    def popup_menu(self, e, entries):
        m = self.tk.Menu(self.root, tearoff=0, bg=BG, fg=FG, activebackground=HOVER,
                         activeforeground=FG, bd=0, font=self.f_text)
        for label, cmd in entries:
            if label is None:
                m.add_separator()
            else:
                m.add_command(label=label, command=cmd)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def running_menu(self, e, g):
        entries = [("Open in browser", lambda: webbrowser.open(g["url"])),
                   ("Copy URL", lambda: self.copy(g["url"]))]
        if g["folder"] and os.path.isdir(g["folder"]):
            entries.append(("Open folder", lambda: os.startfile(g["folder"])))
        entries += [(None, None), ("Stop server", lambda: self.stop_server(g))]
        self.popup_menu(e, entries)

    def history_menu(self, e, h):
        entries = [("Start on a free port", lambda: self.start(h["key"]))]
        if os.path.isdir(h.get("folder", "")):
            entries.append(("Open folder", lambda: os.startfile(h["folder"])))
        entries += [("Copy start command (secrets hidden)", lambda: self.copy(join_cmd(mask_args(h.get("launch", []))))),
                    (None, None), ("Forget", lambda: self.forget(h["key"]))]
        self.popup_menu(e, entries)

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def stop_server(self, g):
        if self.messagebox.askyesno(APP, f"Stop {g['name']} on :{g['port']}?", parent=self.root):
            def run():
                kill_tree(g["root_pid"], g["root_ctime"])  # no-op if it already exited
                self.wake.set()
            threading.Thread(target=run, daemon=True).start()

    def about(self):
        text = (f"{APP} {VERSION}\n{COPYRIGHT}\n\n"
                "Free software under the GNU General Public License v3.0 or later. "
                "It comes with ABSOLUTELY NO WARRANTY.\n\n"
                "Uses psutil (BSD-3-Clause), pystray (LGPL-3.0), Pillow (MIT-CMU) and six (MIT).\n\n"
                "Open the full licence texts?")
        if self.messagebox.askyesno(f"About {APP}", text, parent=self.root):
            for name in ("THIRD_PARTY_NOTICES.txt", "LICENSE"):
                path = HERE / name
                if path.exists():
                    subprocess.Popen(["notepad.exe", str(path)])

    def forget(self, key):
        self.history.pop(key, None)
        self.pending.pop(key, None)
        self.save_history()
        self.render(force=True)

    # ----- actions

    def start(self, key):
        h = self.history.get(key)
        if not h or key in self.pending and not self.pending[key].get("failed"):
            return
        if not os.path.isdir(h.get("cwd", "")):
            self.messagebox.showerror(APP, f"That folder no longer exists:\n{h.get('cwd')}", parent=self.root)
            return
        taken = self.busy | {p["port"] for p in self.pending.values()}
        try:
            launch = reveal_args(h["launch"])
            if any(MASK in a for a in launch):
                raise ValueError("secret was never stored")
        except (OSError, ValueError):
            self.messagebox.showerror(APP, "This command includes a saved secret that can't be decrypted on this "
                                      "Windows account. Start it once by hand and PortShelf will remember it again.",
                                      parent=self.root)
            return
        port = free_port(h.get("last_port") or 3000, taken)
        args = [a.replace("{PORT}", str(port)) for a in launch]
        env = {**os.environ, "PORT": str(port)}
        title = re.sub(r'[&|<>^"%]', "", f"{h['name']} :{port}")
        line = f'cmd.exe /s /k "title {title} & {join_cmd(args)}"'
        si = subprocess.STARTUPINFO()
        si.dwFlags = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7  # SW_SHOWMINNOACTIVE: console sits on the taskbar for logs without stealing focus
        try:
            subprocess.Popen(line, cwd=h["cwd"], env=env, startupinfo=si, creationflags=subprocess.CREATE_NEW_CONSOLE)
        except OSError as err:
            self.messagebox.showerror(APP, f"Couldn't start it:\n{err}", parent=self.root)
            return
        self.pending[key] = {"port": port, "t": time.time()}
        self.wake.set()
        self.render(force=True)

    def remember(self, g, now):
        h = self.history.get(g["key"])
        new = h is None
        h = h or {"first_seen": now}
        try:
            launch = protect_args(g["launch"])
        except OSError:
            launch = mask_args(g["launch"])  # can't encrypt: never store the secret, restart will need a manual start
        h.update(name=g["name"], kind=g["kind"], folder=g["folder"], cwd=g["cwd"], worktree=g["worktree"],
                 launch=launch, mode=g["mode"], last_port=g["port"], last_seen=now)
        self.history[g["key"]] = h
        self.history_dirty = True
        if new:
            if len(self.history) > HISTORY_LIMIT:
                oldest = sorted(self.history, key=lambda k: self.history[k].get("last_seen", 0))
                for k in oldest[: len(self.history) - HISTORY_LIMIT]:
                    del self.history[k]
            self.save_history()

    def save_history(self):
        try:
            save_json(HISTORY_FILE, self.history)
        except OSError:
            pass
        self.history_dirty = False
        self.last_save = time.time()

    def save_settings(self):
        try:
            save_json(SETTINGS_FILE, self.settings)
        except OSError:
            pass

    def on_scan(self, groups, busy):
        now = time.time()
        self.busy = busy
        self.live = groups
        self.live_keys = {g["key"] for g in groups if g["key"]}
        for g in groups:
            if g["key"]:
                self.remember(g, now)
                pend = self.pending.pop(g["key"], None)
                if pend and not pend.get("failed"):
                    webbrowser.open(g["url"])
        for k, pend in list(self.pending.items()):
            age = now - pend["t"]
            if age > START_TIMEOUT + 30:
                del self.pending[k]
            elif age > START_TIMEOUT:
                pend["failed"] = True
        self.update_tray()
        self.render()

    # ----- window state

    def hwnd(self):
        return user32.GetAncestor(self.root.winfo_id(), 2)

    def apply_mode(self):
        pinned = self.settings["pinned"]
        self.root.attributes("-topmost", (not pinned) or self.settings["on_top"])
        self.root.attributes("-alpha", self.settings["opacity"] if pinned else 1.0)

    def show(self):
        self.apply_mode()
        self.render(force=True)
        self.root.deiconify()
        self.root.lift()
        self.visible, self.had_focus = True, False
        if not self.rounded:
            self.rounded = True
            pref = ctypes.c_int(2)  # DWMWCP_ROUND, Windows 11 only
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(self.hwnd(), 33, ctypes.byref(pref), 4)
            except (OSError, AttributeError):
                pass
        self.root.after(30, self.grab_focus)

    def grab_focus(self):
        self.root.focus_force()
        user32.SetForegroundWindow(self.hwnd())

    def hide(self):
        self.root.withdraw()
        self.visible = False

    def toggle(self):
        if self.visible and not self.settings["pinned"]:
            self.hide()
        elif time.time() - self.last_autohide > 0.4:
            self.show()

    def close_clicked(self):
        if self.settings["pinned"]:
            self.set_setting("pinned", False)
        self.hide()

    def toggle_pin(self):
        self.set_setting("pinned", not self.settings["pinned"])
        if self.settings["pinned"] and not self.settings.get("pin_xy"):
            self.settings["pin_xy"] = [self.root.winfo_x(), self.root.winfo_y()]
            self.save_settings()
        self.show()

    def toggle_history(self):
        self.set_setting("history_open", not self.settings["history_open"])
        self.render(force=True)

    def set_setting(self, name, value):
        self.settings[name] = value
        self.save_settings()
        if name in ("pinned", "on_top", "opacity") and self.visible:
            self.apply_mode()
            self.render(force=True)
        if self.tray:
            self.tray.update_menu()

    def drag_start(self, e):
        self.drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def drag_move(self, e):
        if self.drag:
            self.root.geometry(f"+{e.x_root - self.drag[0]}+{e.y_root - self.drag[1]}")

    def drag_end(self, e):
        if not self.drag:
            return
        self.drag = None
        self.settings["pin_xy"] = [self.root.winfo_x(), self.root.winfo_y()]
        if not self.settings["pinned"]:  # dragging the popup pins it to the desktop
            self.set_setting("pinned", True)
            self.apply_mode()
        self.save_settings()

    def watch_focus(self):
        if not self.visible or self.settings["pinned"]:
            return
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        if pid.value == os.getpid():
            self.had_focus = True
        elif self.had_focus:
            self.hide()
            self.last_autohide = time.time()

    # ----- tray

    def make_tray(self):
        import pystray
        Item, Menu = pystray.MenuItem, pystray.Menu

        def post(*msg):
            return lambda: self.q.put(msg)

        def opacity_item(v):
            return Item(f"{int(v * 100)}%", post("set", "opacity", v), radio=True,
                        checked=lambda i: abs(self.settings["opacity"] - v) < 0.01)

        menu = Menu(
            Item("Open PortShelf", post("toggle"), default=True),
            Menu.SEPARATOR,
            Item("Pin to desktop", post("flip", "pinned"), checked=lambda i: self.settings["pinned"]),
            Item("Keep pinned panel on top", post("flip", "on_top"), checked=lambda i: self.settings["on_top"]),
            Item("Pinned opacity", Menu(*(opacity_item(v) for v in (1.0, 0.95, 0.85, 0.75, 0.6)))),
            Item("Show system ports too", post("flip", "show_all"), checked=lambda i: self.settings["show_all"]),
            Item("Start with Windows", post("autostart"), checked=lambda i: autostart_enabled()),
            Menu.SEPARATOR,
            Item("About and licences", post("about")),
            Item("Quit", post("quit")),
        )
        self.tray = None
        self.tray = pystray.Icon(APP, tray_image(0), APP, menu)
        self.tray.run_detached()

    def update_tray(self):
        n = sum(1 for g in self.live if g["dev"])
        if n != self.tray_count:
            self.tray_count = n
            self.tray.icon = tray_image(n)
        names = ", ".join(f"{g['name']} :{g['port']}" for g in self.live if g["dev"])
        self.tray.title = (f"{APP}: {names}" if names else f"{APP}: nothing running")[:127]

    # ----- loop

    def scan_loop(self):
        while True:
            try:
                groups, busy = scan(self.settings["show_all"])
                self.q.put(("scan", groups, busy))
            except Exception as err:  # keep scanning even if one pass trips over a dying process
                print("scan failed:", err, file=sys.stderr)
            self.wake.wait(SCAN_SECONDS)
            self.wake.clear()

    def pump(self):
        try:
            while True:
                self.handle(*self.q.get_nowait())
        except queue.Empty:
            pass
        if SHOW_FLAG.exists():
            SHOW_FLAG.unlink(missing_ok=True)
            self.show()
        self.watch_focus()
        if self.history_dirty and time.time() - self.last_save > 30:
            self.save_history()
        self.root.after(250, self.pump)

    def handle(self, kind, *args):
        if kind == "scan":
            self.on_scan(*args)
        elif kind == "toggle":
            self.toggle() if not self.settings["pinned"] else self.show()
        elif kind == "flip":
            name = args[0]
            if name == "pinned":
                self.toggle_pin()
            else:
                self.set_setting(name, not self.settings[name])
                if name == "show_all":
                    self.wake.set()
        elif kind == "set":
            self.set_setting(*args)
        elif kind == "autostart":
            set_autostart(not autostart_enabled())
            self.tray.update_menu()
        elif kind == "about":
            self.about()
        elif kind == "quit":
            self.save_history()
            self.tray.stop()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def all_children(w):
    for c in w.winfo_children():
        yield c
        yield from all_children(c)


def tray_image(n):
    from PIL import Image, ImageDraw, ImageFont
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 3, size - 3), radius=14, fill=(255, 255, 255, 255),
                        outline=(34, 163, 90, 255) if n else (179, 184, 193, 255), width=5)
    text = str(n) if n < 100 else "99"
    try:
        font = ImageFont.truetype("segoeuib.ttf", 40 if n < 10 else 32)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), text, font=font)
    x = (size - (box[2] - box[0])) / 2 - box[0]
    y = (size - (box[3] - box[1])) / 2 - box[1]
    d.text((x, y), text, font=font, fill=(29, 31, 35, 255) if n else (122, 128, 140, 255))
    return img


def list_mode():
    groups, _ = scan("--all" in sys.argv)
    for g in groups:
        print(f"{g['url']:<28} {g['name']}  [{g['kind']}]")
        print(f"{'':28} folder: {g['folder']}")
        if g.get("launch"):
            print(f"{'':28} restart ({g['mode']}): {join_cmd(mask_args(g['launch']))}")
        if g["extra"]:
            print(f"{'':28} also: {g['extra']}")


def main():
    if "--list" in sys.argv:
        list_mode()
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        user32.SetProcessDPIAware()
    kernel32.CreateMutexW(None, False, "Local\\PortShelf.SingleInstance")
    if ctypes.get_last_error() == 183:  # already running: ask that copy to show itself
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SHOW_FLAG.touch()
        return
    App().run()


if __name__ == "__main__":
    main()
