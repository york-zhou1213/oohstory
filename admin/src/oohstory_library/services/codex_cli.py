from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEX_VENDOR_DIR = PROJECT_ROOT / '.tools' / 'codex-cli'
CODEX_BIN = CODEX_VENDOR_DIR / 'node_modules' / '.bin' / 'codex'
CODEX_PACKAGE = '@openai/codex'
CODEX_HOME_DIR = Path.home() / '.codex'


def codex_env() -> Dict[str, str]:
    return {**os.environ, 'CODEX_HOME': str(CODEX_HOME_DIR)}


def get_codex_command() -> str:
    if CODEX_BIN.exists():
        return str(CODEX_BIN)
    system_codex = shutil.which('codex')
    if system_codex:
        return system_codex
    return str(CODEX_BIN)


def is_codex_available() -> bool:
    cmd = get_codex_command()
    return Path(cmd).exists() or bool(shutil.which(cmd))


def ensure_codex_cli(auto_install: bool = True) -> str:
    if CODEX_BIN.exists():
        return str(CODEX_BIN)

    if not auto_install:
        system_codex = shutil.which('codex')
        if system_codex:
            return system_codex
        raise FileNotFoundError('Codex CLI 未安装')

    npm = shutil.which('npm')
    node = shutil.which('node')
    if not npm or not node:
        raise RuntimeError('当前环境缺少 node/npm，无法自动安装 Codex CLI')

    CODEX_VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [npm, 'install', CODEX_PACKAGE, '--prefix', str(CODEX_VENDOR_DIR)],
        check=True,
        cwd=str(PROJECT_ROOT),
        env={**os.environ, 'npm_config_update_notifier': 'false', 'npm_config_fund': 'false'},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if CODEX_BIN.exists():
        return str(CODEX_BIN)

    system_codex = shutil.which('codex')
    if system_codex:
        return system_codex
    raise RuntimeError('Codex CLI 安装后仍未找到可执行文件')


def run_codex(args: List[str], **kwargs):
    cmd = [ensure_codex_cli(auto_install=True), *args]
    kwargs.setdefault('env', codex_env())
    return subprocess.run(cmd, **kwargs)


def codex_doctor() -> Dict[str, Any]:
    node = shutil.which('node')
    npm = shutil.which('npm')
    system_codex = shutil.which('codex')
    project_codex_exists = CODEX_BIN.exists()
    selected_cmd = get_codex_command()
    using_project_cli = project_codex_exists and Path(selected_cmd) == CODEX_BIN

    result: Dict[str, Any] = {
        'project_root': str(PROJECT_ROOT),
        'node_available': bool(node),
        'npm_available': bool(npm),
        'node_path': node or '',
        'npm_path': npm or '',
        'project_codex_exists': project_codex_exists,
        'project_codex_path': str(CODEX_BIN),
        'system_codex_exists': bool(system_codex),
        'system_codex_path': system_codex or '',
        'selected_codex_path': selected_cmd,
        'using_project_cli': using_project_cli,
        'installed': False,
        'logged_in': False,
        'status_text': '',
        'auto_install_attempted': False,
        'auto_install_success': False,
        'error': '',
    }

    try:
        if not project_codex_exists:
            result['auto_install_attempted'] = True
            selected_cmd = ensure_codex_cli(auto_install=True)
            result['auto_install_success'] = True
            result['selected_codex_path'] = selected_cmd
            result['project_codex_exists'] = CODEX_BIN.exists()
            result['using_project_cli'] = CODEX_BIN.exists() and Path(selected_cmd) == CODEX_BIN

        cmd = result['selected_codex_path']
        if cmd and (Path(cmd).exists() or shutil.which(cmd)):
            result['installed'] = True
            proc = subprocess.run([cmd, 'login', 'status'], capture_output=True, text=True, timeout=10, env=codex_env())
            status_text = ((proc.stdout or '') + (proc.stderr or '')).strip()
            result['status_text'] = status_text
            lowered = status_text.lower()
            result['logged_in'] = ('not logged in' not in lowered) and ('logged in' in lowered)
    except RECOVERABLE_OPERATION_ERRORS as e:
        result['error'] = str(e)

    return result
