"""
Long work off the request threads (architecture review, candidate 9): downloading and re-importing the reference
data (the Setup page) and sending "your move" emails. With a few players on a self-hosted copy, a minute-long
download inside a request held one of the server's few request threads for that minute.

`of(app).submit(kind, work)` starts `work(job)` and answers its `Job` at once; `work` reports what it is doing in
`job.step`, returns when done, and raises `JobError(message, status)` for an answer the page should show. A kind
runs one job at a time unless `single=False`: submitting it again while one runs answers the running one (two
downloads never race). `of(app).get(id)` reads a job; the Setup page polls `/api/jobs/<id>` (app.py).

Two adapters, chosen by `app.config['JOBS']`:

- `Workers` (the default): a small pool of background threads, each job in its own app context.
- `Inline` ('inline'): runs the job inside `submit` (the tests, so answers are the same as before jobs).

Jobs live in memory only (the last `KEEP`): a restart forgets them, which is fine for downloads and emails.
"""

import itertools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

from flask import current_app, has_app_context

log = logging.getLogger(__name__)

RUNNING, DONE, FAILED = 'running', 'done', 'failed'
KEEP = 50  # Finished jobs remembered
WORKERS = 2  # Background threads: a download and an email can run side by side


class JobError(Exception):
    """A job that ended with an answer for the page: its message and the HTTP status that goes with it."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Job:
    def __init__(self, ident, kind):
        self.id, self.kind = ident, kind
        self.state, self.step, self.error, self.status = RUNNING, None, None, None
        self.started_at, self.finished_at = time.time(), None

    @property
    def finished(self):
        return self.state != RUNNING

    def to_dict(self):
        return {'id': self.id, 'kind': self.kind, 'state': self.state, 'step': self.step, 'error': self.error,
                'seconds': round((self.finished_at or time.time()) - self.started_at, 1)}


class Jobs:
    """The interface both adapters share; `_start(job, work)` is what differs."""

    def __init__(self, app):
        self.app = app
        self._jobs = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def submit(self, kind, work, single=True):
        with self._lock:
            if single:
                running = next((j for j in self._jobs.values() if j.kind == kind and not j.finished), None)
                if running is not None:
                    return running
            job = Job(next(self._ids), kind)
            self._jobs[job.id] = job
            finished = [j for j in self._jobs.values() if j.finished]
            for old in finished[:max(0, len(finished) - KEEP)]:
                del self._jobs[old.id]
        self._start(job, work)
        return job

    def get(self, ident):
        return self._jobs.get(ident)

    def _context(self):
        return self.app.app_context()

    def _run(self, job, work):
        with self._context():
            try:
                work(job)
                job.state = DONE
            except JobError as error:
                job.state, job.error, job.status = FAILED, str(error), error.status
            except Exception as error:  # noqa: BLE001 - a job's failure is reported, never lost in a thread
                log.exception('Job %s (%s) failed', job.id, job.kind)
                job.state, job.error, job.status = FAILED, f'That did not work: {error}', 500
            finally:
                job.finished_at = time.time()

    def _start(self, job, work):
        raise NotImplementedError


class Workers(Jobs):
    def __init__(self, app):
        super().__init__(app)
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='job')

    def _start(self, job, work):
        self._pool.submit(self._run, job, work)


class Inline(Jobs):
    def _context(self):
        # Inside a request of this app: its own context, so the request's database session sees what the job did
        if has_app_context() and current_app._get_current_object() is self.app:
            return nullcontext()
        return self.app.app_context()

    def _start(self, job, work):
        self._run(job, work)


ADAPTERS = {'workers': Workers, 'inline': Inline}
_made = threading.Lock()


def of(app):
    """The app's jobs (made on first use from app.config['JOBS'])."""
    jobs = app.extensions.get('jobs')
    if jobs is None:
        with _made:
            jobs = app.extensions.get('jobs')
            if jobs is None:
                jobs = app.extensions['jobs'] = ADAPTERS[app.config.get('JOBS') or 'workers'](app)
    return jobs
