# Self-hosting

A self-hosted copy runs the app on one computer (a home PC, a NAS, a Raspberry Pi or a small rented server) and
everyone in your group opens it in a browser, phones included. Why it's built this way, and what's planned:
[hosted-version-scope.md](hosted-version-scope.md).

## Run it with Docker

```bash
docker compose up -d        # with compose.yaml from this repository
```

or

```bash
docker run -d -p 127.0.0.1:5000:5000 -e COMBAT_SIM_SECURE_COOKIES=1 -v combat-sim-data:/data --restart unless-stopped ghcr.io/mleech142/40k-combat-simulator:latest
```

As shipped it listens on **this computer only** (`127.0.0.1`), with logins sent only over HTTPS: everyone else
comes in through Tailscale's HTTPS (below) or another HTTPS proxy. Open `http://localhost:5000` on the computer
itself. The first visit creates the host (see Players), then goes to the Setup page: **Download latest**
fetches the BSData catalogues and **Download** fetches Wahapedia's rules and stratagems, onto the server, the same
way each desktop user gets them. The image ships neither (see the scope, Data licences).

## Players

The first visit to a new copy asks for the **host**: the **setup code** the copy prints in its log when it starts
(`docker compose logs combat-sim`, a line like `Setup code: ABCD-2345`; only whoever runs the copy can see it), then
a name, an email and a password. The host downloads the game
data on the Setup page, and adds the other players on the **Players** page (name and email). Each player gets a
link (it works once, for 7 days) to send them, where they choose their own password. Forgotten passwords: the host
makes a new link. Players can use everything else but can't open Setup or the Players page or import app data.

**Changing the shared data:** custom factions, units, weapons, rules and stratagems (and how the maths reads
Wahapedia's) are shared by everyone. A player's change on the Data tab is sent to the host as a **proposal**; the
host's apply at once. The host reviews them on the **Proposals** page (the menu shows how many wait): what each
changes, and a warning if the record was changed after it was proposed, with what changed. Approving makes the
change as if the host had made it; rejecting can give a reason. Players see their own proposals' outcome there and
can withdraw one while it waits.

Tell everyone to **use a throwaway password**: the copy is run by someone in the group, not a company.

**What's whose:** each player's armies are their own, and everyone can see everyone's (read-only: opening another
player's army and saving it makes a copy of your own). Scenarios, comparisons and battlefields are private. Old
records from before the copy had players are the host's.

**Playing each other:** on the Play page's New game form, choose the other player under **Against**, which side
you play, your army and one of theirs. Each of you plays your own side in your own browser, with no relay; Undo
asks the other player, as in an online game.

## "Your move" emails

When a move in a game here makes it the other player's move, they get an email, unless they have the game open,
had one for that game in the last 10 minutes, or turned them off on their account page. Emails go through your
own mail server (for example your email provider's SMTP, with an app password):

| Variable | What |
| --- | --- |
| `SMTP_HOST` | The mail server. Not set: no emails. |
| `SMTP_PORT` | 587 (STARTTLS) by default; 465 for SSL. |
| `SMTP_USER`, `SMTP_PASSWORD` | Its login, if it needs one. |
| `SMTP_FROM` | The sender address (default `SMTP_USER`). |
| `SMTP_STARTTLS` | `0` to turn STARTTLS off (only for a server on your own network). |
| `PUBLIC_URL` | The copy's address for links in the emails (default: the address the move was made from). |

## Data and updates

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
| `COMBAT_SIM_SECURE_COOKIES` | `1` (as `compose.yaml` sets it) to send logins only over HTTPS; browsers also allow them on `http://localhost`. Leave it out only for plain HTTP on a trusted network. |

## With Tailscale (recommended)

[Tailscale](https://tailscale.com) puts your group's devices on one private network, and gives the copy an HTTPS
address with nothing opened to the internet:

1. Install Tailscale on the computer running the copy and log in.
2. `tailscale serve --bg 5000` (the first time it gives a link to turn Serve on for your tailnet; open it, approve,
   and run the command again). The copy is then at `https://<computer>.<tailnet>.ts.net`; this keeps working after
   restarts.
3. Put your own settings in `compose.override.yaml` next to `compose.yaml` (Docker Compose reads both; it's
   gitignored, so passwords stay out of the repository). `compose.yaml` already listens on this computer only with
   secure logins; the override adds the address for email links (and your mail server):

   ```yaml
   services:
     combat-sim:
       environment:
         PUBLIC_URL: https://<computer>.<tailnet>.ts.net
   ```

   then `docker compose up -d`.
4. Each friend installs Tailscale; share the computer with them from the Tailscale admin console (Machines → the
   computer → Share) or invite them to your tailnet. Then add them on the Players page and send their join link.

## HTTPS and reaching it from outside

The app serves plain HTTP. On a home network or Tailscale that's enough. To reach it over the internet, put a
reverse proxy in front for HTTPS, for example [Caddy](https://caddyserver.com) (`reverse_proxy combat-sim:5000`;
the proxy must pass the `Host` header on, as Caddy does), and set `COMBAT_SIM_SECURE_COOKIES=1`. Without HTTPS,
passwords cross the network readable.
