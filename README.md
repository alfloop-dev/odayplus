# odayplus

Multi-agent orchestration system and supporting tooling.

## Components

- **`.orchestrator/`** — core orchestration runtime: supervisor, GitHub event bus,
  permission broker, dispatch policy, worker runtime, and their tests.
- **`scripts/`** — operational scripts (status reporting, supervisor watchdog,
  runtime health checks).

## Requirements

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) for environment and dependency management

## Setup

```bash
# Create the virtual environment and install dev dependencies
uv sync

# Copy the example config to a local working config (gitignored)
cp .orchestrator/config.example.json .orchestrator/config.json
```

## Running tests

```bash
uv run pytest
```

## Layout notes

- `config.json` and `config.local.json` are machine-local and **gitignored**;
  `config.example.json` is the tracked template.
- Runtime state and outputs (`logs/`, `metrics/`, `backups/`, `evidence/`,
  `reviews/`, `worker-runtime/`, `*-state.json`, `*.jsonl`) are gitignored.

## License

MIT
