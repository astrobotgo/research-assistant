# Research Assistant

Two AI-assisted agents scan recent astrophysics papers and build a findings-first daily research briefing.

- **Ptolemy** is the discovery and curation agent. It scans arXiv topics, deduplicates papers, uses Copernicus' selection brief, ranks candidates for important new findings and interesting points, and enriches the chosen set with Semantic Scholar metadata.
- **Copernicus** is the synthesis and context agent. It reads recent briefings and selected-paper caches, tracks open questions, advises Ptolemy on what is worth selecting, connects related papers, and writes the final digest grounded in the selected sources.

The daily run can generate Markdown, PDF, cache files, and a static `docs/` site locally. Those generated artifacts are ignored by Git by default so the repository stays focused on the code.

## Ubuntu Runtime Setup

The intended runtime is the Ubuntu machine that runs the daily job. This macOS checkout can be used for editing the source, but `.venv/`, `.env`, reports, caches, `docs/`, and downloaded voice models should be created on the Ubuntu host.

On Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your local API/model settings:

```bash
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-1.5-flash
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:32b
```

## Run

```bash
PYTHONPATH=. .venv/bin/python -m app.main daily
```

Useful options:

```bash
PYTHONPATH=. .venv/bin/python -m app.main daily --days 3 --present 10
PYTHONPATH=. .venv/bin/python -m app.main scan "galaxy clusters" --days 7 --limit 10
```

Daily outputs are written under `data/` and `docs/`, but they are local working artifacts unless you explicitly publish them.

## Automation

Install a daily timer without committing generated outputs:

```bash
./scripts/install-automation.sh systemd 0
```

Cron is also supported:

```bash
./scripts/install-automation.sh cron 0
```

The second argument should stay `0` for normal code-only Git usage. Set it to `1` only on a machine that is intentionally publishing generated report/site files back to `origin`.

## Optional Publishing

When `AUTO_PUSH_REPORTS=1`, [scripts/run-daily.sh](scripts/run-daily.sh) force-adds the generated report/site targets despite `.gitignore`, commits them, and pushes to `origin main`.

That mode is useful for GitHub Pages publishing, but it is intentionally opt-in. Normal development should commit only source files such as `app/`, `scripts/`, `topics.yaml`, `requirements.txt`, and documentation.

If the Ubuntu machine is only supposed to run the assistant for you locally, keep `AUTO_PUSH_REPORTS=0` and only push code changes from whichever computer you are editing on.

## Notes

- `.env` is local and ignored. Keep secrets out of Git.
- `.venv/` is local and ignored. Recreate it from `requirements.txt`.
- Video generation is off by default. To experiment with it later, install `ffmpeg` and Piper on Ubuntu, add the voice model under `models/piper/`, and pass `--video` to the daily command.
