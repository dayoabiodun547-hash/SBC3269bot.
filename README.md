# LinkShortenerBot

Send the bot any link, get a short one back.

## Deploy steps:
1. Create bot with @BotFather, get BOT_TOKEN
2. Push to GitHub (git init, add, commit, push)
3. Deploy on Railway from the GitHub repo
4. Set BOT_TOKEN and DB_PATH variables
5. Go to Settings -> Networking -> Generate Domain
6. Copy that domain, add it as BASE_URL=https://yourdomain.up.railway.app
7. Redeploy, test by sending a link to your bot
