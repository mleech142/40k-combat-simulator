"""
Long work off the request threads (jobs.py, architecture review candidate 9): both adapters at the interface,
one job of a kind at a time, failures reported, and the Setup routes answering 202 with a job to poll when the
work runs in the background.
"""

import threading

import pytest
from flask import Flask

from combat_sim import jobs


def app():
    return Flask('jobs')


def test_inline_runs_the_work_inside_submit():
    job = jobs.Inline(app()).submit('x', lambda job: setattr(job, 'step', 'Working'))
    assert job.state == jobs.DONE and job.step == 'Working' and job.to_dict()['state'] == 'done'


def test_a_failure_is_reported_with_its_message_and_status():
    def fail(job):
        raise jobs.JobError('GitHub could not be reached.', 502)
    job = jobs.Inline(app()).submit('x', fail)
    assert (job.state, job.error, job.status) == (jobs.FAILED, 'GitHub could not be reached.', 502)
    crash = jobs.Inline(app()).submit('x', lambda job: 1 / 0)
    assert crash.state == jobs.FAILED and crash.status == 500 and 'division' in crash.error


def test_workers_run_in_the_background_one_job_of_a_kind_at_a_time():
    runner, release, ran = jobs.Workers(app()), threading.Event(), []

    def slow(job):
        job.step = 'Waiting'
        release.wait(5)
        ran.append(job.id)

    first = runner.submit('bsdata', slow)
    assert not first.finished  # Answered before the work is done
    assert runner.submit('bsdata', slow) is first  # The running one, not a second download
    other = runner.submit('mail', lambda job: ran.append('mail'), single=False)
    release.set()
    runner._pool.shutdown(wait=True)
    assert first.state == jobs.DONE and other.state == jobs.DONE and ran.count(first.id) == 1
    assert runner.get(first.id) is first and runner.get(999) is None


def test_finished_jobs_are_forgotten_after_a_while():
    runner = jobs.Inline(app())
    made = [runner.submit('x', lambda job: None) for _ in range(jobs.KEEP + 5)]
    assert runner.get(made[0].id) is None and runner.get(made[-1].id) is made[-1]


@pytest.fixture
def background(make_client):
    """The app with background workers, as it runs for real."""
    client, _ = make_client({'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [],
                             'melee_weapons': [], 'faction_rules': [], 'stratagems': [], 'unit_modifiers': [],
                             'keywords': [], 'target_profiles': []})
    client.application.config['JOBS'] = 'workers'
    client.application.extensions.pop('jobs', None)
    return client


def test_a_setup_reimport_answers_with_a_job_to_poll(background, monkeypatch):
    from combat_sim import reference
    release, real_load = threading.Event(), reference.load

    def held_load(*args, **kwargs):
        release.wait(5)  # The import is still going when the answer comes back
        return real_load(*args, **kwargs)

    monkeypatch.setattr(reference, 'load', held_load)
    started = background.post('/api/bsdata/reimport')
    assert started.status_code == 202
    job = started.get_json()['job']
    assert job['state'] == 'running' and job['step'] == 'Importing the catalogues'
    assert background.post('/api/bsdata/download').get_json()['job']['id'] == job['id']  # Never two at once
    release.set()
    jobs.of(background.application)._pool.shutdown(wait=True)
    polled = background.get(f'/api/jobs/{job["id"]}').get_json()
    assert polled['state'] == 'done' and polled['kind'] == 'bsdata'
    assert background.get('/api/jobs/9999').status_code == 404
