"""Patch 5: fix test_19 to use AUTO_REMOVED_CONCERN, fix RawJudgment reasoning in new tests."""
import py_compile
import pathlib
import re

TARGET = pathlib.Path(
    r"c:\Users\LENOVO\Desktop\AISEP_AI\src\tests\unit\test_phase_c_validation.py"
)

content = TARGET.read_text(encoding="utf-8")

# Fix 1: test_19 expected CONCERN_SCORE_CONFLICT but we now use AUTO_REMOVED_CONCERN
content = content.replace(
    'assert any("CONCERN_SCORE_CONFLICT" in w for w in result["processing_warnings"])',
    'assert any("AUTO_REMOVED_CONCERN" in w for w in result["processing_warnings"])',
)

# Fix 2: RawJudgment in _make_mock_clf_with_wrong_stage needs reasoning=""
content = content.replace(
    'RawJudgment(criterion=c, raw_score=7.0, criterion_confidence="High",\n'
    '                            suggested_contradiction_severity="none") for c in cnames',
    'RawJudgment(criterion=c, raw_score=7.0, criterion_confidence="High",\n'
    '                            suggested_contradiction_severity="none", reasoning="ok") for c in cnames',
)

TARGET.write_text(content, encoding="utf-8")
print("Patch 5 applied.")

try:
    py_compile.compile(str(TARGET), doraise=True)
    print("Syntax OK.")
except py_compile.PyCompileError as e:
    print(f"Syntax ERROR: {e}")
