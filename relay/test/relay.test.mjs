/**
 * The relay's rules (relay/src/relay.js) on an in-memory store. Run by tests/test_javascript.py.
 */

import assert from 'node:assert/strict';
import test from 'node:test';
import { CODE_LENGTH, MemoryStore, cleanCode, cleanName, handle, newCode, newer, plausibleVersion, route } from '../src/relay.js';

const FILE = { format: 'combat-sim-game', format_version: 1, uid: 'abc', name: 'Grudge', app_version: '2.2.2',
               armies: [{ name: 'Red' }, { name: 'Blue' }], events: [{ by: 0, rolls: [] }], battlefield: null, setup: false, twist: null };

async function created(seat = 0) {
    const store = new MemoryStore();
    const code = newCode();
    const made = await handle(store, { action: 'create', code, body: { game: FILE, seat, name: 'Sam' } });
    assert.equal(made.status, 201);
    return { store, code, mine: made.body.token };
}

test('routes name the request and clean the code', () => {
    assert.deepEqual(route('POST', '/v1/games'), { action: 'create' });
    const code = newCode();
    assert.equal(code.length, CODE_LENGTH);
    assert.deepEqual(route('GET', `/v1/games/${code.toLowerCase()}`, '?since=3'), { action: 'read', code, since: 3 });
    assert.deepEqual(route('POST', `/v1/games/${code}/events`), { action: 'append', code });
    assert.equal(route('POST', '/v1/games/NOPE/join'), null);
    assert.equal(route('DELETE', `/v1/games/${code}`), null);
    assert.equal(cleanCode(`${code.slice(0, 5)}-${code.slice(5)}`.toLowerCase()), code);
});

test('a game is created with one seat and joined once for the other', async () => {
    const { store, code, mine } = await created(1);
    const joined = await handle(store, { action: 'join', code });
    assert.equal(joined.status, 200);
    assert.equal(joined.body.seat, 0);
    assert.deepEqual(joined.body.game.events, FILE.events);
    assert.equal(joined.body.game.name, 'Grudge');
    assert.notEqual(joined.body.token, mine);
    assert.equal((await handle(store, { action: 'join', code })).status, 409);
    const read = await handle(store, { action: 'read', code, token: mine });
    assert.equal(read.body.joined, true);
    assert.equal(read.body.count, 1);
});

test('only a seat\'s token reads or writes', async () => {
    const { store, code } = await created();
    assert.equal((await handle(store, { action: 'read', code, token: 'guess' })).status, 403);
    assert.equal((await handle(store, { action: 'read', code })).status, 403);
    assert.equal((await handle(new MemoryStore(), { action: 'read', code, token: 'x' })).status, 404);
});

test('events are appended only on top of the latest count', async () => {
    const { store, code, mine } = await created();
    const theirs = (await handle(store, { action: 'join', code })).body.token;
    const push = (token, base, by) => handle(store, { action: 'append', code, token, body: { base, events: [{ by, rolls: [] }], app_version: '2.2.2' } });
    assert.equal((await push(mine, 1, 0)).body.count, 2);
    const stale = await push(theirs, 1, 1);  // Didn't fetch my move first
    assert.equal(stale.status, 409);
    assert.equal(stale.body.count, 2);
    assert.equal((await push(theirs, 2, 1)).status, 200);
    const read = await handle(store, { action: 'read', code, token: mine, since: 1 });
    assert.deepEqual(read.body.events.map(e => e.by), [0, 1]);
});

test('an older app version is asked to update, and one that cannot be real is refused', async () => {
    const { store, code, mine } = await created();
    const push = version => handle(store, { action: 'append', code, token: mine, body: { base: 1, events: [{}], app_version: version } });
    assert.equal((await push('2.1.9')).status, 426);
    for (const fake of ['99.0.0', '2.2', 'latest', undefined]) assert.equal((await push(fake)).status, 400);
    assert.equal((await push('3.0.0')).body.app_version, '3.0.0');  // The next major version is real enough
    assert.ok(newer('2.10.0', '2.9.9'));
    assert.ok(!newer('2.2.2', '2.2.2'));
});

test('only your own last action, with no dice, can be taken back', async () => {
    const { store, code, mine } = await created();
    const theirs = (await handle(store, { action: 'join', code })).body.token;
    assert.equal((await handle(store, { action: 'retract', code, token: theirs, body: { count: 1 } })).status, 403);  // Red's
    assert.equal((await handle(store, { action: 'retract', code, token: mine, body: { count: 5 } })).status, 409);
    const taken = await handle(store, { action: 'retract', code, token: mine, body: { count: 1 } });
    assert.equal(taken.body.count, 0);
    await handle(store, { action: 'append', code, token: mine, body: { base: 0, events: [{ by: 0, rolls: [{ total: 6 }] }], app_version: '2.2.2' } });
    assert.equal((await handle(store, { action: 'retract', code, token: mine, body: { count: 1 } })).status, 403);
});

test('a game needs a game file and a seat', async () => {
    const store = new MemoryStore();
    assert.equal((await handle(store, { action: 'create', code: newCode(), body: { game: { format: 'x' }, seat: 0 } })).status, 400);
    assert.equal((await handle(store, { action: 'create', code: newCode(), body: { game: FILE, seat: 3 } })).status, 400);
});

test('each player name is kept, made safe to show', async () => {
    const { store, code, mine } = await created(0);
    await handle(store, { action: 'join', code, body: { name: '  Alex<script>"  ' } });
    const read = await handle(store, { action: 'read', code, token: mine });
    assert.deepEqual(read.body.names, ['Sam', 'Alexscript']);
    assert.equal(cleanName('x'.repeat(99)).length, 40);
    assert.equal(cleanName('   '), null);
    assert.ok(plausibleVersion('2.2.3') && !plausibleVersion('2.2.3.1'));
});

test('a player can close a game: its code stops working', async () => {
    const { store, code, mine } = await created();
    assert.equal((await handle(store, { action: 'close', code, token: 'guess' })).status, 403);
    assert.deepEqual((await handle(store, { action: 'close', code, token: mine })).body, { closed: code });
    assert.equal((await handle(store, { action: 'read', code, token: mine })).status, 404);
    assert.equal((await handle(store, { action: 'join', code })).status, 404);
});
