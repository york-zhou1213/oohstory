"""Classify real-source cover failures for the AI generation fallback."""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
import zlib


MAX_SOURCE_COVER_ATTEMPTS = 5

# Authorized sources use these exact images as generic/no-cover artwork.
# Treating any of them as a real cover hides missing artwork from the redraw
# queue.  Keep exact SHA-256 fingerprints here so genuine covers are never
# rejected merely because they look visually similar.
MISSING_COVER_PLACEHOLDER_SHA256S = frozenset(
    {
        "03d6b67ddf645e1f42f70c83f07313460a8800d8ba74d8196e95fbba14977ada",
        "4bb5bea1b420d1bdbfbccfbcc0cadef3933e4dd161dc2d783e65ef0fb5f1a53f",
        "092621ae366846e36b3c12f5db65bb5e4d5ce522229b370ff5be0655f0e6a403",
        "2051de329325efa8784e6a6e72d389da8544a113bc02e79738c6280dc19551b0",
        "a2bd540cd06fc45028f52dae1b1957a9fdcf4af5565e3cd652c657bedfce9e32",
        "a6447d543022245a323fddba0219b3553f5ff3c35f4174f3872024b3908e2880",
        # OOHStory-owned temporary cover. It is intentionally still treated
        # as missing artwork until a per-title generated cover replaces it.
        "d421cee15a266d258979455101443085bbc686504ee802c55686cbfc92d0b09e",
        "e8ad15fe92c222b1eceac303d0d202a9935a691a383556b61bf334217a8f00fe",
        # Additional ixdzs generic/no-cover artwork confirmed by the catalog
        # owner on 2026-08-04.  Match exact bytes only.
        "2de58d24bb692d91ea50ab1649e17e07e59f75268f7016072165348357acb0ac",
        "2cebc3f5d9eae7022cdbd6bb3ef4b4c9aff563f9403a778be68f7456fd0e1485",
        "d38bec1556647fa57be94cc13e32d90b37fc9f0e6e09b176a7391e4992c63370",
        "b1fded5f3bd46d7cbf554b64492718ef3464add66a4f85c9331900e7f0425ca0",
        "0bbd39b1e59d8110e6e278ed761d6598f6d8a5fb69dcfe60c29a6e0efb227358",
        "d95b47251a1b4d0a0676a05e73e05dbd50f8d435dfe982373f78e39eae1fa587",
        "6a2b00cc756f3c8d8c5290e96294cb1197d07c00a780c91510af435c9dec3a4d",
        # Additional placeholder families confirmed by the catalog owner on
        # 2026-08-04: 17K generic artwork, site-generated title templates,
        # Motie generic artwork, and Qidian/Chuangshi template covers.
        "092726cc9d70603697fecb5692dd57e1e24f06ee88e2b7ed30eb05300df75357",
        "3f511aebbf0ffaa8e04a6c7b23314eafe46de9863bfa99d40fa41062142d4f80",
        "319bd80fcace57e7ceef4d6c0dc8a5bca359cf5e0131a78caae819c889092c51",
        "2d70b0c8073660910c147fa47f04cdc6d2da94b9db16fa1741c29ae840f31d34",
        "773f938ff43a518a7e32be2e3dfe4056646e1fe5d70f33278b1b68a4766ba6fd",
    }
)


def is_missing_cover_placeholder_sha256(value: object) -> bool:
    """Return whether *value* identifies a known missing-cover placeholder."""

    return str(value or "").strip().casefold() in MISSING_COVER_PLACEHOLDER_SHA256S


_PLACEHOLDER_TEMPLATE_WIDTH = 12
_PLACEHOLDER_TEMPLATE_HEIGHT = 16
_PLACEHOLDER_MAE_THRESHOLD = 2.0
_CONVERT_BINARY = shutil.which("convert")

