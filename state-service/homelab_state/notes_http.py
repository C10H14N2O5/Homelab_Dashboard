"""Notes routes share the dashboard's framing and mutation request guards."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from homelab_state.notes import NotesError
from homelab_state.notes_auth import AuthFailure

MAX_NOTES_JSON_BODY = 2 * 1024 * 1024
MAX_NOTES_IMAGE_BODY = 20 * 1024 * 1024
_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_ITEM = re.compile(rf"^/api/notes/({_ID})$")
_RESOURCE = re.compile(rf"^/api/notes/(uploads|images|jobs)/({_ID})(?:/(touch|discard))?$")
_STATIC = {
    "/api/notes/auth": ("GET",),
    "/api/notes/auth/unlock": ("POST",),
    "/api/notes/auth/resume": ("POST",),
    "/api/notes/auth/lock": ("POST",),
    "/api/notes": ("GET",),
    "/api/notes/order": ("PATCH",),
    "/api/notes/settings": ("GET", "PATCH"),
    "/api/notes/status": ("GET",),
    "/api/notes/uploads": ("POST",),
    "/api/notes/backup/retry": ("POST",),
    "/api/notes/reconcile/check": ("POST",),
    "/api/notes/reconcile/apply": ("POST",),
}


def allowed_notes_methods(path: str) -> tuple[str, ...]:
    if path in _STATIC:
        return _STATIC[path]
    if _ITEM.fullmatch(path):
        return ("GET", "PUT", "DELETE")
    match = _RESOURCE.fullmatch(path)
    if match:
        kind, _, action = match.groups()
        if action is None:
            return ("GET",)
        if kind == "uploads":
            return ("POST",)
    return ()


def notes_accepts_body(command: str, raw_path: str) -> bool:
    return command in {"PUT", "PATCH", "DELETE", "POST"} and command in allowed_notes_methods(urlsplit(raw_path).path)


def _availability(handler):
    config = handler.server.config
    enabled = config.notes_enabled
    failure = getattr(handler.server, "_notes_init_error", None) or {}
    return {
        "enabled": enabled,
        "primaryAvailable": False,
        "backupAvailable": False,
        "backupPending": 0,
        "backupBlocked": 0,
        "paths": {"primaryDir": str(config.notes_primary_dir or ""), "backupDir": str(config.notes_backup_dir or "")},
        "resolvedPaths": {"primaryDir": None, "backupDir": None},
        "entryLinks": {"primaryDir": None, "backupDir": None},
        "primaryErrorCode": failure.get("code"), "primaryError": failure.get("message"),
        "backupErrorCode": None, "pathProbePending": False,
        "error": "notes_unavailable" if enabled else "notes_disabled",
    }


def handle_notes_request(handler) -> bool:
    parsed = urlsplit(handler.path)
    path = parsed.path
    if path != "/api/notes" and not path.startswith("/api/notes/"):
        return False
    allowed = allowed_notes_methods(path)
    if not allowed:
        handler._error(404, "notes_route_not_found")
        return True
    if handler.command not in allowed:
        handler._allow(", ".join(allowed))
        return True
    refresh_status = path == "/api/notes/status" and parsed.query == "refresh=1"
    if parsed.query and path != "/api/notes/uploads" and not refresh_status:
        handler._close_rejected_request()
        handler._error(400, "query parameters are not allowed")
        return True
    auth = handler.server.notes_auth
    tokens = handler.headers.get_all("X-Notes-Token", [])
    token = tokens[0] if len(tokens) == 1 and len(tokens[0]) <= 128 else None
    if path == "/api/notes/auth":
        handler._write_json(200, auth.status(token))
        return True
    if path.startswith("/api/notes/auth/"):
        try:
            if path != "/api/notes/auth/unlock" and not auth.authorized(token):
                raise AuthFailure(401, "notes_auth_required")
            payload = handler._read_dashboard_json(4096)
            if payload is None:
                return True
            if path.endswith("/unlock"):
                if not isinstance(payload, dict) or set(payload) != {"password"} or not isinstance(payload["password"], str):
                    handler._error(400, "invalid_password_request")
                    return True
                result = auth.unlock(payload["password"])
            else:
                if payload != {}:
                    handler._error(400, "request must be an empty object")
                    return True
                result = auth.resume(token) if path.endswith("/resume") else auth.lock(token)
            handler._write_json(200, result)
        except AuthFailure as error:
            handler._close_rejected_request()
            if error.retry_after:
                # _write_json uses the shared cache/framing headers. Supply
                # Retry-After through the response helper without logging secrets.
                body = {"error": error.code, "retryAfterSeconds": error.retry_after}
                handler._write_notes_auth_wait(error.retry_after, body)
            else:
                handler._error(error.status, error.code)
        return True
    if not auth.authorized(token):
        handler._close_rejected_request()
        handler._error(401, "notes_auth_required")
        return True
    service = handler.server.get_notes_service()
    if service is None:
        if handler.command == "GET" and path == "/api/notes/status":
            handler._write_json(200, _availability(handler))
        else:
            handler._close_rejected_request()
            state = _availability(handler)
            handler._write_json(503, {"error": state["error"], "message": "笔记未启用或主存储不可用", "status": state})
        return True
    try:
        payload = None
        if handler.command != "GET":
            if path == "/api/notes/uploads":
                if not handler._validate_dashboard_headers("application/octet-stream"):
                    return True
                query = parse_qsl(parsed.query, keep_blank_values=True)
                if len(query) != 2 or {key for key, _ in query} != {"id", "name"}:
                    handler._close_rejected_request()
                    handler._error(400, "upload requires exactly id and name")
                    return True
                values = dict(query)
                if not re.fullmatch(_ID, values["id"]) or not values["name"] or len(values["name"]) > 255:
                    handler._close_rejected_request()
                    handler._error(400, "invalid upload identity")
                    return True
                length = handler._read_content_length(MAX_NOTES_IMAGE_BODY)
                if length is None:
                    return True
                body = handler._read_exact_body(length)
                if body is None:
                    return True
                handler._write_json(201, service.upload(values["id"], values["name"], body))
                return True
            payload = handler._read_dashboard_json(MAX_NOTES_JSON_BODY)
            if payload is None:
                return True
            if not isinstance(payload, dict):
                handler._error(400, "request must be a JSON object")
                return True
        result = None
        if path == "/api/notes":
            result = service.list_notes()
        elif path == "/api/notes/status":
            result = service.status(refresh=refresh_status)
        elif path == "/api/notes/settings":
            result = service.get_settings() if handler.command == "GET" else service.update_settings(payload)
        elif path == "/api/notes/order":
            result = service.update_order(payload)
        elif path in {"/api/notes/backup/retry", "/api/notes/reconcile/check"}:
            if payload:
                handler._error(400, "request must be an empty object")
                return True
            result = service.retry_backup() if path.endswith("retry") else service.reconcile_check()
        elif path == "/api/notes/reconcile/apply":
            result = service.reconcile_apply(payload)
        elif item := _ITEM.fullmatch(path):
            identity = item.group(1)
            if handler.command == "GET":
                result = service.get_note(identity)
            elif handler.command == "PUT":
                result = service.save_note(identity, payload)
            else:
                result = service.delete_note(identity, payload)
        elif resource := _RESOURCE.fullmatch(path):
            kind, identity, action = resource.groups()
            if kind == "images":
                data, mime_type = service.get_image(identity)
                handler.send_response(200)
                handler._common_headers()
                handler.send_header("Content-Type", mime_type)
                handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)
                return True
            if kind == "jobs":
                result = service.get_job(identity)
            elif action:
                if payload:
                    handler._error(400, "request must be an empty object")
                    return True
                result = service.touch_upload(identity) if action == "touch" else service.discard_upload(identity)
            else:
                result = service.get_upload(identity)
        handler._write_json(200, result)
    except NotesError as error:
        response = {"error": error.code, "message": str(error)}
        if error.current is not None:
            response["current"] = error.current
        handler._write_json(error.status, response)
    except (BrokenPipeError, ConnectionResetError):
        handler.close_connection = True
    except Exception:
        # The logger records route/failure, not the private Markdown or image.
        handler.server.logger.exception("notes request failed: %s %s", handler.command, path)
        handler._write_json(500, {"error": "notes_internal_error", "message": "笔记操作失败，草稿仍保留"})
    return True
