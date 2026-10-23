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
