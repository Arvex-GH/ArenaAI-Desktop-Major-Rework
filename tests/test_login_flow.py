"""Authentication regression tests with a fake WebView (no real credentials)."""
from types import SimpleNamespace

from arena.engine import BrowserEngine
import arena.web.app as web


class FakeWindow:
    def __init__(self):
        self.actions = []
    def show(self): self.actions.append('show')
    def hide(self): self.actions.append('hide')
    def resize(self, *args): pass
    def move(self, *args): pass
    def load_url(self, url): self.actions.append(('load', url))


def test_login_window_is_top_level_and_hides_after_success():
    engine = BrowserEngine()
    engine.window, engine.host, engine.loaded = FakeWindow(), 'arena.ai', True
    engine.open_login()
    assert engine.window.actions == ['show']
    assert engine.login_open and engine.state()['open'] and engine.state()['separate_window']
    engine.finish_login()
    assert engine.window.actions == ['show', 'hide']
    assert not engine.login_open and not engine.state()['open']


class FakeEngine:
    def __init__(self):
        self.window = object()
        self.login_open = False
        self.finish_count = 0
        self.imported = None
        self.authenticated = True
    def open_login(self): self.login_open = True
    def state(self): return {'login_open': self.login_open}
    def ready(self): return True
    def has_session(self): return False  # mimic WebView2 not enumerating a cookie
    def get_me(self):
        if self.authenticated:
            return ({'email': 'user@example.test', 'id': '123'}, None)
        return (None, 'Not signed in')
    def sync_to(self, client): pass
    def finish_login(self): self.finish_count += 1; self.login_open = False
    def import_cookies(self, value): self.imported = value; return True
    def wait_ready(self, timeout): return True


def test_login_poll_accepts_confirmed_session_without_enumerated_cookie(monkeypatch):
    engine = FakeEngine()
    fake_client = SimpleNamespace(cookie_str=None, eval_id=None)
    monkeypatch.setattr(web, 'ENGINE', engine)
    monkeypatch.setattr(web, '_client', fake_client)
    monkeypatch.setattr(web, '_me_cache', {'t': 0, 'user': None, 'err': None})
    client = web.app.test_client()
    assert client.post('/api/login', json={'method': 'window'}).get_json()['pending']
    result = client.get('/api/login/poll').get_json()
    assert result['done'] and result['ok'] and result['email'] == 'user@example.test'
    assert engine.finish_count == 1 and not engine.login_open
    assert client.get('/api/status').get_json()['authenticated'] is True


def test_login_window_cancel_is_reported(monkeypatch):
    engine = FakeEngine()
    engine.authenticated = False
    monkeypatch.setattr(web, 'ENGINE', engine)
    result = web.app.test_client().get('/api/login/poll').get_json()
    assert result['done'] and not result['ok'] and 'closed' in result['error']


def test_login_poll_adopts_session_closed_just_after_success(monkeypatch):
    engine = FakeEngine()  # login_open is already False, but /me confirms the account
    monkeypatch.setattr(web, 'ENGINE', engine)
    monkeypatch.setattr(web, '_client', SimpleNamespace(cookie_str=None, eval_id=None))
    monkeypatch.setattr(web, '_me_cache', {'t': 0, 'user': None, 'err': None})
    result = web.app.test_client().get('/api/login/poll').get_json()
    assert result['done'] and result['ok'] and engine.finish_count == 1


def test_status_adopts_persistent_engine_session_without_cookie_evidence(monkeypatch, tmp_path):
    engine = FakeEngine()
    monkeypatch.setattr(web, 'ENGINE', engine)
    monkeypatch.setattr(web, '_client', SimpleNamespace(cookie_str=None, eval_id=None))
    monkeypatch.setattr(web, '_me_cache', {'t': 0, 'user': None, 'err': None})
    monkeypatch.setattr(web, '_perm_mode', lambda: 'default')
    monkeypatch.setattr(web, '_workspace_dir', lambda: tmp_path)
    result = web.app.test_client().get('/api/status').get_json()
    assert result['authenticated'] is True and result['email'] == 'user@example.test'


def test_engine_cookie_fallback_reads_page_cookies():
    class PageWindow(FakeWindow):
        def get_cookies(self): return []
        def evaluate_js(self, script):
            assert script == 'document.cookie'
            return 'theme=dark; arena-auth-prod-v1.0=fake-a; arena-auth-prod-v1.1=fake-b'
    engine = BrowserEngine()
    engine.window, engine.host = PageWindow(), 'arena.ai'
    assert engine.cookies() == {
        'arena-auth-prod-v1.0': 'fake-a',
        'arena-auth-prod-v1.1': 'fake-b',
    }
    assert engine.has_session()


def test_manual_cookie_import_only_saves_arena_auth_chunks(monkeypatch, tmp_path):
    engine = FakeEngine()
    monkeypatch.setattr(web, 'ENGINE', engine)
    monkeypatch.setattr(web, '_me_cache', {'t': 0, 'user': None, 'err': None})
    monkeypatch.setattr(web, 'COOKIE_FILE', tmp_path / 'cookies.txt')
    monkeypatch.setattr(web, 'ensure_config_dir', lambda: tmp_path)
    class Client:
        def __init__(self, cookie_str=None): self.cookie_str = cookie_str
    monkeypatch.setattr(web, 'ArenaClient', Client)
    client = web.app.test_client()
    response = client.post('/api/login', json={'method': 'manual', 'cookie': 'tracking=secret; arena-auth-prod-v1.0=fake; arena-auth-prod-v1.1=chunk'})
    assert response.status_code == 200 and response.get_json()['ok']
    assert engine.imported == 'arena-auth-prod-v1.0=fake; arena-auth-prod-v1.1=chunk'
    assert 'tracking' not in (tmp_path / 'cookies.txt').read_text(encoding='utf-8')
    assert client.post('/api/login', json={'method': 'manual', 'cookie': {'bad': 'type'}}).status_code == 400
    assert client.post('/api/login', json={'method': 'manual', 'cookie': 'x' * 65537}).status_code == 400
