import os

import cloudinary.uploader
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

    def _save(self, name, content):
        # Cloudinary strips the extension from the public_id it returns for
        # image/video uploads (format is tracked separately on their end), so
        # the name Django stores loses its extension entirely. Later calls to
        # url()/delete() re-derive resource_type from that stored name via
        # _get_resource_type() above — without the extension they always fall
        # through to "raw", producing a broken delivery URL (wrong
        # resource_type segment) for an asset Cloudinary actually stored as
        # image/video. Re-attach the original extension so type detection
        # keeps working on every later call, not just at upload time.
        ext = os.path.splitext(name)[1]
        public_id = super()._save(name, content)
        if ext and not public_id.lower().endswith(ext.lower()):
            public_id += ext
        return public_id

    def delete(self, name):
        # The extension _save() re-attaches (see above) is only there so
        # _get_resource_type() keeps working — Cloudinary's *actual* stored
        # public_id for image/video still omits it. Passing the
        # extension-bearing name straight to destroy() would silently target
        # a non-existent public_id (destroy() on a missing resource still
        # reports "ok"-shaped responses in some cases, so this failure mode
        # is easy to miss: nothing gets deleted, and storage quietly leaks).
        resource_type = self._get_resource_type(name)
        public_id = os.path.splitext(name)[0] if resource_type in ("image", "video") else name
        response = cloudinary.uploader.destroy(public_id, invalidate=True, resource_type=resource_type)
        return response.get("result") == "ok"
