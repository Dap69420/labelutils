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

Basic setup takes a few commands:

1. Run `/start label_name:<your label name>` to create managed storage for the server.
2. Run `/setup_staff channel:<staff channel>` to choose where new demo submissions are sent.
3. Run `/setup` to check that storage, staff channels, branding, and Pro settings are ready.

Free servers are automatically assigned managed storage. Pro servers can later use `/storage` to choose a region or `/setup_db` to connect a custom Neon database. Existing LabelUtils data is migrated when Pro storage is changed.

## Commands

### Everyone

- `/help` shows the command list and setup steps inside Discord.
- `/premium` shows how to buy premium for the server.
- `/pro_status` checks whether the current server has premium.

### Artists

- `/submit` opens the demo submission form.
- `/submission ticket_id:<id>` looks up one submission. Artists can only view their own submissions, while staff can view any submission.
- `/my_subs` shows your submissions in the current server.
- `/my_demos` shows a longer list of demos you submitted.
- `/my_stats` shows your total submitted, accepted, rejected, and queued demos.
- `/leaderboard` shows the artists with the most accepted demos, using Discord users so name variations do not split the stats.

### Server Setup

- `/start label_name:<name>` creates managed LabelUtils storage for the server. This is the normal no-Neon setup for free servers.
- `/setup_staff channel:<channel>` sets the private staff channel where new submission cards are posted.
- `/setup` shows the full setup status: storage, premium, branding, staff channel, ticket channel, and thread permissions.
- `/db_status` checks whether server storage is connected.
- `/staff_status` checks which staff channel is connected.
- `/setup_db` lets Pro servers connect a custom Neon database.
- `/storage region:<region>` lets Pro servers move managed storage to West US, Europe (UK), or South-East Asia.

### Staff Submissions

- `/queue` shows the newest submissions still in queue.
- `/recent` shows the newest submissions from all statuses.
- `/panel` opens an admin browser with filters, pages, and refresh controls.
- `/status ticket_id:<id> new_status:<status>` updates a submission to In Queue, Needs Review, Shortlisted, Contacted, Signed, Approved, or Rejected.

Staff submission cards also include approve, reject, and DM actions. Each submission can open a private staff discussion thread, so staff notes, DM replies, and release logs stay attached to the right ticket.

DM reply forwarding works when the artist replies directly to the bot's DM message. Attachments are forwarded as Discord attachment links, so files are not downloaded or reuploaded by the bot.

### Pro Branding And Workflow

Pro is built for teams that want a fuller A&R workflow:

- `/brand` opens a form to set the server's display name, required submit panel caption, and embed color.
- `/brand_info` shows the current Pro branding and premium state.
- `/brand_clear` resets custom branding back to server defaults.
- `/post_panel` posts a branded public submit button panel.
- `/form label:<text> placeholder:<text>` customizes the optional submission question.
- `/templates` opens a form to edit approval and rejection DM templates. Either template can be changed on its own.
- `/limits cooldown_minutes:<minutes> max_submissions_per_user:<count> duplicate_policy:<policy>` configures cooldowns, total user caps, and duplicate-link handling.
- `/routing approved_channel:<channel> rejected_channel:<channel>` sends approved and rejected updates to selected channels.
- `/extras footer_text:<text> logo_url:<url|none> success_message:<text>` customizes staff card footers, thumbnails, and submitter confirmation text.

### Pro A&R Tools

- `/note ticket_id:<id> note:<text>` adds a private staff note to a submission.
- `/reviewer ticket_id:<id> reviewer:<member>` assigns a reviewer to a submission.
- `/shortlist ticket_id:<id> enabled:<true|false>` adds or removes a submission from the shortlist.
- `/shortlisted` shows shortlisted submissions.
- `/priority ticket_id:<id> enabled:<true|false>` marks or unmarks a submission as priority.
- `/rate ticket_id:<id> score:<1-10>` gives a demo an internal rating.
- `/reasons reasons:<reason | reason>` sets saved rejection reasons, or shows the current saved reasons when left blank.
- `/digest channel:<channel>` posts a weekly submission digest and can save the digest target channel.
- `/analytics` shows Pro submission analytics.
- `/export` exports submissions as a CSV file.

When a Pro server changes storage region or connects a custom database, LabelUtils migrates existing submissions, tickets, branding, and Pro settings before switching. Old managed storage is cleaned up after a successful move.

## Support Tickets

Pro servers can run a normal ticket-tool style flow:

- `/ticket_channel channel:<channel>` sets the private staff channel where ticket cards appear.
- `/ticket_panel channel:<channel>` posts the public button panel users click to open tickets.
- `/tickets status:<optional>` lists recent support tickets, optionally filtered by Open, Waiting, Answered, or Resolved.
- `/ticket_set ticket_id:<id> new_status:<status>` updates a support ticket to Open, Waiting, Answered, or Resolved.

Ticket cards are private to staff. The submitter only gets a confirmation and can be contacted by DM. Staff can press DM on a ticket card, and the user's DM reply is routed back into the ticket thread.

## Premium

- `/premium` shows how to buy premium.
- `/pro_status` checks the server's current premium state.
- `/redeem code:<coupon>` redeems a premium coupon for the current server.
- `/coupon days:<days> uses:<uses> plan:<plan> code:<optional>` lets the bot owner create reusable premium coupons.
- `/pro_add guild_id:<id> days:<days> plan:<plan>` lets the bot owner manually grant premium.
- `/pro_remove guild_id:<id>` lets the bot owner remove premium from a server.

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
