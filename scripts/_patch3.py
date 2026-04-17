"""Patch 3: add _check_stage_consistency validator to validate_canonical_report."""
import py_compile
import pathlib

TARGET = pathlib.Path(
    r"c:\Users\LENOVO\Desktop\AISEP_AI\src\modules\evaluation\application\services\report_validity.py"
)

STAGE_VALIDATOR = '''

def _check_stage_consistency(
    canonical: Mapping[str, Any], narrative_texts: list[str]
) -> list[str]:
    """Bug 5 validator: flag any narrative text that uses lower-stage language
    after sanitize_canonical_report should have removed it."""
    flags: list[str] = []
    stage = ((canonical.get("classification") or {}).get("stage") or {}).get("value") or ""
    stage = stage.upper().strip()
    pattern = _LOWER_STAGE_LANGUAGE.get(stage)
    if pattern is None:
        return flags

    offending = [t for t in narrative_texts if pattern.search(t)]
    if offending:
        flags.append(
            f"STAGE_NARRATIVE_CONTRADICTION: classified stage={stage} but "
            f"{len(offending)} narrative field(s) use lower-stage language. "
            f"Example: \\"{offending[0][:120]}\\""
        )
    return flags
'''

content = TARGET.read_text(encoding="utf-8")

# Insert the new function just before the public entry points section
INSERT_BEFORE = "def validate_canonical_report"
idx = content.find(INSERT_BEFORE)
assert idx > 0, "Could not find validate_canonical_report"

content = content[:idx] + STAGE_VALIDATOR + "\n" + content[idx:]

# Also add _check_stage_consistency call inside validate_canonical_report
OLD_FLAGS = '    flags.extend(_check_criterion_kq_consistency(canonical))\n\n    return ReportValidity(is_valid=True'
NEW_FLAGS = (
    '    flags.extend(_check_criterion_kq_consistency(canonical))\n'
    '    flags.extend(_check_stage_consistency(canonical, narrative_texts))\n\n'
    '    return ReportValidity(is_valid=True'
)
content = content.replace(OLD_FLAGS, NEW_FLAGS, 1)

TARGET.write_text(content, encoding="utf-8")
print("Patch 3 applied.")

try:
    py_compile.compile(str(TARGET), doraise=True)
    print("Syntax OK.")
except py_compile.PyCompileError as e:
    print(f"Syntax ERROR: {e}")
