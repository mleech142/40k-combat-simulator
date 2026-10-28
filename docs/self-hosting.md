# Self-hosting

A self-hosted copy runs the app on one computer (a home PC, a NAS, a Raspberry Pi or a small rented server) and
everyone in your group opens it in a browser, phones included. Why it's built this way, and what's planned:
[hosted-version-scope.md](hosted-version-scope.md).

> **Release 2.8 is still being built.** Logins work; players' own armies and games, "your move" emails and
> proposed edits are still to come (the scope's section 8).

## Run it with Docker

```bash
docker compose up -d        # with compose.yaml from this repository
```

or

```bash
docker run -d -p 5000:5000 -v combat-sim-data:/data --restart unless-stopped ghcr.io/mleech142/40k-combat-simulator:latest
```

Then open `http://<that computer's address>:5000`. The first visit creates the host (see Players), then goes to
the Setup page: **Download latest**
fetches the BSData catalogues and **Download** fetches Wahapedia's rules and stratagems, onto the server, the same
way each desktop user gets them. The image ships neither (see the scope, Data licences).

## Players

The first visit to a new copy asks for the **host**: a name, an email and a password. The host downloads the game
data on the Setup page, and adds the other players on the **Players** page (name and email). Each player gets a
link (it works once, for 7 days) to send them, where they choose their own password. Forgotten passwords: the host
makes a new link. Players can use everything else but can't open Setup or the Players page, or change the shared
data (custom factions, rules, stratagems): later in 2.8 they'll propose changes for the host to approve.

Tell everyone to **use a throwaway password**: the copy is run by someone in the group, not a company.

Everything the app writes is in the `/data` volume: the app data JSON, the database (armies, scenarios, games),
`settings.json`, `secret_key` (signs logins: keep it private), and the BSData and Wahapedia downloads. Back up that
volume.

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
| `COMBAT_SIM_SECURE_COOKIES` | `1` once it's served over HTTPS, so browsers send the login only over HTTPS. |

## HTTPS and reaching it from outside

The app serves plain HTTP. On a home network or Tailscale that's enough. To reach it over the internet, put a
reverse proxy in front for HTTPS, for example [Caddy](https://caddyserver.com) (`reverse_proxy combat-sim:5000`;
the proxy must pass the `Host` header on, as Caddy does), and set `COMBAT_SIM_SECURE_COOKIES=1`. Without HTTPS,
passwords cross the network readable.
