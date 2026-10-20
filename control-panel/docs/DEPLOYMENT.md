# Deployment with Portainer

These steps assume Portainer CE managing a single Docker host. Everything is done in Portainer (or its API). The stack never needs
`docker` commands on the host.

## 1. Files and folders on the host

```text
/opt/ai-marketplace-monitor/control-panel/     # this folder (SRC_ROOT), mounted read-only into the containers
/srv/appdata/marketplace-monitor/              # DATA_ROOT, mode 700
    aimm/  forwarder-data/  guard-data/  fb-session/
```

## 2. Create the stack

1. *Stacks → Add stack → Web editor.* Name it `marketplace-monitor` and paste `stack/docker-compose.yml`.
2. *Environment variables:* add every name from `stack/stack.env.example` with your own values. Generate `GUARD_TOKEN` and `AGENT_TOKEN`
   with, for example, `python3 -c "import secrets;print(secrets.token_urlsafe(24))"`.
3. *Deploy the stack.* Three containers start: `marketplace-scraper` (idle, scraper off), `gemini-guard` and `ntfy-to-telegram`.
   All three should report *healthy* within about a minute.

## 3. First run in the panel

Open `http://<LAN_IP>:8480`:

1. **Facebook → Connect.** A browser window opens in the panel. Log in, complete any check Facebook asks for, then press *Save session*.
2. **Searches → New search.** Enter the name (the main phrase), price range, city and radius. Use *Advanced options* for extra phrases.
3. **Notifications.** Send `/start` to your bot, press *Detect chat*, then send a test message.
4. **Home.** Run a manual scan first and read *Scan activity*. Start monitoring when the results look right.

## 4. Updating code

Replace the files in `SRC_ROOT`, then restart only what changed. In Portainer: *Containers → select → Restart*.

| Changed | Restart | Note |
|---|---|---|
| `forwarder/` | `ntfy-to-telegram` | Re-renders `config.toml` on start (validated, written only if different) |
| `gemini-guard/` | `gemini-guard` | |
| `scraper-extras/agent.py`, `aimm.conf` | `marketplace-scraper` | Only while no scan or monitoring runs |
| `scraper-extras/sitecustomize.py`, `listing_check.py` | — | Used at the next scraper start or availability check |

A relevance-rules change (`cfggen.py`, `PROMPT_VERSION`) makes AIMM re-evaluate listings at the next scan, because the cache key includes the rules. That uses AI quota once per listing.

## 5. Health checks after a deploy

- All three containers are *healthy* in Portainer.
- The panel loads. *Home → Connection* shows the scraper agent and the AI as reachable.
- After the next scan, *Scan activity* shows start and end time, the counters and the time breakdown.

## Watchtower

The compose file labels all three services `com.centurylinklabs.watchtower.enable: "false"`. The scraper image is pinned by digest, and updates are deliberate.
