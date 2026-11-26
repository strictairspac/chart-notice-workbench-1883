"""Validation of a printing-office archive and of the receipt it carries.

An archive is submitted per printing office (source).  Every entry names the
old batch number this office used and carries the signed acceptance receipt:
the original erratum (勘误) and the original signature (签字).  The same old
number may be reused by another office, so it is only meaningful together with
the source.  Receipts already kept by this database are not restored here.
"""

import json
from datetime import date
from hashlib import sha256

MAX_ENTRIES = 500
MAX_OLD_NO = 48
MAX_TEXT = 140
SOURCE_RULE = "来源须为 1–48 个字符的印务台名称"

_MISSING = object()


def digest_of(value):
    return sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def _clean_text(value):
    return isinstance(value, str) and bool(value.strip())


def _erratum(raw):
    """Normalize the original erratum; a missing one leaves the entry incomplete."""
    if raw is _MISSING or raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("原勘误须为对象或留空")
    allowed = {"seq", "issue_date", "chart", "notice"}
    if not set(raw) <= allowed:
        raise ValueError("原勘误只能含 seq、issue_date、chart、notice")
    seq = raw.get("seq")
    if type(seq) is not int or not 1 <= seq <= 2147483647:
        raise ValueError("原勘误收录流水号须为正整数")
    issue_date = raw.get("issue_date")
    try:
        issue = date.fromisoformat(issue_date)
    except (ValueError, TypeError) as exc:
        raise ValueError("原勘误签发日须用 YYYY-MM-DD") from exc
    if issue.year < 1970 or issue.isoformat() != issue_date:
        raise ValueError("原勘误签发日须用 1970 年以后的 YYYY-MM-DD")
    for key, limit in (("chart", 24), ("notice", MAX_TEXT)):
        if not _clean_text(raw.get(key)) or len(raw[key]) > limit:
            raise ValueError(f"原勘误 {key} 须为不超过 {limit} 字符的内容")
    return {"seq": seq, "issue_date": issue.isoformat(), "chart": raw["chart"], "notice": raw["notice"]}


def _signature(raw):
    """Normalize the original signature; a missing one leaves the entry incomplete."""
    if raw is _MISSING or raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("原签字须为对象或留空")
    allowed = {"actor", "note", "created_at"}
    if not set(raw) <= allowed:
        raise ValueError("原签字只能含 actor、note、created_at")
    actor = raw.get("actor")
    if not _clean_text(actor) or len(actor) > 48:
        raise ValueError("原签字账号须为不超过 48 字符的内容")
    note = raw.get("note")
    if not _clean_text(note) or len(note) > MAX_TEXT * 3:
        raise ValueError("原签字意见须为不超过 420 字符的内容")
    created_at = raw.get("created_at", 0)
    if type(created_at) is not int or created_at < 0:
        raise ValueError("原签字时间须为非负整数秒")
    return {"actor": actor.strip(), "note": note.strip(), "created_at": created_at}


def entry_state(erratum, signature):
    """A receipt is restored only when both the erratum and signature are kept."""
    if erratum is None or signature is None:
        return "incomplete"
    return "restored"


def normalize_entry(raw):
    if not isinstance(raw, dict):
        raise ValueError("留档明细须为对象")
    if not set(raw) <= {"old_no", "erratum", "signature"}:
        raise ValueError("留档明细只能含 old_no、erratum、signature")
    old_no = str(raw.get("old_no", "")).strip()
    if not old_no or len(old_no) > MAX_OLD_NO:
        raise ValueError(f"旧批号须为 1–{MAX_OLD_NO} 字符的内容")
    erratum = _erratum(raw.get("erratum", _MISSING))
    signature = _signature(raw.get("signature", _MISSING))
    if erratum is None and signature is None:
        raise ValueError("留档明细须至少含原勘误或原签字")
    content = {
        "old_no": old_no, "erratum": erratum, "signature": signature,
        "state": entry_state(erratum, signature),
    }
    return {**content, "digest": digest_of({"erratum": erratum, "signature": signature})}


def normalize(payload):
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip() or len(source.strip()) > 48:
        raise ValueError(SOURCE_RULE)
    source = source.strip()
    archive_key = payload.get("archive_key")
    if archive_key is not None:
        archive_key = str(archive_key).strip()
        if not archive_key or len(archive_key) > 96:
            raise ValueError("留档标识须为 1–96 字符的内容")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_ENTRIES:
        raise ValueError(f"留档明细须为 1–{MAX_ENTRIES} 行的列表")
    normalized = []
    seen = set()
    for index, raw in enumerate(entries, start=1):
        try:
            entry = normalize_entry(raw)
        except ValueError as exc:
            raise ValueError(f"第 {index} 条留档：{exc}") from exc
        # A single archive speaks for one office, so repeated old numbers inside
        # it are the same number and must not disagree.
        if entry["old_no"] in seen:
            raise ValueError(f"第 {index} 条留档：同一留档内旧批号 {entry['old_no']} 重复")
        seen.add(entry["old_no"])
        normalized.append(entry)
    if archive_key is None:
        # Without an explicit archive key, identical content is the same archive;
        # retrying it must resume rather than restart.
        archive_key = "sha256:" + digest_of({"source": source, "entries": [
            {"old_no": entry["old_no"], "digest": entry["digest"]} for entry in normalized
        ]})
    return {
        "source": source, "archive_key": archive_key, "entries": normalized,
        "entries_digest": digest_of(normalized), "total": len(normalized),
    }
