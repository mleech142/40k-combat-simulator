# Self-hosting

A self-hosted copy runs the app on one computer (a home PC, a NAS, a Raspberry Pi or a small rented server) and
everyone in your group opens it in a browser, phones included. Why it's built this way, and what's planned:
[hosted-version-scope.md](hosted-version-scope.md).

> **Release 2.8 is still being built.** A self-hosted copy has **no login yet** (that's step b), so anyone who can
> reach its address can use it and change its data. Until then, run it only where just your group can reach it,
> such as your home network or a [Tailscale](https://tailscale.com) network, never open to the internet.

## Run it with Docker

```bash
docker compose up -d        # with compose.yaml from this repository
```

or

```bash
docker run -d -p 5000:5000 -v combat-sim-data:/data --restart unless-stopped ghcr.io/mleech142/40k-combat-simulator:latest
```

Then open `http://<that computer's address>:5000`. The first visit goes to the Setup page: **Download latest**
fetches the BSData catalogues and **Download** fetches Wahapedia's rules and stratagems, onto the server, the same
way each desktop user gets them. The image ships neither (see the scope, Data licences).

Everything the app writes is in the `/data` volume: the app data JSON, the database (armies, scenarios, games),
`settings.json`, and the BSData and Wahapedia downloads. Back up that volume.

**Updating:** `docker compose pull && docker compose up -d`. Each release publishes `:<version>` and `:latest`. As
with the desktop builds, a newer release's shipped app data doesn't replace your copy.

**Building it yourself:** `docker build -t combat-sim .` in the repository, or uncomment `build: .` in
`compose.yaml`.

## Without Docker

```bash
pip install -r requirements.txt
COMBAT_SIM_DATA=/srv/combat-sim python run.py --hosted --port 5000
```

## Hosted mode

`run.py --hosted` (or `COMBAT_SIM_HOSTED=1`, set in the image) is for a copy other people's browsers reach:

- It listens on every network address (`--host` to choose one) and on exactly the port given, and opens no
  browser.
- `--debug` is refused: Flask's debugger would let anyone who reaches it run code on the server.
- What only makes sense on your own computer is turned off: the folder dialog and choosing a BSData folder by path
  (the Setup page hides them; the catalogues are always the downloaded ones), and changing the relay's address from
  the page (set `RELAY_URL` when starting the copy instead).

Settings:

| Variable | What |
| --- | --- |
| `COMBAT_SIM_DATA` | The data folder (`/data` in the image). Seeded with the shipped app data JSON on first start. |
| `COMBAT_SIM_HOSTED` | `1` for hosted mode (`--hosted` sets it). |
| `RELAY_URL` | The relay for online games (default: the project's, [relay.md](relay.md)). |

## HTTPS and reaching it from outside

The app serves plain HTTP. On a home network or Tailscale that's enough. To reach it over the internet, put a
reverse proxy in front for HTTPS, for example [Caddy](https://caddyserver.com) (`reverse_proxy combat-sim:5000`),
and only once the login (step b) is released.
