"""Audit Laya's silent per-question truncation for the launcher's wire requests.

Run inside a separately configured Laya checkout/venv, using the SDK's own
build_sequence. Tokenizer only: no model, no GPU, no writes. Reads generated
bench/layla-wire-requests.json on stdin and prints JSON. The working directory
must contain models/laya and its typed-decisions/multilingual subdirectories.
Set USE_TF=0 and HF_HUB_OFFLINE=1 to require already-cached tokenizers.
"""
import json
import os
import sys

from laya.common import build_sequence, render_options, serialize_state
from transformers import AutoTokenizer

data = json.load(sys.stdin)
root = os.path.join(os.getcwd(), "models", "laya")
checkpoints = {"english": root, "typed-decisions": os.path.join(root, "typed-decisions"), "multilingual": os.path.join(root, "multilingual")}


def internal(q):
    crit = q.get("criteria")
    if q["type"] == "choice" and isinstance(crit, list):
        crit = {c: None for c in crit}
    return {"t": q["type"], "ins": q["instructions"], "crit": crit}


report = {}
for name, path in checkpoints.items():
    cfg = json.load(open(os.path.join(path, "rl_agent_config.json")))
    tok_dir = os.path.join(path, "tokenizer")
    tokenizer_source = tok_dir if os.path.isdir(tok_dir) else os.path.join(root, "tokenizer")
    tok = AutoTokenizer.from_pretrained(tokenizer_source)
    max_len, head = cfg["max_len"], cfg["head_max_len"]
    out = {"max_len": max_len, "head_max_len": head, "tokenizer": os.path.relpath(tokenizer_source, root)}
    if tokenizer_source != tok_dir:
        out["tokenizer_note"] = "checkpoint has no tokenizer dir; SDK falls back to cfg encoder %s, audited with root ModernBERT tokenizer" % cfg["encoder"]
    options = {}
    for qid, q in data["questions"].items():
        qi = internal(q)
        opts = render_options(qi)
        raw = [len(tok(" " + o, add_special_tokens=False)["input_ids"]) for o in opts]
        options[qid] = {"options": len(opts), "option_tokens_raw_max": max(raw), "option_tokens_sum": sum(raw) + len(opts)}
    out["options"] = options
    for key in ("state", "state_input_only"):
        truncated, worst, lengths, state_tokens = 0, 0, [], []
        for row in data["states"]:
            st = len(tok(serialize_state(row[key]), add_special_tokens=False)["input_ids"])
            state_tokens.append(st)
            for qid, q in data["questions"].items():
                ids, markers = build_sequence(tok, row[key], internal(q), max_len, head)
                lengths.append(len(ids))
                prefix = ids.index(tok.sep_token_id, markers[-1]) + 1
                kept = min(st, max(0, max_len - prefix - 1))
                if kept < st:
                    truncated += 1
                    worst = max(worst, st - kept)
        out[key] = {"sequences": len(lengths), "truncated_sequences": truncated, "max_tokens_dropped": worst,
                    "state_tokens_max": max(state_tokens), "state_tokens_min": min(state_tokens),
                    "sequence_len_max": max(lengths), "sequence_len_mean": round(sum(lengths) / len(lengths), 1)}
    report[name] = out
print(json.dumps(report, indent=2))
