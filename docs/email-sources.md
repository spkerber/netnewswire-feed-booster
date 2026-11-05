# Email Sources (Future)

**Status: groundwork for a future feature, not usable yet.** The bridge's side is built and tested: the inbound route, sender checks, sanitizer, and feed. The piece that delivers mail to it is not included yet. That piece is a mail-host hook, such as a Cloudflare Email Worker. Without it, an email source cannot receive mail end to end. Neither the Mac-only nor the hosted setup needs any of this. With no secret set, the inbound route rejects every request, so deploying the bridge without configuring email is safe.

Once the hook exists, you'll need a domain of your own on Cloudflare, Email Routing on a subdomain kept for feeds, and one secret. If you only want newsletters as feeds today, a hosted email-to-feed service such as [Kill the Newsletter](https://kill-the-newsletter.com/) does that with no setup. The rest of this page documents the design, for anyone building the hook.

An email source turns a newsletter with no feed into one. Its mail goes to an address of its own, the bridge keeps a sanitized copy of each message, and the feed is served on the usual `/feeds/<token>/generated/<source-id>.rss` route, so NetNewsWire needs nothing new.

```bash
python3 -m netnewswire_feed_booster subscribe-email-source \
  --title "Weekly Notes" --sender-domain news.example.net --group Newsletters
```

The command prints the source's address once, for example `inbox+weekly-notes-<random>@feeds.example.com`, built from `RSS_INBOUND_EMAIL_ADDRESS`. It also writes an empty feed, so NetNewsWire can subscribe before any mail arrives. The address is kept only in the private registry, never in an export or a served feed; `list --show-sensitive` shows it again. Running the command again for the same title replaces its sender domains and keeps the address, and `--new-address` replaces the address too. The bridge reads the registry shipped with each deploy, so run `deploy-modal` after adding or changing an email source. To turn one address off, run `set-status <source-id> --status paused` and redeploy. Its stored items stay until the source leaves the registry.

Run mail routing on a subdomain kept for feeds, never on the domain your own mail uses, and never route a personal mailbox into the bridge. With subaddressing, one routing rule for `RSS_INBOUND_EMAIL_ADDRESS` delivers every source's subaddress. If the mail host can't do subaddressing, set `RSS_INBOUND_EMAIL_ADDRESS` to the bare subdomain (`feeds.example.com`). Addresses then look like `weekly-notes-<random>@feeds.example.com`, and each one needs its own routing rule.

## What the Bridge Accepts

The mail host's inbound hook (for Cloudflare Email Routing, an Email Worker) posts each message on its own:

```text
POST /inbound/email
Authorization: Bearer <RSS_INBOUND_EMAIL_SECRET>
X-Envelope-To: <the envelope recipient>
Content-Type: message/rfc822

<the raw message>
```

The bridge checks the secret, then the declared size, then the address, all before reading the message. Then it reads the message and checks the sender. No rejected message leaves anything behind.

| Status | Meaning | The hook should |
| --- | --- | --- |
| 202 | Accepted: the item is stored and the feed rebuilt | Accept |
| 200 | Already stored: same `Message-ID` | Accept |
| 400 | No usable `X-Envelope-To`, or a message that can't be parsed | Reject |
| 401 | Missing or wrong secret, or no secret of at least 32 characters configured | Fail temporarily, so the sender retries |
| 403 | Not from one of the source's sender domains | Reject |
| 404 | No active email source holds the address | Reject |
| 413 | Larger than `RSS_INBOUND_EMAIL_MAX_BYTES` (default 2 MiB) | Reject |
| 5xx | The bridge failed | Fail temporarily |

**Senders.** The From domain must be one of the source's `--sender-domain` values or a subdomain of one, and the mail host must vouch for it with one of these: a DMARC pass for the From domain, a DKIM pass signed by a sender domain, or an SPF pass for an envelope sender in one. The bridge reads only the first `Authentication-Results` header from `mx.cloudflare.net`. Cloudflare adds that header above everything else in the message; a sender can write its own further down, and those are ignored. A newsletter sent through a mailing service may be signed with the service's domain. Read the first message's `Authentication-Results` before adding a service's domain as a sender, because that admits everyone the service sends for.

**What is kept.** No part of the raw message is written anywhere. The bridge keeps one sanitized item per message under `/cache/email/<source-id>/`, named by a hash of its `Message-ID`, and rebuilds `/cache/generated/<source-id>.rss` from the newest `RSS_MAX_ITEMS_PER_SOURCE` items, deleting older ones. The sanitizer keeps a short list of plain markup and removes the rest:

- scripts, styles, forms, frames, hidden content, event handlers, and every attribute not on its list;
- links named in `List-Unsubscribe`, links whose path, text, or surrounding text is about unsubscribing, preferences, or an account, and every `mailto:` link;
- the recipient's address wherever it appears, including original recipients named in forwarding headers, and percent-encoded and base64 forms of it inside links;
- tracking pixels, every remote image not on the source's `--image-host` list (none by default, because loading a remote image tells the sender when you read it), and tracking query parameters such as `utm_*`.

Each accepted message counts as that source's refresh, so `bridge-health` lists it with the date of its last mail.

## Rotating the Inbound Secret

The secret lives in three places only: `data/private.env`, the Modal secret that `deploy-modal` builds from it, and the inbound hook's own secret store. To rotate it:

1. Generate a new value, for example with `openssl rand -hex 32`, and set it as `RSS_INBOUND_EMAIL_SECRET` in `data/private.env`.
2. Run `./scripts/netnewswire_workflow.sh deploy-modal`. It replaces the Modal secret and redeploys.
3. Set the same value in the hook's secret store. For a Cloudflare Worker that is `wrangler secret put RSS_INBOUND_EMAIL_SECRET`.

Between steps 2 and 3 the bridge answers the hook with 401. A hook that turns a 401 into a temporary failure makes senders retry later, so no mail is lost. Rotate whenever the value may have leaked, and never paste it into a chat, an issue, or a log.
