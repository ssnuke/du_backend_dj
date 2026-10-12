"""
Promotion documents: what a Player submits before they can be promoted to IR.

Three kinds (core/promotion_docs.py). The file goes straight from the browser
to the private bucket through a signed link; this module only hands out that
link, then records the file once it has really arrived.

  GET  /api/promotion_docs/<ir_id>/?requester_ir_id=            status of all three
  POST /api/promotion_docs/<ir_id>/<kind>/upload_url/            get a signed upload link
  POST /api/promotion_docs/<ir_id>/<kind>/confirm/               record the finished upload

The people who promote a Player send the documents for them, after checking
them: an Admin or CTC, or an LDC who is allowed to promote that Player. The
Player cannot send their own. Those same people can open the files; the Player
only sees which of the three have been received.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import AccessLevel, Ir, PromotionDocument
from core.promotion_docs import DOCUMENTS, DOCUMENT_BY_KIND, MB
from core.utils import private_storage

NOT_CONFIGURED = Response(
    {"detail": "Document storage is not set up yet. Please try again later."},
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
)


def _get_ir(ir_id):
    try:
        return Ir.objects.get(ir_id=ir_id)
    except Ir.DoesNotExist:
        return None


def _is_promoter_of(requester, player):
    """Admin/CTC, or an LDC who is allowed to promote this Player."""
    if requester.has_full_access():
        return True
    return requester.can_promote_player(player)


def _may_view(requester, player):
    """Who sees that documents exist: the Player (status only) and their promoters."""
    return requester.ir_id == player.ir_id or _is_promoter_of(requester, player)


def _status_payload(requester, player):
    docs = {d.kind: d for d in player.promotion_documents.select_related("uploaded_by")}
    # Links only for those who check the files. The Player sees what has been
    # received, not the papers themselves.
    may_open = _is_promoter_of(requester, player)
    out = []
    for spec in DOCUMENTS:
        doc = docs.get(spec["kind"])
        row = {
            "kind": spec["kind"],
            "label": spec["label"],
            "description": spec["description"],
            "accept": spec["content_types"],
            "max_mb": spec["max_bytes"] // MB,
            "uploaded": doc is not None,
            "original_name": doc.original_name if doc else None,
            "size_bytes": doc.size_bytes if doc else None,
            "uploaded_at": doc.uploaded_at.isoformat() if doc else None,
            "uploaded_by_name": doc.uploaded_by.ir_name if doc and doc.uploaded_by else None,
            "view_url": None,
        }
        if doc and may_open:
            try:
                row["view_url"] = private_storage.presign_download(doc.file_key, doc.original_name)
            except private_storage.StorageNotConfigured:
                pass
        out.append(row)
    missing = [r["kind"] for r in out if not r["uploaded"]]
    return {"ir_id": player.ir_id, "documents": out, "complete": not missing, "missing": missing}


def _folder_for(player):
    """
    The folder this Player's files go in. Once they have any file the folder
    is fixed, even if their name is later changed: otherwise a rename would
    split their three documents across two folders.
    """
    existing = player.promotion_documents.values_list("file_key", flat=True).first()
    if existing and existing.count("/") == 2:
        return existing.split("/")[1]
    return private_storage.person_folder(player.ir_name, player.ir_id)


class PromotionDocsStatus(APIView):
    def get(self, request, ir_id):
        requester = _get_ir(request.GET.get("requester_ir_id"))
        player = _get_ir(ir_id)
        if not requester or not player:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        if not _may_view(requester, player):
            return Response({"detail": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)
        return Response(_status_payload(requester, player))


def _player_and_uploader_or_error(request, ir_id, kind):
    """(player, uploader, None), or (None, None, error Response)."""
    if kind not in DOCUMENT_BY_KIND:
        return None, None, Response({"detail": "Unknown document type"}, status=status.HTTP_404_NOT_FOUND)
    uploader = _get_ir(request.data.get("requester_ir_id"))
    player = _get_ir(ir_id)
    if not uploader or not player:
        return None, None, Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
    if player.ir_access_level != AccessLevel.PLAYER:
        return None, None, Response({"detail": "Documents are only needed for Players"}, status=status.HTTP_400_BAD_REQUEST)
    if not _is_promoter_of(uploader, player):
        return None, None, Response(
            {"detail": "Only the person who promotes this Player can send their documents"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return player, uploader, None


class PromotionDocUploadUrl(APIView):
    """Validate what the Player is about to send and return a signed upload link."""

    def post(self, request, ir_id, kind):
        player, _uploader, err = _player_and_uploader_or_error(request, ir_id, kind)
        if err:
            return err
        spec = DOCUMENT_BY_KIND[kind]

        content_type = (request.data.get("content_type") or "").lower()
        filename = (request.data.get("filename") or "file").strip()[:150]
        try:
            size = int(request.data.get("size"))
        except (TypeError, ValueError):
            return Response({"detail": "size is required"}, status=status.HTTP_400_BAD_REQUEST)

        if content_type not in spec["content_types"]:
            return Response(
                {"detail": f"{spec['label']} must be one of: " + ", ".join(t.split("/")[-1].upper() for t in spec["content_types"])},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if size <= 0 or size > spec["max_bytes"]:
            return Response(
                {"detail": f"{spec['label']} must be under {spec['max_bytes'] // MB} MB"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        key = private_storage.new_key(_folder_for(player), kind, filename)
        try:
            url = private_storage.presign_upload(key, content_type)
        except private_storage.StorageNotConfigured:
            return NOT_CONFIGURED
        return Response({"upload_url": url, "key": key, "content_type": content_type})


class PromotionDocConfirm(APIView):
    """
    Record an upload once it has really arrived. The file is looked up in storage
    rather than taken on the client's word, and its size and type are checked
    again against what was allowed.
    """

    def post(self, request, ir_id, kind):
        player, uploader, err = _player_and_uploader_or_error(request, ir_id, kind)
        if err:
            return err
        spec = DOCUMENT_BY_KIND[kind]

        key = request.data.get("key") or ""
        # The key must be one we issued for THIS person and kind, or a file for
        # one Player could be attached to another's record.
        if not private_storage.key_belongs_to(key, player.ir_id, kind):
            return Response({"detail": "Invalid upload"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            found = private_storage.head(key)
        except private_storage.StorageNotConfigured:
            return NOT_CONFIGURED
        if found is None:
            return Response({"detail": "The file did not finish uploading. Please try again."}, status=status.HTTP_400_BAD_REQUEST)
        size, content_type = found
        if size <= 0 or size > spec["max_bytes"] or (content_type or "").lower() not in spec["content_types"]:
            private_storage.delete(key)
            return Response({"detail": "That file is not allowed for this document."}, status=status.HTTP_400_BAD_REQUEST)

        existing = PromotionDocument.objects.filter(ir=player, kind=kind).first()
        old_key = existing.file_key if existing else None
        PromotionDocument.objects.update_or_create(
            ir=player, kind=kind,
            defaults={
                "file_key": key,
                "original_name": (request.data.get("filename") or "file").strip()[:200],
                "content_type": content_type.lower(),
                "size_bytes": size,
                "uploaded_by": uploader,
            },
        )
        if old_key and old_key != key:
            private_storage.delete(old_key)  # replaced: don't leave the old file behind
        return Response(_status_payload(uploader, player), status=status.HTTP_201_CREATED)
