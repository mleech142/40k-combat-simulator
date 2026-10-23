/**
 * The relay as a Cloudflare Worker: requests are routed (relay.js) to one Durable Object per game, named by
 * its invite code, so each game's log is kept in order. Games expire EXPIRES_DAYS after their last change.
 */

import { DurableObject } from 'cloudflare:workers';
import { EXPIRES_DAYS, MAX_BODY, handle, newCode, route } from './relay.js';

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

export default {
    async fetch(request, env) {
        const url = new URL(request.url);
        const routed = route(request.method, url.pathname, url.search);
        if (!routed) return json(404, { error: 'Unknown request.' });
        if (Number(request.headers.get('Content-Length') || 0) > MAX_BODY) return json(413, { error: 'That is too big for the relay.' });
        const code = routed.code || newCode();
        const stub = env.GAMES.get(env.GAMES.idFromName(code));
        return stub.fetch(new Request(`https://game/${code}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ...routed, code,
                token: (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '') || null,
                body: request.method === 'POST' ? await request.json().catch(() => null) : null,
            }),
        }));
    },
};

export class Game extends DurableObject {
    async fetch(request) {
        const result = await handle(this.ctx.storage, await request.json());
        if (result.touched) await this.ctx.storage.setAlarm(Date.now() + EXPIRES_DAYS * 24 * 60 * 60 * 1000);
        if (result.body && result.body.closed) await this.ctx.storage.deleteAlarm();  // Nothing left to expire
        return json(result.status, result.body);
    }

    async alarm() {
        await this.ctx.storage.deleteAll();
    }
}
