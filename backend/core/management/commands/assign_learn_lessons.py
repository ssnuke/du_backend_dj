"""
Propose which Basic Training course slot each existing video fills.

The course outline (core/learn_curriculum.py) needs every video assigned to a
slot through `lesson_key`. Existing videos have none, and only the titles say
which is which, so this matches titles to slots and reports. Dry run unless
--apply; a video is only assigned when its title matches exactly ONE slot and
that slot is still free, so nothing is guessed. Whatever it leaves can be
set by hand in the admin.
"""
import re

from django.core.management.base import BaseCommand

from core.learn_curriculum import LESSONS
from core.models import LearnVideo

# Words a title may use for each slot, compared with spaces and punctuation
# removed ("Why N.M." -> "whynm").
ALIASES = {
    "welcome": ["welcome"],
    "why_nm": ["whynm", "whynetworkmarketing"],
    "switch_on": ["switchon"],
    # Only the numbered forms: a bare "Info part" could be either, so it is
    # left for you to place by hand rather than guessed.
    "info_part_1": ["infopart1", "info1"],
    "info_part_2": ["infopart2", "info2"],
    "oh1": ["oh1"],
    "closings": ["closings", "closing"],
    "prospecting": ["prospecting"],
    "success_formula": ["successformula", "sucessformula", "successforumla", "sucessforumla"],
}


def squash(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def match(title):
    """
    The slot a title belongs to, or [] / several keys when it is not clear.

    A title can hit more than one slot ("Scripts" contains nothing else, but
    "Info scripts" would hit two); the slot with the longest matching alias
    wins only if it is strictly longer than the rest.
    """
    t = re.sub(r"^\d+", "", squash(title))  # a leading list number is not part of the name
    hits = {key: max(len(w) for w in words if w in t) for key, words in ALIASES.items() if any(w in t for w in words)}
    if len(hits) <= 1:
        return list(hits)
    longest = max(hits.values())
    best = [k for k, n in hits.items() if n == longest]
    return best if len(best) == 1 else list(hits)


class Command(BaseCommand):
    help = "Assign Basic Training videos to course slots by title (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        taken = set(
            LearnVideo.objects.exclude(lesson_key__isnull=True).values_list("lesson_key", flat=True)
        )
        todo = LearnVideo.objects.filter(category="basic_training", lesson_key__isnull=True).order_by("order", "created_at")

        proposals, skipped = [], []
        for video in todo:
            keys = match(video.title)
            if len(keys) == 1 and keys[0] not in taken:
                taken.add(keys[0])
                proposals.append((video, keys[0]))
            else:
                skipped.append((video, keys))

        titles = {l["key"]: l["title"] for l in LESSONS}
        for video, key in proposals:
            self.stdout.write(f"  {video.title!r}  ->  {titles[key]}")
            if opts["apply"]:
                LearnVideo.objects.filter(pk=video.pk).update(lesson_key=key)
        for video, keys in skipped:
            why = "matches nothing" if not keys else f"unclear ({', '.join(keys)})"
            self.stdout.write(f"  {video.title!r}  left unassigned: {why} - set it in the admin")
        free = [titles[k] for k in titles if k not in taken]
        self.stdout.write(f"{'Assigned' if opts['apply'] else 'Would assign'} {len(proposals)}. "
                          f"Slots still empty (shown as Coming soon): {', '.join(free) or 'none'}.")
        if not opts["apply"]:
            self.stdout.write("Dry run - nothing written. Re-run with --apply.")
