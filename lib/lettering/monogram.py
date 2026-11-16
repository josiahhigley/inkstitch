# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Monogram helpers for the Release 2 lettering kit (first slice).

Assembles a three-slot monogram into lettering text without depending on wx.
Center enlargement is applied by the Monogram panel after render_text.
"""

from __future__ import annotations

from typing import Dict, Mapping, MutableMapping, Optional, Tuple


SLOT_KEYS = ("left", "center", "right")


def normalize_initial(value: Optional[str], *, max_chars: int = 1) -> str:
    """Return at most *max_chars* non-space characters from *value* (stripped)."""
    if not value:
        return ""
    cleaned = "".join(ch for ch in str(value).strip() if not ch.isspace())
    if max_chars <= 0:
        return ""
    return cleaned[:max_chars]


def compose_monogram_text(left: str = "", center: str = "", right: str = "",
                          *, max_chars_per_slot: int = 1) -> str:
    """Join Left/Center/Right initials into the text string Lettering renders."""
    slots = [
        normalize_initial(left, max_chars=max_chars_per_slot),
        normalize_initial(center, max_chars=max_chars_per_slot),
        normalize_initial(right, max_chars=max_chars_per_slot),
    ]
    return "".join(slots)


def parse_monogram_slots(text: Optional[str], *, max_chars_per_slot: int = 1) -> Tuple[str, str, str]:
    """Best-effort split of existing lettering text into three monogram slots.

    One character → center only. Two → left + right. Three or more → left,
    center, right (extra characters beyond the first of each slot are dropped
    when max_chars_per_slot is 1).
    """
    cleaned = normalize_initial(text or "", max_chars=3 * max(1, max_chars_per_slot))
    if not cleaned:
        return "", "", ""
    if len(cleaned) == 1:
        return "", cleaned, ""
    if len(cleaned) == 2:
        return (
            normalize_initial(cleaned[0], max_chars=max_chars_per_slot),
            "",
            normalize_initial(cleaned[1], max_chars=max_chars_per_slot),
        )
    # Three or more: take first as left, middle-ish as center, last as right.
    return (
        normalize_initial(cleaned[0], max_chars=max_chars_per_slot),
        normalize_initial(cleaned[1], max_chars=max_chars_per_slot),
        normalize_initial(cleaned[2], max_chars=max_chars_per_slot),
    )


def default_monogram_settings() -> Dict[str, object]:
    """Settings keys the Monogram panel stores alongside lettering settings."""
    return {
        "monogram_mode": True,
        "monogram_left": "",
        "monogram_center": "",
        "monogram_right": "",
        "monogram_center_enlargement": False,
    }


def apply_monogram_to_settings(settings: MutableMapping[str, object],
                               left: str = "",
                               center: str = "",
                               right: str = "",
                               center_enlargement: bool = False) -> Mapping[str, object]:
    """Update a lettering settings mapping with monogram slots and composed text."""
    left_n = normalize_initial(left)
    center_n = normalize_initial(center)
    right_n = normalize_initial(right)
    settings["monogram_mode"] = True
    settings["monogram_left"] = left_n
    settings["monogram_center"] = center_n
    settings["monogram_right"] = right_n
    settings["monogram_center_enlargement"] = bool(center_enlargement)
    settings["text"] = compose_monogram_text(left_n, center_n, right_n)
    # Centered alignment is the usual default for three-letter monograms.
    if "text_align" not in settings or settings.get("text_align") is None:
        settings["text_align"] = 1  # Center in LetteringOptionsPanel choices
    return settings
