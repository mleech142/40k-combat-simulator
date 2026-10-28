"""
"Your move" emails on a self-hosted copy (docs/self-hosting.md), sent through the host's own mail server:

    SMTP_HOST, SMTP_PORT (587; 465 means SSL), SMTP_USER, SMTP_PASSWORD, SMTP_FROM (default SMTP_USER),
    SMTP_STARTTLS (on unless 0), PUBLIC_URL (the copy's address in links; default the address the move came from)

With no SMTP_HOST nothing is sent. Emails go in the background, so a slow mail server never slows a move, and
a failure is only logged. A player gets none while they're looking at the game (its page asked for moves in the
last minute), nor a second for the same game within a few minutes.
"""

import logging
import os
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage

log = logging.getLogger(__name__)

SEEN_WINDOW = 60  # seconds: a player whose page asked for moves this recently is watching
REPEAT_WINDOW = 10 * 60  # seconds between two emails for the same game

_seen = {}  # (game ID, player ID) -> when their page last asked
_sent = {}  # (game ID, player ID) -> when they were last emailed
_lock = threading.Lock()


def configured():
    return bool(os.environ.get('SMTP_HOST'))


def seen(game_id, player_id):
    """The player's page has just shown them the game."""
    with _lock:
        _seen[(game_id, player_id)] = time.time()


def plain(text):
    """One line of text for a mail header."""
    return ' '.join(str(text or '').split())[:200]


def your_move(player, by, saved, host_url):
    """Email `player` that `by` has moved in the game `saved` and it's their move. Answers whether it was sent off."""
    if not configured():
        return False
    key, now = (saved.id, player.id), time.time()
    with _lock:
        if now - _seen.get(key, 0) < SEEN_WINDOW or now - _sent.get(key, 0) < REPEAT_WINDOW:
            return False
        _sent[key] = now
    base = (os.environ.get('PUBLIC_URL') or host_url).rstrip('/')
    message = EmailMessage()
    message['Subject'] = plain(f'Your move: {saved.name}')
    message['From'] = os.environ.get('SMTP_FROM') or os.environ.get('SMTP_USER') or 'combat-sim@localhost'
    message['To'] = plain(player.email)
    message.set_content(f'{plain(by)} has moved in {plain(saved.name)}, and it is your move.\n\n'
                        f'Open the game: {base}/play?game={saved.id}\n\n'
                        'You can turn these emails off on your account page.\n')
    threading.Thread(target=send, args=(message,), daemon=True).start()
    return True


def send(message):
    host = os.environ['SMTP_HOST']
    port = int(os.environ.get('SMTP_PORT') or 587)
    user, password = os.environ.get('SMTP_USER'), os.environ.get('SMTP_PASSWORD')
    context = ssl.create_default_context()
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, context=context, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            if os.environ.get('SMTP_STARTTLS', '1') != '0':
                server.starttls(context=context)
        with server:
            if user:
                server.login(user, password or '')
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        log.warning('Could not send the "your move" email to %s: %s', message['To'], error)
