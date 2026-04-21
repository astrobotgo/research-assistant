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

## Notes

- If you already have historical PDFs in `data/reports/`, rebuilding the site will include them automatically.
- If you want a custom domain later, we can add `docs/CNAME`.
