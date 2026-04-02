from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Set


def _split_exts(s: str) -> Set[str]:
    if not s:
        return set()
    parts = re.split(r"[,\s;]+", s.strip())
    return {p.lower().lstrip(".") for p in parts if p}


def _ext_of(path: str) -> str:
    base = os.path.basename(path or "")
    if "." not in base:
        return ""
    return base.rsplit(".", 1)[-1].lower()


def _generate_detailed_extension_summary(
    disallowed_files: List[Dict[str, Any]], max_examples: int = 5
) -> str:
    if not disallowed_files:
        return ""

    ext_groups: Dict[str, List[str]] = {}
    for file_info in disallowed_files:
        filename = file_info.get("name", "")
        ext = _ext_of(filename)
        ext_groups.setdefault(ext, []).append(filename)

    sorted_exts = sorted(ext_groups.items(), key=lambda x: (-len(x[1]), x[0]))

    summary_parts = []
    for ext, filenames in sorted_exts:
        count = len(filenames)
        ext_display = f".{ext}" if ext else "(no extension)"
        examples = filenames[:max_examples]
        examples_str = ", ".join(f'"{os.path.basename(f)}"' for f in examples)
        if count > max_examples:
            examples_str += f" (+{count - max_examples} more)"
        summary_parts.append(
            f"{ext_display}: {count} file{'s' if count != 1 else ''} ({examples_str})"
        )

    return "; ".join(summary_parts)
