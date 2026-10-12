"""
Private document storage (Cloudflare R2, a separate bucket from the public media one).

Promotion documents are financial and legal papers and a recorded mandate. The
bucket the rest of the app uses is public (AWS_QUERYSTRING_AUTH = False, served
from a custom domain), which is right for avatars and chat images and wrong
for these. So they live in their own bucket with NO public access, and every
read is a short-lived signed link handed out by the server.

Files move browser -> R2 directly through a signed upload link, so a large
video never passes through the web server. The app database holds only a row
of metadata (see PromotionDocument).

boto3 is imported inside the functions: it is only needed when this is used,
and the tests replace these functions rather than talk to R2.
"""
import re
import unicodedata
import uuid

from django.conf import settings


class StorageNotConfigured(Exception):
    """The private bucket's settings are missing."""


def is_configured():
    return bool(
        settings.R2_ACCESS_KEY_ID and settings.R2_SECRET_ACCESS_KEY
        and settings.R2_ENDPOINT_URL and settings.R2_PRIVATE_BUCKET_NAME
    )


def _client():
    if not is_configured():
        raise StorageNotConfigured("Private document storage is not configured")
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def person_folder(name, ir_id):
    """
    The folder (key prefix) a person's files live in: "Ravi-Kumar__DU1234".

    The name makes it findable in the Cloudflare dashboard; the ID makes it
    unique, since two people can share a name and a name can change. Accents
    are folded to plain letters and anything else becomes a hyphen, so the
    result is safe in a path. It contains no underscore except the "__" before
    the ID, which is what lets a key be matched back to its owner.
    """
    ascii_name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-")[:40].strip("-") or "player"
    return f"{slug}__{ir_id}"


def new_key(folder, kind, filename):
    """
    promotion/<folder>/<kind>__<random>__<original name>

    The kind leads the file name so the three documents are told apart at a
    glance inside the folder; the random part means a replacement never
    overwrites the file it replaces.
    """
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (filename or "file"))[-80:]
    return f"promotion/{folder}/{kind}__{uuid.uuid4().hex[:8]}__{safe}"


def key_belongs_to(key, ir_id, kind):
    """True when `key` is a file we issued for this person and document kind."""
    parts = (key or "").split("/")
    return (
        len(parts) == 3
        and parts[0] == "promotion"
        and parts[1].endswith(f"__{ir_id}")
        and parts[2].startswith(f"{kind}__")
    )


def presign_upload(key, content_type, expires=900):
    """A link the browser can PUT the file to. The content type is part of the signature."""
    return _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.R2_PRIVATE_BUCKET_NAME, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


def presign_download(key, filename=None, expires=300):
    """A short-lived link to read the file."""
    params = {"Bucket": settings.R2_PRIVATE_BUCKET_NAME, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'inline; filename="{filename}"'
    return _client().generate_presigned_url("get_object", Params=params, ExpiresIn=expires)


def head(key):
    """(size_bytes, content_type) of a stored object, or None if it is not there."""
    from botocore.exceptions import ClientError
    try:
        meta = _client().head_object(Bucket=settings.R2_PRIVATE_BUCKET_NAME, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return meta["ContentLength"], meta.get("ContentType", "")


def delete(key):
    """Best effort: a leftover object is harmless, a failed delete must not block anything."""
    try:
        _client().delete_object(Bucket=settings.R2_PRIVATE_BUCKET_NAME, Key=key)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Could not delete private object %s", key)
