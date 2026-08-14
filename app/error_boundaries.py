"""Explicit failures that request/provider boundaries may recover from."""

from __future__ import annotations

import smtplib
import sqlite3
import subprocess
import zipfile

import aiohttp
import pymysql
import requests
from argon2.exceptions import Argon2Error
from google.auth.exceptions import GoogleAuthError


RECOVERABLE_INTEGRATION_ERRORS: tuple[type[Exception], ...] = (
    Argon2Error,
    EOFError,
    GoogleAuthError,
    LookupError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    aiohttp.ClientError,
    pymysql.MySQLError,
    requests.RequestException,
    smtplib.SMTPException,
    sqlite3.Error,
    subprocess.SubprocessError,
    zipfile.BadZipFile,
)
