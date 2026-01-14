# titan-hltv-test

Local workspace for extracting CS2/Faceit demo metrics (HLTV 3.0-style).

## Run (Codespaces / local)

Install:

```bash
python -m pip install -e .
```

Analyze one or more demos:

```bash
titan-hltv path/to/match.dem
titan-hltv path/to/a.dem path/to/b.dem --out report.json
```

Debug parser schema (recommended on first run in Codespaces):

```bash
titan-hltv path/to/match.dem --debug-schema --out report.json
```

Current status: basic metrics (best-effort) + scaffolding for HLTV 3.0-style metrics.