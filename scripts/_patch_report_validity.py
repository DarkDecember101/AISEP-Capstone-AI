"""One-shot patch script for report_validity.py — run once then delete."""
import re
import pathlib

TARGET = pathlib.Path(
    r"c:\Users\LENOVO\Desktop\AISEP_AI\src\modules\evaluation\application\services\report_validity.py"
)

content = TARGET.read_text(encoding="utf-8")

# ── Replacement 1: convert "Flag contradictory top_concerns" block into
#    a proper REMOVE pass (Bug 4 fix). ─────────────────────────────────────────

OLD_CONCERN_BLOCK = re.compile(
    r"    # .{0,5}Flag contradictory top_concerns .{0,60}\n"
    r"    concerns: list = list\(narrative\.get\(\"top_concerns\"\) or \[\]\)\n"
    r"    for concern in concerns:.*?"
    r"    return canonical, corrections\n",
    re.DOTALL,
)

NEW_CONCERN_BLOCK = '''\
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
                        f"AUTO_REMOVED_CONCERN: top_concern removed \\u2014 references \\'{crit_name}\\' "
                        f"as weak/limited but criterion scored {score:.0f} with {ev_strength}. "
                        f"Concern: \\"{concern[:100]}\\""
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


def _correct_stage_narrative_contradictions(canonical: dict) -> tuple[dict, list[str]]:
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
                f"AUTO_REMOVED_CONCERN: stage contradiction \\u2014 uses "
                f"sub-{stage} language for classified stage={stage}. "
                f"Concern: \\"{concern[:120]}\\""
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
                f"AUTO_REMOVED_REC: stage contradiction \\u2014 uses "
                f"sub-{stage} language for classified stage={stage}. "
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

match = OLD_CONCERN_BLOCK.search(content)
if match:
    content = content[:match.start()] + NEW_CONCERN_BLOCK + \
        content[match.end():]
    print("Replacement 1 applied.")
else:
    print("WARNING: Replacement 1 pattern not found.")

# ── Replacement 2: update sanitize_canonical_report to call
#    _correct_stage_narrative_contradictions if not already present. ────────────
if "_correct_stage_narrative_contradictions" not in content:
    content = content.replace(
        "    canonical, _ = _filter_contradictory_recommendations(canonical)\n    return canonical",
        "    canonical, _ = _filter_contradictory_recommendations(canonical)\n"
        "    canonical, _ = _correct_stage_narrative_contradictions(canonical)\n"
        "    return canonical",
    )
    print("Replacement 2 applied.")
else:
    print("Replacement 2 not needed (already present).")

TARGET.write_text(content, encoding="utf-8")
print("Done.")
