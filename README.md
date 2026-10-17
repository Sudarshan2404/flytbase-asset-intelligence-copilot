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

## License

[MIT](LICENSE)
