# Nagle Analytics

Nagle Analytics is a personal data-visualization site for interactive charts, sports analytics, maps, public-data stories, and visual experiments. The website presents finished and developing projects in a browsable portfolio while keeping each visualization accessible as a standalone page.

## Main pages

- `index.html` — homepage and project overview
- `viz-gallery.html` — visualization gallery
- `usl-cup-tracker.html` — interactive USL Championship season tracker
- `maps.html` — spatial-visualization projects
- `about.html` — background and project philosophy
- `featured.html` — preserved featured-project page, omitted from primary navigation
- `articles.html` — preserved articles page, omitted from primary navigation
- `backup-index-old.html` — preserved historical backup, intentionally not linked

## Repository structure

- `assets/brand/` contains brand artwork.
- `assets/css/site.css` contains shared, low-risk site foundations; page-specific styles remain with their pages.
- `assets/usl/crests/` contains local team crest images.
- `data/usl/` contains the tracker CSV and JSON data, logs, and update diagnostics.
- `scripts/` contains the Python data-discovery and standings-update scripts.
- `.github/workflows/update-usl-standings.yml` runs the data-update workflow.

## Preview locally

The site loads data over HTTP, so serve the repository instead of opening files directly. From the repository root, run:

```sh
python -m http.server 8000
```

Then open `http://localhost:8000/` in a browser. Stop the server with Ctrl+C.

## USL data and assets

The tracker reads historical standings from `data/usl/standings-history.csv` and the latest table from `data/usl/current-standings.json`. Other JSON files in `data/usl/` support update diagnostics. `data/usl/team-crests.json` maps team names to crest paths, colors, and team links; the matching image assets live in `assets/usl/crests/`.

`scripts/discover-usl-endpoints.py` discovers source endpoints and `scripts/fetch-usl-standings.py` prepares updated standings data. The GitHub Actions workflow can be run manually and is also scheduled weekly. Review generated data and diagnostics before relying on an update.

## Add a future project

1. Create a focused HTML page and keep project-specific styles and scripts with it unless a pattern is genuinely shared.
2. Add the project to `viz-gallery.html` and, when appropriate, the homepage.
3. Reuse the standard navigation, shared stylesheet, skip link, and `main-content` target.
4. Store project assets in a clearly named subdirectory and document any data-update process.
5. Serve locally and verify links, keyboard interaction, responsive layouts, and browser-console output before opening a pull request.

## Testing and contribution workflow

Automated checks can validate files, references, JSON, and Python syntax, but real browser interaction and viewport tests still need to be completed manually. This includes keyboard and touch behavior, chart replay and tooltips, crest rendering, reduced motion, responsive layouts, and browser-console checks.

For changes, create a feature branch from the current default branch, keep edits scoped, preview and validate locally, commit intentionally, push the feature branch, and open a pull request for review. Do not work directly on the default branch or merge until review and required checks are complete.
