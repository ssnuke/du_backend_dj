"""
The three things a Player must submit before they can be promoted to IR.

Each kind has the file types it accepts and a size cap, checked when the upload
link is requested and again against what actually landed in storage.
"""
MB = 1024 * 1024

DOCUMENTS = [
    {
        "kind": "financial_explanation",
        "label": "Financial explanation",
        "description": "A document explaining the financial side of joining.",
        "content_types": ["application/pdf", "image/jpeg", "image/png", "image/heic"],
        "max_bytes": 15 * MB,
    },
    {
        "kind": "legal_notary",
        "label": "Legal notary",
        "description": "The notarised legal document.",
        "content_types": ["application/pdf", "image/jpeg", "image/png", "image/heic"],
        "max_bytes": 15 * MB,
    },
    {
        "kind": "welcome_mandate_video",
        "label": "Welcome mandate video",
        "description": "Your recorded welcome mandate.",
        "content_types": ["video/mp4", "video/quicktime", "video/webm"],
        "max_bytes": 300 * MB,
    },
]

DOCUMENT_BY_KIND = {d["kind"]: d for d in DOCUMENTS}
KIND_CHOICES = [(d["kind"], d["label"]) for d in DOCUMENTS]
