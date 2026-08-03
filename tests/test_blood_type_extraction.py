"""§11 priority test 1 - regression for the AB -> B extraction bug.

An earlier version of `_BLOOD_TYPE_RE` used `\\D{0,N}` as the separator
between the label and the letter. `\\D` matches letters too, so greedy
backtracking let it consume the "A" of "AB" as filler, silently returning
"B-" for "Blood Type AB-". Fixed by requiring an explicit
`\\s*[:\\-]?\\s*` separator. This file locks that fix in.
"""

from app.db.models.document import Document
from app.services.documents import extract_blood_type


def _doc(extracted_text: str | None = None, summary_en: str | None = None) -> Document:
    return Document(doc_kind="blood_work", extracted_text=extracted_text, summary_en=summary_en)


def test_ab_positive_not_truncated_to_b():
    doc = _doc(extracted_text="Blood Type AB+")
    assert extract_blood_type(doc) == "AB+"


def test_ab_negative_not_truncated_to_b():
    doc = _doc(extracted_text="Blood Type AB-")
    assert extract_blood_type(doc) == "AB-"


def test_plain_a_and_b_and_o():
    assert extract_blood_type(_doc(extracted_text="Blood Type A+")) == "A+"
    assert extract_blood_type(_doc(extracted_text="Blood Type B-")) == "B-"
    assert extract_blood_type(_doc(extracted_text="Blood Type O")) == "O"


def test_no_sign_defaults_to_empty_suffix():
    assert extract_blood_type(_doc(extracted_text="blood type AB")) == "AB"


def test_case_and_colon_variants():
    assert extract_blood_type(_doc(extracted_text="BLOOD TYPE: ab+")) == "AB+"
    assert extract_blood_type(_doc(extracted_text="Blood Type-AB-")) == "AB-"


def test_hyphenated_label_with_no_space_is_not_matched_known_narrow_limit():
    # "blood\s*type" requires whitespace (or nothing) between the two words,
    # not a hyphen - "blood-type" (hyphen joining the label itself, as
    # opposed to hyphen-as-separator-before-the-letter above) is outside the
    # deliberately narrow pattern and returns None rather than guessing.
    assert extract_blood_type(_doc(extracted_text="blood-type AB-")) is None


def test_chinese_blood_type_ab():
    doc = _doc(extracted_text="血型：AB型")
    assert extract_blood_type(doc) == "AB"


def test_chinese_blood_type_with_sign():
    doc = _doc(extracted_text="血型A型+")
    assert extract_blood_type(doc) == "A+"


def test_no_match_returns_none():
    assert extract_blood_type(_doc(extracted_text="Cholesterol: 180 mg/dL")) is None


def test_none_document_returns_none():
    assert extract_blood_type(None) is None


def test_searches_summary_when_extracted_text_absent():
    doc = _doc(extracted_text=None, summary_en="Blood Type: AB+. Results normal.")
    assert extract_blood_type(doc) == "AB+"