# Small, compressed RGB reference images.  Three source sites paint each book's
# title/author into the middle of a fixed placeholder, so their comparison mask
# deliberately excludes those changing rows.  This catches the template family
# instead of relying on a SHA that changes for every title.
_PLACEHOLDER_VISUAL_TEMPLATES = {
    "17k_generic": (
        False,
        "eNqNkd1PglAYxv9TL/0L3Lhr6zq32rrx0sqr1ocfQDbG0hEKpQSJdqEFFviByIE0k1HUI62ue8bePeec3/txDnG80UcimM9E8H/x6x9KasTefG69vOi6DnOvaaZh1K6urgWhVCyqqgoAZFtVbxTloFAoMczxyUmFZbe2t3f39zMUdVo8B/AZx8+ttilJU11/FkVbVb3Hx6dG46FWG+m6eXcXhSGmIpblmmZLEPiz89pFlaeZdr1+w/P+aBRMJuF6HUUR+mH6lqI0ZPmS42iWrQvCraJ8JfvE99fQ+0a4Nj6kbG4dRQBWqxXxvLdEy+USoO/7MNhHDMPQNM1+v79YLLATBCQM171er9vtYgkSwHA47HQ6hmEQQmzbfn31bdsqFI4kScISLXCUzWabzabnea7rAgsCHzVZlrEsC7lHh/lUKkVRFE3T0+k0QBtCYFC/VML7MZlMJruzs7e3m8/n8bzur8CAlGWZ4zh4x5kh5nI5/JqfU8dxMKooimBgNE3D8JgKp+Px+KfIbDarVquVSqVcLvOJ0uk0CuLiIAeDARKR9Q1lh+/0",
    ),
    "pink_title_template": (
        True,
        "eNpVUm1PwkAM/v//QWNQiZ+QRBSJH4HgZz8QQMLYcAMHbuN2b+1tI/Z2vOil6bW9565P2+MsFzmXXDg524JzZ3OWkwalrQgFnLZ6SRvRUqEGd8UC6CzlGGwh3EKUAMLxotIXDEmSF5PADHzdX+rhHCiuwWEoKUhl31/v0Fubz0S3Z+pxDEpqyn56h/KSjesfCHcQbcxggR+xTplOmKVWM4eaKcapGa9wusI4ASHB2wLjFnPhrIFL2GxxEeIqRj+G78wSOPOpy1RSaS5UmkHGVJjJjGv1j48BrMqyLApSxpjCGHLR1ZXbZh7KajadNRp33e5rs/nQ6bx0u72r65vAD6qiFGfMZNZqtQf94Wj03uu9dZ6eG7f38885HbmJuFyH6kA6p/FxQW0mF0+cXV2Ws5CkpRBkUMS5NR+LcdxQo4y8L9+Pwsj3lqeg7Y+oR3/8D4xlSbrP9izb//0tvy/KGLU=",
    ),
    "chuangshi_title_template": (
        True,
        "eNpdUWlPwkAU/P+fTUzUmBCCEZREAnLIUWo5SooUFApFKjel5263B8X4xdcU8Ejmw+y+efPm7eqmrZvkHzSAEfKgiiw3BCaeeeS24wNOpRAGsuWtQZydiV0TO+/TtSgt8R+NB54M2/uYb213D/pMqZmlWou1iknQhbBrkZ2sIpoTGh1xMJoK4qxc4wsMr+o4CBA4B0m2Kmrw4+5oQbO9LNWmuWFXXCjaQROmheMzJzDtUYXtc/3Z22TD9aWNYlr27uSjGVa13mnyIvs6AVm1NajUeTA/+YRWq406lpayileyAeSlOzSQ83t3yE+cPXF8i3jB47h7dGj3gMCmnv/1VKqenV+mHws38eRdMhWNJSLR20yuCF3wXADL9sfSPJXJ54tUiWJohgWeSucEUcLWIQ/IHO/zOhK7uIpEY/EyVQNx4v4hmBhUD3lApuoWbLeWteVaUTQEHC7/fRmsBkN1ZCtQJR4cj+0/4bHjr/rCtNHcSjNltkRHDeAb4Mv+DA==",
    ),
    "motie_generic": (
        False,
        "eNqNj8kSgjAQBf//D0UkYQ0BDFtAPNo1KSguon2g3sw0r2Dbtvclm/DTWdf1dQmC935ZllVYThwbhGma5nnuus45R/A7fd+3bTsLwzCM46iUiuM4TdO6rq21hIeAydUJWms2aGVZGmMIjEmShP5WCD0ITwEt9DRNg0Yz/byS5zkbKxDwcZAZeVZVxQbTnGCsBHJRFDTwkXonFULghJBlGePxkYRECL/ACUEJ+gvh+vgDCu+XIERRdLsE4QMNBQdq",
    ),
    "qidian_title_template": (
        True,
        "eNpNUttOwkAQ/f8fMFHxgQcflGiUaEpFS4GkgLWQ0gulQFstLfSyvWxpweiDA4sNycnmdHZ6zszshAiH/wjCFEUZIYAyHsWbEklauOsQ0jDepriASJzkpwl7JHl/pKqzr4npygvbcvxTkTjODdtTDZsbKsxApAdSrcm1ekM/TIgUnJ4fC/J8ajm8pIv653Pn/faJ0Q0bTMEaRZsUb3VjyfRGsm45XsTLC7rDS5qx8uMA4bIkIADT9l57Q6or3FHs9T3FixrRITlAkiQfTxa1epNmey/tvjDWFN0Ci9OmDsPZd7F0A+BrLwpQWrqQyeTFT7b5xtkOAL8DhwjEiQt8jiXt7Pyq0+03mc5IVFptju32LypVZTKH2/1Us91UNy8r1QbN3NQehJHMtrk3pvtYp+bQfnYsCcSL7S+c7ip03ACKIe6nb3EYVIYO5NhmWpRdk8kAEMJ6g5IEUVZnkjz9EMaQQ3SOW4H2y7AybWfpOauQOJbr8QdoAwFZ",
    ),
}


