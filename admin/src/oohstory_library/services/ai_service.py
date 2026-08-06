
"""AI 服务模块 - 对接 OpenAI 兼容 API / Codex 登录态"""

import os
import re
import json
import asyncio
import signal
import subprocess
import uuid
import httpx
from typing import Optional, List, Dict, Any
from pathlib import Path

from oohstory_library.services.projects_manager import GLOBAL_CONFIG_DIR
from oohstory_library.services.codex_cli import codex_env, ensure_codex_cli

# 默认 AI API 配置
DEFAULT_AI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_AI_API_KEY = ""
DEFAULT_AI_MODEL = "gemini-3.1-pro-preview"
PLACEHOLDER_API_KEYS = {"", "your-openai-api-key-here", "******"}
CODEX_MODEL_OPTIONS = [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "codex-auto-review",
]
PROVIDER_MODEL_CATALOGS = {
    "claude_cli": ["claude-opus-4-8", "claude-sonnet-4-8", "claude-opus-4-6", "claude-sonnet-4-6"],
    "gemini_cli": ["gemini-3.1-pro-preview", "gemini-2.5-flash"],
    "anthropic": ["claude-opus-4-8", "claude-sonnet-4-8", "claude-opus-4-6", "claude-sonnet-4-6", "claude-3-5-haiku-latest"],
    "openai": ["gpt-5.5", "gpt-5.4", "gpt-4o"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "moonshot": ["kimi-k2-0711-preview", "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
    "gemini_openai": ["gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-2.5-flash"],
    "xai": ["grok-4", "grok-3", "grok-3-mini"],
    "modelscope": ["Qwen/Qwen3-Coder-480B-A35B-Instruct", "Qwen/Qwen2.5-72B-Instruct", "ZhipuAI/GLM-5"],
    "generic": ["gpt-5.5", "gpt-5.4", "gemini-3.1-pro-preview", "deepseek-chat"],
}


class AIService:
    """AI 服务封装"""

    def __init__(self, base_url: str = None, api_key: str = None, model: str = None):
        self.base_url = base_url if base_url is not None else os.getenv("AI_BASE_URL", DEFAULT_AI_BASE_URL)
        self.api_key = api_key if api_key is not None else os.getenv("AI_API_KEY", DEFAULT_AI_API_KEY)
        self.model = model if model is not None else os.getenv("AI_MODEL", DEFAULT_AI_MODEL)
        self.timeout = 1800  # 3分钟超时

    def _has_real_api_key(self) -> bool:
        return (self.api_key or "").strip() not in PLACEHOLDER_API_KEYS

    def _is_openai_base_url(self) -> bool:
        return "api.openai.com" in (self.base_url or "")

    def _is_anthropic(self) -> bool:
        """判断当前配置是否指向 Anthropic 原生 API"""
        return "api.anthropic.com" in (self.base_url or "")

    def _is_claude_cli(self) -> bool:
        """判断是否使用本地 Claude CLI"""
        return (self.base_url or "").strip().lower() == "claude-cli"

    def _is_gemini_cli(self) -> bool:
        """判断是否使用本地 Gemini CLI"""
        return (self.base_url or "").strip().lower() == "gemini-cli"

    def _provider_key(self) -> str:
        base_url = (self.base_url or "").strip().lower()
        if base_url == "claude-cli":
            return "claude_cli"
        if base_url == "gemini-cli":
            return "gemini_cli"
        if "api.anthropic.com" in base_url:
            return "anthropic"
        if "api.openai.com" in base_url:
            return "openai"
        if "api.deepseek.com" in base_url:
            return "deepseek"
        if "api.moonshot.cn" in base_url:
            return "moonshot"
        if "generativelanguage.googleapis.com" in base_url:
            return "gemini_openai"
        if "api.x.ai" in base_url:
            return "xai"
        if "modelscope.cn" in base_url:
            return "modelscope"
        return "generic"

    def _dedupe_models(self, models: List[str]) -> List[str]:
        seen = set()
        return [m for m in models if m and not (m in seen or seen.add(m))]

    def _fallback_models(self) -> List[str]:
        return self._dedupe_models([self.model, *PROVIDER_MODEL_CATALOGS.get(self._provider_key(), PROVIDER_MODEL_CATALOGS["generic"])])

    def _openai_compat_url(self, suffix: str) -> str:
        base_url = (self.base_url or DEFAULT_AI_BASE_URL).rstrip("/")
        suffix = suffix.lstrip("/")
        if base_url.endswith(("/v1", "/v1beta/openai", "/openai", "/compatible-mode/v1")):
            return f"{base_url}/{suffix}"
        return f"{base_url}/v1/{suffix}"

    def check_claude_cli_ready(self) -> dict:
        """检测本地 Claude CLI 是否可用及登录状态"""
        import shutil, subprocess
        path = shutil.which("claude")
        if not path:
            return {"ready": False, "reason": "未找到 claude 命令，请先安装 Claude Code CLI"}
        try:
            r = subprocess.run(
                ["claude", "--version"],
                capture_output=True, text=True, timeout=5
            )
            version = (r.stdout or r.stderr or "").strip().splitlines()[0] if r.returncode == 0 else ""
            if not version:
                return {"ready": False, "reason": "claude --version 调用失败"}
            return {"ready": True, "version": version, "path": path}
        except Exception as e:
            return {"ready": False, "reason": str(e)}

    def check_gemini_cli_ready(self) -> dict:
        import shutil, subprocess
        path = shutil.which("gemini")
        if not path:
            return {"ready": False, "reason": "未找到 gemini 命令，请先安装 Gemini CLI"}
        try:
            r = subprocess.run(["gemini", "--version"], capture_output=True, text=True, timeout=5)
            version = (r.stdout or r.stderr or "").strip().splitlines()[0] if r.returncode == 0 else ""
            if not version:
                return {"ready": False, "reason": "gemini --version 调用失败"}
            return {"ready": True, "version": version, "path": path, "default_model": "gemini-3.1-pro-preview"}
        except Exception as e:
            return {"ready": False, "reason": str(e)}

    def _build_gemini_cli_cmd(self, user_prompt: str, system_prompt: str, stream: bool) -> List[str]:
        prompt = user_prompt.strip()
        if system_prompt.strip():
            prompt = f"[System]\n{system_prompt.strip()}\n\n[User]\n{prompt}"
        cmd = ["gemini", "-p", prompt, "--approval-mode", "plan"]
        if self.model and self.model.strip():
            cmd += ["--model", self.model.strip()]
        cmd += ["--output-format", "stream-json" if stream else "json"]
        return cmd

    async def _chat_gemini_cli(self, messages: List[Dict], temperature: float, max_tokens: int) -> str:
        system_prompt, user_prompt = self._extract_cli_messages(messages)
        cmd = self._build_gemini_cli_cmd(user_prompt, system_prompt, stream=False)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            await self._terminate_process_group(proc)
            raise
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="ignore").strip()
            raise Exception(f"Gemini CLI 调用失败: {err or f'exit code {proc.returncode}'}")
        raw = (stdout or b"").decode("utf-8", errors="ignore").strip()
        try:
            data = json.loads(raw)
            return str(data.get("response") or data.get("text") or data.get("content") or raw).strip()
        except Exception:
            return raw

    async def _chat_stream_gemini_cli(self, messages: List[Dict], temperature: float, max_tokens: int):
        system_prompt, user_prompt = self._extract_cli_messages(messages)
        cmd = self._build_gemini_cli_cmd(user_prompt, system_prompt, stream=True)
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            ev_type = event.get("type")
            if ev_type != "message" or event.get("role") != "assistant":
                continue

            text = ""
            for key in ("content", "text", "response"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    text = value
                    break
            if text:
                yield text
        stderr = await proc.stderr.read()
        await proc.wait()
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="ignore").strip()
            yield f"[ERROR] Gemini CLI: {err or f'exit code {proc.returncode}'}"

    def _build_claude_cli_cmd(self, user_prompt: str, system_prompt: str, stream: bool) -> List[str]:
        """组装 claude CLI 命令"""
        # --allowedTools "" 禁止工具调用，避免交互弹窗；同时兼容 root 用户（不用 --dangerously-skip-permissions）
        cmd = ["claude", "-p", user_prompt, "--allowedTools", ""]
        if self.model and self.model.strip():
            cmd += ["--model", self.model.strip()]
        if system_prompt:
            cmd += ["--append-system-prompt", system_prompt]
        if stream:
            cmd += ["--output-format", "stream-json", "--verbose", "--include-partial-messages"]
        else:
            cmd += ["--output-format", "text"]
        return cmd

    def _extract_cli_messages(self, messages: List[Dict]) -> tuple[str, str]:
        """将 OpenAI 格式消息列表拆分为 (system_prompt, user_prompt)"""
        system_parts, user_parts = [], []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    item if isinstance(item, str) else item.get("text", "")
                    for item in content
                )
            if role == "system":
                system_parts.append(content)
            else:
                user_parts.append(str(content))
        return "\n\n".join(system_parts), "\n\n".join(user_parts)

    async def _chat_claude_cli(self, messages: List[Dict], temperature: float, max_tokens: int) -> str:
        """通过本地 Claude CLI 发送非流式请求"""
        system_prompt, user_prompt = self._extract_cli_messages(messages)
        cmd = self._build_claude_cli_cmd(user_prompt, system_prompt, stream=False)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            await self._terminate_process_group(proc)
            raise
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="ignore").strip()
            raise Exception(f"Claude CLI 调用失败: {err or f'exit code {proc.returncode}'}")
        return (stdout or b"").decode("utf-8", errors="ignore").strip()

    async def _chat_stream_claude_cli(self, messages: List[Dict], temperature: float, max_tokens: int):
        """通过本地 Claude CLI 发送流式请求，逐块 yield 增量文本"""
        system_prompt, user_prompt = self._extract_cli_messages(messages)
        cmd = self._build_claude_cli_cmd(user_prompt, system_prompt, stream=True)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )

        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            ev_type = event.get("type")
            if ev_type == "stream_event":
                # 真正的增量流：stream_event -> content_block_delta -> text_delta
                inner = event.get("event", {})
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield text
            elif ev_type == "result":
                if event.get("is_error"):
                    yield f"[ERROR] Claude CLI: {event.get('result', '未知错误')}"
                return

        await proc.wait()
        if proc.returncode not in (0, None):
            err_data = await proc.stderr.read()
            err = err_data.decode("utf-8", errors="ignore").strip()
            if err:
                yield f"[ERROR] Claude CLI: {err}"

    def _anthropic_headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _to_anthropic_payload(self, messages: List[Dict], temperature: float, max_tokens: int, stream: bool) -> Dict[str, Any]:
        """将 OpenAI 消息格式转为 Anthropic Messages API 格式"""
        system_parts = []
        user_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    item if isinstance(item, str) else item.get("text", "")
                    for item in content
                )
            if role == "system":
                system_parts.append(content)
            else:
                user_messages.append({"role": role, "content": content})

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": user_messages,
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        return payload

    async def _chat_anthropic(self, messages: List[Dict], temperature: float, max_tokens: int) -> str:
        """使用 Anthropic 原生 Messages API 发送非流式请求"""
        import aiohttp
        url = "https://api.anthropic.com/v1/messages"
        payload = self._to_anthropic_payload(messages, temperature, max_tokens, stream=False)

        connector = aiohttp.TCPConnector(force_close=True, enable_cleanup_closed=True)
        timeout = aiohttp.ClientTimeout(total=self.timeout, connect=30, sock_read=self.timeout)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
            async with session.post(url, json=payload, headers=self._anthropic_headers()) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    raise Exception(f"Anthropic API {resp.status}: {err[:200]}")
                data = await resp.json()
                # 响应格式: {"content": [{"type": "text", "text": "..."}], ...}
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        return block["text"]
                raise Exception(f"Anthropic 返回了空内容: {data}")

    async def _chat_stream_anthropic(self, messages: List[Dict], temperature: float, max_tokens: int):
        """使用 Anthropic 原生 Messages API 发送流式请求"""
        import aiohttp
        url = "https://api.anthropic.com/v1/messages"
        payload = self._to_anthropic_payload(messages, temperature, max_tokens, stream=True)

        connector = aiohttp.TCPConnector(force_close=True, enable_cleanup_closed=True)
        timeout = aiohttp.ClientTimeout(total=self.timeout, connect=30, sock_read=self.timeout)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
            async with session.post(url, json=payload, headers=self._anthropic_headers()) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    yield f"[ERROR] Anthropic API {resp.status}: {err[:200]}"
                    return

                buffer = ""
                async for chunk in resp.content.iter_any():
                    buffer += chunk.decode("utf-8", errors="ignore")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line or line.startswith("event:"):
                            continue
                        if line.startswith("data:"):
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                return
                            try:
                                ev = json.loads(raw)
                                # content_block_delta 事件携带文本增量
                                if ev.get("type") == "content_block_delta":
                                    delta = ev.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        yield delta["text"]
                                elif ev.get("type") == "message_stop":
                                    return
                            except json.JSONDecodeError:
                                continue

    def _codex_logged_in(self) -> bool:
        try:
            result = subprocess.run([ensure_codex_cli(), "login", "status"], capture_output=True, text=True, timeout=10, env=codex_env())
            text = ((result.stdout or "") + (result.stderr or "")).strip().lower()
            return ("not logged in" not in text) and ("logged in" in text)
        except Exception:
            return False

    def _normalize_codex_model(self, model_name: str) -> str:
        name = (model_name or "").strip()
        if name.lower() == "gpt-5.6-soul":
            return "gpt-5.6-sol"
        return name

    def _codex_available_models(self) -> List[str]:
        models = []
        cache_file = Path.home() / ".codex" / "models_cache.json"
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            for item in data.get("models") or []:
                slug = item.get("slug")
                if slug and item.get("visibility", "list") == "list":
                    models.append(slug)
        except Exception:
            pass
        preferred = [*models, *CODEX_MODEL_OPTIONS]
        seen = set()
        return [m for m in preferred if m and not (m in seen or seen.add(m))]

    def _codex_error_from_output(self, stdout: bytes, stderr: bytes) -> str:
        err = (stderr or b"").decode("utf-8", errors="ignore").strip()
        for line in (stdout or b"").decode("utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "error" and event.get("message"):
                return str(event["message"])
            failed_error = (event.get("error") or {}).get("message") if isinstance(event.get("error"), dict) else None
            if event.get("type") == "turn.failed" and failed_error:
                return str(failed_error)
        return err

    def should_use_codex_auth(self) -> bool:
        model_name = self._normalize_codex_model(self.model).lower()
        if self._has_real_api_key():
            return False
        if not self._codex_logged_in():
            return False
        if "codex" in model_name:
            return True
        return self._is_openai_base_url() and model_name in {m.lower() for m in self._codex_available_models()}

    def _messages_to_codex_prompt(self, messages: List[Dict]) -> str:
        blocks = []
        for msg in messages:
            role = str(msg.get("role", "user")).upper()
            content = msg.get("content", "")
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        if isinstance(item.get("text"), str):
                            parts.append(item["text"])
                content = "\n".join(parts)
            elif not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            blocks.append(f"[{role}]\n{content}".strip())
        return "\n\n".join(blocks)

    @staticmethod
    async def _terminate_process_group(
        proc: asyncio.subprocess.Process,
    ) -> None:
        """Stop a cancelled CLI request together with all descendants."""

        if proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
            return
        except asyncio.TimeoutError:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        await proc.wait()

    async def _chat_via_codex_exec(self, messages: List[Dict]) -> str:
        prompt = self._messages_to_codex_prompt(messages)
        model_name = self._normalize_codex_model(self.model)
        login_status = ""
        try:
            status_proc = subprocess.run([ensure_codex_cli(), "login", "status"], capture_output=True, text=True, timeout=10, env=codex_env())
            login_status = ((status_proc.stdout or "") + (status_proc.stderr or "")).strip().lower()
        except Exception:
            login_status = ""

        # ChatGPT 登录态只支持本账号暴露在 Codex 模型缓存中的 slug。
        include_model = bool(model_name)
        if "logged in using chatgpt" in login_status:
            available_models = self._codex_available_models()
            if available_models and model_name not in available_models:
                model_name = available_models[0]
                include_model = True

        # 改成一次性非交互模式：显式使用 `-` 从 stdin 读取完整 prompt，随后立即关闭 stdin。
        # 这样能避免长 prompt / 特殊字符作为 argv 传入时触发 Codex 继续等待额外 stdin 输入。
        # 同时用 --output-last-message 兜底拿最终文本，避免只依赖 JSON 事件流。
        output_file = (
            Path("/tmp")
            / f"codex-last-message-{os.getpid()}-{uuid.uuid4().hex}.txt"
        )

        def cleanup_output() -> None:
            try:
                output_file.unlink(missing_ok=True)
            except OSError:
                pass

        try:
            output_file.unlink(missing_ok=True)
        except Exception:
            pass

        cmd = [ensure_codex_cli(), "exec", "-", "--json", "--output-last-message", str(output_file), "--color", "never"]
        if include_model:
            cmd += ["-m", model_name]
        cmd += ["--skip-git-repo-check", "--ephemeral", "--dangerously-bypass-approvals-and-sandbox"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            cwd=str(Path.cwd()),
            env=codex_env(),
            start_new_session=True,
        )
        try:
            stdout, stderr = await proc.communicate(prompt.encode("utf-8"))
        except asyncio.CancelledError:
            await self._terminate_process_group(proc)
            cleanup_output()
            raise
        if proc.returncode != 0:
            err = self._codex_error_from_output(stdout, stderr)
            cleanup_output()
            raise Exception(f"Codex exec failed: {err or f'code {proc.returncode}'}")

        final_text = ""
        for line in (stdout or b"").decode("utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    final_text = item["text"].strip()
            elif event.get("type") == "turn.completed" and not final_text:
                usage = event.get("usage") or {}
                self._debug(f"[CodexExec] turn.completed with usage={usage} but no final agent_message captured")
        if final_text:
            cleanup_output()
            return final_text

        if output_file.exists():
            try:
                text = output_file.read_text(encoding="utf-8").strip()
                if text:
                    cleanup_output()
                    return text
            except Exception:
                pass

        decoded_stdout = (stdout or b"").decode("utf-8", errors="ignore").strip()
        for line in reversed(decoded_stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    cleanup_output()
                    return text.strip()

        cleanup_output()
        raise Exception("Codex exec returned no final agent message")

    def _extract_message_content(self, data: Dict[str, Any]) -> str:
        """兼容不同 OpenAI 兼容网关（尤其 Gemini）的 message/content 返回格式。"""
        choices = data.get("choices") or []
        if not choices:
            raise KeyError("choices")

        message = choices[0].get("message") or {}
        content = message.get("content")

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif item.get("type") == "text" and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif isinstance(item.get("content"), str):
                        parts.append(item["content"])
            text = "".join(parts).strip()
            if text:
                return text

        # 某些兼容实现把文本放在 reasoning / output_text 等字段里
        for key in ("output_text", "text", "reasoning"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value

        # 最后兜底：把整个返回压成字符串，避免直接 KeyError('content')
        return json.dumps(data, ensure_ascii=False)

    def _debug_enabled(self) -> bool:
        flag = os.getenv("WEBNOVEL_DEBUG", "").strip().lower()
        return flag in {"1", "true", "yes", "on", "debug"}

    def _debug(self, message: str) -> None:
        if self._debug_enabled():
            print(message)

    async def chat(self, messages: List[Dict], temperature: float = 0.7, max_tokens: int = 4000, response_format: str = None) -> str:
        """发送聊天请求 - 使用 aiohttp 提升稳定性"""
        if self._is_claude_cli():
            return await self._chat_claude_cli(messages, temperature, max_tokens)

        if self._is_gemini_cli():
            return await self._chat_gemini_cli(messages, temperature, max_tokens)
        if self._is_anthropic():
            return await self._chat_anthropic(messages, temperature, max_tokens)
        if self.should_use_codex_auth():
            return await self._chat_via_codex_exec(messages)

        import aiohttp

        url = self._openai_compat_url("chat/completions")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json"
        }
        if self._has_real_api_key():
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False
        }

        # 添加 response_format 参数（强制 JSON 输出）
        if response_format in {"json", "json_object"}:
            payload["response_format"] = {"type": "json_object"}

        self._debug(f"Applying AI Request: {url} (Model: {self.model})")

        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                # 每次重试都创建新的 connector 和 session，确保连接干净
                connector = aiohttp.TCPConnector(force_close=True, enable_cleanup_closed=True)
                timeout = aiohttp.ClientTimeout(total=self.timeout, connect=30, sock_read=self.timeout)
                # trust_env=False 忽略系统代理，避免 localhost 连接问题
                async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
                    async with session.post(url, json=payload, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            return self._extract_message_content(data)
                        else:
                            error_text = await response.text()
                            print(f"AI Error {response.status}: {error_text[:200]}")
                            raise Exception(f"AI API returned {response.status}: {error_text[:100]}")
            except Exception as e:
                last_error = e
                print(f"AI Request Failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(3 + attempt * 2)  # 递增等待：3s, 5s
                    continue
                raise last_error


    async def chat_stream(self, messages: List[Dict], temperature: float = 0.7, max_tokens: int = 16000):
        """流式发送聊天请求"""
        if self._is_claude_cli():
            async for chunk in self._chat_stream_claude_cli(messages, temperature, max_tokens):
                yield chunk
            return

        if self._is_gemini_cli():
            async for chunk in self._chat_stream_gemini_cli(messages, temperature, max_tokens):
                yield chunk
            return
        if self._is_anthropic():
            async for chunk in self._chat_stream_anthropic(messages, temperature, max_tokens):
                yield chunk
            return
        if self.should_use_codex_auth():
            # Codex CLI 当前这里先走非流式结果，再一次性吐出；先保证能用。
            text = await self._chat_via_codex_exec(messages)
            if text:
                yield text
            return

        import aiohttp

        url = self._openai_compat_url("chat/completions")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/event-stream"
        }
        if self._has_real_api_key():
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }

        self._debug(f"Applying AI Stream Request: {url} (Model: {self.model})")
        # 调试：打印请求消息的长度和摘要
        try:
            msg_preview = json.dumps(messages, ensure_ascii=False)[:500]
            self._debug(f"Request Messages Preview: {msg_preview}...")
            self._debug(f"Total Messages count: {len(messages)}")
        except:
            pass

        max_retries = 3
        for attempt in range(max_retries):
            try:
                connector = aiohttp.TCPConnector(force_close=True, enable_cleanup_closed=True)
                timeout = aiohttp.ClientTimeout(total=self.timeout, connect=30, sock_read=300)
                # trust_env=False 忽略系统代理
                async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
                    async with session.post(url, json=payload, headers=headers) as response:
                        self._debug(f"AI Stream Response Status: {response.status}")
                        self._debug(f"AI Stream Content-Type: {response.headers.get('Content-Type', 'unknown')}")

                        if response.status != 200:
                            error_text = await response.text()
                            print(f"AI Stream Error: {error_text[:200]}")
                            yield f"[ERROR] AI API returned {response.status}: {error_text[:100]}"
                            return

                        content_type = response.headers.get('Content-Type', '')

                        # 如果返回的是 JSON 而不是 SSE，直接解析
                        if 'application/json' in content_type:
                            self._debug("AI returned JSON instead of SSE, parsing as single response")
                            raw_text = await response.text()
                            try:
                                data = json.loads(raw_text)
                                if "choices" in data and len(data["choices"]) > 0:
                                    content = self._extract_message_content(data)
                                    if content:
                                        yield content
                                        self._debug(f"AI JSON response length: {len(content)}")
                                        return
                            except json.JSONDecodeError as e:
                                print(f"Failed to parse JSON response: {e}")
                                yield f"[ERROR] Failed to parse AI response"
                                return

                        # SSE 流式处理 - 使用 buffer 累积并按行分割
                        chunk_count = 0
                        buffer = ""
                        self._debug("Starting SSE stream reading...")

                        async for chunk in response.content.iter_any():
                            raw_chunk = chunk.decode('utf-8', errors='ignore')
                            # 调试：打印原始数据块的前50个字符
                            self._debug(f"Raw chunk received ({len(raw_chunk)}): {raw_chunk[:100]}...")
                            buffer += raw_chunk

                            # 按行分割处理
                            while '\n' in buffer:
                                line, buffer = buffer.split('\n', 1)
                                line = line.strip()

                                if not line:
                                    continue
                                if line == "data: [DONE]":
                                    self._debug(f"AI Stream completed, total chunks: {chunk_count}")
                                    return

                                if line.startswith("data: "):
                                    try:
                                        data = json.loads(line[6:])
                                        if "choices" in data and len(data["choices"]) > 0:
                                            delta = data["choices"][0].get("delta", {})
                                            if "content" in delta:
                                                chunk_count += 1
                                                if chunk_count == 1:
                                                    self._debug("First SSE chunk received!")
                                                yield delta["content"]
                                    except json.JSONDecodeError:
                                        continue

                        # 某些网关可能在最后一个 data 行后不补换行；补一次 flush，避免丢失尾块导致“半句截断”。
                        if buffer.strip():
                            buffer += "\n"
                            while '\n' in buffer:
                                line, buffer = buffer.split('\n', 1)
                                line = line.strip()

                                if not line:
                                    continue
                                if line == "data: [DONE]":
                                    self._debug(f"AI Stream completed (tail flush), total chunks: {chunk_count}")
                                    return

                                if line.startswith("data: "):
                                    try:
                                        data = json.loads(line[6:])
                                        if "choices" in data and len(data["choices"]) > 0:
                                            delta = data["choices"][0].get("delta", {})
                                            if "content" in delta:
                                                chunk_count += 1
                                                if chunk_count == 1:
                                                    self._debug("First SSE chunk received (tail flush)!")
                                                yield delta["content"]
                                    except json.JSONDecodeError:
                                        continue

                        self._debug(f"AI Stream ended, total chunks: {chunk_count}")
                        return
            except Exception as e:
                print(f"AI Stream Request Failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(3 + attempt * 2)
                    continue
                yield f"[ERROR] {str(e)}"




    async def list_models(self) -> List[str]:
        """获取可用模型列表"""
        if self.should_use_codex_auth():
            preferred = [self._normalize_codex_model(self.model), *self._codex_available_models()]
            seen = set()
            return [m for m in preferred if m and not (m in seen or seen.add(m))]

        model_name = self._normalize_codex_model(self.model).lower()
        if not self._has_real_api_key() and self._is_openai_base_url() and "codex" in model_name:
            preferred = [self._normalize_codex_model(self.model), *self._codex_available_models()]
            seen = set()
            return [m for m in preferred if m and not (m in seen or seen.add(m))]

        if self._is_gemini_cli():
            return self._fallback_models()

        if self._is_claude_cli():
            return self._fallback_models()

        if self._is_anthropic():
            if not self._has_real_api_key():
                return self._fallback_models()
            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=True, http2=False, trust_env=False) as client:
                    response = await client.get("https://api.anthropic.com/v1/models", headers=self._anthropic_headers())
                    if response.status_code == 200:
                        data = response.json()
                        models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
                        if models:
                            return self._dedupe_models([self.model, *models])
                    print(f"Error fetching Anthropic models: {response.status_code} - {response.text}")
            except Exception as e:
                print(f"Error fetching Anthropic models: {e}")
            return self._fallback_models()

        if not self._has_real_api_key():
            return self._fallback_models()

        url = self._openai_compat_url("models")

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        if self._has_real_api_key():
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            # trust_env=False 防止受到系统代理影响 (解决 localhost 502 问题)
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, http2=False, trust_env=False) as client:
                response = await client.get(url, headers=headers)
                if response.status_code != 200:
                    print(f"Error fetching models: {response.status_code} - {response.text}")
                    return self._fallback_models()

                data = response.json()
                if "data" in data and isinstance(data["data"], list):
                    models = [m.get("id") for m in data["data"] if isinstance(m, dict) and m.get("id")]
                    return self._dedupe_models([self.model, *models]) if models else self._fallback_models()
                return self._fallback_models()
        except Exception as e:
            print(f"Error fetching models from {url}: {e}")
            return self._fallback_models()

    async def generate_outline(self, genre: str, premise: str, volumes: int = 1) -> str:
        """生成大纲"""
        system_prompt = """你是一位专业的网文大纲策划师。请根据用户提供的题材和设定，生成详细的小说大纲。

大纲格式要求：
1. 使用 Markdown 格式
2. 包含故事概要、主要角色、卷纲规划
3. 每卷包含 20-30 章的详细规划
4. 每章包含：标题、目标、爽点设计、Strand类型(Quest/Fire/Constellation)"""

        user_prompt = f"""题材：{genre}
核心设定：{premise}
规划卷数：{volumes} 卷

请生成完整的小说大纲。"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        return await self.chat(messages, temperature=0.8, max_tokens=8000)

    async def write_chapter(
        self,
        chapter_num: int,
        chapter_outline: str,
        previous_summary: str = "",
        characters: List[str] = None,
        settings: str = "",
        word_count: int = 4000
    ) -> str:
        """AI 写作章节"""
        system_prompt = f"""你是一位专业的网文作者。请根据大纲和上下文，创作精彩的章节内容。

写作要求：
1. 字数：{word_count} 字左右
2. 风格：节奏紧凑、对话生动、描写细腻
3. 结构：开头引人入胜、中间层层推进、结尾留有悬念
4. 遵循大纲设定，不要擅自发挥
5. 保持与前文的一致性"""

        context = f"""## 第 {chapter_num} 章大纲
{chapter_outline}

## 前情摘要
{previous_summary or '（这是第一章，无前情）'}

## 出场角色
{', '.join(characters) if characters else '（根据大纲安排）'}

## 世界观设定
{settings or '（使用默认设定）'}

请开始创作第 {chapter_num} 章正文："""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context}
        ]

        return await self.chat(messages, temperature=0.7, max_tokens=8000)

    async def review_chapter(self, content: str, previous_context: str = "", chapter_outline: str = "") -> Dict[str, Any]:
        """审查章节质量（含上下文重复性检查与大纲一致性检查）"""
        system_prompt = """你是一位“找茬”专家的网文主编，专门负责揪出正文与大纲不符的错误。你的审核标准极度严苛，绝不容忍任何设定偏差。

        【审核核心任务】
        请对比【本章大纲】与【正文内容】，重点检查以下几点：
        1. **专有名词一致性**：大纲里的宗门、地名、人名、物品名，正文必须完全一致。
           - 例子：大纲写“落云宗”，正文写“流云宗” -> ❌ 严重错误！
           - 例子：大纲写“万剑窟”，正文写“矿山” -> ❌ 严重错误！
        2. **核心剧情一致性**：大纲规定的核心冲突起因、经过、结果，正文是否篡改？

        【评分规则】
        1. **Consistency (一致性)**：初始 100 分。发现 1 个名词错误扣 20 分；发现 1 个剧情篡改扣 30 分。必须严格！
        2. **High Point (爽点)**：评估剧情是否能调动情绪（期待感、震惊、满足感）。无爽点=0-40，平淡=41-60，有亮点=61-80，极爽=81-100。
        3. **Pacing (节奏)**：评估剧情推进速度。太拖沓或太赶=0-50，正常=60-80，起伏有致=81-100。
        4. **OOC (人设)**：人物行为是否符合性格。严重崩坏=0-50，符合=60-100。
        5. **Continuity (连贯性)**：场景切换是否自然。

        【返回格式（JSON）】
        {
          "comparison_log": {
             "outline_entities": ["大纲名词1", "大纲名词2"],
             "content_entities": ["正文名词1", "正文名词2"],
             "mismatch_found": true/false
          },
          "scores": {
              "high_point": 85,
              "consistency": 95,
              "pacing": 70,
              "ooc": 90,
              "continuity": 88
          },
          "issues": [
             "❌ 宗门名称错误：大纲为[落云宗]，正文写成了[流云宗]",
             "⚠️ 节奏建议：开篇说明文略多"
          ],
          "suggestions": ["建议1", "建议2"],
          "summary": "简短评价"
        }"""

        user_content = f"请审查以下章节：\n\n{content}"

        context_info = []
        if chapter_outline:
            context_info.append(f"【本章大纲（用于检查剧情一致性）】\n{chapter_outline}")
        if previous_context:
            context_info.append(f"【上一章结尾（用于检查衔接与重复）】\n{previous_context}\n\n【特别注意】请仔细检查新章节开头是否机械复述了上一章结尾。")

        if context_info:
            user_content = "\n\n".join(context_info) + "\n\n" + user_content

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        result = await self.chat(messages, temperature=0.3, max_tokens=2000)

        # 尝试解析 JSON
        try:
            # 提取 JSON 部分
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0]
            elif "```" in result:
                result = result.split("```")[1].split("```")[0]
            return json.loads(result.strip())
        except Exception:
            return {
                "scores": {"high_point": 0, "consistency": 0, "pacing": 0, "ooc": 0, "continuity": 0},
                "issues": ["无法解析审查结果"],
                "suggestions": [],
                "summary": result
            }

    async def generate_titles(self, genre: str, outline: str) -> List[str]:
        """根据大纲生成书名"""
        system_prompt = f"""你是一位深谙网文市场规律的金牌主编，擅长打造爆款书名。
请根据大纲，为这部{genre}小说起 8 个极具吸引力、充满噱头和点击欲望的书名。

【起名核心法则】
1. **突出核心卖点**：必须一秒展示金手指、极致反差或核心爽点。
2. **拒绝平庸**：不要“xx传”、“xx记”这种古早文名，要“开局...”、“我...”、夸张对比。
3. **情绪调动**：利用震惊、好奇、贪婪（金手指）、优越感（无敌）等情绪。
4. **流派定制**：
   - 修仙/玄幻：要霸气宏大或极致苟道/稳健（如《我有一剑...》《开局签到...》）。
   - 都市/系统：要直白爽快，突出身份反差或系统功能（如《让你...没让你...》《校花...》）。
   - 悬疑/惊悚：要细思恐极或规则怪谈感。

【输出要求】
1. 只输出书名，每行一个。
2. 不要有任何解释或序号。
3. 必须生成 8 个。
4. **字数限制**：严格控制在 15 个字以内，短小精悍。"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"【小说大纲】\n{outline[:3000]}"}
        ]

        content = await self.chat(messages, temperature=0.8, max_tokens=200)

        # 清洗数据
        titles = []
        for line in content.split("\n"):
            line = line.strip()
            # 去除序号 (1. xxx)
            line = re.sub(r"^\d+[\.、\s]*", "", line)
            # 去除书名号
            line = line.replace("《", "").replace("》", "")
            if line:
                titles.append(line)

        return titles[:8]

    async def polish_chapter(self, content: str, suggestions: List[str] = None) -> str:
        """AI 润色章节"""
        system_prompt = """你是一位专业的网文主编。请根据改进建议，对用户提供的章节内容进行润色和重写。

要求：
1. **严格遵循改进建议**：针对性地解决提出的问题（如节奏、爽点、人设等）。
2. **提升文笔**：优化描写，增强代入感，使用 show-don't-tell 手法。
3. **保持原意**：不要随意更改核心剧情走向，除非建议中明确要求。
4. **输出完整正文**：直接输出润色后的内容，不要包含“好的”、“这是润色后的版本”等废话。"""

        user_prompt = f"""请润色以下章节内容：

【改进建议】
{chr(10).join([f"- {s}" for s in suggestions]) if suggestions else "- 请全面提升文笔，增强画面感和代入感。"}

【原文】
{content}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        return await self.chat(messages, temperature=0.7, max_tokens=8000)

    async def generate_chapter_summary(self, content: str) -> str:
        """生成章节摘要"""
        messages = [
            {"role": "system", "content": "请用 100-200 字概括以下章节的主要情节，用于后续章节的上下文参考。"},
            {"role": "user", "content": content}
        ]
        return await self.chat(messages, temperature=0.3, max_tokens=500)



CONFIG_FILE = GLOBAL_CONFIG_DIR / "ai_config.json"

def _load_config_from_file() -> Dict[str, str]:
    """从文件加载 AI 配置"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Warning: Failed to load AI config: {e}")
    return {}

