"""Discover and validate AI runtimes used by independent oh-story tasks."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_MAX_PARALLEL = 8
OPENCLAW_ACTIVE_SESSION_STATUSES = {
    "running",
    "queued",
    "pending",
    "starting",
}
OPENCLAW_STALE_ACTIVE_SECONDS = 30 * 60
_catalog_cache: Dict[str, Any] = {}
_catalog_cached_at = 0.0
_TASK_AGENT_LOCK = Path("/tmp/oohstory-library-model-agent.lock")
_REASONING_LABELS = {
    "default": "跟随工具默认",
    "off": "关闭推理",
    "minimal": "极低",
    "low": "低",
    "medium": "中",
    "high": "高",
    "xhigh": "超高",
    "adaptive": "自适应",
    "max": "最高",
    "ultra": "极致",
}


def rotate_openclaw_session(
    task: Dict[str, Any],
    *,
    reason: str = "bounded_context_continuation",
) -> tuple[str, str]:
    """Move one durable-artifact task to a fresh OpenClaw parent context."""

    previous = str(task.get("ai_session_id") or "").strip()
    generation = int(task.get("ai_session_generation") or 0) + 1
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        raise ValueError("拆书任务缺少任务标识，不能轮换 AI 会话")
    current = f"library-{task_id}-part-{generation:03d}"
    history = [
        item
        for item in (task.get("ai_session_history") or [])
        if isinstance(item, dict)
    ]
    if previous:
        history.append(
            {
                "session_id": previous,
                "rotated_at": datetime.now().isoformat(timespec="seconds"),
                "reason": reason,
            }
        )
    task["ai_session_history"] = history[-64:]
    task["ai_session_previous_id"] = previous or None
    task["ai_session_id"] = current
    task["ai_session_generation"] = generation
    task["ai_session_rotation_reason"] = reason
    task.pop("openclaw_lineage", None)
    return previous, current


def cli_runtime_env() -> Dict[str, str]:
    """Provide user CLI locations missing from systemd's minimal environment."""
    home = Path.home()
    env = dict(os.environ)
    env.setdefault("HOME", str(home))
    nvm_bins = [
        str(path)
        for path in sorted(
            (home / ".nvm" / "versions" / "node").glob("v*/bin"),
            reverse=True,
        )
        if path.is_dir()
    ]
    candidates = [
        *nvm_bins,
        str(home / ".local" / "bin"),
        str(home / ".opencode" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    existing = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    env["PATH"] = os.pathsep.join(dict.fromkeys([*candidates, *existing]))
    return env


def find_cli(name: str) -> Optional[str]:
    return shutil.which(name, path=cli_runtime_env()["PATH"])


def openclaw_session_lineage_state(
    *,
    agent_id: str,
    session_id: str,
    session_store: str | Path,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Return the persisted root session and every recursively spawned child.

    ``openclaw agent --json`` can return successfully after the parent calls
    ``sessions_yield`` even though its chapter workers are still running and
    will wake the parent again.  The session registry is therefore the durable
    lifecycle source; a zero CLI exit code is not a completion signal.
    """

    selected_agent = str(agent_id or "").strip()
    selected_session = str(session_id or "").strip()
    store_path = Path(session_store).expanduser()
    empty = {
        "exists": False,
        "active": False,
        "root_key": "",
        "root_status": "",
        "lineage_keys": [],
        "active_keys": [],
        "statuses": {},
        "session_store": str(store_path),
    }
    if not selected_agent or not selected_session or not store_path.is_file():
        return empty
    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return empty
    if not isinstance(store, dict):
        return empty

    root_key = f"agent:{selected_agent}:explicit:{selected_session}"
    if not isinstance(store.get(root_key), dict):
        return {**empty, "root_key": root_key}

    lineage = {root_key}
    changed = True
    while changed:
        changed = False
        for key, entry in store.items():
            if (
                isinstance(entry, dict)
                and str(entry.get("spawnedBy") or "") in lineage
                and key not in lineage
            ):
                lineage.add(key)
                changed = True

    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    statuses: Dict[str, str] = {}
    active_keys: List[str] = []
    for key in sorted(lineage):
        entry = store.get(key)
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").strip().casefold()
        statuses[key] = status
        if status not in OPENCLAW_ACTIVE_SESSION_STATUSES:
            continue
        try:
            updated_at = int(entry.get("updatedAt") or 0)
        except (TypeError, ValueError):
            updated_at = 0
        # A crashed Gateway can leave a child permanently marked running.
        # Keep a generous window so genuine long chapter calls are never
        # mistaken for completion, while stale registry debris cannot block a
        # book forever.  A running root is always authoritative.
        fresh = (
            key == root_key
            or updated_at <= 0
            or current_ms - updated_at <= OPENCLAW_STALE_ACTIVE_SECONDS * 1000
        )
        if fresh:
            active_keys.append(key)

    root_entry = store[root_key]
    return {
        "exists": True,
        "active": bool(active_keys),
        "root_key": root_key,
        "root_status": str(root_entry.get("status") or "").strip().casefold(),
        "lineage_keys": sorted(lineage),
        "active_keys": active_keys,
        "statuses": statuses,
        "session_store": str(store_path),
    }


def _task_agent_id(model_key: str) -> str:
    """Return one deterministic isolated OpenClaw agent per selected model."""
    digest = hashlib.sha256(model_key.encode("utf-8")).hexdigest()[:16]
    return f"library-model-{digest}"


def ensure_openclaw_task_agent(
    binary: str,
    model_key: str,
    workspace_root: Path,
) -> Dict[str, str]:
    """Pin parent and spawned child sessions to exactly one selected model.

    A run-level ``--model`` only changes the parent session.  Native
    ``sessions_spawn`` otherwise resolves from the configured agent default,
    which can silently send chapter workers to another provider.  A dedicated
    agent makes the selected model the agent default as well, and an empty
    fallback list prevents cross-provider quota leakage.
    """
    selected_model = str(model_key or "").strip()
    if not selected_model or selected_model == "default" or "/" not in selected_model:
        raise RuntimeError("OpenClaw 拆书必须选择明确的厂商/模型，禁止使用默认模型")

    workspace = workspace_root.expanduser().resolve()
    agent_id = _task_agent_id(selected_model)
    _TASK_AGENT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with _TASK_AGENT_LOCK.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = subprocess.run(
            [binary, "agents", "list", "--json"],
            cwd="/",
            env=cli_runtime_env(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or result.stdout or "无法读取 OpenClaw agent 配置").strip()
            )
        try:
            agents = json.loads(result.stdout or "[]")
        except ValueError as exc:
            raise RuntimeError("OpenClaw agent 配置不是有效 JSON") from exc
        existing = next(
            (
                item
                for item in agents
                if isinstance(item, dict) and item.get("id") == agent_id
            ),
            None,
        )
        if existing:
            if str(existing.get("model") or "").strip() != selected_model:
                raise RuntimeError("拆书专用 agent 的模型配置与前端选择不一致")
            agent_dir = str(existing.get("agentDir") or "").strip()
        else:
            created = subprocess.run(
                [
                    binary,
                    "agents",
                    "add",
                    agent_id,
                    "--non-interactive",
                    "--workspace",
                    str(workspace),
                    "--model",
                    selected_model,
                    "--json",
                ],
                cwd="/",
                env=cli_runtime_env(),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if created.returncode != 0:
                raise RuntimeError(
                    (
                        created.stderr
                        or created.stdout
                        or "创建拆书专用 OpenClaw agent 失败"
                    ).strip()
                )
            try:
                payload = json.loads(created.stdout or "{}")
            except ValueError as exc:
                raise RuntimeError("OpenClaw agent 创建结果不是有效 JSON") from exc
            if str(payload.get("model") or "").strip() != selected_model:
                raise RuntimeError("新建拆书专用 agent 未锁定前端选择的模型")
            agent_dir = str(payload.get("agentDir") or "").strip()

        cleared = subprocess.run(
            [binary, "models", "--agent", agent_id, "fallbacks", "clear"],
            cwd="/",
            env=cli_runtime_env(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if cleared.returncode != 0:
            raise RuntimeError(
                (
                    cleared.stderr
                    or cleared.stdout
                    or "无法关闭拆书专用 agent 的模型回落"
                ).strip()
            )
        if not agent_dir:
            raise RuntimeError("拆书专用 OpenClaw agent 缺少独立状态目录")
        session_store = Path(agent_dir).expanduser().resolve().parent / "sessions" / "sessions.json"
        return {
            "agent_id": agent_id,
            "agent_dir": agent_dir,
            "session_store": str(session_store),
            "model": selected_model,
        }


def _run_json(command: List[str], timeout: int = 8) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=cli_runtime_env(),
        )
        if result.returncode != 0:
            return {}
        payload = json.loads(result.stdout or "{}")
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _reasoning_options(levels: List[str]) -> List[Dict[str, str]]:
    return [
        {"id": level, "name": _REASONING_LABELS.get(level, level)}
        for level in levels
    ]


def _configured_model_metadata(binary: str) -> Dict[str, Dict[str, Any]]:
    payload = _run_json(
        [binary, "config", "get", "models.providers", "--json"]
    )
    result: Dict[str, Dict[str, Any]] = {}
    for provider_id, provider in payload.items():
        if not isinstance(provider, dict):
            continue
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or "").strip()
            if model_id:
                result[f"{provider_id}/{model_id}"] = model
    return result


def _openclaw_reasoning_profile(
    model_key: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> tuple[List[Dict[str, str]], str]:
    provider, _, model_id = model_key.partition("/")
    provider = provider.strip().lower()
    model_id = model_id.strip().lower()
    reasoning = (metadata or {}).get("reasoning")

    if reasoning is False:
        levels = ["off"]
        default = "off"
    elif provider == "openai":
        levels = ["off", "minimal", "low", "medium", "high"]
        if model_id.startswith(
            (
                "gpt-5.6",
                "gpt-5.5",
                "gpt-5.4",
                "gpt-5.3-codex-spark",
            )
        ):
            levels.append("xhigh")
        if model_id.startswith("gpt-5.6"):
            levels.append("max")
        if model_id in {"gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra"}:
            levels.append("ultra")
        default = (
            "low"
            if model_id == "gpt-5.6-sol"
            else "medium"
        )
    elif provider in {"google", "google-gemini-cli", "google-vertex"}:
        if "gemini-3" in model_id and "pro" in model_id:
            levels = ["off", "low", "adaptive", "high"]
        else:
            levels = ["off", "minimal", "low", "medium", "adaptive", "high"]
        default = "medium"
    elif provider in {"anthropic", "claude"}:
        levels = ["off", "minimal", "low", "medium", "high"]
        if any(
            token in model_id
            for token in ("fable-5", "mythos-5", "sonnet-5", "opus-4-7", "opus-4-8")
        ):
            levels.extend(["xhigh", "adaptive", "max"])
        elif any(
            token in model_id
            for token in ("sonnet-4-6", "opus-4-6")
        ):
            levels.extend(["adaptive", "max"])
        default = "high"
    elif provider == "xai":
        levels = ["off", "minimal", "low", "medium", "high"]
        default = "low"
    elif provider == "ollama":
        levels = ["off", "low", "medium", "high", "max"]
        default = "off"
    else:
        # OpenClaw 对声明支持 reasoning、但没有专属策略的模型使用基础档位。
        levels = ["off", "minimal", "low", "medium", "high"]
        default = "medium"
    return _reasoning_options(levels), default


def _openclaw_runner() -> Dict[str, Any]:
    binary = find_cli("openclaw")
    payload = _run_json([binary, "models", "list", "--json"]) if binary else {}
    configured_metadata = _configured_model_metadata(binary) if binary else {}
    profiles = []
    for model in payload.get("models") or []:
        if not isinstance(model, dict):
            continue
        tags = set(model.get("tags") or [])
        if (
            not model.get("available")
            or model.get("missing")
            or "configured" not in tags
        ):
            continue
        key = str(model.get("key") or "").strip()
        if not key:
            continue
        reasoning_options, default_reasoning_effort = (
            _openclaw_reasoning_profile(key, configured_metadata.get(key))
        )
        profiles.append(
            {
                "id": key,
                "name": str(model.get("name") or key),
                "default": "default" in tags,
                "context_window": int(model.get("contextWindow") or 0),
                "reasoning_options": reasoning_options,
                "default_reasoning_effort": default_reasoning_effort,
            }
        )
    profiles.sort(key=lambda item: (not item["default"], item["name"].lower()))
    return {
        "id": "openclaw",
        "name": "OpenClaw 独立会话",
        "description": "为每本书启动独立 OpenClaw 会话，并执行 oh-story 拆文技能。",
        "available": bool(binary and profiles),
        "profiles": profiles,
        "default_profile": next(
            (item["id"] for item in profiles if item["default"]),
            profiles[0]["id"] if profiles else "",
        ),
        "health": (
            f"已发现 {len(profiles)} 个已配置模型"
            if profiles
            else "OpenClaw 未安装或没有可用的已配置模型"
        ),
    }


def _claude_runner() -> Dict[str, Any]:
    binary = find_cli("claude")
    status = _run_json([binary, "auth", "status", "--json"]) if binary else {}
    available = bool(binary and status.get("loggedIn"))
    profiles = (
        [
            {
                "id": "default",
                "name": "Claude 账号默认模型",
                "default": True,
                "context_window": 0,
                "reasoning_options": _reasoning_options(
                    ["low", "medium", "high", "xhigh", "max"]
                ),
                "default_reasoning_effort": "high",
            }
        ]
        if available
        else []
    )
    return {
        "id": "claude",
        "name": "Claude Code 独立会话",
        "description": "使用已登录的 Claude Code，并显式加载项目内 oh-story 插件。",
        "available": available,
        "profiles": profiles,
        "default_profile": "default" if available else "",
        "health": "Claude Code 已登录" if available else "Claude Code 未安装或未登录",
    }


def _opencode_runner() -> Dict[str, Any]:
    binary = find_cli("opencode")
    profiles = (
        [
            {
                "id": "default",
                "name": "OpenCode 默认模型",
                "default": True,
                "context_window": 0,
                "reasoning_options": _reasoning_options(["default"]),
                "default_reasoning_effort": "default",
            }
        ]
        if binary
        else []
    )
    return {
        "id": "opencode",
        "name": "OpenCode 独立会话",
        "description": "使用 OpenCode 当前配置启动独立 oh-story 拆文任务。",
        "available": bool(binary),
        "profiles": profiles,
        "default_profile": "default" if binary else "",
        "health": "OpenCode 已安装" if binary else "OpenCode 当前未安装",
    }


def max_parallel_tasks() -> int:
    try:
        configured = int(os.getenv("LIBRARY_TASK_MAX_PARALLEL", ""))
    except ValueError:
        configured = DEFAULT_MAX_PARALLEL
    return min(max(configured or DEFAULT_MAX_PARALLEL, 3), 32)


def task_runner_catalog(*, refresh: bool = False) -> Dict[str, Any]:
    global _catalog_cache, _catalog_cached_at
    now = time.monotonic()
    if not refresh and _catalog_cache and now - _catalog_cached_at < 30:
        return _catalog_cache
    runners = [_openclaw_runner(), _claude_runner(), _opencode_runner()]
    default_runner = next(
        (runner["id"] for runner in runners if runner["id"] == "openclaw" and runner["available"]),
        next((runner["id"] for runner in runners if runner["available"]), ""),
    )
    _catalog_cache = {
        "runners": runners,
        "default_runner": default_runner,
        "max_parallel": max_parallel_tasks(),
        "policy": "每个任务独立进程、独立 AI 会话、独立日志；同一本书仍保持幂等复用。",
    }
    _catalog_cached_at = now
    return _catalog_cache


def resolve_task_runner(
    runner_id: Optional[str],
    profile_id: Optional[str],
    reasoning_effort: Optional[str] = None,
) -> Dict[str, str]:
    catalog = task_runner_catalog()
    requested_runner = str(runner_id or "").strip() or catalog["default_runner"]
    runner = next(
        (item for item in catalog["runners"] if item["id"] == requested_runner),
        None,
    )
    if not runner:
        raise ValueError("未知的拆书执行工具")
    if not runner["available"]:
        raise ValueError(str(runner["health"]))
    requested_profile = str(profile_id or "").strip() or runner["default_profile"]
    valid_profiles = {item["id"] for item in runner["profiles"]}
    if requested_profile not in valid_profiles:
        raise ValueError("所选 AI 配置不存在或当前不可用")
    profile = next(
        item for item in runner["profiles"] if item["id"] == requested_profile
    )
    reasoning_options = profile.get("reasoning_options") or _reasoning_options(
        ["default"]
    )
    requested_reasoning = (
        str(reasoning_effort or "").strip().lower()
        or str(profile.get("default_reasoning_effort") or "").strip().lower()
        or str(reasoning_options[0]["id"])
    )
    valid_reasoning = {
        str(item.get("id") or "") for item in reasoning_options
    }
    if requested_reasoning not in valid_reasoning:
        raise ValueError("所选 AI 智商不受当前厂商或模型支持")
    return {
        "runner_id": str(runner["id"]),
        "runner_name": str(runner["name"]),
        "profile_id": requested_profile,
        "profile_name": str(profile["name"]),
        "reasoning_effort": requested_reasoning,
        "reasoning_name": next(
            str(item.get("name") or requested_reasoning)
            for item in reasoning_options
            if item.get("id") == requested_reasoning
        ),
    }
