"""Access point filters, as typed in the filter box.

    AP-FORUM-2*          starts with AP-FORUM-2
    *-2??                ends in -2 and two more characters
    forum floor 2        contains "forum floor 2" (no wildcard: matches anywhere)
    AP-A-1*, AP-B-1*     either of the two (comma separated)

A filter is compared with four things of an access point: its name, its
location, its site tag and its controller. It matches when any of them fits.
Matching ignores case.

The same filter is used for the dashboard, for saved groups and for the
export, always through the database (ILIKE), so a group contains exactly what
the filter showed.
"""

import re

MAX_PATTERN_LENGTH = 300
MAX_PARTS = 30


def parts(text):
    found = [re.sub(r"\s+", " ", p.strip()) for p in (text or "").split(",")]
    return [p for p in found if p][:MAX_PARTS]


def _like(part):
    escaped = part.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if "*" in part or "?" in part:
        return escaped.replace("*", "%").replace("?", "_")
    return "%" + escaped + "%"


def like_patterns(text):
    """The filter as a list for `column ILIKE ANY (...)`; empty when there is no filter."""
    return [_like(p) for p in parts((text or "")[:MAX_PATTERN_LENGTH])]


def normalize(text):
    """How a filter is stored and shown."""
    return ", ".join(parts((text or "")[:MAX_PATTERN_LENGTH]))


MATCH_COLUMNS = ("ap_name", "location", "site_tag", "wlc")


def match_sql(alias, placeholder, columns=MATCH_COLUMNS):
    """SQL condition: any of the columns of `alias` fits the filter in `placeholder`."""
    prefix = alias + "." if alias else ""
    return "(" + " OR ".join(f"{prefix}{c} ILIKE ANY ({placeholder})" for c in columns) + ")"
