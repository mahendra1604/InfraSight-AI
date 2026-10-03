import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    import app.main as module
    module = importlib.reload(module)
    with TestClient(module.app) as test_client:
        yield test_client
    module.engine.dispose()


def test_review_updates_progress_and_audit_once(client):
    before = client.get('/api/summary').json()
    assert client.post('/api/reports/1/review', json={'action': 'approve'}).status_code == 200
    after = client.get('/api/summary').json()
    assert after['actual'] > before['actual']
    activity = next(a for a in client.get('/api/activities').json() if a['id'] == 'A102')
    assert activity['actual'] == 60
    assert len(client.get('/api/audit').json()) == 1
    assert client.post('/api/reports/1/review', json={'action': 'approve'}).status_code == 409
    assert len(client.get('/api/audit').json()) == 1


def test_rejection_does_not_change_progress(client):
    before = client.get('/api/activities').json()
    assert client.post('/api/reports/1/review', json={'action': 'reject'}).status_code == 200
    assert client.get('/api/activities').json() == before


def test_invalid_progress_and_unknown_activity(client):
    report = {'activity_id': 'A102', 'text': 'Update', 'progress': 101, 'reporting_date': '2026-10-01'}
    assert client.post('/api/reports', json=report).status_code == 422
    report.update(progress=50, activity_id='missing')
    assert client.post('/api/reports', json=report).status_code == 404


def test_older_report_cannot_overwrite_newer_progress(client):
    report = {'activity_id': 'A102', 'text': 'Update', 'progress': 80, 'reporting_date': '2026-10-02'}
    new_id = client.post('/api/reports', json=report).json()['id']
    assert client.post(f'/api/reports/{new_id}/review', json={'action': 'approve'}).status_code == 200
    assert client.post('/api/reports/1/review', json={'action': 'approve'}).status_code == 409


@pytest.mark.parametrize('rows,valid', [
    ('A,Start,100,50,\nB,Next,20,0,A\n', True),
    ('A,Start,100,50,B\nB,Next,20,0,A\n', False),
    ('A,Start,100,50,Z\n', False),
    ('A,Start,100,50,\nA,Duplicate,20,0,\n', False),
    ('A,Start,NaN,50,\n', False),
])
def test_schedule_validation(client, rows, valid):
    text = 'activity_id,name,planned_progress,actual_progress,predecessor\n' + rows
    response = client.post('/api/schedules/validate', files={'file': ('schedule.csv', text, 'text/csv')})
    assert response.status_code == (200 if valid else 422)
    assert len(client.get('/api/activities').json()) == 6
