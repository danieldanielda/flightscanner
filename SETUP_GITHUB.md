# Publish To GitHub

## 1. Create a new empty GitHub repository

Create a repository in your GitHub account, for example `flight-watch`.

## 2. Add the files from this folder

If you want to do it from the terminal, run these commands after replacing `YOUR_GITHUB_USERNAME`:

```powershell
git config --global --add safe.directory "C:/Users/Greg/Documents/New project"
git init
git checkout -b codex/flight-watch
git add .
git commit -m "Add GitHub Actions flight watcher"
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/flight-watch.git
git push -u origin codex/flight-watch
```

Then open GitHub and create a pull request, or change the default branch flow if you prefer to push to `main`.

## 3. Add GitHub Actions secrets

Open:

`Settings -> Secrets and variables -> Actions`

Add:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 4. Enable and test the workflow

Open:

`Actions -> Flight Watch -> Run workflow`

Run it once manually to verify everything.

## 5. Security follow-up

Your Telegram bot token was shared in chat earlier. After GitHub is configured, rotate the bot token in `@BotFather`, then update:

- GitHub secret `TELEGRAM_BOT_TOKEN`
- local file `telegram-config.json` if you still want local testing
