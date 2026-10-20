# Changes compared to upstream

Upstream: [BoPeng/ai-marketplace-monitor](https://github.com/BoPeng/ai-marketplace-monitor). The stack runs the upstream image at **v0.10.2**,
pinned by digest. Upstream's own source is **not modified**. Everything below is either new code around it (`control-panel/`) or a runtime patch
loaded only inside the scraper process (`scraper-extras/sitecustomize.py`), and every patch fails safe.

## Original upstream behaviour retained

- Facebook Marketplace search, listing retrieval and parsing, price and keyword filters, and AIMM's listing and AI-verdict cache.
- AI evaluation flow and prompt structure (`AIBackend.get_prompt`): the fork adds a per-search `extra_prompt` and `rating_prompt`, which are upstream config options.
- Search scheduling (interval and random max interval), and the ntfy notification format AIMM emits (consumed by the forwarder).
- The configuration file format (`config.toml`). The panel generates it, and AIMM's own loader validates every version.

## New features

- **Bilingual control panel** (`forwarder/panel.py`, `static/`): setup wizard, saved searches, manual scan, monitoring and pause, per-search and all-listings views, saved and blocked listings, clean-up, settings.
- **Telegram forwarder** (`forwarder/forwarder.py`): immediate alerts with rating and great-deal price thresholds, quiet hours, a daily recap (skipped when nothing happened), separate health alerts, and a persistent outbox with retries.
- **AI guard** (`gemini-guard/guard.py`): an OpenAI-compatible proxy to Gemini with a pinned model, per-minute and per-day caps, a quota circuit breaker, and validated verdicts.
- **In-browser Facebook login** (`scraper-extras/login_helper.py`, noVNC through the panel) and **session persistence** across scraper restarts.
- **Feedback loop** (`forwarder/feedback.py`): listings blocked as wrong, poor or scam become "do not rate high" examples for the AI.
- **Scan activity and history:** outcome classification, counters, rejection examples, timing breakdown, and live progress with the last step.
- **Listing availability lifecycle** (`forwarder/availability.py`, `scraper-extras/listing_check.py`): bounded re-checks; sold, unavailable and unverified states.

## Bug fixes (relative to running upstream as-is, or to earlier fork versions)

| Problem | Fix |
|---|---|
| AIMM opens the prompt with the config section name ("buy a p2"). The model read the id as a model number ("not the SodaStream P2") | A generic context block says the id is internal and gives the real search name |
| Phrases joined with "and" were read as one combined product | The rules state that phrases are alternatives |
| A clearance post titled "למכירה" (several items, one line "SodaStream 50₪") was rated 4 and alerted | Evidence-based rules and scale: generic titles are not evidence, multi-item mentions are weak (rated at most 3) |
| AIMM's verdict parser takes the last line matching `Rating…[1-5]`, so "Rating: N/A (price 150)" parses as 1 and stray "Rating" words can override | The guard accepts only a well-formed final `Rating N:` line, neutralises the word elsewhere, and treats anything else as invalid (retried, then an AI failure, never a score) |
| AIMM pluralises the item name in notification titles ("Found 11 new p2s"). Short ids were stored under a non-existent search, missing from that search's view and its currency and alert rules | Titles are resolved against the real search ids, and old records are mapped when read (stored data untouched) |
| "updated", "discounted" and "Another look" titles were not recognised | Title parser covers all AIMM title forms |
| Scan counter "reviewed" counted only freshly parsed pages (19), while 68 unique listings and 66 AI evaluations happened | Unique Facebook item ids, separate AI evaluations and cache hits |
| Editing a search silently dropped the first extra phrase when it differed from the name, and renaming kept unrelated old phrases unnoticed | All extra phrases are shown, with a warning on rename |
| A finished scan noticed late got the notice time as its end | End = the scraper's last write |
| Generator fixes only reached `config.toml` after a settings edit | The panel re-renders (validated, only when different) on start |

## Performance changes

- AIMM waited up to 8 s per listing page for a "See more" button. Measured on production scans, 61 waits for 59 pages all timed out. The wait is now 1.5 s (`AIMM_SEE_MORE_WAIT_MS`), and buttons that are present are still clicked. Expected saving: about 6.5 s per opened listing page. The new per-stage timers measure the real effect.
- Stage timers (listing pages, AI, AI quota wait, forwarding and storage, remainder) make further bottlenecks visible without guessing.
- No concurrency was added. Facebook session stability and the AI quota favour one page and one AI request at a time.

## UI and language

- Hebrew RTL is the default, with English as a full alternative. The panel, scan outcomes, Telegram alerts, recaps and AI explanations follow the selected language. Listing text is never translated.
- Scan activity: start, end, duration, outcome, counters, *Why rejected?* (rating spread and closest misses), and time breakdown.

## Storage and listing lifecycle

- Listings: append-only `alerts.jsonl`, one record per change, with deduplication of identical deliveries. A listing can belong to several searches.
- Availability in a separate `listing-availability.json`. Original records are never rewritten.
- Search ids are never reused, so a new search never inherits a deleted search's listings.

## Deployment and operations

- A Docker Compose stack for Portainer: internal network, LAN-only panel port, read-only containers, resource limits, Watchtower disabled, and a scraper image pinned by digest.
- An optional Portainer API helper (`tools/portainer.py`). The token is read from a file and never printed.
- Daily AIMM cache pruning and a log-health summary.
- An offline test suite (about 350 checks), plus optional browser and real-AIMM tests.

## Known limitations and divergence from upstream

- The fork tracks **v0.10.2**. Upstream has since released newer versions (0.10.x, with new notification channels and fixes) that are not
  included. The runtime patches target 0.10.2 internals (`FacebookItemPage._expand_see_more`, `FacebookMarketplace.get_listing_details`,
  `OpenAIBackend.evaluate`, `User.notify`). They fail safe on other versions, but upgrading needs a test pass.
- Only Facebook Marketplace and the Gemini guard are wired into the panel. Other AIMM providers and marketplaces are untested here.
- Distance is enforced only by Facebook's search radius. The application does not compute distances.
- "Sold" is shown only when Facebook exposes its `is_sold` flag. Other disappearances are reported as "unavailable".
- Photos are not analysed by the AI.
