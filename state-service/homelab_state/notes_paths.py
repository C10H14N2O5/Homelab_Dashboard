"""Notes entry resolution. All potentially remote calls run in a bounded worker."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys


class StoragePathError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalized(path):
    return os.path.normcase(os.path.normpath(str(path)))


def within(path, root):
    try:
        return os.path.commonpath([normalized(path), normalized(root)]) == normalized(root)
    except ValueError:
        return False


def overlaps(first, second):
    return within(first, second) or within(second, first)


def absolute_entry(path):
    return Path(os.path.abspath(path))


def _unprefix(value):
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    return value[4:] if value.startswith("\\\\?\\") else value


def resolve_entry(entry, *, local=False):
    seen = set()
    linked = False

    def walk(path):
        nonlocal linked
        path = absolute_entry(path)
        if local:
            require_local(path)
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                raise StoragePathError("storage_target_missing", "存储入口或链接目标不存在") from None
            tag = getattr(info, "st_reparse_tag", 0)
            is_link = stat.S_ISLNK(info.st_mode) or tag == 0xA000000C
            is_junction = tag == 0xA0000003
            if tag and not (is_link or is_junction):
                raise StoragePathError("unsupported_link_type", "不支持的链接类型")
            if is_link or is_junction:
                key = normalized(current)
                if key in seen or len(seen) >= 40:
                    raise StoragePathError("storage_link_loop", "存储入口存在链接循环或超过 40 跳")
                seen.add(key)
                target_text = os.readlink(current)
                if is_junction and "volume{" in target_text.lower():
                    raise StoragePathError("unsupported_link_type", "不支持的链接类型：卷挂载点")
                target = Path(_unprefix(target_text))
                linked = True
                current = walk(target if target.is_absolute() else current.parent / target)
        return current

    target = walk(entry)
    if not target.is_dir():
        raise StoragePathError("storage_not_directory", "存储目标不是目录")
    return target, linked


def require_local(target):
    if str(target).startswith("\\\\"):
        raise StoragePathError("primary_not_local", "笔记主目录的真实目标必须位于本机磁盘")
    if os.name == "nt":
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(target.anchor)) not in (2, 3, 6):
            raise StoragePathError("primary_not_local", "笔记主目录不能使用网络映射盘或不可用磁盘")


def validate_target(entry, target, request):
    for directory in request.get("forbiddenRoots", []):
        if within(entry, directory) or overlaps(target, directory):
            raise StoragePathError("storage_boundary", "Notes storage must not be located inside or contain the public Web Root")
    for directory in request.get("protectedDirs", []):
        if overlaps(entry, directory) or overlaps(target, directory):
            raise StoragePathError("storage_boundary", "笔记目录不得与状态目录或另一笔记目录重叠")
    config_file = request.get("configFile")
    if config_file and (within(config_file, entry) or within(config_file, target)):
        raise StoragePathError("storage_boundary", "笔记目录不得包含配置文件")
    other = request.get("otherTarget")
    if other and overlaps(target, other):
        raise StoragePathError("storage_overlap", "两个笔记目录的真实目标不得重叠")


def resolve_request(request):
    entry = absolute_entry(request["root"])
    if any(within(entry, directory) for directory in request.get("forbiddenRoots", [])):
        raise StoragePathError("storage_boundary", "Notes storage must not be located inside the public Web Root")
    target, linked = resolve_entry(entry, local=request.get("primary", False))
    if request.get("primary"):
        require_local(target)
    validate_target(entry, target, request)
    return target, {"target": str(target), "linked": linked}


def run_probe(request, state, timeout=5):
    """Caller serializes this state; unreaped children are never replaced."""
    previous = state.get("process")
    if previous is not None and previous.poll() is None:
        raise StoragePathError("worker_unreaped", "上一个目录探测进程未退出，请重启后端")
    command = [sys.executable, "--notes-worker"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "homelab_state.notes_worker"]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parent.parent) + os.pathsep + environment.get("PYTHONPATH", "")
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    state["process"] = process
    try:
        output, _ = process.communicate(json.dumps(request).encode(), timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            raise StoragePathError("worker_unreaped", "目录探测进程未退出，请重启后端") from None
        raise StoragePathError("path_probe_timeout", "存储目录解析超时") from None
    finally:
        if process.poll() is not None:
            state["process"] = None
    if process.returncode:
        raise StoragePathError("worker_failed", "目录探测进程失败")
    response = json.loads(output)
    if not response.get("ok"):
        code = response.get("code") or ("storage_permission" if response.get("kind") == "PermissionError" else "backup_unavailable")
        raise StoragePathError(code, response.get("error", "目录探测失败"))
    return response["result"]