def _normalise_cover_pixels(data: bytes) -> bytes | None:
    if not data or not _CONVERT_BINARY:
        return None
    try:
        result = subprocess.run(
            [
                _CONVERT_BINARY,
                "-limit", "memory", "32MiB",
                "-limit", "map", "64MiB",
                "-limit", "disk", "0",
                "-",
                "-auto-orient",
                "-resize", "12x16^",
                "-gravity", "center",
                "-extent", "12x16",
                "-alpha", "off",
                "-colorspace", "sRGB",
                "-depth", "8",
                "rgb:-",
            ],
            input=data,
            capture_output=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    expected = _PLACEHOLDER_TEMPLATE_WIDTH * _PLACEHOLDER_TEMPLATE_HEIGHT * 3
    if result.returncode or len(result.stdout) != expected:
        return None
    return result.stdout


def missing_cover_placeholder_family(
    data: bytes,
    *,
    sha256: object = "",
) -> str | None:
    """Return the registered placeholder family for a downloaded cover."""

    digest = str(sha256 or "").strip().casefold()
    if not digest and data:
        digest = hashlib.sha256(data).hexdigest()
    if is_missing_cover_placeholder_sha256(digest):
        return "registered_sha256"
    pixels = _normalise_cover_pixels(data)
    if pixels is None:
        return None
    for family, (ignore_title_band, encoded) in _PLACEHOLDER_VISUAL_TEMPLATES.items():
        reference = zlib.decompress(base64.b64decode(encoded))
        total = count = 0
        for pixel_index in range(_PLACEHOLDER_TEMPLATE_WIDTH * _PLACEHOLDER_TEMPLATE_HEIGHT):
            row = pixel_index // _PLACEHOLDER_TEMPLATE_WIDTH
            if ignore_title_band and 8 <= row < 14:
                continue
            offset = pixel_index * 3
            total += sum(
                abs(pixels[offset + channel] - reference[offset + channel])
                for channel in range(3)
            )
            count += 3
        if count and total / count <= _PLACEHOLDER_MAE_THRESHOLD:
            return family
    return None


def is_missing_cover_placeholder_image(
    data: bytes,
    *,
    sha256: object = "",
) -> bool:
    """Match exact fingerprints and visually stable placeholder templates."""

    return missing_cover_placeholder_family(data, sha256=sha256) is not None

# These errors describe a deterministic source/identity/image rejection.  A
# second request cannot make the candidate safe, so the title-based AI cover
# may be queued immediately instead of repeatedly probing a known-bad image.
PERMANENT_SOURCE_FAILURE_MARKERS = (
    "没有真实封面",
    "没有可用封面",
    "无可安全使用",
    "空图片",
    "图片为空",
    "源站失效",
    "作品 id 与目录",
    "source_id 不一致",
    "书名不一致",
    "作者不一致",
    "身份不匹配",
    "校验不通过",
    "校验失败",
    "无法机械验真",
    "不同作品",
    "不在当前授权来源",
    "不在可信",
    "不是支持的图片格式",
    "不是可识别图片",
    "封面文件大小异常",
    "仍含下载站水印",
    "官方搜索没有找到",
    "404",
    "410 gone",
    "not found",
)

def should_generate_ai_fallback(
    error: str,
    *,
    attempts: int,
    max_attempts: int = MAX_SOURCE_COVER_ATTEMPTS,
) -> bool:
    """Return true only for an explicit, deterministic source rejection.

    Attempt exhaustion is deliberately not an AI authorization signal.  A
    timeout, rate limit, Cloudflare challenge, HTTP 5xx, parser regression, or
    database/queue failure remains unresolved no matter how many times it has
    been retried.  Those jobs must stay retryable or await manual diagnosis.
    """

    message = str(error or "").strip().casefold()
    if not message:
        return False
    # Keep the public call signature because queue callers still record retry
    # budgets, but never use those counters to infer that a source is absent.
    _ = attempts, max_attempts
    return any(marker in message for marker in PERMANENT_SOURCE_FAILURE_MARKERS)
