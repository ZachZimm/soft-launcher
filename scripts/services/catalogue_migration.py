"""Migrate only four shipped defaults, preserving all custom TOML bytes."""
import hashlib
import re
import tomllib

OLD = {
    "draft_reply": {"id": "draft_reply", "description": "Draft a short reply to clipboard text using Bonsai",
        "exec": {"kind": "bonsai", "arg": "clipboard", "template": "reply"}},
    "summarise_clipboard": {"id": "summarise_clipboard", "description": "Summarise clipboard text using Bonsai",
        "exec": {"kind": "bonsai", "arg": "clipboard", "template": "summarise"}},
    "translate_clipboard": {"id": "translate_clipboard", "description": "Translate clipboard text into English using Bonsai",
        "exec": {"kind": "bonsai", "arg": "clipboard", "template": "translate"}},
    "clef_status": {"id": "clef_status", "description": "Show local Ollama model availability including clef-flash-q8:latest",
        "exec": {"kind": "builtin", "name": "clef_status", "arg": "none"}},
}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def sections(source):
    marks = list(re.finditer(r"(?m)^\[\[action\]\][ \t\r]*(?:#.*)?$", source))
    return [(m.start(), marks[i + 1].start() if i + 1 < len(marks) else len(source)) for i, m in enumerate(marks)]


def migrate(source, defaults):
    original = tomllib.loads(source)
    actions = original.get("action", [])
    if not isinstance(actions, list):
        raise ValueError("invalid installed catalogue")
    ids = {a.get("id") for a in actions}
    default_blocks = {}
    for start, end in sections(defaults):
        block = defaults[start:end]
        default_blocks[tomllib.loads(block)["action"][0]["id"]] = block.rstrip() + "\n\n"
    replacements = []
    for start, end in sections(source):
        block = source[start:end]
        parsed = tomllib.loads(block)
        entries = parsed.get("action", [])
        if len(entries) != 1 or set(parsed) != {"action"}:
            continue
        action = dict(entries[0])
        key = action.get("id")
        if key not in OLD:
            continue
        # Accept only optional flags with the old default meaning.
        needs_text = OLD[key]["exec"]["kind"] == "bonsai"
        for flag, expected in (("needs_text", needs_text), ("needsText", needs_text), ("requires_confirmation", False), ("requiresConfirmation", False)):
            if action.get(flag) is expected:
                action.pop(flag, None)
        if action != OLD[key]:
            continue
        target = "send_to_agent" if key == "draft_reply" else key
        if target != key and target in ids:
            continue
        # Comments between entries belong to the following custom entry as
        # often as the default above it. Keep that suffix byte for byte.
        lines = block.splitlines(keepends=True)
        trailing = []
        while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")):
            trailing.insert(0, lines.pop())
        replacement = default_blocks[target].rstrip() + "\n" + "".join(trailing)
        replacements.append((start, end, replacement, key))
    updated = source
    for start, end, block, _ in reversed(replacements):
        updated = updated[:start] + block + updated[end:]
    result = tomllib.loads(updated)
    result_ids = [a["id"] for a in result.get("action", [])]
    if len(result_ids) != len(set(result_ids)):
        raise ValueError("migration would duplicate action IDs")
    return updated, [item[3] for item in replacements]
