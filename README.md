# AI Marketplace Monitor — Hebrew/English Control Panel Edition

A self-hosted Facebook Marketplace watcher with AI relevance scoring, Telegram alerts and a bilingual web control panel
(**Hebrew RTL by default, English available**). It runs the upstream
[AI Marketplace Monitor](https://github.com/BoPeng/ai-marketplace-monitor) scraper unchanged inside Docker and adds
three small services around it: a control panel with a Telegram forwarder, a guarded AI proxy, and in-container scraper extras.

> **Fork notice.** This repository is a fork of [BoPeng/ai-marketplace-monitor](https://github.com/BoPeng/ai-marketplace-monitor)
> (AGPL-3.0). The `production` branch starts at upstream tag **v0.10.2**, which is the scraper version the stack runs, and adds the
> `control-panel/` directory. `main` mirrors upstream. The upstream README is kept at [docs/UPSTREAM-README.md](docs/UPSTREAM-README.md).
> See [NOTICE.md](NOTICE.md) for attribution and licensing.

---

## Contents

- [Features](#features)
- [Languages](#languages)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Configuration](#configuration)
- [Deployment with Portainer](#deployment-with-portainer)
- [Using the panel](#using-the-panel)
- [AI relevance scoring and cache](#ai-relevance-scoring-and-cache)
- [Scan progress and timing](#scan-progress-and-timing)
- [Listing availability](#listing-availability)
- [Troubleshooting](#troubleshooting)
- [Testing](#testing)
- [Security and privacy](#security-and-privacy)
- [Backup, upgrade and rollback](#backup-upgrade-and-rollback)
- [Disclaimer](#disclaimer)
- [Changes compared to upstream](#changes-compared-to-upstream)
- [Credits and license](#credits-and-license)

## Features

**Searching**
- Saved searches with a main phrase plus extra phrases, price range, per-search city and radius, condition, listing age and delivery method.
- Manual one-off scans, continuous monitoring with a randomised interval, and pause/resume.
- The panel is the only writer of the scraper's `config.toml`. Every change is validated by the scraper's own config loader before it is written.

**Relevance**
- Every listing is rated 1–5 by an AI model (Google Gemini through the included guard). Listings below the threshold are rejected.
- Evidence-based rules: product identity first, from the listing text. Generic titles ("for sale") and one-line mentions in multi-item posts are not enough. The user's description counts as preferences, never as facts assumed to be true.
- AI failures are never turned into a score. Such listings are kept as *unrated* and shown separately.
- Blocking a listing as "not relevant" (wrong item, poor quality, scam) feeds it back to the AI as a negative example.

**Results**
- An all-listings view and a view per saved search. A listing that matches several searches appears in each.
- New-listing and price-drop indicators, saved listings, filters (price, place, price drops), bulk actions and history clean-up.
- Availability lifecycle: listings that were sold or removed are detected with bounded re-checks and hidden by default (see [Listing availability](#listing-availability)).

**Notifications (Telegram)**
- Immediate alerts above a configurable rating or below a "great deal" price, quiet hours, and a daily recap.
- No recap is sent on days when nothing happened. Health alerts (monitoring stuck, AI paused) are sent as separate messages and de-duplicated.
- A persistent outbox retries Telegram delivery. Alerts are never sent twice for the same event.

**Operations**
- Scan activity on the home page: start and end time, duration, outcome, unique listings, AI evaluations and cache hits, rejected, passed, forwarded and saved, failures, the closest rejected examples, and where the time went.
- A live progress view while a scan runs: stage, counters, last step, and stuck detection.
- Facebook login through a browser window inside the container, viewed through the panel (noVNC). Your password is never stored, only the session.
- Docker resource limits, internal-only networks, no published ports except the panel on your LAN address.

## Languages

The whole interface is available in **Hebrew (right-to-left, the default)** and **English**. Switch the language in *Settings*.

The selected language applies to the panel, scan status and outcomes, Telegram alerts and recaps, and the AI's explanations of its ratings.
Listing titles, prices, places and links are always shown as the seller wrote them, never translated.
Changing the language changes only the language of explanations. The rating scale and the response parser are identical in both languages.

## Architecture

```
                         ┌────────────── Docker stack "marketplace-monitor" ──────────────┐
  Facebook  ◀──browser── │ marketplace-scraper  (upstream image, AIMM v0.10.2)            │
                         │   + scraper-extras: control agent, session persistence,       │
                         │     login helper, availability checker, timing                │
                         │        │ AI requests (OpenAI API)       │ matches (ntfy format)│
                         │        ▼                                ▼                      │
  Gemini API ◀────────── │ gemini-guard  ─────────────▶  ntfy-to-telegram (control panel) │ ──▶ Telegram
                         │ pins the model, rate limits,  web UI, settings, config         │
                         │ circuit breaker, validates    generation, listings store,      │
                         │ every verdict                 recaps, health, availability     │
                         └──────────────── internal network "mm_backend" ─────────────────┘
                                                          │ LAN only: http://<LAN_IP>:8480
```

| Service | Code | Role |
|---|---|---|
| `marketplace-scraper` | upstream image + `control-panel/scraper-extras/` | Runs AIMM (off until you start a scan). The control agent on an internal port starts and stops it, validates configs, serves the log, opens the login helper and runs availability checks. |
| `gemini-guard` | `control-panel/gemini-guard/guard.py` | An OpenAI-compatible endpoint for AIMM. Holds the only copy of the Gemini key, pins the model, enforces per-minute and per-day caps, opens a breaker when the quota is exhausted, and accepts only well-formed `Rating N: …` verdicts. |
| `ntfy-to-telegram` | `control-panel/forwarder/` | The control panel (stdlib Python, no framework) and the ntfy-compatible ingest for AIMM's notifications. Stores listings (`alerts.jsonl`), sends Telegram messages, writes `config.toml`. |

State lives in `${DATA_ROOT}`: `aimm/` (generated config, AIMM cache and log, timing), `forwarder-data/` (listings, outbox, settings, scan history, availability), `guard-data/` and `fb-session/` (mode 700).

## Prerequisites

- A Docker host with Docker Engine 24+ (tested with Portainer CE managing the stack), about 2.5 GB RAM for the scraper and 1.5 CPU.
- A Facebook account you are willing to use for Marketplace searches. The account's UI language must be English (US) or Hebrew.
- A Google AI Studio (Gemini) API key. A free-tier key from a project **without billing** is recommended, so usage can never be charged.
- A Telegram bot token from @BotFather, and a chat with the bot.

## Setup

1. Copy `control-panel/` to the Docker host, for example `/opt/ai-marketplace-monitor/control-panel`.
2. Create the data folder (`DATA_ROOT`) with subfolders `aimm`, `forwarder-data`, `guard-data` and `fb-session`, mode 700.
3. Create the stack from `control-panel/stack/docker-compose.yml`, with variables taken from `control-panel/stack/stack.env.example` (see below).
4. Open `http://<LAN_IP>:8480`, log in with `UI_USER`/`UI_PASS` and follow the four setup steps: connect Facebook, create your first search,
   connect Telegram, and start.

Step-by-step Portainer instructions: [control-panel/docs/DEPLOYMENT.md](control-panel/docs/DEPLOYMENT.md).

## Configuration

All settings are in the stack environment. **Never commit real values.** `stack.env.example` lists names and placeholders only.

| Variable | Required | Meaning |
|---|---|---|
| `DATA_ROOT` | yes | Host folder for all state |
| `SRC_ROOT` | yes | Host path of `control-panel/` (mounted read-only) |
| `LAN_IP` | yes | Host LAN address. The panel is published on it only, never on `0.0.0.0` |
| `TZ` | no | Time zone for recaps, quiet hours and quota days (default `Asia/Jerusalem`) |
| `UI_USER`, `UI_PASS` | yes | Panel login (`UI_PASS` ≥ 12 characters) |
| `TG_BOT_TOKEN`, `TG_CHAT_ID` | yes | Telegram bot token and target chat. The panel can detect the chat id for you |
| `GEMINI_API_KEY` | yes | Gemini key. Only the guard sees it |
| `GUARD_TOKEN`, `AGENT_TOKEN` | yes | Random secrets of 16 or more characters for internal calls |
| `GEMINI_MODEL`, `GEMINI_RPM`, `GEMINI_RPD` | no | Pinned model and local request caps (defaults `gemini-3.5-flash-lite`, 8/min, 400/day) |
| `NOTIFY_ENABLED`, `RECAP_HOUR` | no | Initial values only. Afterwards they are set in the panel |
| `RETENTION_DAYS` | no | AIMM cache retention (default 60) |
| `SCRAPER_REPLICAS` | no | 1 = scraper container present (idle until started) |

Optional tuning, set on the panel service unless noted:

| Variable | Default | Meaning |
|---|---|---|
| `AVAIL_CONFIRM_N`, `AVAIL_MIN_GAP_H` | 2, 6 | A listing becomes *unavailable* only after this many failed looks, at least this many hours apart |
| `AVAIL_RECHECK_H`, `AVAIL_BATCH`, `AVAIL_DAILY_CAP`, `AVAIL_IDLE_GAP_MIN` | 12, 8, 60, 30 | Re-check pacing and the daily page budget for the availability check |
| `AIMM_SEE_MORE_WAIT_MS` (scraper) | 1500 | How long to wait for a "See more" button on a listing page |

**Facebook.** Log in through the panel (*Facebook → Connect*). A real browser window opens inside the container and is shown in the panel. You log in, solve any checkpoint yourself, and press *Save session*. Nothing types or clicks for you.

**AI provider.** The guard calls Gemini. To change the model, change `GEMINI_MODEL`. AIMM is configured with `provider = openai` pointing at the guard, and the guard ignores the model name AIMM sends.

**Telegram.** Create a bot with @BotFather, send it `/start`, then use *Notifications → Detect chat* in the panel.

## Deployment with Portainer

The stack is designed to be managed only through Portainer (stack editor or API). The code folders are bind-mounted read-only, so a code update means updating the folder and restarting the affected container in Portainer:

| Changed | Restart |
|---|---|
| `forwarder/` (panel, alerts) | `ntfy-to-telegram` |
| `gemini-guard/` | `gemini-guard` |
| `scraper-extras/agent.py` | `marketplace-scraper`, only while no scan or monitoring runs |
| `scraper-extras/sitecustomize.py`, `listing_check.py` | Nothing. Picked up at the next scraper start |

`control-panel/tools/portainer.py` is an optional API helper. It reads `PORTAINER_URL`, `PORTAINER_KEY_FILE`, `PORTAINER_STACK_ID` and `PORTAINER_ENDPOINT_ID` from the environment, and passes the token to curl from a file so it never appears in logs.

## Using the panel

- **Home:** setup status, the scan button, connection health, and *Scan activity* (the latest scan in plain words plus recent scans).
- **Searches:** create, edit, duplicate, enable or disable, and delete. The search name is the main phrase. *Advanced options* hold extra phrases, keywords, condition and more. Renaming a search shows a warning when old extra phrases would still be searched.
- **Listings:** all results, or *Listings: <search>* for one search. Filters, sorting, saved items, "Not relevant" (feeds the AI), delete, and clean-up. Sold or unavailable listings are hidden unless you switch on *Also show sold or unavailable listings*.
- **Notifications:** Telegram on or off, immediate-alert threshold, quiet hours, recap time, and *Send summary now*.
- **Facebook:** session status, login helper, connection diagnostics.

**Hard constraints and preferences.** The phrases, price range, city and radius are sent to Facebook as search filters. Facebook's radius is approximate, and listings from nearby towns can appear: **the application does not calculate distances itself**. The search description, such as included accessories or condition wishes, is treated by the AI as a preference that ranks matches. It is never assumed to be met.

## AI relevance scoring and cache

For every listing, AIMM builds a prompt from the listing text (title, description, price, condition, location) and the search. **Photos are not sent to the model.** The panel adds two generic blocks to every search (`forwarder/cfggen.py`):

- **Relevance rules:**
  - The internal search id is not a product name.
  - Phrases are alternatives, and Hebrew, English, transliteration and spelling variants count.
  - Product identity must be evidenced by the text, in the title or only in the description.
  - Generic titles are not evidence, and multi-item posts are weak evidence.
  - Parts or accessories only, or a different product that shares a word, are not a match. Bundles are.
  - Preferences are never claimed without evidence.
- **Rating scale:**
  - 1 = not the product
  - 2 = unclear
  - 3 = uncertain or weak evidence
  - 4 = clearly the product
  - 5 = clearly the product, with preferences confirmed or an excellent price

  The explanation is written in the panel language. The default threshold is 4.

The guard accepts only a final line of the form `Rating N: <reason>` with N from 1 to 5. Anything else, such as "Rating: N/A" or "4.5", is retried and then reported as an AI failure. A malformed answer can never become a score.

**Cache.** AIMM caches each verdict under a key made of the search configuration (including the rules text, which carries a version number), the AI and marketplace configuration, and the listing content (photo excluded). Changing a search's phrases, prices or description, the rules version, or the panel language, or a seller editing the listing, therefore produces a fresh evaluation. Two searches never share a verdict. The guard's model is not part of the key: after changing `GEMINI_MODEL`, bump `PROMPT_VERSION` in `cfggen.py` if old verdicts should be re-evaluated.

## Scan progress and timing

While a scan runs, the home page shows the stage (connecting, searching, opening listings, AI evaluation), the counters, the elapsed time, the scraper's last step and its age, and a warning if nothing happened for several minutes.

When a scan ends, the panel records:
- start and end time, and duration. The end is the scraper's last write, so a completion noticed late is not reported as a timeout;
- the outcome: completed, no listings found, all rejected, AI failures, forwarding or storage failure, timeout, cancelled, login or checkpoint;
- unique listings (by Facebook item id), AI evaluations and how many came from cache, rejected evaluations, passed, forwarded and saved, and AI, read and storage failures;
- a rating histogram and the closest rejected examples;
- **where the time went:** listing pages, AI evaluation (including time spent waiting for quota or backoff), forwarding and storage, and the remainder (search pages, browser, login). These come from cumulative timers in the scraper process (`scraper-extras/sitecustomize.py` → `timing.json`) and in the guard, never from estimates.

One measured improvement is included. AIMM waited up to 8 seconds per listing for a "See more" button. On the measured scans the button never appeared, and the timeout cost about 8 seconds per listing page. The wait is now 1.5 seconds (`AIMM_SEE_MORE_WAIT_MS`), and a present button is still clicked.

## Listing availability

Stored listings are re-checked in small batches using the saved session, and only when it is safe:
- while the scraper is off, at most every 30 minutes;
- or right after a monitoring cycle ends, while AIMM's browser is idle;
- never during a manual scan or while the login helper is open;
- 8 pages per batch and 60 per day by default.

| State | Meaning |
|---|---|
| Available | The page shows the listing, and Facebook's own data for that listing says it is live and not sold |
| Sold | Facebook's own data for that listing says `is_sold: true` |
| Unavailable | The page stopped showing the listing (Facebook redirects removed listings to Marketplace) on 2 looks at least 6 hours apart. The listing may have been removed, expired, hidden or sold |
| Unverified | Not yet checked successfully |

A network error, timeout, login wall, checkpoint or ambiguous page never changes a state. A login wall pauses checks for 6 hours. A listing that appears again in a search becomes available again. Original records are never modified: availability is a separate file.
Status changes do not send new-listing alerts.

**Known limitations.** "Sold" depends on Facebook exposing the flag on the listing page. Unavailable covers every other reason a listing disappears. Facebook can change these pages at any time. In that case, checks report *unverified* instead of guessing.

## Troubleshooting

| Symptom | What to check |
|---|---|
| "The scan returned no listings" | Search city and phrases. Try the same search on Facebook in a browser |
| "Listings found but all rejected" | Open *Why rejected?* under Scan activity. The closest misses and the rating spread show whether the search description is too narrow |
| Many AI failures | The guard's quota (the panel shows when the breaker reopens) and `GEMINI_RPD` |
| Scan looks stuck | *Last step* and its age on the home page. Facebook checkpoints are reported explicitly. Cancel and use *Facebook → Connect* |
| "Could not read the listings" | The Facebook account language must be English (US) or Hebrew |
| No Telegram messages | *Notifications* toggle, quiet hours, and the outbox line in the daily recap |
| Listing marked unavailable but it is still online | It will return to *Available* at the next search hit or check. Raise `AVAIL_CONFIRM_N` if this repeats |

## Testing

The control panel has an offline test suite (no Docker, Facebook, Telegram or network needed):

```bash
cd control-panel
bash tests/run_all.sh
```

Browser and real-AIMM tests (`test_ui_browser`, `test_session_patch`, `test_logflood`) run when `AIMM_LIB` points to a folder with
`ai_marketplace_monitor==0.10.2` and Playwright installed. The upstream package keeps its own tests under `tests/`.

## Security and privacy

- Secrets live only in the stack environment. The Gemini key exists only in the guard.
- The panel requires login, uses CSRF protection and Origin checks, and is published on the LAN address only. All other services use an internal network.
- Containers are read-only where possible and drop capabilities. No-new-privileges is set, and resource limits are applied.
- The Facebook session file stays in its own mode-700 folder and is mounted into the scraper only. No password is ever stored.
- Logs and diagnostics never print tokens, cookies or prompt text. Scan diagnostics show listing titles and AI reasons only.
- Data stays on your host. Listing data goes to Telegram only, and listing text goes to Gemini for rating.

## Backup, upgrade and rollback

- **Backup:** stop monitoring, then copy `${DATA_ROOT}` (all state) and your stack environment. `fb-session/` contains a live session, so store it like a password.
- **Upgrade the control panel:** replace `control-panel/` with the new version, then restart the containers listed in [Deployment](#deployment-with-portainer). Settings are migrated on load.
- **Upgrade the scraper:** the image is pinned by digest. The extras patch AIMM 0.10.2 internals and fail safe, but test a new image on a copy first.
- **Rollback:** restore the previous `control-panel/` folder and restart the same containers. The data format is backwards compatible: new fields are optional and old records are read as they are.

## Disclaimer

This project automates a personal Facebook account's view of Marketplace. Facebook may change its pages, rate-limit or challenge the account,
or disallow automated access under its terms at any time. Login and session behaviour (checkpoints, expiry) directly affects scraping, and
results can be incomplete. Use it with moderate search intervals and at your own risk. The AI ratings are suggestions, not facts.

## Changes compared to upstream

A categorised comparison is in [control-panel/docs/CHANGES-VS-UPSTREAM.md](control-panel/docs/CHANGES-VS-UPSTREAM.md).

## Credits and license

- Scraper, search and AI evaluation engine: **AI Marketplace Monitor** by Bo Peng and contributors, <https://github.com/BoPeng/ai-marketplace-monitor>.
- This fork and its control panel are distributed under the same license, the **GNU Affero General Public License v3.0** ([LICENSE](LICENSE)). See [NOTICE.md](NOTICE.md).
