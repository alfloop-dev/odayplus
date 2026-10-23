"""Server-retained binding of actual Survey attachment bytes, not URI claims."""
from __future__ import annotations

from dataclasses import dataclass

from modules.market_survey.domain.models import MediaAttachment, MediaKind


@dataclass(frozen=True)
class SurveyAttachmentRecord:
    media_id: str
    tenant_id: str
    assignment_id: str
    target_id: str
    target_kind: str
    uploader_id: str
    object_uri: str
    object_generation: int
    sha256: str
    size_bytes: int
    content_type: str
    media_kind: MediaKind
    captured_at: str
    caption: str | None
    recorded_at: str

    def native_attachment(self) -> MediaAttachment:
        return MediaAttachment(blob_id=self.media_id, media_id=self.media_id,
            captured_at=self.captured_at, media_kind=self.media_kind,
            sha256=self.sha256, storage_uri=self.object_uri,
            caption=self.caption, media_type=self.content_type,
            metadata={"object_generation": self.object_generation,
                      "size_bytes": self.size_bytes, "assignment_id": self.assignment_id})

    def public(self) -> dict:
        # Private storage URI/credentials never become the browser's authority.
        return {"mediaId": self.media_id, "assignmentId": self.assignment_id,
            "sha256": self.sha256, "sizeBytes": self.size_bytes,
            "declaredContentType": self.content_type, "mediaKind": self.media_kind.value,
            "submittedCapturedAt": self.captured_at, "caption": self.caption,
            "uploadedBy": self.uploader_id, "recordedAt": self.recorded_at}
