# Bug-fix notes

## Chat composer visibility and layout

The chat screen used a flex-column viewport, but the message scroller did not have
`min-height: 0`. In a flex layout this can let the scroller keep its content-based
minimum height and push the message composer outside the visible viewport. The
composer also had no explicit flex sizing, and the textarea inherited `width: 100%`
while sharing a row with action buttons, which could force the row wider than a
mobile screen.

The chat CSS now:
- gives the message scroller `min-height: 0` and the remaining flexible height;
- keeps the header and composer from shrinking;
- constrains the composer and row to the available width;
- lets the textarea flex from a zero basis with `min-width: 0`;
- keeps the composer above the bottom safe-area on mobile devices.

## Validation performed

- All Python files passed Python's built-in syntax compilation.
- All 8 JavaScript files passed `node --check`.
- The full pytest suite could not be executed in this environment because the
  Flask dependencies are not installed and package downloads are unavailable.
  Run `pip install -r requirements.txt` and then `pytest -q` in the deployment
  or local development environment to run the full suite.

This patch addresses the composer/layout defect found in the supplied source.
It is not a claim that every runtime or deployment issue has been exhaustively
verified without running the application against its configured database and
services.
