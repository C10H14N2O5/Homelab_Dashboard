from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase

from homelab_state.notes_auth import AuthFailure, NotesAuth
from tests import test_notes_http as transport
from tests.test_notes_http import identity


class NotesAuthTests(TestCase):
    def test_disabled_empty_exact_password_and_restart(self):
        self.assertTrue(NotesAuth(True, '').authorized(None))
        with self.assertRaises(AuthFailure):
            NotesAuth(False, 'secret').unlock('secret')
        auth = NotesAuth(True, ' secret中文 ')
        with self.assertRaises(AuthFailure):
            auth.unlock('secret中文')
        token = auth.unlock(' secret中文 ')['token']
        self.assertGreaterEqual(len(token), 43)
        self.assertTrue(auth.authorized(token))
        self.assertFalse(NotesAuth(True, ' secret中文 ').authorized(token))
        auth.lock(token)
        self.assertFalse(auth.authorized(token))

    def test_global_cooldown_success_reset_and_existing_sessions(self):
        now = [100.0]
        auth = NotesAuth(True, 'correct', clock=lambda: now[0])
        token = auth.unlock('correct')['token']
        for index in range(5):
            with self.assertRaises(AuthFailure) as caught:
                auth.unlock('incorrect')
            self.assertEqual(caught.exception.status, 401 if index < 4 else 429)
        self.assertEqual(auth.status()['retryAfterSeconds'], 30)
        self.assertTrue(auth.authorized(token))
        with self.assertRaises(AuthFailure) as caught:
            auth.unlock('correct')
        self.assertEqual(caught.exception.status, 429)
        now[0] += 30
        auth.unlock('correct')
        for _ in range(4):
            with self.assertRaises(AuthFailure): auth.unlock('incorrect')
        auth.unlock('correct')
        with self.assertRaises(AuthFailure) as caught: auth.unlock('incorrect')
        self.assertEqual(caught.exception.status, 401)

    def test_atomic_resume_and_capacity_do_not_evict(self):
        auth = NotesAuth(True, 'secret', capacity=1)
        token = auth.unlock('secret')['token']
        with self.assertRaises(AuthFailure) as caught: auth.unlock('secret')
        self.assertEqual(caught.exception.code, 'notes_auth_busy')
        def rotate():
            try: return auth.resume(token)['token']
            except AuthFailure: return None
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: rotate(), range(8)))
        replacement = [result for result in results if result]
        self.assertEqual(len(replacement), 1)
        self.assertFalse(auth.authorized(token))
        self.assertTrue(auth.authorized(replacement[0]))
        auth.lock(replacement[0])
        self.assertTrue(auth.authorized(auth.unlock('secret')['token']))


class NotesAuthHTTPTests(TestCase):
    # Reuse transport/fixture helpers without inheriting the unprotected suite.
    setUp = transport.NotesHTTPTests.setUp
    tearDown = transport.NotesHTTPTests.tearDown
    exchange = transport.NotesHTTPTests.exchange
    request = transport.NotesHTTPTests.request
    raw = transport.NotesHTTPTests.raw

    def protect(self):
        self.server.notes_auth = NotesAuth(True, 'test-password')
        status, result, _ = self.request('POST', '/api/notes/auth/unlock', {'password': 'test-password'})
        self.assertEqual(status, 200)
        return {'X-Notes-Token': result['token']}

    def test_every_business_route_is_gated_and_status_is_minimal(self):
        token = self.protect()
        item = identity()
        routes = [('GET', ''), ('GET', '/status'), ('GET', '/status?refresh=1'), ('GET', '/settings'),
                  ('PATCH', '/settings'), ('PATCH', '/order'), ('GET', '/' + item), ('PUT', '/' + item),
                  ('DELETE', '/' + item), ('GET', '/images/' + item), ('GET', '/uploads/' + item),
                  ('POST', '/uploads?id=' + item + '&name=a.png'), ('POST', '/uploads/' + item + '/touch'),
                  ('POST', '/uploads/' + item + '/discard'), ('GET', '/jobs/' + item),
                  ('POST', '/backup/retry'), ('POST', '/reconcile/check'), ('POST', '/reconcile/apply')]
        for method, path in routes:
            with self.subTest(method=method, path=path):
                status, result, _ = self.request(method, '/api/notes' + path, {} if method != 'GET' else None)
                self.assertEqual((status, result), (401, {'error': 'notes_auth_required'}))
        status, result, headers = self.request('GET', '/api/notes/auth')
        self.assertEqual(set(result), {'enabled', 'passwordRequired', 'authenticated', 'retryAfterSeconds'})
        self.assertFalse(result['authenticated'])
        self.assertIn('no-store', headers['cache-control'])
        self.assertEqual(self.request('GET', '/api/notes', headers=token)[0], 200)
        self.assertEqual(self.request('GET', '/healthz')[0], 200)

    def test_resume_rotation_lock_and_cooldown_headers(self):
        token = self.protect()
        status, result, _ = self.request('POST', '/api/notes/auth/resume', {}, token)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('GET', '/api/notes', headers=token)[0], 401)
        rotated = {'X-Notes-Token': result['token']}
        for _ in range(4): self.assertEqual(self.request('POST', '/api/notes/auth/unlock', {'password': 'wrong'})[0], 401)
        status, result, headers = self.request('POST', '/api/notes/auth/unlock', {'password': 'wrong'})
        self.assertEqual(status, 429)
        self.assertEqual(int(headers['retry-after']), result['retryAfterSeconds'])
        self.assertEqual(self.request('GET', '/api/notes/status', headers=rotated)[0], 200)
        self.assertEqual(self.request('POST', '/api/notes/auth/lock', {}, rotated)[0], 200)
        self.assertEqual(self.request('GET', '/api/notes', headers=rotated)[0], 401)

    def test_auth_request_guards_and_limits(self):
        self.protect()
        for headers in [{'Origin': 'https://evil.test'}, {'X-Requested-With': ''}, {'Content-Type': 'text/plain'}]:
            self.assertGreaterEqual(self.request('POST', '/api/notes/auth/unlock', {'password': 'test-password'}, headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/notes/auth/unlock', {'password': 'x' * 5000})[0], 413)
        for payload in [[], {}, {'password': 1}, {'password': 'test-password', 'extra': True}]:
            self.assertEqual(self.request('POST', '/api/notes/auth/unlock', payload)[0], 400)
        response = self.raw('POST /api/notes/auth/unlock HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}')
        self.assertIn(b'400', response.split(b'\r\n')[0])
