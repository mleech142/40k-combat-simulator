# 40K Combat Simulator

The language of the simulator, Analytics and Play mode: games of Warhammer 40,000 (11th edition) between friends,
worked out by the app.

## Language

### Play: changing what happened

**Undo**:
Taking back your own last action in a game, allowed only while no dice have been rolled since it. In a game against
someone on another PC, it needs the other player's OK and becomes a Correction.
_Avoid_: Take-back, rewind

**Correction**:
A change to the game that one player asks for and the other must accept before it happens, such as wounds,
positions, CP, VP, or rewinding an action. It is logged as a correction.
_Avoid_: Take-back, fix, override

### Play: the opponent's turn

**Reaction window**:
A point in one player's turn where the other player may decide something, such as Fire Overwatch, a Command
Re-roll or Heroic Intervention. The game waits for them unless a standing order answers it. Live and async games
use the same windows.
_Avoid_: Prompt, interrupt, live mode

**Standing order**:
A choice a player sets in advance that answers a reaction window, or a defender's decision, without asking them,
such as "Overwatch with this unit if it's charged" or "remove casualties from the back".
_Avoid_: Default, auto-reaction, pre-set

### A self-hosted copy: who's who

**Copy**:
One running install of the app with its own data: a desktop install used by one person, or a self-hosted copy
that a group's players reach in their browsers.
_Avoid_: Instance, server, deployment

**Host**:
The player who runs a self-hosted copy: they set up its game data, add the other players and approve their
proposals. A desktop install's one user is its host.
_Avoid_: Admin, owner, superuser

**Player**:
Someone with a login on a self-hosted copy, the host included. In a game, each side is played by one player.
_Avoid_: User, account, member

**Proposal**:
A player's change to the copy's shared data (custom factions, units, weapons, rules, stratagems) that waits for
the host to approve or reject it.
_Avoid_: Request, suggestion, pending edit

### Play: how the two sides reach each other

**Connection**:
How a game's two sides reach each other, one of four kinds: hot-seat, game file, relay or same copy. It decides
which side a player plays, how the other player's moves arrive and how Undo works.
_Avoid_: Transport, channel, mode

**Hot-seat**:
A connection where one app plays both sides, the players taking turns at the same screen.
_Avoid_: Local game, single-PC game

**Game file**:
A connection where each player's app plays one side and they swap the game as a file by hand.
_Avoid_: Play by email, save file

**Relay**:
A connection where each player's app plays one side and moves pass through the relay server; a game started with
one army waits there until the other player joins with theirs.
_Avoid_: Online server, cloud game

**Same copy**:
A connection where both players use one self-hosted copy, each in their own browser, with no relay.
_Avoid_: Local online, shared server game
