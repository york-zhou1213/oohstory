from oohstory_library.services.cover_failure_policy import (
    _PLACEHOLDER_VISUAL_TEMPLATES,
    is_missing_cover_placeholder_image,
    is_missing_cover_placeholder_sha256,
    missing_cover_placeholder_family,
    should_generate_ai_fallback,
)
import base64
import zlib


def test_requested_missing_cover_placeholder_is_redraw_eligible() -> None:
    digests = {
        "03d6b67ddf645e1f42f70c83f07313460a8800d8ba74d8196e95fbba14977ada",
        "4bb5bea1b420d1bdbfbccfbcc0cadef3933e4dd161dc2d783e65ef0fb5f1a53f",
        "092621ae366846e36b3c12f5db65bb5e4d5ce522229b370ff5be0655f0e6a403",
        "2051de329325efa8784e6a6e72d389da8544a113bc02e79738c6280dc19551b0",
        "a2bd540cd06fc45028f52dae1b1957a9fdcf4af5565e3cd652c657bedfce9e32",
        "a6447d543022245a323fddba0219b3553f5ff3c35f4174f3872024b3908e2880",
        "d421cee15a266d258979455101443085bbc686504ee802c55686cbfc92d0b09e",
        "e8ad15fe92c222b1eceac303d0d202a9935a691a383556b61bf334217a8f00fe",
        "2de58d24bb692d91ea50ab1649e17e07e59f75268f7016072165348357acb0ac",
        "2cebc3f5d9eae7022cdbd6bb3ef4b4c9aff563f9403a778be68f7456fd0e1485",
        "d38bec1556647fa57be94cc13e32d90b37fc9f0e6e09b176a7391e4992c63370",
        "b1fded5f3bd46d7cbf554b64492718ef3464add66a4f85c9331900e7f0425ca0",
        "0bbd39b1e59d8110e6e278ed761d6598f6d8a5fb69dcfe60c29a6e0efb227358",
        "d95b47251a1b4d0a0676a05e73e05dbd50f8d435dfe982373f78e39eae1fa587",
        "6a2b00cc756f3c8d8c5290e96294cb1197d07c00a780c91510af435c9dec3a4d",
        "092726cc9d70603697fecb5692dd57e1e24f06ee88e2b7ed30eb05300df75357",
        "3f511aebbf0ffaa8e04a6c7b23314eafe46de9863bfa99d40fa41062142d4f80",
        "319bd80fcace57e7ceef4d6c0dc8a5bca359cf5e0131a78caae819c889092c51",
        "2d70b0c8073660910c147fa47f04cdc6d2da94b9db16fa1741c29ae840f31d34",
        "773f938ff43a518a7e32be2e3dfe4056646e1fe5d70f33278b1b68a4766ba6fd",
    }

    assert all(
        is_missing_cover_placeholder_sha256(digest.upper())
        for digest in digests
    )
    assert not is_missing_cover_placeholder_sha256("0" * 64)
    assert should_generate_ai_fallback(
        "原站返回暂无封面占位图，没有真实封面",
        attempts=1,
    )


def _ppm(rgb: bytes) -> bytes:
    return b"P6\n12 16\n255\n" + rgb


def test_visual_placeholder_templates_cover_title_specific_variants() -> None:
    for family, (ignore_title_band, encoded) in _PLACEHOLDER_VISUAL_TEMPLATES.items():
        pixels = bytearray(zlib.decompress(base64.b64decode(encoded)))
        if ignore_title_band:
            for row in range(8, 14):
                start = row * 12 * 3
                pixels[start:start + 12 * 3] = bytes([255, 0, 255]) * 12
        data = _ppm(bytes(pixels))
        assert missing_cover_placeholder_family(data) == family
        assert is_missing_cover_placeholder_image(data)


def test_visual_placeholder_classifier_rejects_unrelated_cover() -> None:
    assert not is_missing_cover_placeholder_image(_ppm(bytes(12 * 16 * 3)))


def test_exact_placeholder_does_not_require_image_decode(monkeypatch) -> None:
    monkeypatch.setattr(
        "oohstory_library.services.cover_failure_policy._normalise_cover_pixels",
        lambda _data: (_ for _ in ()).throw(AssertionError("should not decode")),
    )
    assert is_missing_cover_placeholder_image(
        b"not-an-image",
        sha256="092726cc9d70603697fecb5692dd57e1e24f06ee88e2b7ed30eb05300df75357",
    )
