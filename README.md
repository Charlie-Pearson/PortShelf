# PortShelf

**Your local dev servers, at a glance, in the Windows tray.**

PortShelf is a small, free Windows app. It shows every development server
running on your machine (`localhost:3000`, `localhost:8080` and so on). For each
one you see what the project is, what's running it, which folder it's running
from, and a link to open it. It also remembers the projects you've run before,
so you can start them again with one click on a free port.

- Version 1.0.0 · Windows 10 and 11 · Licence: GPL-3.0-or-later
- Copyright © 2026 Charlie Pearson

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Install](#install)
- [Using PortShelf](#using-portshelf)
- [How it works](#how-it-works)
- [Privacy and security](#privacy-and-security)
- [Files and settings](#files-and-settings)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Uninstall](#uninstall)
- [Licence](#licence)
- [Third-party software and credits](#third-party-software-and-credits)

---

## Features

- **Live list of running servers.** For each one: the project name, what's
  running it (Vite, Next.js, Astro, Python server, PHP server, Django, Flask and
  more), its folder, and a clickable `localhost:port` link. It refreshes every
  3 seconds.
- **Tray icon with a count.** The icon shows how many servers are running, and
  its tooltip lists them.
- **One-click Stop** on every row, with a confirmation first.
- **Desktop widget mode.** Pin the panel to your desktop as a see-through
  widget, with adjustable opacity and an optional "always on top".
- **Previously running.** A hidden drop-down at the bottom of the panel lists
  projects PortShelf has seen before. Click one and PortShelf:
  - restarts it with the command you originally used;
  - picks a free port automatically;
  - opens your browser as soon as the server answers.
- **Minimal white design** that fits in with Windows 11.
- **No setup per project.** It finds servers you start any way you like: a
  terminal, VS Code, an AI coding tool, a script.
- **Nothing leaves your machine.** No accounts, no telemetry, no internet use.

## Requirements

- Windows 10 or 11.
- [Python 3.11 or newer](https://www.python.org/downloads/). During Python's
  setup, tick **"Add python.exe to PATH"**.

PortShelf doesn't need admin rights.

## Install

1. Download or clone this folder and put it somewhere permanent, such as
   `C:\Users\<you>\PortShelf`. The Start Menu shortcut and the
   "Start with Windows" option point at this folder, so don't run it from
   Downloads and then delete it.
2. Double-click **`install.bat`**. It:
   - checks you have Python 3.11 or newer;
   - installs the exact tested versions of four packages from PyPI into your
     user account (`psutil`, `pystray`, `Pillow`, `six`);
   - adds **PortShelf** to your Start Menu;
   - starts PortShelf.
3. Find the icon in the system tray, by the clock. Windows hides new icons
   under the **`^`** overflow arrow, so drag it onto the taskbar to keep it
   visible.
4. Optional: right-click the tray icon and tick **Start with Windows**.

### Manual install

If you'd rather not run the batch file:

```bash
python -m pip install --user -r requirements.txt
```

```bash
pythonw portshelf.pyw
```

`pythonw` runs it without a console window. To see what PortShelf detects
without opening the app, use `python portshelf.pyw --list` (add `--all` to
include system ports).

## Using PortShelf

### The panel

**Click the tray icon** to open the panel above the taskbar. It closes when you
click anywhere else, or when you press `Esc`.

| On a running server | What it does |
|---|---|
| Click the row or the `localhost:port` link | Opens it in your default browser |
| **Stop** (right-hand side) | Asks to confirm, then shuts down the server and the command that started it (e.g. `npm run dev`) |
| Right-click | Open in browser, Copy URL, Open folder, Stop server |

The header has three buttons: **refresh** (scan now), **pin**, and **close**.

### Pinning it to the desktop

Click the **pin** icon, or just drag the panel by its header. It then stays
where you put it and goes slightly see-through. From the tray menu you can:

- **Pinned opacity:** choose 100% to 60%.
- **Keep pinned panel on top:** keep it above other windows.

Click **close** on a pinned panel to unpin and hide it.

### Previously running

Click **Previously running** at the bottom of the panel to expand it. It lists
the last 25 projects PortShelf has seen that aren't running now. Each one
shows its folder, its last port, and when it was last seen.

- **Click a project (or ▶)** to start it again. PortShelf picks a free port
  (the previous one if it's still free) and starts the project in a console
  window that opens **minimised** on the taskbar, so its logs are one click
  away. Your browser opens when the server responds. If nothing answers within
  90 seconds, the row says so; check the console window for errors.
- **Right-click** for: Start on a free port, Open folder, Copy start command
  (with any secrets hidden), and Forget (remove it from the list).

### Tray menu

Right-click the tray icon for:

- **Open PortShelf**
- **Pin to desktop**
- **Keep pinned panel on top**
- **Pinned opacity**
- **Show system ports too:** also list non-development listeners such as
  Windows services, Docker and VPNs, shown greyed out and without a Stop button.
- **Start with Windows**
- **About and licences:** the version, copyright and licences, with the option
  to open the full licence texts.
- **Quit**

## How it works

PortShelf is a single Python file, [`portshelf.pyw`](portshelf.pyw). In plain
terms:

### 1. Finding servers

Every 3 seconds it asks Windows for all TCP ports in the *listening* state and
which process owns each one. It does this through
[psutil](https://github.com/giampaolo/psutil) and doesn't connect to the ports.

### 2. Deciding what counts as a dev server

A listening process is shown when **all** of these are true:

- PortShelf can read its working folder. Processes owned by other users or
  running as admin are hidden from a normal account.
- That folder isn't inside `C:\Windows`, `Program Files` or `ProgramData`.
- Its program is either:
  - a known developer runtime (`node`, `python`, `php`, `bun`, `deno`, `ruby`,
    `java`, `dotnet`, `go`, `hugo`, `caddy`, `nginx` and others); or
  - not installed in a system or app folder (`Windows`, `Program Files`,
    `ProgramData`, `AppData`).

That shows your projects and hides things like Docker, VPN helpers, WSL relays
and Windows services. **Show system ports too** turns the filter off.

### 3. Naming it

The project name comes from the nearest `package.json`, `pyproject.toml` or
`composer.json` name. Generic template names such as `my-app` or
`vite-project` are skipped, and then it falls back to the folder name. A
generic folder like `demo`, `dist` or `public` is shown as
`Parent / demo`. Git worktrees created under `.claude/worktrees/` are labelled
"worktree".

The type label ("Vite", "PHP server", …) comes from matching the command line
against known tools. Anything else shows the program and script, e.g.
`node · server.mjs`.

### 4. Finding the command you actually ran

The process holding the port is often not the one you started. `npm run dev`
starts `node …/vite.js` underneath, for example. PortShelf walks up the
parent processes while they share the same folder and are developer runtimes
or `cmd /c` wrappers. It stops at your terminal or editor. The top of that
chain is saved as the restart command, and `node …/npm-cli.js run dev` is
turned back into `npm run dev`.

### 5. Restarting on a free port

PortShelf turns the saved command into a template with a port slot:

| You originally ran | PortShelf restarts it as |
|---|---|
| `python -m http.server 8000` | `python -m http.server 8001` |
| `php -S localhost:8090 router.php` | `php -S localhost:8091 router.php` |
| `node server.js --port 4000` | `node server.js --port 4001` |
| `npm run dev`, where the script runs Vite, Next.js, Astro, Nuxt, Angular, Gatsby, Docusaurus, Eleventy, Parcel, Remix, http-server or live-server | `npm run dev -- --port=4001` (no `--` for yarn, pnpm or bun) |
| `python manage.py runserver` | `python manage.py runserver 8001` |
| Uvicorn or Flask | `… --port 8001` |
| Streamlit | `… --server.port=8001` |
| anything else | the same command, with the `PORT` environment variable set |

The free port is the previous port if it's still free. Otherwise PortShelf
tries the next 200 ports up, checking both the list of listening ports and a
test bind. The command runs as `cmd.exe /k` in the project folder, in a new
minimised console window. Scripts that already set their own port, or run
several things at once (`concurrently`, `&&`, `npm-run-all`), are left alone
apart from the `PORT` variable.

A project is recognised as "the same one" by its folder plus its command with
the port removed. That's how PortShelf spots the restarted server (even if the
tool ignored the port it was given), opens your browser at the right address,
and keeps it as one entry in history.

### 6. The interface

The interface is built with Python's own Tkinter. The tray icon uses
[pystray](https://github.com/moses-palmer/pystray), and its count image is
drawn with [Pillow](https://github.com/python-pillow/Pillow). Only one copy of
PortShelf runs at a time: opening it again simply shows the existing panel.

## Privacy and security

**What PortShelf does:**

- It runs entirely on your PC, as your normal Windows user, without admin
  rights.
- It **never connects to the internet** and **doesn't open any ports
  itself**. The only network-related things it does are asking Windows which
  ports are in use and test-binding a port before using it.
- It only reads information Windows already gives your account about your own
  processes: name, folder, command line and parent process.
- It only starts or stops anything when you click. Stopping always asks first.

**What it saves**, in `%APPDATA%\PortShelf\`, which only your account can read:

- `settings.json`: panel preferences.
- `history.json`: for each project: name, type, folder, last port, when it
  was last seen, and its start command.

**Protecting secrets in commands.** Some people pass passwords or API keys on
the command line, e.g. `--token=…`, `--api-key …`, `API_KEY=…` or
`https://user:password@host`. Arguments that look like secrets are **never
written to disk in plain text**:

- They're encrypted with Windows' built-in
  [DPAPI](https://learn.microsoft.com/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata),
  which only your Windows account on this PC can decrypt. A copied or synced
  `history.json` can't be read elsewhere.
- They're shown as `•••` in the app and in **Copy start command**.
- If encryption ever fails, the secret isn't saved at all. That project then
  has to be started by hand once.

Detection works on argument names. A secret passed under an unusual name (say
`--k abc123`) won't be recognised, so environment variables or `.env` files
remain the safer place for secrets.

**Safe stopping.** PortShelf checks that the process ID still belongs to the
same process (by its start time) before stopping it. A server that has exited,
and had its ID reused by another program, is never touched.

**Things to know:**

- Restarting runs whatever command is saved in `history.json`. Anything that
  can edit files in your `AppData` folder can already run programs as you, so
  this adds no new risk. To check what a project will run, right-click it →
  **Copy start command**.
- `install.bat` installs pinned package versions from PyPI. Review
  `requirements.txt` if you want to check them.
- Report security issues privately to the maintainer rather than in a public
  issue.

## Files and settings

| File | What it is |
|---|---|
| `portshelf.pyw` | The app |
| `requirements.txt` | The exact Python package versions it was tested with |
| `install.bat` / `uninstall.bat` | Setup and removal |
| `LICENSE` | GNU General Public License v3.0 (full text) |
| `THIRD_PARTY_NOTICES.txt` | Credits and full licence texts of the packages it uses |
| `%APPDATA%\PortShelf\settings.json` | Your preferences |
| `%APPDATA%\PortShelf\history.json` | Projects seen before (up to 60) |

`settings.json` keys:

- `pinned`: true or false.
- `on_top`: true or false.
- `opacity`: 0.6 to 1.0, used when pinned.
- `show_all`: true or false.
- `history_open`: true or false.
- `pin_xy`: the pinned position.

Close PortShelf before editing them by hand.

## Troubleshooting

**A server isn't listed.**
Run `python portshelf.pyw --list --all` in the PortShelf folder.
- If it appears there as "system", it was filtered out, usually because it
  runs from `Program Files` or `AppData`. Use **Show system ports too**.
- If it doesn't appear at all, it may be running as admin or as another user,
  or inside WSL or Docker. Those show up via **Show system ports too** as their
  relay process.

**The tray icon isn't visible.**
Click the `^` arrow by the clock and drag the icon onto the taskbar. In
Windows 11 you can also go to Settings → Personalisation → Taskbar → Other
system tray icons.

**A restart says "Didn't come up".**
Open its console window from the taskbar to see the error. Usually the
project's dependencies need installing, or its script needs a port flag
PortShelf doesn't know about. To see what it ran, right-click the project →
**Copy start command**.

**The panel opened and closed immediately.**
Clicking the tray icon while the panel is open closes it. Click once and wait.

**Nothing happens when I start PortShelf a second time.**
It's already running in the tray. Starting it again opens the panel instead.

## Known limitations

- Windows only. It uses Windows-specific APIs for the tray, DPAPI and window
  handling.
- The panel opens on the primary monitor.
- When restarting, `%` signs in a command may be expanded by `cmd.exe` as
  environment variables.
- Tools that ignore both a port flag and `PORT` restart on their own port.
  PortShelf still finds them and opens the right address.
- Servers inside WSL or Docker are only visible through their Windows-side
  relay (with **Show system ports too**), without project details.

## Uninstall

Double-click **`uninstall.bat`**. It:

- stops PortShelf;
- removes the Start Menu shortcut and the Start-with-Windows entry;
- asks whether to delete your settings and history.

Then delete the PortShelf folder. Its Python packages are left in place in case
other programs use them. The script shows the command to remove them too.

## Licence

PortShelf is free software: you can redistribute it and/or modify it under the
terms of the **GNU General Public License** as published by the Free Software
Foundation, either **version 3** of the License, or (at your option) any later
version. See [`LICENSE`](LICENSE) for the full text.

PortShelf is distributed in the hope that it will be useful, but **WITHOUT ANY
WARRANTY**; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
A PARTICULAR PURPOSE.

In short:

- You can use, study, share and change PortShelf for any purpose.
- If you distribute it, or a modified version, you must do so under the GPL
  too, include the licence, and make the source code available.

Copyright © 2026 Charlie Pearson.

## Third-party software and credits

PortShelf's own code is original. It relies on these open-source packages,
which `install.bat` downloads from [PyPI](https://pypi.org) into your Python
installation. They are **not bundled or modified** by PortShelf.

| Package | Version | Licence | Used for | Project |
|---|---|---|---|---|
| psutil | 7.2.2 | BSD-3-Clause | Listing listening ports and reading process details | [github.com/giampaolo/psutil](https://github.com/giampaolo/psutil) |
| pystray | 0.19.5 | LGPL-3.0-or-later | The system tray icon and menu | [github.com/moses-palmer/pystray](https://github.com/moses-palmer/pystray) |
| Pillow | 13.0.0 | MIT-CMU | Drawing the tray icon | [github.com/python-pillow/Pillow](https://github.com/python-pillow/Pillow) |
| six | 1.17.0 | MIT | Required by pystray | [github.com/benjaminp/six](https://github.com/benjaminp/six) |

Their **full licence texts** are in
[`THIRD_PARTY_NOTICES.txt`](THIRD_PARTY_NOTICES.txt). You can also open them
from the tray menu → **About and licences**. All four licences are compatible
with GPL-3.0.

pystray is LGPL-licensed. Because PortShelf uses it as a separately installed,
unmodified package, you can replace it with any other compatible version: just
install a different version with pip.

PortShelf also uses, without bundling them:

- **Python** and its standard library, including Tkinter, under the
  [PSF License](https://docs.python.org/3/license.html).
- Windows' built-in **Segoe UI**, **Segoe Fluent Icons / Segoe MDL2 Assets**
  and **Cascadia Mono** fonts. These are drawn by Windows and aren't
  distributed with PortShelf.

"Windows" is a trademark of Microsoft Corporation. PortShelf isn't affiliated
with or endorsed by Microsoft, or by the authors of the packages above.
