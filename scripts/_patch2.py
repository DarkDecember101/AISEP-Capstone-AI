"""Patch: replace flag-only concern block with removal block + add stage contradiction functions."""
import sys
import py_compile
import pathlib
import re

TARGET = pathlib.Path(
    r"c:\Users\LENOVO\Desktop\AISEP_AI\src\modules\evaluation\application\services\report_validity.py"
)

content = TARGET.read_text(encoding="utf-8")

# Find the line numbers of key boundaries
lines = content.splitlines(keepends=True)

# Find "Flag contradictory top_concerns" line index
flag_line = next(i for i, ln in enumerate(
    lines) if "Flag contradictory top_concerns" in ln)
# Find the "return canonical, corrections" that closes _filter_contradictory_recommendations
# (there should be exactly one, just before "def sanitize_canonical_report")
sanitize_def_line = next(i for i, ln in enumerate(
    lines) if "def sanitize_canonical_report" in ln)
# The last "return canonical, corrections" before sanitize_def_line
ret_line = max(
    i for i, ln in enumerate(lines[:sanitize_def_line])
    if ln.strip() == "return canonical, corrections"
)

print(
    f"flag_line={flag_line+1}, ret_line={ret_line+1}, sanitize_def_line={sanitize_def_line+1}")

# Build the replacement block (replaces lines flag_line..ret_line inclusive)
replacement = '''\
    # ── Remove contradictory top_concerns (Bug 4 fix) ──────────────────────────
    concerns: list = list(narrative.get("top_concerns") or [])
    filtered_concerns: list = []
    for concern in concerns:
        if not isinstance(concern, str):
            filtered_concerns.append(concern)
            continue
        removed_concern = False
        if _WEAK_EVIDENCE_LANGUAGE.search(concern):
            for crit_name, crit_data in criteria_by_name.items():
                if not _text_mentions_criterion(concern, crit_name):
                    continue
                score = crit_data.get("final_score")
                ev_strength = crit_data.get("evidence_strength_summary") or ""
                if (
                    isinstance(score, (int, float))
                    and score >= _HIGH_SCORE_THRESHOLD
                    and ev_strength in _STRONG_EVIDENCE_LEVELS
                ):
                    corrections.append(
                        "AUTO_REMOVED_CONCERN: top_concern removed \u2014 references "
                        f"'{crit_name}' as weak/limited but criterion scored {score:.0f} "
                        f"with {ev_strength}. Concern: \\"{concern[:100]}\\""
                    )
                    removed_concern = True
                    break
        if not removed_concern:
            filtered_concerns.append(concern)

    if len(filtered_concerns) < len(concerns):
        narrative["top_concerns"] = filtered_concerns

    if corrections:
        canonical["narrative"] = narrative
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws

    return canonical, corrections


def _correct_stage_narrative_contradictions(canonical: dict) -> "tuple[dict, list[str]]":
    """
    Bug 5 fix: remove top_concerns and recommendations that use language
    appropriate for a LOWER stage than what is classified.
    """
    corrections: list[str] = []
    stage = ((canonical.get("classification") or {}).get("stage") or {}).get("value") or ""
    stage = stage.upper().strip()
    pattern = _LOWER_STAGE_LANGUAGE.get(stage)
    if pattern is None:
        return canonical, corrections

    narrative = canonical.get("narrative")
    if not isinstance(narrative, dict):
        return canonical, corrections

    concerns: list = list(narrative.get("top_concerns") or [])
    filtered_concerns: list = []
    for concern in concerns:
        if isinstance(concern, str) and pattern.search(concern):
            corrections.append(
                f"AUTO_REMOVED_CONCERN: stage contradiction \u2014 uses sub-{stage} "
                f"language for classified stage={stage}. Concern: \\"{concern[:120]}\\""
            )
        else:
            filtered_concerns.append(concern)

    recs: list = list(narrative.get("recommendations") or [])
    filtered_recs: list = []
    for rec in recs:
        if not isinstance(rec, dict):
            filtered_recs.append(rec)
            continue
        rec_text = " ".join(str(v) for v in rec.values() if isinstance(v, str))
        if pattern.search(rec_text):
            corrections.append(
                f"AUTO_REMOVED_REC: stage contradiction \u2014 uses sub-{stage} "
                f"language for classified stage={stage}. "
                f"Text: \\"{rec.get('recommendation', '')[:100]}\\""
            )
        else:
            filtered_recs.append(rec)

    if corrections:
        narrative["top_concerns"] = filtered_concerns
        narrative["recommendations"] = filtered_recs
        canonical["narrative"] = narrative
        pws = list(canonical.get("processing_warnings") or [])
        pws.extend(corrections)
        canonical["processing_warnings"] = pws

    return canonical, corrections

'''

# Replace lines flag_line to ret_line inclusive with replacement
new_lines = lines[:flag_line] + [replacement] + lines[ret_line + 1:]
new_content = "".join(new_lines)

TARGET.write_text(new_content, encoding="utf-8")
print("Patch applied.")

# Quick syntax check
try:
    py_compile.compile(str(TARGET), doraise=True)
    print("Syntax OK.")
except py_compile.PyCompileError as e:
    print(f"Syntax ERROR: {e}")
    sys.exit(1)
