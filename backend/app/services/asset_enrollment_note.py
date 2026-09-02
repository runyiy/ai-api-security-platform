import re


ASSET_ENROLLMENT_NOTE_AUTH_MATERIAL_CODE = (
    "asset_enrollment_note_auth_material"
)
ASSET_ENROLLMENT_NOTE_AUTH_MATERIAL_MESSAGE = (
    "Enrollment decision note contains prohibited authentication material."
)


_PROHIBITED_NOTE_AUTH_MATERIAL = re.compile(
    r"(?:"
    r"\bauthorization\s*[:=]|"
    r"\bbearer\s+\S+|"
    r"\b(?:set-)?cookie\s*[:=]|"
    r"\b(?:x[-_ ]?)?api[-_ ]?key\s*[:=]|"
    r"\b(?:access|refresh)[-_ ]?token\s*[:=]|"
    r"\b(?:credential|credentials|"
    r"(?:db[-_ ]?)?(?:password|passwd)|"
    r"(?:client[-_ ]?)?secret)\s*[:=]"
    r")",
    flags=re.IGNORECASE,
)


class AssetEnrollmentNoteAuthMaterialError(ValueError):
    def __init__(self) -> None:
        super().__init__(ASSET_ENROLLMENT_NOTE_AUTH_MATERIAL_MESSAGE)


def validate_non_secret_enrollment_note(note: str) -> str:
    if _PROHIBITED_NOTE_AUTH_MATERIAL.search(note):
        raise AssetEnrollmentNoteAuthMaterialError
    return note
