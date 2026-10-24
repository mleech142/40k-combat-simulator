# The Play relay

The project's relay is `https://tabletop-relay.tabletop-relay.workers.dev` (`routes.DEFAULT_RELAY_URL`), on the
project owner's Cloudflare account.

Online Play games go through a small relay that keeps each game's event log in order and lets each player's app
fetch the other's moves (design: [play-mode-design.md](play-mode-design.md), Connection). It runs on Cloudflare
Workers' free plan, in `relay/`.

## What it does

- One Durable Object per game, named by its **invite code** (10 letters and digits, no look-alikes). Its storage
  holds the game file (frozen armies, battlefield, twist), each event under its own key, and `meta`: each seat's
  token **hash**, each player's name as they gave it (Setup page; no markup characters, at most 40), the event
  count, and the newest app version that played.
- `POST /v1/games` creates a game for one seat, with both armies or with the other seat's army left empty (an
  **open seat**: no moves until it's filled); `GET /v1/games/<code>/invite` shows what joining gives (the game file,
  and whether it needs the joiner's army); `POST /v1/games/<code>/join` gives the other seat once, and for an open
  seat takes the game the joiner's app completed with their army, only if the creator's army and the game's ID are
  exactly as they were (`canonical` JSON); `GET ...?file=1` also returns the game file (how the creator's app gets
  the completed game);
  `GET /v1/games/<code>?since=n` returns the events from the n-th on; `POST .../events` appends only on top of
  the current count (409 otherwise: fetch the other player's moves first); `POST .../retract` takes back your
  own last event if no dice were rolled (the app stopped using it in 2.5: online, Undo is a Correction the other player accepts); `POST .../close` deletes the game (the app's "New invite code" moves a game
  to a new code and closes the old one, cutting off anyone who took the code first). Every request but create and
  join needs `Authorization: Bearer <token>`. App versions must be X.Y.Z and at most one major version ahead.
- Limits: 2 MB per request, 20,000 events per game, and a game is deleted 90 days after its last change
  (a Durable Object alarm).
- It never reads the rules or the events. Games carry no rule text: stratagems and rules are frozen with a `ref`
  (their Wahapedia ID, else their name), and each PC shows the text from its own data (`snapshot.rule_texts`);
  games frozen before that have their text stripped when they're sent (`game_file.strip_text`). The frozen armies
  do carry units' names, stats, keywords, bases and weapon profiles (from BSData), as the design allows.

The rules are in `relay/src/relay.js` (no Cloudflare code, tested by `relay/test/relay.test.mjs` through
`tests/test_javascript.py`). `relay/src/index.js` is the Worker around it, and `relay/dev-server.mjs` runs the same
rules on this PC in memory: the app's online tests (`tests/test_play_online.py`) use it, and so can you
(`node relay/dev-server.mjs`, then set the relay address on the Setup page to `http://127.0.0.1:8787`).

## What's in a game (on the relay and in game files)

The game's `uid`, name and app version; both frozen armies (army name, faction, force disposition; each unit's
name, BSData ID, models, points, stats, keywords, base, weapons, wargear, leader and enhancement effects, warlord;
the stratagems and rules on offer by name, CP, effect and `ref`); the battlefield (terrain and objectives); the
mission options and twist; and every event (the action, every die, models' wounds, positions and alive/dead,
units' activity, CP, VP, mission state, corrections with their note, and which seat sent it). No emails, accounts,
paths or settings. The relay also sees each player's IP address, as any website does.

## Checks on what comes in

Everything another app sends, whether a game file, a joined game or moves fetched from the relay, is checked before it is
saved (`game_file.check_game` / `check_more`): plain JSON with no `<` or `>` in any text, IDs and keys of letters,
digits and `_ . : + -`, numbers that are numbers, events that only name the game's own units and models and the
engine's own actions, a plausible app version, and a log that replays. Moves that fail are not added, and the
game stays as it was. The page also escapes every ID, key and logged value it writes into HTML.

## Deploying

Needs Node.js and a Cloudflare account (the free plan is enough).

```
cd relay
npm install
npx wrangler login      # opens the browser to allow wrangler into your Cloudflare account
npx wrangler deploy
```

The deploy prints the relay's address, `https://tabletop-relay.<your subdomain>.workers.dev`. Put it in
`DEFAULT_RELAY_URL` in `src/combat_sim/play/routes.py` so every install uses it, or set it per install on the
Setup page. Deploy again after changing anything in `relay/src`. The Worker's name and address must not use
"Warhammer" or "40K".

A friend can host their own relay the same way and enter its address on their Setup page; both players in a
game must use the same relay.
