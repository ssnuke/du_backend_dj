"""
Promotion documents: the three papers kept for everyone except CTC and Admin.

Three kinds (core/promotion_docs.py). The file goes straight from the browser
to the private bucket through a signed link; this module only hands out that
link, then records the file once it has really arrived.

  GET  /api/promotion_docs/<ir_id>/?requester_ir_id=            status of all three
  POST /api/promotion_docs/<ir_id>/<kind>/upload_url/            get a signed upload link
  POST /api/promotion_docs/<ir_id>/<kind>/confirm/               record the finished upload
  GET  /api/promotion_docs_overview/?requester_ir_id=            who has sent what (Admin/CTC/LDC)

Who may send a person's documents depends on who they are:

  * IR, PRO, LS and LDC — only an LDC: for the members of their teams, and for
    themselves. Sending these is the LDC's validation step, so the person
    cannot send their own, and neither can an Admin or CTC (who can still open
    every file). An LDC's papers are sent by that LDC alone. This is also how
    existing members, who joined before documents were asked for, get theirs on
    record: their LDC finds them under "Missing" and sends them.
  * Player — the person themselves, or the people who look after them: an Admin
    or CTC (anyone), or an LDC (the Players they may promote, and the
    members of their teams).

Those people, and the person, can open the files. A Player still needs all
three before they can be promoted to IR; for everyone else they are simply kept
on record.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Count, F, Max, Q

from core.models import AccessLevel, Ir, PromotionDocument
from core.promotion_docs import DOCUMENTS, DOCUMENT_BY_KIND, MB
from core.utils import private_storage
from core.views.get import get_status_manageable_irs

NOT_CONFIGURED = Response(
    {"detail": "Document storage is not set up yet. Please try again later."},
    status=status.HTTP_503_SERVICE_UNAVAILABLE,
)


def _get_ir(ir_id):
    try:
        return Ir.objects.get(ir_id=ir_id)
    except Ir.DoesNotExist:
        return None


def needs_documents(ir):
    """Everyone keeps documents except CTC and Admin."""
    return ir.ir_access_level not in (AccessLevel.ADMIN, AccessLevel.CTC)


# Their documents are validated, and so sent, by an LDC.
LDC_VALIDATED_ROLES = (AccessLevel.LDC, AccessLevel.LS, AccessLevel.GC, AccessLevel.IR)


def can_send_documents(requester, person):
    """Whether `requester` may upload `person`'s documents. The one rule; the screen follows it."""
    if not needs_documents(person):
        return False
    if person.ir_access_level in LDC_VALIDATED_ROLES:
        if requester.ir_access_level != AccessLevel.LDC:
            return False
        if requester.ir_id == person.ir_id:
            return True
        # Another LDC's papers are not theirs to send; their team members' are.
        return person.ir_access_level != AccessLevel.LDC and _ldc_looks_after(requester, person)
    return requester.ir_id == person.ir_id or _is_promoter_of(requester, person)


def _ldc_looks_after(requester, person):
    """
    An LDC's people for documents: the members of the teams they belong to (the
    same set the IR Status tab lets them manage), plus a Player they may
    promote — one under them, or one they registered — who is handled in the
    Players tab even when they are not in a team.
    """
    if requester.can_promote_player(person):
        return True
    return get_status_manageable_irs(requester).filter(ir_id=person.ir_id).exists()


def _is_promoter_of(requester, person):
    """
    Whether `requester` looks after `person`'s documents: Admin/CTC (anyone), or
    an LDC for their own people (see _ldc_looks_after).
    (Kept under its old name — a Player's promoter is who it was written for.)
    """
    if requester.has_full_access():
        return True
    if requester.ir_access_level != AccessLevel.LDC:
        return False
    return _ldc_looks_after(requester, person)


def _may_view(requester, person):
    """Who sees that documents exist: the person themselves and those who look after them."""
    return requester.ir_id == person.ir_id or _is_promoter_of(requester, person)


