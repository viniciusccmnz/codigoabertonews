"""Bancos locais curados: curiosidades (data/facts.json) e teorias (data/theories.json)."""
from __future__ import annotations

import json

from ..config import DATA
from ..topic import Topic
from ..util.text import entities


def load_topics() -> list[Topic]:
    data = json.loads((DATA / "facts.json").read_text("utf-8"))
    out = []
    for f in data["facts"]:
        ents = []
        for s in [f["hook"]] + f["body"]:
            for e in entities(s):
                if e not in ents:
                    ents.append(e)
        out.append(Topic(
            id="fact-" + f["id"], theme="curiosity", title=f["hook"], sentences=list(f["body"]), subject=f["subject"],
            entities=ents[:6], image_queries=list(f.get("image_queries", [])), source_name=f.get("source", ""),
            year=f.get("year"), extra={"hook": f["hook"], "context": f.get("context", ""), "takeaway": f.get("takeaway", "")},
        ))
    return out


def load_theories() -> list[Topic]:
    data = json.loads((DATA / "theories.json").read_text("utf-8"))
    out = []
    for t in data["theories"]:
        ents = []
        for s in [t["hook"]] + t["facts"]:
            for e in entities(s):
                if e not in ents:
                    ents.append(e)
        out.append(Topic(
            id="theory-" + t["id"], theme="theory", title=t["hook"], sentences=list(t["facts"]), subject=t["subject"],
            entities=ents[:6], image_queries=list(t.get("image_queries", [])), source_name=t.get("source", ""),
            extra={"hook": t["hook"], "origin": t.get("origin", ""), "claims": list(t.get("claims", [])), "verdict": t.get("verdict", "")},
        ))
    return out
