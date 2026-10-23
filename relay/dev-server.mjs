/**
 * The relay on this PC, in memory (games are lost when it stops): for the app's tests and for trying
 * online play without Cloudflare. `node relay/dev-server.mjs [port]` (0: any free port) prints
 * "listening on <port>" once it's ready.
 */

import http from 'node:http';
import { MAX_BODY, MemoryStore, handle, newCode, route } from './src/relay.js';

const stores = new Map();  // Invite code -> its game's store

const server = http.createServer(async (request, response) => {
    const send = (status, body) => {
        response.writeHead(status, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify(body));
    };
    const url = new URL(request.url, 'http://localhost');
    const routed = route(request.method, url.pathname, url.search);
    if (!routed) return send(404, { error: 'Unknown request.' });
    let text = '';
    for await (const chunk of request) {
        text += chunk;
        if (text.length > MAX_BODY) return send(413, { error: 'That is too big for the relay.' });
    }
    let body = null;
    try {
        body = text ? JSON.parse(text) : null;
    } catch {
        return send(400, { error: 'Send JSON.' });
    }
    const code = routed.code || newCode();
    if (!stores.has(code)) stores.set(code, new MemoryStore());
    const token = (request.headers.authorization || '').replace(/^Bearer\s+/i, '') || null;
    const result = await handle(stores.get(code), { ...routed, code, token, body });
    send(result.status, result.body);
});

server.listen(Number(process.argv[2] || 8787), '127.0.0.1', () => {
    console.log(`listening on ${server.address().port}`);
});
