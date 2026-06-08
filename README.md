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
- `DATABASE_URL` bot-owner control database for encrypted server database URLs, staff channel IDs, and manual premium grants
- `POOL_DATABASE_URL_1` managed storage database for West US
- `POOL_DATABASE_URL_2` managed storage database for Europe (UK)
- `POOL_DATABASE_URL_3` managed storage database for South-East Asia
- `CONFIG_ENCRYPTION_KEY` Fernet key used to encrypt server database URLs
- `DISCORD_GUILD_ID` strongly recommended while testing so slash-command changes appear immediately in that server
- `STAFF_CHANNEL_ID` optional fallback staff channel for single-server installs
- `FORCE_IPV4` optional, defaults to `1`; set to `0` only if your host needs IPv6 DNS results
- `PORT` optional, defaults to `7860`; set this to the web port your host assigns
- `CLEAR_GLOBAL_COMMANDS` optional, defaults to `1` when testing with `DISCORD_GUILD_ID`; set to `0` if you want to keep global commands
- `OWNER_USER_IDS` comma-separated Discord user IDs allowed to run owner-only premium commands
- `PREMIUM_CONTACT` text shown by `/premium`, such as contact details and accepted crypto

Enable Message Content Intent for the bot in the Discord Developer Portal if you want artist DM replies to staff messages to appear in the staff thread.

Generate `CONFIG_ENCRYPTION_KEY` with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

After the bot is running, a server administrator should run:

- `/start` to automatically assign this server to a random managed pooled database. LabelUtils creates a private PostgreSQL schema like `guild_123456789` and prepares that server's submissions, tickets, branding, and Pro settings tables inside it.
- `/setup_staff` to choose where new submissions and tickets are sent for that server.
- `/setup` to verify the server database, staff channel, and required bot-owner config.
- `/storage` is a Pro command for choosing West US, Europe (UK), or South-East Asia managed storage.
- `/setup_db` is optional Pro advanced setup for servers that want to bring their own Neon PostgreSQL URL instead of using managed pooled storage.

Users can run `/help` for the command list, `/submission` to check one label submission, `/my_subs` or `/my_demos` to see their own ticket history, and `/my_stats` to see submitted/accepted counts. `/leaderboard` shows a paginated leaderboard of Discord submitters with the most accepted demos. New submissions are checked for duplicate demo links and each staff submission card opens a staff discussion thread when the bot has thread permissions.

Premium is manually managed through the control database:

- `/premium` shows users how to contact you to buy premium.
- `/pro_status` checks the current server's premium state.
- `/redeem` lets a server administrator redeem a premium coupon for the current server.
- `/coupon` creates a reusable premium coupon with a configurable use count. Owner-only via `OWNER_USER_IDS`.
- `/pro_add` grants premium to a server. Owner-only via `OWNER_USER_IDS`.
- `/pro_remove` removes premium from a server. Owner-only via `OWNER_USER_IDS`.
- `/brand` opens a form where Pro servers customize the display name, embed color, and submit panel caption used in supported server-specific messages.
- `/brand_info` shows the active brand settings.
- `/brand_clear` resets branding to server defaults.
- `/storage` lets Pro servers choose West US, Europe (UK), or South-East Asia managed storage.
- `/form` customizes the optional submission prompt.
- `/templates` opens a form for approval/rejection DMs. Either field can be left blank to keep the current template.
- `/limits` configures cooldowns, submission caps, and duplicate-link policy.
- `/routing` routes approved/rejected updates to separate channels.
- `/extras` sets footer text, logo thumbnail, and custom success message.
- `/post_panel` posts a branded submit button panel.
- `/note` adds private staff notes to submissions.
- `/reviewer` assigns staff reviewers to submissions.
- `/shortlist`, `/shortlisted`, `/priority`, and `/rate` add A&R workflow tools for Pro servers.
- `/reasons` stores common rejection reasons for staff.
- `/digest` posts a weekly submission digest to a chosen channel.
- `/ticket_channel` chooses the private staff channel where support ticket cards are posted.
- `/ticket_panel` posts the public button panel users click to open tickets.
- `/tickets` and `/ticket_set` manage support tickets with separate statuses. Ticket cards have Resolved and DM buttons; DM replies are routed back into the ticket thread.
- `/analytics` shows submission analytics.
- `/export` exports a CSV.
- Artist replies to staff DMs are forwarded into the submission's staff thread. Attachments are forwarded as Discord attachment links, so the bot does not download/reupload files into memory.

Each server's submissions, support tickets, Pro branding, and Pro settings are stored either in its managed pooled schema or in its custom configured Neon database. The owner control database stores pool assignment, encrypted custom database links, staff-channel setup, and premium status.

Submission threads also receive release logs for submission creation, approval, rejection, and staff DM actions.
Visible submission and premium dates use Discord native timestamps, so Discord renders them in each user's local timezone.
Discord modals support a maximum of five text inputs, so LabelUtils keeps the five core submission fields and Pro form customization currently changes the optional message field's label and placeholder.

Discord does not support changing a bot's actual avatar or online presence separately per server. Pro branding is server-specific inside LabelUtils messages and embeds, and `/brand` also tries to update the bot's server nickname when Discord permissions allow it.

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