def _save_config_to_file(config: Dict[str, str]):
    """保存 AI 配置到文件"""
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"Error saving AI config: {e}")

# 全局实例
_ai_service: Optional[AIService] = None


def get_ai_service() -> AIService:
    global _ai_service
    if _ai_service is None:
        # 1. 尝试从文件加载
        file_config = _load_config_from_file()

        # 2. 环境变量兜底；如果文件里明确写了空 api_key，保留空值，不再回退到占位默认值
        base_url = file_config["base_url"] if "base_url" in file_config else os.getenv("AI_BASE_URL", DEFAULT_AI_BASE_URL)
        api_key = file_config["api_key"] if "api_key" in file_config else os.getenv("AI_API_KEY", DEFAULT_AI_API_KEY)
        model = file_config["model"] if "model" in file_config else os.getenv("AI_MODEL", DEFAULT_AI_MODEL)

        _ai_service = AIService(base_url, api_key, model)
    return _ai_service


def configure_ai_service(base_url: str = None, api_key: str = None, model: str = None):
    global _ai_service

    # 保存到文件
    config_data = {
        "base_url": base_url or DEFAULT_AI_BASE_URL,
        "api_key": api_key or "",
        "model": model or DEFAULT_AI_MODEL
    }
    _save_config_to_file(config_data)

    # 更新内存实例
    _ai_service = AIService(base_url, api_key, model)
