import json

from src.modules.evaluation.application.dto.pipeline_schema import (
    ClassificationField,
    ClassificationResult,
    CriterionEvidence,
    EvidenceMappingResult,
    EvidenceUnit,
)
from src.modules.evaluation.application.services.evidence_excerpt_localizer import (
    localize_excerpts_in_results,
    parse_supporting_evidence_location,
    should_localize_excerpt,
)


def test_should_localize_excerpt_detects_english_sentence():
    assert should_localize_excerpt(
        "Company Purpose: To become the primary outlet of user-generated video content on the Internet."
    )


def test_should_localize_excerpt_skips_vietnamese_text():
    assert not should_localize_excerpt(
        "Muc tieu cong ty: tro thanh nen tang video do nguoi dung tao noi dung."
    )


def test_parse_supporting_evidence_location_extracts_excerpt_from_json_string():
    raw = json.dumps({
        "source_type": "Pitch Deck",
        "source_id": "41",
        "slide_number_or_page_number": 1,
        "excerpt_or_summary": "Solution: Consumers upload videos to YouTube.",
        "section_name": "Solution",
    })
    excerpt, page, section_name = parse_supporting_evidence_location(raw)

    assert excerpt == "Solution: Consumers upload videos to YouTube."
    assert page == 1
    assert section_name == "Solution"


def test_localize_excerpts_in_results_updates_classification_and_evidence():
    classification = ClassificationResult(
        stage=ClassificationField(
            value="SEED",
            confidence="High",
            resolution_source="inferred",
            supporting_evidence_locations=[
                json.dumps({
                    "source_type": "Pitch Deck",
                    "source_id": "41",
                    "slide_number_or_page_number": 1,
                    "excerpt_or_summary": "Company Purpose: To become the primary outlet of user-generated video content on the Internet.",
                    "section_name": "Purpose",
                })
            ],
        ),
        main_industry=ClassificationField(
            value="MEDIA",
            confidence="Medium",
            resolution_source="inferred",
            supporting_evidence_locations=[],
        ),
        subindustry=None,
        operational_notes=[],
    )
    evidence = EvidenceMappingResult(
        criteria_evidence=[
            CriterionEvidence(
                criterion="Problem_&_Customer_Pain",
                strongest_evidence_level="DIRECT",
                evidence_units=[
                    EvidenceUnit(
                        source_type="Pitch Deck",
                        source_id="41",
                        slide_number_or_page_number=1,
                        excerpt_or_summary="Solution: Consumers upload their videos to YouTube. YouTube serves the content to millions of viewers.",
                    )
                ],
            )
        ]
    )

    translated = {
        "Company Purpose: To become the primary outlet of user-generated video content on the Internet.": "Muc tieu cong ty: tro thanh nen tang chinh cho noi dung video do nguoi dung tao tren Internet.",
        "Solution: Consumers upload their videos to YouTube. YouTube serves the content to millions of viewers.": "Giai phap: nguoi dung tai video len YouTube va nen tang phan phoi noi dung toi hang trieu nguoi xem.",
    }

    updated_classification, updated_evidence, localized_count = localize_excerpts_in_results(
        classification_res=classification,
        evidence_res=evidence,
        translate_batch=lambda excerpts: [translated[item] for item in excerpts],
    )

    raw_loc = updated_classification.stage.supporting_evidence_locations[0]
    excerpt, _, _ = parse_supporting_evidence_location(raw_loc)
    assert excerpt.startswith("Muc tieu cong ty:")
    assert (
        updated_evidence.criteria_evidence[0].evidence_units[0].excerpt_or_summary
        == "Giai phap: nguoi dung tai video len YouTube va nen tang phan phoi noi dung toi hang trieu nguoi xem."
    )
    assert localized_count == 2
