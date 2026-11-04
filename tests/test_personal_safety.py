"""Regression tests for the personal fork's local API boundaries."""
from types import SimpleNamespace

import arena.web.app as web


def test_rejects_nonlocal_host():
    client = web.app.test_client()
    response = client.get('/api/status', headers={'Host': 'attacker.example'})
    assert response.status_code == 403


def test_rejects_cross_origin_post():
    client = web.app.test_client()
    response = client.post('/api/new', headers={'Host': 'localhost', 'Origin': 'https://attacker.example'})
    assert response.status_code == 403


def test_rejects_invalid_chat_id(monkeypatch):
    monkeypatch.setattr(web, 'get_client', lambda: SimpleNamespace(cookie_str='session'))
    monkeypatch.setattr(web, '_signed_in_somewhere', lambda client: True)
    monkeypatch.setattr(web, '_ui_mode', 'battle')
    monkeypatch.setattr(web, '_ui_modality', 'text')
    response = web.app.test_client().post('/api/chat', json={'message': 'hello', 'chat_id': '../../bad'})
    assert response.status_code == 400
    assert response.get_json()['error'] == 'Invalid chat ID'


def test_open_chat_restores_mode_and_modality(monkeypatch, tmp_path):
    monkeypatch.setattr(web, 'CHATS_DIR', tmp_path)
    monkeypatch.setattr(web, '_ui_mode', 'battle')
    monkeypatch.setattr(web, '_ui_modality', 'text')
    client = SimpleNamespace(eval_id=None, active_mode=None, active_modality=None)
    monkeypatch.setattr(web, 'get_client', lambda: client)
    chat_id = '12345678-1234-1234-1234-123456789abc'
    web._save_chat(chat_id, 'saved', [], mode='side-by-side', modality='code', eval_id='saved-eval')
    response = web.app.test_client().get('/api/chats/' + chat_id)
    assert response.status_code == 200
    assert (web._ui_mode, web._ui_modality) == ('side-by-side', 'code')
    assert (client.active_mode, client.active_modality, client.eval_id) == ('side-by-side', 'webdev', 'saved-eval')


def test_sandbox_mirror_preserves_untracked_and_changed_files(monkeypatch, tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.setattr(web, 'CHATS_DIR', tmp_path / 'chats')
    monkeypatch.setattr(web, '_code_root', lambda *args: root)
    conv = {'chat_id': '12345678-1234-1234-1234-123456789abc', 'title': 'Test', 'mode': 'direct', 'mirror_ok': True}
    target = root / 'local.txt'
    target.write_text('personal data', encoding='utf-8')
    write = {'id': '1', 'toolName': 'create_file', 'args': {'path': 'local.txt', 'content': 'replacement'}}
    rows = list(web._sandbox_call('a', write, conv))
    assert 'untracked or has local changes' in rows[-1]
    assert target.read_text(encoding='utf-8') == 'personal data'
    delete = {'id': '2', 'toolName': 'delete_file', 'args': {'path': 'local.txt'}}
    rows = list(web._sandbox_call('a', delete, conv))
    assert 'untracked or has local changes' in rows[-1]
    assert target.exists()
    web._sync_save(root, {'local.txt': web._digest(b'previous content')})
    rows = list(web._sandbox_call('a', delete, conv))
    assert 'untracked or has local changes' in rows[-1]
    assert target.exists()


def test_sandbox_tracked_delete_needs_separate_permission(monkeypatch, tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.setattr(web, 'CHATS_DIR', tmp_path / 'chats')
    monkeypatch.setattr(web, '_code_root', lambda *args: root)
    calls = []
    def deny_delete(mode, tool, info):
        calls.append(tool)
        if False:
            yield None
        return 'deny'
    monkeypatch.setattr(web, '_permit', deny_delete)
    target = root / 'generated.txt'
    target.write_text('generated', encoding='utf-8')
    web._sync_save(root, {'generated.txt': web._digest(b'generated')})
    conv = {'chat_id': '12345678-1234-1234-1234-123456789abc', 'title': 'Test', 'mode': 'direct', 'mirror_ok': True}
    delete = {'id': '1', 'toolName': 'delete_file', 'args': {'path': 'generated.txt'}}
    rows = list(web._sandbox_call('a', delete, conv))
    assert calls == ['delete']
    assert 'permission was not granted' in rows[-1]
    assert target.exists()
