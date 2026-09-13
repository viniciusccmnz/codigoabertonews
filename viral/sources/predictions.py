"""Previsoes com base em dados: ajuste de curva sobre series historicas.

Sem oraculo: regressao linear ou exponencial (em log) sobre os pontos de
data/trends.json, projetada para o ano alvo. O video sempre diz que e projecao.
"""
from __future__ import annotations

import json

import numpy as np

from ..config import DATA
from ..topic import Topic
from ..util.text import humanize_number


def project(series: list[list[float]], model: str, year: float, cap: float | None = None) -> float:
    xs = np.array([p[0] for p in series], dtype=np.float64)
    ys = np.array([p[1] for p in series], dtype=np.float64)
    if model == "exp" and np.all(ys > 0):
        b, a = np.polyfit(xs, np.log(ys), 1)
        v = float(np.exp(a + b * year))
    else:
        k = min(len(xs), 5)  # tendencia recente pesa mais
        b, a = np.polyfit(xs[-k:], ys[-k:], 1)
        v = float(a + b * year)
    if cap is not None:
        v = min(v, cap)
    return max(v, 0.0)


def _fmt(v: float, decimals: int | None) -> str:
    if decimals is not None and v < 1000:
        if 0 < v < 10 ** (-decimals):
            floor = f"{10 ** (-decimals):.{decimals}f}".replace(".", ",")
            return f"menos de {floor}"
        s = f"{v:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return s.rstrip("0").rstrip(",") if "," in s else s
    return humanize_number(v)


def _times(ratio: float) -> str:
    if ratio >= 1000:
        return humanize_number(ratio) + " de vezes"
    return f"{int(round(ratio))} vezes"


def load_topics(current_year: int) -> list[Topic]:
    data = json.loads((DATA / "trends.json").read_text("utf-8"))
    out = []
    for s in data["series"]:
        ser = s["series"]
        target = int(s.get("project_to", current_year + 4))
        if target <= current_year:
            target = current_year + 4
        proj = project(ser, s.get("model", "linear"), target, s.get("cap"))
        dec = s.get("decimals")
        first, last = ser[0], ser[-1]
        mid = ser[len(ser) // 2] if len(ser) >= 3 else None
        pts = [first] + ([mid] if mid and mid is not first and mid is not last else []) + [last]
        sentences = [s["point"].format(year=int(p[0]), value=_fmt(p[1], dec)) for p in pts]
        span = int(last[0]) - int(first[0])
        pct = (last[1] - first[1]) / first[1] * 100 if first[1] else 0
        if abs(pct) >= 1000:
            growth = f"Isso é {_times(last[1] / first[1])} mais em {span} anos." if pct > 0 else f"Isso é uma queda de {abs(pct):.0f}% em {span} anos."
        else:
            growth = (f"Isso é um crescimento de {pct:.0f}% em {span} anos." if pct > 0 else f"Isso é uma queda de {abs(pct):.0f}% em {span} anos.")
        sentences.append(growth)
        projection = s["projection"].format(year=target, value=_fmt(proj, dec))
        out.append(Topic(
            id="trend-" + s["id"], theme="prediction", title=f"{s['subject']} em {target}", sentences=sentences, subject=s["subject"],
            entities=[], image_queries=list(s.get("image_queries", [])), source_name=s.get("source_short", ""),
            year=target, extra={
                "source_spoken": s["source"], "projection_line": projection, "target_year": target,
                "chart": {"series": [[float(x), float(y)] for x, y in ser], "projection": [[float(target), proj]],
                          "unit": s.get("unit", ""), "title": s.get("label", "")},
                "projected_value": proj, "projected_text": _fmt(proj, dec), "unit_spoken": s.get("unit_spoken", ""),
                "points": [(int(p[0]), (_fmt(p[1], dec) + " " + s.get("unit_spoken", "")).strip()) for p in pts],
                "context": s.get("context", ""), "implication": s.get("implication", ""), "counter": s.get("counter", ""),
                "last_year": int(last[0]), "last_value_text": _fmt(last[1], dec),
            },
        ))
    return out
