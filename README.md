# Gauntlet

A durable eval runner for AI agents.

## Run it locally

### What you need

- [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/). uv installs Python 3.13 for you.
- An [Anthropic API key](https://console.anthropic.com/).
- The [Temporal CLI](https://docs.temporal.io/cli/setup-cli), for the local Temporal server:
  - macOS or Linux: `brew install temporal`
  - Windows: download [temporal for Windows](https://temporal.download/cli/archive/latest?platform=windows&arch=amd64), unzip it, and put `temporal.exe` on your `PATH`.

### Set up (once)

These commands work in PowerShell and in a macOS or Linux terminal.

```bash
git clone https://github.com/Siddhant-Shankar/Gauntlet.git
cd Gauntlet
uv sync                                            # Python 3.13 and every package in uv.lock
cp .env.example .env                               # then paste your key after ANTHROPIC_API_KEY=
uv run gauntlet demo-db demo/shop.sql demo/shop.db # build the demo database
uv run pytest                                      # the first run downloads a Temporal test server
```

### Run it (three terminals, each in the Gauntlet folder)

```bash
# 1. The Temporal server, saving runs to temporal.db. Its own UI is at http://localhost:8233
temporal server start-dev --db-filename temporal.db

# 2. A worker: runs the cases and calls Claude, so it needs the key in .env
uv run --env-file .env gauntlet worker

# 3. The web app: http://127.0.0.1:8000 (interactive API docs at /docs)
uv run gauntlet web
```

Open http://127.0.0.1:8000, pick agents and k, and start a run.

Runs are saved in `temporal.db`, so they survive restarting the server. Finished runs are deleted
after 24 hours, the dev server's retention period.

### Start a run without the web page

Through the web app's API, from PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/runs -ContentType "application/json" `
  -Body '{"suite": "shop", "k": 3, "agents": ["claude-haiku-5-5", "claude-sonnet-5-5"]}'
```

or with curl:

```bash
curl -X POST http://127.0.0.1:8000/api/runs -H "Content-Type: application/json" \
  -d '{"suite": "shop", "k": 3, "agents": ["claude-haiku-5-5", "claude-sonnet-5-5"]}'
```

Or from the command line, which waits and prints a report:

```bash
uv run gauntlet run demo/suite.json --db demo/shop.db -k 3 --agent claude-haiku-5-5
```

Each run asks Claude once per case per attempt: the demo suite's 24 cases at k = 3 make 72 calls
per agent.

### Try the crash demo

1. Start a run with k = 3.
2. While it runs, stop the worker (Ctrl+C in terminal 2). The run page says no worker is answering.
3. Start the worker again. The run finishes, and the attempts that were already done are not
   asked again.

### Review what no check could decide

An answer with no SQL in it can't be checked, so the run page shows it with a note box and
**Mark passed** and **Mark failed**. The case waits up to 7 days for that decision.

## Development

```bash
uv run pytest
uv run mypy src tests
uv run ruff check .
uv run ruff format --check .
```
