"""Concrete failures that service/provider boundaries may recover from.

The tuple intentionally excludes programming errors such as ``AttributeError``,
``AssertionError`` and ``MemoryError``. Those must propagate instead of being
silently converted into an empty result or a retry.
"""

from __future__ import annotations

import sqlite3
import subprocess
import tarfile
import zipfile

import aiohttp
import httpx
import pymysql
import redis
import requests


RECOVERABLE_OPERATION_ERRORS: tuple[type[Exception], ...] = (
    EOFError,
    LookupError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    aiohttp.ClientError,
    httpx.HTTPError,
    pymysql.MySQLError,
    redis.RedisError,
    requests.RequestException,
    sqlite3.Error,
    subprocess.SubprocessError,
    tarfile.TarError,
    zipfile.BadZipFile,
)
