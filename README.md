---
title: LabelUtils
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 7860
---

# LabelUtils

LabelUtils Discord submission bot.

## Environment variables

Add these runtime secrets/environment variables:

- `DISCORD_BOT_TOKEN`
- `DATABASE_URL` bot-owner control database for encrypted server settings
- `CONFIG_ENCRYPTION_KEY` Fernet key used to encrypt server database URLs
- `DISCORD_GUILD_ID` strongly recommended while testing so slash-command changes appear immediately in that server
- `STAFF_CHANNEL_ID` optional fallback staff channel for single-server installs
- `FORCE_IPV4` optional, defaults to `1`; set to `0` only if your host needs IPv6 DNS results
- `PORT` optional, defaults to `7860`; set this to the web port your host assigns
- `CLEAR_GLOBAL_COMMANDS` optional, defaults to `1` when testing with `DISCORD_GUILD_ID`; set to `0` if you want to keep global commands
- `OWNER_USER_IDS` comma-separated Discord user IDs allowed to run owner-only premium commands
- `PREMIUM_CONTACT` text shown by `/premium`, such as contact details and accepted crypto

Generate `CONFIG_ENCRYPTION_KEY` with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

After the bot is running, a server administrator should run:

- `/setup_database` to paste that server's Neon PostgreSQL URL. LabelUtils tests the connection, creates the `label_submissions` table if needed, then stores the URL encrypted in the bot-owner control database.
- `/setup_staff_channel` to choose where new submissions are sent for that server.
- `/setup_status` to verify the server database, staff channel, and required bot-owner config.

Users can run `/my_submissions` or `/my_demos` to see their own ticket history, and `/my_stats` to see submitted/accepted counts. `/accepted_leaderboard` shows a paginated leaderboard of Discord submitters with the most accepted demos. New submissions are checked for duplicate demo links and each staff submission card opens a staff discussion thread when the bot has thread permissions.

Premium is manually managed through the control database:

- `/premium` shows users how to contact you to buy premium.
- `/premium_status` checks the current server's premium state.
- `/premium_add` grants premium to a server. Owner-only via `OWNER_USER_IDS`.
- `/premium_remove` removes premium from a server. Owner-only via `OWNER_USER_IDS`.
- `/setup_brand` lets Pro servers customize the display name, embed color, and tagline used in supported server-specific messages.
- `/brand_status` shows the active brand settings.
- `/brand_reset` resets branding to server defaults.

Discord does not support changing a bot's actual avatar or online presence separately per server. Pro branding is server-specific inside LabelUtils messages and embeds, and `/setup_brand` also tries to update the bot's server nickname when Discord permissions allow it.

The app exposes a small health endpoint on `/` and `/health` while the Discord bot runs in the same process.

## JustRunMy.App setup

Use the Python app flow:

1. Zip this project folder.
2. Create a Python application in JustRunMy.App.
3. Upload the zip.
4. Add the environment variables above.
5. Add/open the app port using the same value as `PORT`.
6. Start the app.

The app can be started with either:

```bash
python bot.py
```

or:

```bash
python app.py
```

`app.py` exists only as a conventional Python hosting entrypoint.

## Render setup

Create a Web Service from this repo and set:

```bash
Build Command: pip install -r requirements.txt
Start Command: python bot.py
```

Do not use Render's default `gunicorn your_application.wsgi` command; this is a Discord bot, not a Django app.
