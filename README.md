# Asset Intelligence Copilot

Challenge repository for the FlytBase Hack Day (Hacktoberfest 2026).

Build a chat copilot that answers operators' plain-English questions about an oil field, correctly and with evidence, using records, sensor readings, manuals, repair reports and notes.

## Start here

| Document | What it is |
| :--- | :--- |
| [`01-problem-statement.md`](01-problem-statement.md) | The challenge, what to build, what to submit and how we judge |
| [`02-dataset-guide.md`](02-dataset-guide.md) | Every file and field in the sample dataset |
| [`03-evaluation-queries.md`](03-evaluation-queries.md) | Example questions with expected answers, to check your progress |
| [`04-prerequisites.md`](04-prerequisites.md) | What to set up before the event: coding agent and model access |

## Sample data

`sample-data/` holds a fictional oil field: 200 wells in 4 areas, with 4 years of history.

- `sample-data/fleet/`: areas, teams and people, well-type manuals, and operating procedures
- `sample-data/assets/WELL-0001/` to `WELL-0200/`: one folder per well

All data is synthetic and created for this challenge. Manuals and procedures are simplified, fictional documents, not real operating or legal guidance.

## Application setup (Milestone 1)

The backend is a small FastAPI service with a SQLite ingest pipeline. It discovers
wells from `sample-data/assets/` (or another `--data-dir`) and does not hardcode
the well count.

### Install

Python 3.14 is sufficient. From the repository root:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On macOS or Linux, activate with `source .venv/bin/activate`.

### Ingest the dataset

```
python -m app.ingest
```

Defaults:

- data directory: `sample-data` (override with `--data-dir` or `DATA_DIR`)
- database: `data/copilot.db` (override with `--db` or `DATABASE_PATH`)

Example for a later evaluation dataset:

```
python -m app.ingest --data-dir path\to\eval-data --db data\eval.db
```

The command prints per-table counts and any ingest warnings (malformed rows or
broken references). Re-running it updates existing records and does not create
duplicates.

### Run the API

```
uvicorn app.main:app --reload
```

Health check: `GET http://127.0.0.1:8000/health`

### Tests

```
pytest
```

`data/`, `*.db`, and other runtime artifacts are gitignored.

## License

[MIT](LICENSE)
