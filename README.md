---
title: LabelUtils
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 7860
---

# LabelUtils

LabelUtils is a Discord bot for labels, collectives, and A&R teams that need a clean way to collect demos, review submissions, manage staff notes, and stay in touch with artists.

## What It Does

Artists submit demos through a Discord form. Staff receive a private submission card with approve, reject, and DM actions. Each submission can open a private staff discussion thread, so decisions, notes, DMs, and release logs stay attached to the right ticket.

Servers can also use LabelUtils as a private support-ticket tool. Users click a public ticket button, while the actual ticket card appears in a staff-only channel with Resolved and DM buttons.

## Getting Started

Server admins use:

- `/start` to create managed storage for the server.
- `/setup_staff` to choose where demo submissions are sent.
- `/setup` to check the server setup.
- `/help` to see the command list inside Discord.

Free servers are automatically assigned managed storage. Pro servers can choose a storage region or connect their own Neon database.

## Artist Commands

- `/submit` opens the demo submission form.
- `/submission` checks one submitted demo by ticket ID.
- `/my_subs` shows your submitted demos.
- `/my_demos` shows more of your submission history.
- `/my_stats` shows submitted, accepted, rejected, and queued counts.
- `/leaderboard` shows top accepted submitters.

## Staff Workflow

Staff can:

- Browse submissions with `/queue`, `/recent`, and `/panel`.
- Update a submission with `/status`.
- Approve, reject, or DM artists from submission cards.
- Keep release logs inside staff threads.
- Receive artist replies from DMs back inside the matching staff thread.

DM reply forwarding works when the artist replies directly to the bot's DM message. Attachments are forwarded as Discord attachment links, so files are not downloaded or reuploaded by the bot.

## Pro Features

Pro is built for teams that want a fuller A&R workflow:

- Custom branding with `/brand`, `/brand_info`, and `/brand_clear`.
- Custom submit panel with `/post_panel`.
- Custom approval and rejection DM templates with `/templates`.
- Custom form prompt with `/form`.
- Cooldowns, duplicate-link behavior, and submission limits with `/limits`.
- Approved/rejected routing channels with `/routing`.
- Footer, logo, and success-message customization with `/extras`.
- Staff notes with `/note`.
- Reviewer assignment with `/reviewer`.
- Shortlist tools with `/shortlist` and `/shortlisted`.
- Priority submissions with `/priority`.
- Demo ratings with `/rate`.
- Saved rejection reasons with `/reasons`.
- Weekly digest with `/digest`.
- Analytics with `/analytics`.
- CSV export with `/export`.
- Storage region selection with `/storage`.
- Optional custom Neon database with `/setup_db`.

When a Pro server changes storage region or connects a custom database, LabelUtils migrates existing submissions, tickets, branding, and Pro settings before switching. Old managed storage is cleaned up after a successful move.

## Support Tickets

Pro servers can run a normal ticket-tool style flow:

- `/ticket_channel` sets the private staff channel where ticket cards appear.
- `/ticket_panel` posts the public button panel users click to open tickets.
- `/tickets` lists recent support tickets.
- `/ticket_set` updates ticket status.

Ticket cards are private to staff. The submitter only gets a confirmation and can be contacted by DM. Staff can press DM on a ticket card, and the user's DM reply is routed back into the ticket thread.

## Premium

- `/premium` shows how to buy premium.
- `/pro_status` checks the server's current premium state.
- `/redeem` redeems a premium coupon for the current server.

Premium is manually handled by the bot owner, so labels can contact the owner, pay, and receive a redeemable coupon.

## Planned Pro+

Pro+ is planned as a deeper white-label option for labels that want LabelUtils to feel like their own private bot.

The idea is to let a Pro+ server connect its own Discord bot token, so the bot can use that label's own bot identity instead of the shared LabelUtils identity. This would allow a custom bot name, avatar, online status, profile description, and invite identity for that label.

Current Pro branding already customizes server-specific messages, embeds, panels, templates, and the bot nickname where Discord permissions allow it. Pro+ would go further by running a separate branded bot connection for the buyer.

This feature is not part of the current Pro plan yet. It requires stronger hosting because every custom bot needs its own Discord gateway connection and more memory. Pro purchases help fund better VPS capacity so Pro+ can be built and supported properly.

When Pro+ is added, Discord bot tokens will be treated like passwords. Tokens should be stored securely, never shown in public messages or logs, and removable or replaceable by the server owner at any time.

## Notes

Discord modals allow up to five text inputs. LabelUtils keeps the five core demo fields and lets Pro servers customize the optional message prompt.

Discord does not allow a bot to have a different avatar or online status per server. LabelUtils branding applies inside server-specific messages and embeds, and `/brand` also tries to update the bot's server nickname when permissions allow it.
