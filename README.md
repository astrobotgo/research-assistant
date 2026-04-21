# Research Assistant

This repository generates dated daily PDF digests in `data/reports/` and now publishes a matching static archive site into `docs/` for GitHub Pages.

## GitHub Pages Setup

1. Push this repository to GitHub.
2. In the GitHub repo, open `Settings` -> `Pages`.
3. Under `Build and deployment`, choose `Deploy from a branch`.
4. Select your main branch and the `/docs` folder, then save.
5. After the next push that includes `docs/`, GitHub will publish the site at `https://<username>.github.io/<repo>/`.

## Publishing Flow

- Each daily run already writes `data/reports/daily-YYYY-MM-DD.pdf` and `data/reports/latest.pdf`.
- The daily command now also rebuilds `docs/` so GitHub Pages has:
  - `docs/index.html` with a human-friendly archive
  - `docs/latest.pdf` for the newest report
  - `docs/reports/*.pdf` for dated report links
- Your normal workflow stays the same: run the assistant, commit the updated `data/reports/` and `docs/` files, and push.

## Automatic Push After Daily Runs

- The repo includes [scripts/run-daily.sh](/Users/kfinner/Documents/GitHub/research-assistant/scripts/run-daily.sh), which can:
  - run the daily report job
  - rebuild the site in `docs/`
  - optionally commit and push the updated report/site files
- To install the timer with auto-push enabled on Ubuntu:

```bash
./scripts/install-automation.sh systemd 1
```

- For cron with auto-push enabled:

```bash
./scripts/install-automation.sh cron 1
```

- Auto-push requires this machine to already have non-interactive Git push access to `origin`, usually via SSH keys or a credential helper.

## Notes

- If you already have historical PDFs in `data/reports/`, rebuilding the site will include them automatically.
- If you want a custom domain later, we can add `docs/CNAME`.
