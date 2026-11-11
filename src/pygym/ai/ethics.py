"""A cheap check for the most obvious content problems in a generated exercise.

The real content rules are in the prompt (prompt.CONTENT_RULES): everyday topics, names from many
cultures, no stereotypes, no sensitive topics, no real people or brands, no real personal data.
This module only catches the plainest slips, so the model can be asked to fix them:

  * a word from a short deny list (violence and weapons, drugs, alcohol and tobacco, gambling,
    adult content, self-harm, medical diagnosis, politics and religion);
  * an email address that is not clearly fake (fake ones use example.com/.org/.net or end in
    .test / .example / .invalid).

It is deliberately small and NOT complete: it can't see stereotypes, tone or a harmful idea in
harmless words, and a word list always misses things. Words that are common in harmless tasks
were left out on purpose ("vote", "shoot", "party", "temple", "drug" as in drugstore): a false
alarm costs a model call. Every AI exercise still carries the "may be incorrect" warning.
"""
from __future__ import annotations

import re

# One entry per word; the check also matches a plural "s". Lower case.
DENY_WORDS = (
    # violence and weapons
    "kill", "killed", "killing", "murder", "weapon", "gun", "rifle", "pistol", "bomb", "ammo",
    "ammunition", "terrorist", "terrorism",
    # drugs, alcohol, tobacco
    "cocaine", "heroin", "cannabis", "marijuana", "alcohol", "beer", "wine", "vodka", "whisky",
    "whiskey", "cigarette", "drunk",
    # gambling
    "casino", "gambling", "gamble", "betting", "poker",
    # adult content
    "porn", "pornography", "nude", "nsfw", "erotic",
    # self-harm
    "suicide", "self-harm", "overdose",
    # medical diagnosis
    "diagnosis", "diagnose", "cancer", "tumor", "tumour",
    # politics and religion
    "election", "politician", "political", "religion", "religious", "church", "mosque",
)

_WORDS = re.compile(r"\b(" + "|".join(re.escape(w) for w in DENY_WORDS) + r")s?\b", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@([\w-]+(?:\.[\w-]+)+)\b")
_FAKE_DOMAIN = re.compile(r"(^|\.)example\.(com|org|net)$|\.(test|example|invalid|localhost)$",
                          re.IGNORECASE)

FIX = ("set the exercise in an everyday, neutral situation (shopping, school, sport, travel, "
       "weather, cooking) and use made-up names")


def problem(text: str) -> str:
    """A short problem text for the model when `text` breaks a content rule this module can
    see, else "". Never raises."""
    if not isinstance(text, str) or not text:
        return ""
    found = _WORDS.search(text)
    if found:
        return (f"the exercise mentions '{found.group(0)}', a sensitive topic; keep the topic "
                "everyday and neutral")
    for email in _EMAIL.finditer(text):
        if not _FAKE_DOMAIN.search(email.group(1)):
            return (f"the exercise has a real-looking email address '{email.group(0)}'; keep "
                    "personal data clearly fake, like name@example.com")
    return ""
