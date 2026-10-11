"""In-memory Notes authorization, independent from dashboard Basic Auth."""
from __future__ import annotations

import hashlib
import math
import secrets
import threading
import time


class AuthFailure(Exception):
    def __init__(self, status, code, retry_after=0):
        super().__init__(code)
        self.status, self.code, self.retry_after = status, code, retry_after


class NotesAuth:
    def __init__(self, enabled, password, *, clock=time.monotonic, capacity=4096):
        self.enabled = enabled
        self.required = bool(password)
        self._digest = hashlib.sha256(password.encode("utf-8")).digest()
        self._clock, self._capacity = clock, capacity
        self._sessions = set()
        self._lock = threading.Lock()
        self._failures = 0
        self._blocked_until = 0.0

    def _remaining(self):
        return max(0, math.ceil(self._blocked_until - self._clock()))

    def status(self, token=None):
        with self._lock:
            return {"enabled": self.enabled, "passwordRequired": self.required,
                    "authenticated": self.enabled and (not self.required or token in self._sessions),
                    "retryAfterSeconds": self._remaining()}

    def authorized(self, token):
        with self._lock:
            return not self.required or token in self._sessions

    def unlock(self, password):
        with self._lock:
            if not self.enabled:
                raise AuthFailure(503, "notes_disabled")
            if not self.required:
                return {"token": None}
            remaining = self._remaining()
            if remaining:
                raise AuthFailure(429, "notes_auth_wait", remaining)
            if self._blocked_until:
                self._blocked_until = 0.0
                self._failures = 0
            if not secrets.compare_digest(hashlib.sha256(password.encode("utf-8")).digest(), self._digest):
                self._failures += 1
                if self._failures >= 5:
                    self._blocked_until = self._clock() + 30
                    raise AuthFailure(429, "notes_auth_wait", 30)
                raise AuthFailure(401, "notes_password_incorrect")
            self._failures = 0
            if len(self._sessions) >= self._capacity:
                raise AuthFailure(503, "notes_auth_busy")
            token = secrets.token_urlsafe(32)
            self._sessions.add(token)
            return {"token": token}

    def resume(self, token):
        with self._lock:
            if not self.required:
                return {"token": None}
            if token not in self._sessions:
                raise AuthFailure(401, "notes_auth_required")
            replacement = secrets.token_urlsafe(32)
            self._sessions.remove(token)
            self._sessions.add(replacement)
            return {"token": replacement}

    def lock(self, token):
        with self._lock:
            self._sessions.discard(token)
            return {"locked": True}
