"""
The Basic Training course outline.

A fixed, ordered list of lessons. A LearnVideo is assigned to one slot through
its `lesson_key` (in the Django admin); a slot with no published video shows as
"Coming soon" and is skipped when working out what a Player may watch next.

`scripts` are the ids of the reference scripts shown with the lesson (they
match the ids in the app's TRAINING_SCRIPTS: info, dkd, drilling,
purpose-call). A script is a bundled screen, not a server resource, so this
only decides where it is offered.
"""

LESSONS = [
    {"key": "welcome", "title": "Welcome", "scripts": []},
    {"key": "why_nm", "title": "Why NM", "scripts": []},
    {"key": "switch_on", "title": "Switch ON", "scripts": []},
    {"key": "info_part_1", "title": "Info Part 1", "scripts": ["info"]},
    {"key": "info_part_2", "title": "Info Part 2", "scripts": ["info"]},
    {"key": "oh1", "title": "OH1", "scripts": []},
    {"key": "scripts", "title": "Scripts", "scripts": ["dkd", "drilling"]},
    {"key": "closings", "title": "Closings", "scripts": []},
    {"key": "prospecting", "title": "Prospecting", "scripts": ["purpose-call"]},
    {"key": "success_formula", "title": "Success Formula", "scripts": []},
]

LESSON_CHOICES = [(lesson["key"], lesson["title"]) for lesson in LESSONS]
