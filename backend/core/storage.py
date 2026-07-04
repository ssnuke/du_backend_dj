import os

from cloudinary_storage.storage import MediaCloudinaryStorage


class AutoCloudinaryStorage(MediaCloudinaryStorage):
    """
    Picks Cloudinary's resource_type (image/video/raw) per file extension
    instead of always uploading as "raw". Raw storage stores files as opaque
    blobs with none of Cloudinary's automatic optimization (no f_auto/q_auto
    format+quality compression, no on-the-fly resized derivatives) — which is
    why bandwidth usage can run far higher than storage usage. Routing
    images/videos through their proper resource type unlocks that pipeline.

    Voice notes (audio-only .webm/.ogg/etc.) intentionally map to "video" —
    Cloudinary has no separate "audio" resource type; audio-only files use
    the video pipeline too.

    This only affects new uploads. Existing ChatMessage.attachment_url /
    Sticker.image_url values already store a fully-resolved Cloudinary URL
    from upload time, so nothing needs to change for already-uploaded files.
    """

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
    AV_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".ogg", ".mp3", ".wav", ".m4a"}

    def _get_resource_type(self, name):
        ext = os.path.splitext(name)[1].lower()
        if ext in self.IMAGE_EXTENSIONS:
            return "image"
        if ext in self.AV_EXTENSIONS:
            return "video"
        return "raw"
