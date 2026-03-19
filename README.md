# Flight Watch via GitHub Actions

This repository contains a free, autonomous flight watcher that runs on GitHub Actions every hour and sends Telegram notifications only when it finds matching flight search results.

## What it checks

- Departure airport: `TLV` (Ben Gurion, Tel Aviv)
- Preferred destinations: `TBS`, `GYD`, `BUS`, `EVN`
- Dates: `2026-03-24`, `2026-03-25`, `2026-03-26`, `2026-03-29`, `2026-03-30`
- Fallback behavior: if no preferred destination is found, it checks a configurable list of major European airports
- Notifications: only when something is found
- Telegram message includes a direct source link and a Google Flights comparison link
- Implemented search sources in this version: `Skyscanner`, `Kayak`, `Kiwi`, `Aviasales`
- Duplicate finds are suppressed between hourly runs using a persisted state cache in GitHub Actions

## Important note

This version uses browser automation and best-effort text detection on aggregator result pages. Airline and aggregator sites sometimes change their markup or anti-bot behavior, so selectors and result heuristics may need adjustment later.

The monitor also checks that the page text appears to mention at least one of the target airlines: `EL AL`, `Arkia`, or `Israir`.

Direct airline support for `EL AL`, `Arkia`, and `Israir` is not hardcoded yet because those booking flows are often session-based and need source-specific testing. This version focuses on a free autonomous setup that links back to the aggregator page where the match was found.

## GitHub setup

1. Create a GitHub repository and push these files.
2. In the repository, open `Settings -> Secrets and variables -> Actions`.
3. Add these secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Open the `Actions` tab and enable workflows if GitHub asks.
5. Run `Flight Watch` once with `Run workflow` to test it immediately.
6. If Telegram does not receive a message while you know matching flights exist, inspect the workflow log and adjust the text markers in [flight_monitor.py](C:\Users\Greg\Documents\New%20project\flight_monitor.py).

## Local note

`telegram-config.json` is ignored by `.gitignore` and should not be committed. The GitHub Actions workflow uses repository secrets instead of the local JSON file.
