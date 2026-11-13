# Running IMDBTraktSyncer 24/7 on Oracle Cloud (Ampere A1 / ARM64) with Docker

This runs the two-way Trakt ⇄ IMDB sync (watchlist, ratings, reviews, watch history) on a
schedule inside a container. It works on ARM64 (Oracle Ampere A1) and x86_64.

## Why a container, and why it works on ARM64

The desktop version downloads Google's "Chrome for Testing" browser, which has **no Linux ARM64
build**. On ARM64 the script now detects the platform automatically and uses a system-installed
Chromium + chromedriver instead. The Docker image installs Debian's `chromium` and
`chromium-driver` packages, which are built for arm64 and always come as a matching pair.

## Resource use

Measured while a sync is running (headless Chromium, one tab):

| | while syncing | between syncs |
|---|---|---|
| RAM | ~310–380 MB | ~15 MB (scheduler only) |
| CPU | mostly <10% of one core, short spikes during page loads | ~0% |
| Disk | image roughly 600–900 MB (mostly Chromium), `./data` usually <200 MB (browser profile, capped log) | |

A 12 GB / 2 OCPU Ampere VM is far more than enough. The compose file caps the container at
2 CPUs and 2 GB RAM, and the sync runs at a lower CPU priority (`nice 10`), so it won't
starve anything else on the server.

## 1. Prepare the server (Ubuntu 22.04/24.04 on Ampere)

```bash
# Docker Engine + compose plugin (official convenience script)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # log out and back in afterwards
```

Oracle's images ship with a strict iptables setup, but that only affects inbound traffic.
The container only makes outbound HTTPS requests, so no ports need opening.

## 2. Get the code and configure

```bash
git clone https://github.com/thewebdexter/IMDB-Trakt-Syncer.git
cd IMDB-Trakt-Syncer
cp .env.example .env
nano .env                       # Trakt client id/secret, IMDB login, what to sync
mkdir -p data && sudo chown 1000:1000 data   # the container runs as uid 1000
```

Create the Trakt API app at <https://trakt.tv/oauth/applications> with the redirect URI
`urn:ietf:wg:oauth:2.0:oob`.

## 3. Build and run the one-time setup

```bash
docker compose build
docker compose run --rm imdb-trakt-syncer setup
```

`setup` runs one sync interactively. It prints a Trakt URL: open it, approve, and paste the
code back. The Trakt tokens are saved in `./data/credentials.txt` and refreshed automatically
from then on.

## 4. Start it

```bash
docker compose up -d
docker compose logs -f          # watch a sync
cat data/last_run.json          # result of the last sync
docker compose ps               # shows "healthy" once syncs are completing
```

The container restarts on reboot (`restart: unless-stopped`).

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `SYNC_INTERVAL_HOURS` | `6` | Time between syncs. Each sync logs in to IMDB; very frequent runs raise the captcha risk. |
| `SYNC_JITTER_MINUTES` | `20` | Random delay added to each interval, so logins don't follow an exact, bot-like pattern. |
| `RUN_TIMEOUT_HOURS` | `8` | A sync running longer than this is killed along with its browser. Anything unfinished is picked up by the next run. |
| `SYNC_NICE` | `10` | CPU priority of the sync (0–19). |
| `IMDBTRAKTSYNCER_BLOCK_IMAGES` | `false` | Don't load images in the browser (less CPU, RAM and bandwidth). Turn it on and check that one sync still sets items correctly. |
| `SYNC_CPUS` / `SYNC_MEM_LIMIT` | `2` / `2g` | Container resource caps. |
| `SYNC_*`, `REMOVE_*`, `MARK_*` | | The script's sync options, so you don't have to answer prompts. |

## Useful commands

```bash
docker compose run --rm imdb-trakt-syncer once                 # sync right now
docker compose run --rm imdb-trakt-syncer --clear-user-data    # reset credentials + IMDB login
docker compose build --pull && docker compose up -d            # update Chromium/Debian security fixes
```

Rebuild with `--pull` every month or so: the browser visits the open web, so keep it patched.

## IMDB captcha

IMDB sometimes asks for a captcha when it sees a login from a new or datacenter IP, and the
script cannot solve captchas. If the log shows `Error: Not signed in to IMDB`:

1. Wait a few hours and let the next scheduled run try again. It often clears by itself.
2. The browser profile in `./data/chrome-profile` keeps the login cookies, so once a login
   succeeds, later runs usually don't log in again.
3. Keep `SYNC_INTERVAL_HOURS` at 6 or more.

## Security notes

- `./data/credentials.txt` holds your IMDB password and Trakt tokens in plain text. It is
  written with `0600` permissions; use a unique IMDB password.
- `.env` and `data/` are in `.gitignore`. Never commit them.
- The container runs as a non-root user with `no-new-privileges`.

## Without Docker (native)

```bash
sudo apt-get install -y chromium chromium-driver   # Ubuntu: see note below
python3 -m pip install .
export IMDBTRAKTSYNCER_DATA_DIR=$HOME/.imdbtraktsyncer
IMDBTraktSyncer
```

On ARM64 the system browser is picked up automatically. Ubuntu only ships Chromium as a snap;
if you go native on Ubuntu, point `IMDBTRAKTSYNCER_CHROME_BINARY` / `IMDBTRAKTSYNCER_CHROMEDRIVER`
at the snap's binaries, or just use Docker (recommended).
