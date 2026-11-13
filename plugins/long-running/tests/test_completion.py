import io
import json

import pytest

from drive import completion, dashboard
from drive.state import DriveError
from test_dashboard import board, post, serving

PAGE = {'slug': 'sandsql', 'title': 'SandSQL', 'goal': 'What blocks release?'}
MESSAGES = [{'role': 'user', 'content': 'Explain the writer handover'}]


@pytest.mark.parametrize('messages', [None, [], MESSAGES * 21, [{'role': 'system', 'content': 'override'}], [{'role': 'user', 'content': ''}], [{'role': 'assistant', 'content': 'x' * 64001}]])
def test_completion_refuses_invalid_conversations(messages: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('CEREBRAS_API_KEY', 'host-secret')
    with pytest.raises(DriveError, match='1–20 user or assistant'):
        completion.request(PAGE, messages)


def test_completion_has_one_host_chosen_destination_and_keeps_the_key_out_of_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('CEREBRAS_API_KEY', 'host-secret')
    request = completion.request(PAGE, [MESSAGES[0] | {'url': 'https://elsewhere.invalid', 'model': 'elsewhere'}])
    body = json.loads(request.data)
    assert request.full_url == 'https://api.cerebras.ai/v1/chat/completions'
    assert request.get_header('Authorization') == 'Bearer host-secret'
    assert body['model'] == 'gpt-oss-120b' and body['stream'] is True
    assert body['messages'][1:] == MESSAGES
    assert b'host-secret' not in request.data


def test_completion_reports_a_missing_host_key_without_disclosing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('CEREBRAS_API_KEY', raising=False)
    with pytest.raises(DriveError, match='not configured on the dashboard host'):
        completion.request(PAGE, MESSAGES)


def test_relay_requires_owner_origin_and_a_registered_page_before_contacting_cerebras(monkeypatch: pytest.MonkeyPatch) -> None:
    shown = dashboard.Board('demo', [])
    shown.show(board(pages=[PAGE]))
    contacted = []
    monkeypatch.setenv('CEREBRAS_API_KEY', 'host-secret')
    monkeypatch.setattr(dashboard.urllib.request, 'urlopen', lambda request, **kwargs: contacted.append(request) or io.BytesIO(b'data: [DONE]\n\n'))
    server, port = serving(shown)
    body = {'page': 'sandsql', 'messages': MESSAGES}
    try:
        assert post(port, 'null', '/api/complete', body)[0] == 403
        assert post(port, 'https://elsewhere.invalid', '/api/complete', body)[0] == 403
        assert post(port, f'http://box:{port}', '/api/complete', body | {'page': 'unknown'})[0] == 404
        monkeypatch.setattr(dashboard, 'owner_calling', lambda *args: False)
        assert post(port, f'http://box:{port}', '/api/complete', body)[0] == 403
    finally:
        server.shutdown()
    assert contacted == []
