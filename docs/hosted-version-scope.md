# Hosted version: scope

Testing list item 12, written October 2026. The owner's decisions are in section 7, and the build plan that follows
from them is section 8.

The question: could players use the app in a browser, and later on a phone, with nothing to install? That means
either a **Docker image** a group runs for itself, or a **hosted site** the owner runs for everyone. Either way
it reopens the settled Platforms decision in [play-mode-design.md](play-mode-design.md): "not hosting the engine
and BSData-derived content on a server".

## 1. Data licences (the deciding question)

Today the app ships none of the game data. Each user downloads BSData and Wahapedia themselves, onto their own PC
(`CLAUDE.md`, Data). The relay carries frozen army snapshots (names, stats, weapon profiles) between two friends,
but no rule text.

What each source says (checked October 2026):

| Source | Licence | What it means for a server |
| --- | --- | --- |
| **BSData wh40k-11e** | None. The repository has no licence file (GitHub reports none) and its README says only "maintained by community, in no way endorsed by any company/publisher". | With no licence, the files are all rights reserved by their contributors. The content itself (unit names, stats, points, wargear) is Games Workshop's. A server that downloads them once and shows them to many people is redistributing them. |
| **Wahapedia export** | None stated. The export page carries "© Vyacheslav Maltsev 2013-2026" and asks that published work mention Wahapedia ("powered by Wahapedia"). | Stratagems, detachment and army rules word for word. Showing them on a site anyone can reach is publishing Games Workshop's rule text. This is the highest-risk data. |
| **Games Workshop IP guidelines** | Fan sites are tolerated if non-commercial and marked "unofficial". Computer games and apps based on its settings may be made only under licence. Fan content must not reproduce rules or stats from its publications. Zero tolerance on infringement. | A public hosted site holding rules and stats for everyone is the clearest breach of these. A private copy among friends looks the same as today's desktop app. The Vassal 40K module's maintainer got a cease-and-desist in 2008. |

