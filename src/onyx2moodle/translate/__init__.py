"""Per-target translators: ONYX AssessmentItem -> Moodle XML question fragment.

Each translator returns a string containing one `<question type="...">...</question>`
block, ready to concatenate inside a `<quiz>` envelope.
"""

from .common import (
    embed_images_as_base64,
    extract_question_html,
    rewrite_math_delimiters,
    to_category_path,
)

__all__ = [
    "embed_images_as_base64",
    "extract_question_html",
    "rewrite_math_delimiters",
    "to_category_path",
]