def _status_payload(requester, player):
    docs = {d.kind: d for d in player.promotion_documents.select_related("uploaded_by")}
    # Links for the person who sent the files and for those who check them.
    may_open = requester.ir_id == player.ir_id or _is_promoter_of(requester, player)
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
            "content_type": doc.content_type if doc else None,
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
    return {
        "ir_id": player.ir_id, "documents": out, "complete": not missing, "missing": missing,
        # The screen shows the add / replace buttons from this, not from its own guess.
        "can_upload": can_send_documents(requester, player),
    }


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
    if not needs_documents(player):
        return None, None, Response({"detail": "Documents are not needed for Admin or CTC"}, status=status.HTTP_400_BAD_REQUEST)
    if not can_send_documents(uploader, player):
        who = (
            "Documents for an IR, PRO, LS or LDC are sent by an LDC"
            if player.ir_access_level in LDC_VALIDATED_ROLES
            else "Only this person, or the leader who looks after them, can send their documents"
        )
        return None, None, Response({"detail": who}, status=status.HTTP_403_FORBIDDEN)
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


def _managed_people(requester):
    """
    Who `requester` sees in the Documents list, never including CTC/Admin or themselves.

    Admin and CTC: everyone. An LDC: the members of their teams only — not their
    whole downline. (A Player they may promote is not listed here; the Players tab
    is where they are reviewed, and a Player who is also in their team shows up
    here as a team member.)
    """
    if requester.has_full_access():
        return Ir.objects.exclude(ir_access_level__in=(AccessLevel.ADMIN, AccessLevel.CTC)).exclude(ir_id=requester.ir_id)
    return get_status_manageable_irs(requester).exclude(
        ir_access_level__in=(AccessLevel.ADMIN, AccessLevel.CTC)
    ).exclude(ir_id=requester.ir_id)


class PromotionDocsOverview(APIView):
    """
    Everyone's documents at a glance, for the people who look after them.

      show=uploaded (default)  people who have sent at least one
      show=pending             people still missing at least one
      show=all                 everyone in scope

    Links are not included here (there can be hundreds of people); open a person
    to get the signed links from the per-person endpoint.
    """

    def get(self, request):
        requester = _get_ir(request.GET.get("requester_ir_id"))
        if not requester:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        if requester.ir_access_level > AccessLevel.LDC:
            return Response({"detail": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

        show = request.GET.get("show", "uploaded")
        if show not in ("uploaded", "pending", "all"):
            return Response({"detail": "show must be uploaded, pending or all"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            limit = min(max(int(request.GET.get("limit", 20)), 1), 50)
            offset = max(int(request.GET.get("offset", 0)), 0)
        except (TypeError, ValueError):
            return Response({"detail": "limit and offset must be numbers"}, status=status.HTTP_400_BAD_REQUEST)

        qs = _managed_people(requester).annotate(
            doc_count=Count("promotion_documents", distinct=True),
            last_upload=Max("promotion_documents__uploaded_at"),
        )
        search = (request.GET.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(ir_name__icontains=search) | Q(ir_id__icontains=search))
        if show == "uploaded":
            qs = qs.filter(doc_count__gt=0)
        elif show == "pending":
            qs = qs.filter(doc_count__lt=len(DOCUMENTS))

        total = qs.count()
        page = list(qs.order_by(F("last_upload").desc(nulls_last=True), "ir_name")[offset:offset + limit])
        kinds_by_ir = {}
        for ir_id, kind in PromotionDocument.objects.filter(ir__in=[p.pk for p in page]).values_list("ir_id", "kind"):
            kinds_by_ir.setdefault(ir_id, set()).add(kind)

        results = []
        for ir in page:
            have = kinds_by_ir.get(ir.ir_id, set())
            results.append({
                "ir_id": ir.ir_id,
                "ir_name": ir.ir_name,
                "role": AccessLevel.get_role_name(ir.ir_access_level),
                "status": "active" if ir.status else "inactive",
                "documents": [{"kind": d["kind"], "label": d["label"], "uploaded": d["kind"] in have} for d in DOCUMENTS],
                "documents_count": len(have),
                "complete": len(have) == len(DOCUMENTS),
                "last_uploaded_at": ir.last_upload.isoformat() if ir.last_upload else None,
            })
        return Response({"results": results, "total": total, "has_more": offset + limit < total})
