from src.modules.evaluation.application.services.processing_warning_sanitizer import (
    sanitize_processing_warnings,
)


def test_sanitize_processing_warnings_preserves_utf8_text():
    result = sanitize_processing_warnings([
        "\u0110\u00e3 \u00e1p d\u1ee5ng l\u1edbp \u0111\u00e1nh gi\u00e1 subindustry: FINTECH (\u0111\u1ed9 tin c\u1eady High).",
        "Unknown stage 'SERIES_A' \u2014 using MVP weight profile as fallback.",
    ])

    assert result == [
        "\u0110\u00e3 \u00e1p d\u1ee5ng l\u1edbp \u0111\u00e1nh gi\u00e1 subindustry: FINTECH (\u0111\u1ed9 tin c\u1eady High).",
        "Unknown stage 'SERIES_A' - using MVP weight profile as fallback.",
    ]


def test_sanitize_processing_warnings_repairs_common_mojibake():
    mojibake = (
        "\u0110\u00e3 \u00e1p d\u1ee5ng l\u1edbp \u0111\u00e1nh gi\u00e1 subindustry."
        .encode("utf-8")
        .decode("latin-1")
    )

    result = sanitize_processing_warnings([mojibake])

    assert result == ["\u0110\u00e3 \u00e1p d\u1ee5ng l\u1edbp \u0111\u00e1nh gi\u00e1 subindustry."]


def test_sanitize_processing_warnings_unescapes_quotes_and_keeps_vietnamese():
    result = sanitize_processing_warnings([
        'AUTO_REMOVED_REC: removed a\\"B\u1ed5 sung ph\u00e2n t\u00edch \u0111\u1ed1i th\u1ee7 v\u00e0 t\u1ed1c \u0111\u1ed9 t\u0103ng tr\u01b0\u1edfng\\" item.'
    ])

    assert result == [
        'AUTO_REMOVED_REC: removed a"B\u1ed5 sung ph\u00e2n t\u00edch \u0111\u1ed1i th\u1ee7 v\u00e0 t\u1ed1c \u0111\u1ed9 t\u0103ng tr\u01b0\u1edfng" item.'
    ]


def test_sanitize_processing_warnings_deduplicates_after_repair():
    mojibake = (
        "\u0110\u1ed1i th\u1ee7 c\u1ea1nh tranh c\u1ea7n b\u1ed5 sung."
        .encode("utf-8")
        .decode("latin-1")
    )

    result = sanitize_processing_warnings([
        mojibake,
        "\u0110\u1ed1i th\u1ee7 c\u1ea1nh tranh c\u1ea7n b\u1ed5 sung.",
    ])

    assert result == ["\u0110\u1ed1i th\u1ee7 c\u1ea1nh tranh c\u1ea7n b\u1ed5 sung."]
