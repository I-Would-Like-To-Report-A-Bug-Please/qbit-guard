from __future__ import annotations

from typing import Any, Dict, Set, Tuple


def merge_torrent_state(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(previous or {})
    merged.update(current or {})
    return merged


def should_process(
    torrent_hash: str,
    torrent: Dict[str, Any],
    seen: Set[str],
    retry_state: Dict[str, Dict[str, Any]],
    inflight: Set[str],
    now_ts: float,
    rescan_keyword: str,
) -> Tuple[bool, str]:
    category = (torrent.get("category") or "").strip().lower()
    tags = (torrent.get("tags") or "").strip().lower()

    if torrent_hash in inflight:
        return False, "in-flight"
    if rescan_keyword and (rescan_keyword in category or rescan_keyword in tags):
        return True, "manual-rescan"

    retry = retry_state.get(torrent_hash)
    if retry and now_ts >= float(retry.get("next_retry_at", 0.0)):
        return True, "retry"
    if torrent_hash not in seen:
        return True, "new"
    return False, "already-seen"
