---
name: run-console
description: Launch this app (Django serving the committed React console bundle) on a scratch database seeded with real blocks, demo circuits and evaluated matches, then drive it in headless Chromium to screenshot a console page. Use this whenever asked to run, start or launch the app, to see or show the console (Match Journal, a match's trace, Circuits), to screenshot a page or element of the frontend, or to confirm a change works in the real UI against the real API rather than only in tests or the demo adapter.
---

# Run the console and screenshot it

The server reads the real API, so every screenshot shows rows that the rules engine
produced from the 5 sample blocks. Run every command from the repo root. `$RUN` is a
fresh directory in your scratchpad. It holds the database, the session key, the server
log and the screenshots.

## 1. Scratch database and seed

Never point `DATABASE_URL` at the dev `db.sqlite3`, because the seed creates a user and
40 matches that would then show up in the developer's own console.

```bash
export RUN=<scratchpad>/console-run && mkdir -p $RUN
export DJANGO_SECRET_KEY=x DATABASE_URL=sqlite:///$RUN/db.sqlite3 DJANGO_DEBUG=True
python manage.py migrate -v0
python .claude/skills/run-console/seed.py $RUN/session
```

The seed prints `Evaluation(blocks=5, matches=40, ...)` and `matches 40`. It creates
user `watcher` (password `watcher`) with the 8 demo circuits, copies symbol, name and
decimals from `frontend/src/console/api/demo/fixtures/tokens.json` onto the placeholder
tokens, and writes a signed-in session key for watcher to `$RUN/session`.

## 2. Server

`DJANGO_DEBUG` must be the exact string `True`. With DEBUG off, runserver exits on the
`ALLOWED_HOSTS` check.

```bash
nohup python manage.py runserver 127.0.0.1:8049 > $RUN/server.log 2>&1 &
for i in $(seq 30); do curl -sf -o /dev/null http://127.0.0.1:8049/signin && break; sleep 1; done
curl -s -b sessionid=$(cat $RUN/session) "http://127.0.0.1:8049/api/matches/?page_size=2"
```

The last command returns JSON journal rows. A 403 or an empty list means the seed or
session step failed.

The page is the committed bundle in `project/app/static/frontend/`. It is built with
`frontend/.env`, whose `VITE_CONSOLE_SOURCES` sets `matches=http,matchDetail=http`, so
the journal calls the API above. If you changed anything under `frontend/src`, run
`cd frontend && npm run build` first, or the server keeps serving the old bundle and the
screenshot will not show your change.

## 3. Screenshot

Playwright for Node is installed globally at `/opt/node22/lib/node_modules/playwright`
with browsers in `/opt/pw-browsers`. There is no Python Playwright. Do not run
`playwright install`. `shot.cjs` requires that absolute path.

```bash
node .claude/skills/run-console/shot.cjs $RUN/session $RUN/journal
```

Arguments are `<session-file> <out-dir> [path] [selector] [index]`. The defaults are
`/journal/`, `.jrow` and `1`. The browser context sets `prefers-reduced-motion: reduce`,
which the console honours by drawing the boot, power-on and trace animations in their
final state. Without it a 4 s wait still caught the trace pane half lit. The script waits
for the selector and for network idle, and writes `<out-dir>/page.png` (full page) and
`<out-dir>/element.png` (the selector's match at that index). It prints the match count, the element's text and the
browser console errors. The default run prints:

```
count 40
text STABLE-2K | 30,000USDT | 0x739e…86ba → 0xacc3…7eb4 | 16:22:23 UTC · block 18,000,004 · tx 70
errors []
```

Other pages use the same command with a different path and selector:

```bash
node .claude/skills/run-console/shot.cjs $RUN/session $RUN/trace '/journal/?match=39' .tpane 0
node .claude/skills/run-console/shot.cjs $RUN/session $RUN/circuits /circuits/ body 0
```

Read `page.png` and `element.png` with the Read tool before reporting. A successful exit
only means the selector existed, so the image can still be blank or half drawn. To check
an animation itself, remove `reducedMotion` from `shot.cjs`.

## 4. Stop

```bash
lsof -ti:8049 -sTCP:LISTEN | xargs -r kill
```

Do not use `pkill -f`, because it can match other sessions' servers.
