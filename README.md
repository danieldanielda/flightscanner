# Flight Watch via GitHub Actions

This repository contains a free, autonomous flight watcher that runs on GitHub Actions every hour and sends Telegram notifications only when it finds matching flight search results.

## What it checks

- Departure airport: `TLV` (Ben Gurion, Tel Aviv)
- Preferred destinations: `TBS`, `GYD`, `BUS`, `EVN`
- Dates: `2026-03-24`, `2026-03-25`, `2026-03-26`, `2026-03-29`, `2026-03-30`
- Fallback behavior: if no preferred destination is found, it checks a configurable list of major European airports
- Notifications: only when something is found
- Telegram message is sent in Russian and only for concrete flight options with a departure time and a direct offer link
- Implemented search source in this version: `Skyscanner`
- Duplicate finds are suppressed between hourly runs using a persisted state cache in GitHub Actions
- A weekly keepalive workflow creates a small commit so scheduled workflows stay active
- Europe fallback is intentionally limited to a smaller fast-check set so the hourly workflow finishes reliably

## Important note

This version uses browser automation and Skyscanner response parsing to avoid noisy generic search links. It now skips non-specific results and only sends alerts when it can identify a concrete option with a usable booking link. Skyscanner can still change markup or API payloads over time, so this remains a best-effort monitor.

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
