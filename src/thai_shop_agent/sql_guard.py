"""Cleaning of model-written SQL. (The SELECT-only guard and table allow-list come in a later phase.)"""

import re

_FENCE = re.compile(r"```[ \t]*(?:sqlite|sql)?[ \t]*\n?(.*?)```", re.IGNORECASE | re.DOTALL)


def strip_code_fence(text: str) -> str:
    """Return the SQL inside the first ``` fence if there is one, otherwise the text itself, trimmed."""
    m = _FENCE.search(text)
    if m:
        return m.group(1).strip()
    # an unterminated fence (output cut off) still starts with ```
    return re.sub(r"^```[ \t]*(?:sqlite|sql)?", "", text.strip(), flags=re.IGNORECASE).strip()