The Games Workshop page blocks automated reads, so its terms above come from coverage of its 2021 update
([Spikey Bits](https://spikeybits.com/lookout-youtube-gw-just-updated-their-ip-guidelines/),
[Dakka Dakka](https://www.dakkadakka.com/dakkaforum/posts/list/799841.page),
[Frontline Gaming](https://frontlinegaming.org/2021/07/25/how-should-gw-deal-with-its-ip/)). **The owner should
read the current text** ([Intellectual Property Guidelines](https://www.warhammer.com/shop/Intellectual-Property-Guidelines))
before deciding.

**Conclusion.** The data rules out a site the owner hosts for the public: it would hold and serve BSData-derived
stats and Wahapedia's rule text to anyone. A **self-hosted** copy is different: the people who run it download the
data onto their own server themselves, exactly as each user does on a PC today, and only their group reaches it.
That keeps the current position ("we never ship or host the data") intact.

## 2. The options

### A. Docker image for self-hosting (recommended first step)

A group (or one player) runs the app on a home PC, NAS, Raspberry Pi or a small rented server, and everyone in the
group opens it in a browser, phones included.

- **The image ships no data,** like the Windows and Linux builds. On first start the Setup page downloads BSData
  and Wahapedia onto the server (into a mounted data folder: the app data JSON, the database, `settings.json`).
- **Play without the relay:** both players can use the same instance, each in their own browser. Today a game's
  seat is per install (`SavedGame.seat`), so this needs a seat per browser (see section 3). Games with players on
  other instances still go through the relay.
- **Work needed** (rough sizes):
  - *Small:* a Dockerfile and compose file; a `HOST` / `PORT` setting in `run.py` (today it binds 127.0.0.1 only);
    data folder from an environment variable (`paths.py` already takes `DATA_JSON`, `DATABASE_URL`,
    `BSDATA_DIR`, `WAHAPEDIA_DIR`); docs.
  - *Medium:* a **hosted mode** switch that turns off what only makes sense on your own PC: the native folder
    dialog (`/api/bsdata/browse`) and choosing any folder path (`/api/bsdata/folder` would let a visitor point the
    app at any folder on the server); the relay address setting (`/api/play/relay`) becomes admin-only.
  - *Medium:* a **login** (one shared group password at first) and protection against cross-site requests on every
    write. Today any page that can reach the app can change its data, which is safe only because it listens on
    127.0.0.1.
  - *Medium to large:* **per-player seats and lists** (section 3).
- **Upkeep:** the group's own. Updates are pulling a new image. Release builds would add the image to
  `release.yml`.

### B. A site the owner hosts for everyone (not recommended)

Everything in A, plus: the owner's server holds and serves the data (section 1 rules this out), real accounts with
emails and password resets, per-user storage for every table, abuse protection, backups, and running costs that
grow with users. It also attracts attention, which the design avoids on purpose.

### C. The phone app with no server (the design's existing plan)

The Platforms plan: an installable web app running the Python engine in the browser through Pyodide, with each
user's data on their own device. It keeps the data position of today and needs no hosting. Open problems found
while scoping:

- Downloading BSData and Wahapedia from inside a browser may be blocked: other sites have to allow it (CORS), and
  GitHub's zip downloads and Wahapedia probably don't. Users could pick the files from their device instead.
- Flask and SQLite would have to run in the browser, or the routes be called directly; numpy is available in
  Pyodide.
- It's the largest of the three, and A already gives phones a browser to use.

## 3. Accounts and per-user storage

The app is built for one user per install: one SQLite database, one `settings.json` (player name, relay address,
Show Play), one app data JSON.

| Data | Today | On a shared instance |
| --- | --- | --- |
| BSData, Wahapedia, reference tables | One per install | Shared: fine as is. |
| App data JSON (custom factions, rules, stratagems, unit modifiers) | One per install | Shared by the group. Edits by one player change it for everyone: fine for friends, or admin-only. |
| Saved armies, scenarios, comparisons, battlefields | One user | Need an owner (a `user` column, nullable so old rows stay the admin's) and filtering in every list route. |
| Saved games and seats | `SavedGame.seat` per install | A seat per player: the game records which user plays each side, and `play/seats.py` checks the logged-in user instead of the install. |
| Settings | One file | Split: server settings (admin) and each player's (name). |

**Smallest useful version:** one group password and everyone shares everything, games played hot-seat or with
each side picked in the browser (trusted, like dice today). **Next:** named players (a name and password each, no
emails), owning their armies and their seat in a game.

## 4. Hosting, costs and security

- **Costs:** self-hosting on hardware the group already has is free; a small rented server is a few pounds a
  month. The app is light (SQLite, one process) apart from Monte Carlo runs and Analytics matrices, which use one
  CPU core each for a few seconds.
- **Security once it's reachable from other machines:**
  - HTTPS through a reverse proxy (Caddy or similar) in the compose file; the app never serves HTTPS itself.
  - The login and cross-site request protection above; `--debug` stays refused in hosted mode.
  - Uploads are already checked: game files (`game_file.check_game`), army share codes (`army_share.py`) and app
    data imports (`/api/data/import`, which should become admin-only).
  - Downloads only from the fixed BSData and Wahapedia addresses (already the case).
  - Advice in the docs: don't expose it to the whole internet without the login; a VPN such as Tailscale is the
    simplest safe setup for a group.

## 5. Phone-sized pages

Every page is built for a desktop screen and mouse. A self-hosted instance makes phones possible straight away,
but the pages need work to be usable:

- Simulator, Armies and Match view: mostly layout (stacking columns, larger buttons). Medium.
- Analytics' matrix: needs a phone layout of its own. Medium.
- The Play board: touch for pan, zoom and dragging units, long-press instead of hover for the unit card, and the
  side panels as drawers. Large; the design already asked for touch-ready controls.

## 6. Order (agreed)

1. **Docker self-hosting with named players** (sections 2A and 3), with the host approving players' edits to the
   shared data. Its own release.
2. **Phone layouts,** page by page, the Play board last.
3. **Pyodide phone app (C)** only if a no-server version is still wanted after 1 and 2.

A public site hosted by the owner (B) is out.

## 7. Decisions (agreed with the owner, October 2026)

1. A public site hosted by the owner is out of the question for now; self-hosting is the way forward.
2. **Named players** from the first version (no shared group password).
3. Players **propose** edits to the shared app data (custom factions, rules, stratagems, modifiers); the **host**
   approves or rejects them. The host's own edits apply at once.
4. A Docker image is **published with every release**.
5. Phone layouts come **after** named players.

## 8. Build plan: release 2.8, Self-hosting

Built in steps, each to master with its tests; released when all are done (step a alone has no login, so it is
not released by itself). Each step's finer design is agreed when it starts.

a. **Docker image and hosted mode.** `run.py --hosted` (or `COMBAT_SIM_HOSTED=1`) with `--host` / `--port`; the
   data folder from `COMBAT_SIM_DATA` (seeded with the shipped app data JSON, as the packaged app does). Hosted mode
   refuses what only makes sense on your own PC: the folder dialog, choosing a BSData folder by path, changing the
   relay address (from the page; `RELAY_URL` still works), and `--debug`. The Setup page hides them. A `Dockerfile`
   and `compose.yaml` (data in a volume, running as a non-root user), and `release.yml` publishes the image to
   GitHub's registry (`ghcr.io`) with the release. Docs: running it, HTTPS through a reverse proxy, Tailscale.
b. **Named players and the host.** The first visit to a new instance creates the host (a name and password). The
   host adds players (a name and a first password they change; no emails) and can reset or remove them. Sessions
   by cookie, with a secret key kept in the data folder; writes refused from other sites (the `Origin` must be the
   instance's own, cookies `SameSite=Lax`). Host only: Setup (downloads, re-import, relay address), app data
   import, and approving edits. Outside hosted mode nothing changes: no login.
c. **Players' own lists and games.** Saved armies, scenarios, comparisons and battlefields get an owner (a new
   nullable column: old rows are the host's). A game between two players on the same instance gives each their
   seat (the game records each seat's player; `play/seats.py` checks the logged-in player instead of the install),
   with no relay. Games with players on other instances still go through the relay.
d. **Proposed edits.** In hosted mode a player's Data-tab write is saved as a proposal instead (who, when, what it
   changes, shown as a readable summary) and answered 202. The host's Data tab lists proposals to approve (applied
   through the same code as a direct edit, then `save_data()`) or reject; the player sees their proposals' outcome.

Questions to agree at each step's start: can players see (read-only) each other's armies (c)? What happens to a
proposal when the record it edits changed after it was made (d)?
