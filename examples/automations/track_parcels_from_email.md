# Automatic parcel tracking from e-mail (IMAP → Quickpac)

Companion guide for [`track_parcels_from_email.yaml`](track_parcels_from_email.yaml): watch your mailbox(es) for shipping e-mails, extract the Quickpac tracking code, and register it with `quickpac.track_parcel` — fully automatic, no extra custom component required.

Quickpac is a **code-based** carrier: it has no account inbox, so every parcel must be registered by its tracking code before the integration can follow it. This recipe automates exactly that step.

**How it works, in one line:** the core [IMAP integration](https://www.home-assistant.io/integrations/imap/) fires an `imap_content` event for every new e-mail (including the body); a keyword gate flags likely shipping mail, an AI Task extracts the tracking code, and the automation calls `quickpac.track_parcel`.

```
new e-mail ──imap_content──▶ automation ──▶ looks like a shipping mail? ──▶ ai_task.generate_data ──▶ quickpac.track_parcel
```

**No regex fast path, unlike this integration's siblings.** Quickpac identcodes are confirmed to be **plain digits with no letter prefix** (see `carrier-research/api/quickpac/tracking.md`), but no real identcode has ever been captured, so the *length* is unconfirmed. A bare digit-string regex would match order numbers, invoice numbers and phone numbers just as readily as a tracking code — there is nothing safe to anchor on yet. This recipe goes straight to the AI fallback.

## Prerequisites

- This integration, with the `quickpac.track_parcel` action available (field `tracking_code`).
- The core **IMAP** integration (ships with Home Assistant, no HACS needed).
- An **AI Task** entity (e.g. Anthropic/Claude, Google, OpenAI) — **required** here, not optional, since there is no regex fallback to fall back to.

## Step 1 — IMAP entries

Add **Settings → Devices & services → Add integration → IMAP** for every account you want to watch:

| Field | Value |
|---|---|
| Server | `imap.gmail.com` (Gmail) — mind the hostname, it is **not** `imap.google.com` |
| Port | `993` |
| Username | your address |
| Password | see the Gmail note below |
| Charset | `utf-8` |
| Folder | `INBOX` (or a label/subfolder — see below) |

Then open the entry's **Configure** (options) and set:

- **Message data to include in the event**: enable **text** (the automation needs the body!)
- **Max message size**: raise it to `30000` — carrier mails are long and the default cuts them off before the tracking code appears.
- *search*: `UnSeen UnDeleted` works, but **scoping it to the carrier's sender is recommended** — see [Scope & privacy](#scope--privacy) below. Keep *push* enabled (IMAP IDLE → events arrive within seconds).

**Multiple mailboxes / accounts:** each IMAP entry is one account × folder combination. Add the same account again with a different folder to watch labels (Gmail labels appear as IMAP folders). All entries fire the *same* `imap_content` event, so **one automation covers all of them**.

**Gmail note:** since May 2025 Google blocks plain-password IMAP logins ("less secure apps"). Use an **app password** instead (requires 2-step verification): <https://myaccount.google.com/apppasswords>.

## Scope & privacy

By default this recipe is broad. The core IMAP integration fires an
`imap_content` event — **including the full message body** — for *every* new
mail its *search* matches, and the automation reacts to **all** of those events.
With the default `search: UnSeen UnDeleted` that means every incoming e-mail
runs through the automation's templates, and — if you keep the AI fallback —
every mail passing the keyword gate has up to 6000 characters of its body sent
to your AI Task, **possibly a cloud model**.

None of that data leaves through *this* integration — it only exposes the
`track_parcel` action. The mailbox access and the event stream belong to Home
Assistant's **core IMAP integration**, using the username / app-password you
gave it — which grants full read access to your **entire** mailbox, not just
parcel mail. So it is worth narrowing what it ever sees.

Narrow it at the source (most effective first):

1. **Scope the IMAP `search` to the carrier's sender.** In the IMAP entry's
   options, e.g. `search: FROM "noreply@thecarrier.example" UNSEEN` (chain
   several with `OR`: `OR FROM "a@x" FROM "b@y" UNSEEN`). Only matching mail
   ever becomes an event, so the automation — and the AI — never see the rest.
2. **Or point the entry at a dedicated folder/label.** Add a server-side mail
   rule that files shipping notifications into e.g. a `Parcels` label, and set
   the IMAP entry's *Folder* to it. Same effect, and it survives sender changes
   better.
3. **Add a sender allowlist** as an extra automation `condition` — defense in
   depth if you keep a broad search.
4. **There is no local-only option here** — with no regex fast path, every
   candidate mail's body goes to the AI Task. Narrowing the IMAP `search` (#1)
   is the only way to limit that.

## Step 2 — the automation

Paste [`track_parcels_from_email.yaml`](track_parcels_from_email.yaml) and adapt the notify action, the keyword list and the AI entity to your setup.

### Tracking-code format

Confirmed: **plain digits, no letter prefix** — Quickpac's own OpenAPI document types the identcode as `integer($int64)` on two of its routes, which rules out a letter prefix outright. Unconfirmed: the length. No real identcode has ever been captured (`carrier-research/quickpac.md`, `blocker: real-parcel`), so this example cannot anchor a regex the way `ha-planzer`'s (`[0-9]{4,6}[.][0-9]{8,12}`) or `ha-dragonfly`'s do — a bare digit string would match order numbers, invoice numbers and phone numbers just as readily. Once a real identcode's length is confirmed, add a regex fast path here (see those two repos' examples for the pattern) and open an issue/PR so the next person benefits.

### Design notes

- **AI-only, not AI-fallback.** Every mail that passes the keyword gate goes to the AI Task — there is no cheap regex path to skip it for the common case.
- **Duplicates are harmless:** calling `track_parcel` twice for the same code is a no-op, and the `initial` condition already suppresses re-triggers of the same message.
- **`mode: queued`** so a burst of mails (mailbox sync) is processed one by one instead of being dropped.

## Pitfalls we hit so you don't have to

1. **The `initial` event flag means the opposite of what you might expect.** In the IMAP integration `initial: true` = *first time this message is seen* (new mail); `false` = a duplicate trigger of the same message. So the condition must **require** `initial`, not exclude it.
2. **Raise the max message size.** With the default the body is truncated before the tracking code appears in most carrier mails. `30000` is plenty.
3. **Enable "text" in the event options.** Without it the event has headers only and there is nothing to extract.
4. **Gmail = app password.** Plain passwords stopped working on Google IMAP in May 2025; app passwords (with 2FA) are the supported route. And the host is `imap.gmail.com`.

## Testing without waiting for a real parcel

Fire a fake event and watch the automation trace (Settings → Automations → your automation → Traces):

```bash
curl -X POST -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" \
  http://YOUR_HA:8123/api/events/imap_content \
  -d '{"sender":"noreply@quickpac.ch","subject":"Ihre Sendung ist unterwegs",
       "text":"Sendungsnummer: 990000123456","initial":true,"folder":"INBOX","username":"test"}'
```

Then `quickpac.untrack_parcel` the test code afterwards. For a full end-to-end test, forward a real shipping mail to the watched mailbox — it must arrive **unread**.
