"""extendedTextInteraction / uploadInteraction -> Moodle essay."""
from __future__ import annotations

from ..parser import AssessmentItem
from .common import embed_images_as_base64, extract_question_html


def translate(item: AssessmentItem, assets: list = None, category_path: list[str] = None) -> str:
    assets = assets or []
    # Drop the interaction; the essay editor itself is the answer field.
    replacements = {i.response_identifier: "" for i in item.interactions}
    body_html = extract_question_html(item, replacements)
    body_html, image_files = embed_images_as_base64(body_html, assets)
    body_with_files = body_html + ("\n" + "\n".join(image_files) if image_files else "")

    is_upload = any(i.kind == "upload" for i in item.interactions)
    attachments = "1" if is_upload else "0"
    responsefield = "0" if is_upload else "1"
    responseformat = "noinline" if is_upload else "editor"

    # Pull the first feedback block as model answer if present
    model_answer = ""
    for fb in item.feedback:
        if fb.html:
            model_answer = fb.html
            break

    return f"""<question type="essay">
    <name><text>{_escape(item.title or 'Untitled')}</text></name>
    <questiontext format="html">
      <text><![CDATA[{_cdata(body_with_files)}]]></text>
    </questiontext>
    <generalfeedback format="html"><text/></generalfeedback>
    <defaultgrade>1.0000000</defaultgrade>
    <penalty>0.0000000</penalty>
    <hidden>0</hidden>
    <idnumber/>
    <responseformat>{responseformat}</responseformat>
    <responserequired>{responsefield}</responserequired>
    <responsefieldlines>15</responsefieldlines>
    <minwordlimit></minwordlimit>
    <maxwordlimit></maxwordlimit>
    <attachments>{attachments}</attachments>
    <attachmentsrequired>0</attachmentsrequired>
    <maxbytes>0</maxbytes>
    <filetypeslist></filetypeslist>
    <graderinfo format="html"><text><![CDATA[{_cdata(model_answer)}]]></text></graderinfo>
    <responsetemplate format="html"><text/></responsetemplate>
  </question>"""


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cdata(s: str) -> str:
    return s.replace("]]>", "]]]]><![CDATA[>")
