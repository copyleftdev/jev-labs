"""Survey the aion-context corpora for Jev test-harness suitability.

We are looking for corpora that give us something rare: ground truth we did not
invent. A semantic-model harness is only as honest as its labels, and labels
authored by the tester measure the tester's opinion. These repositories carry
regulatory text with structural facts attached -- citation numbers, effective
dates, part/section hierarchy, cross-references written by legislators -- and
those facts are verifiable independently of anyone's reading.

This script does not test Jev. It measures what the corpora can support.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path("/tmp/aion_probe")

CFR_CITE = re.compile(r"\b(\d+)\s+C\.?F\.?R\.?\s*(?:Sec(?:tion)?s?\.?\s*)?§*\s*([\d.]+)", re.I)
USC_CITE = re.compile(r"\b(\d+)\s+U\.?S\.?C\.?\s*(?:Sec(?:tion)?s?\.?\s*)?§*\s*([\d.]+)", re.I)
TITLE34 = re.compile(r"Title\s+34\s+of\s+the\s+Code\s+of\s+Federal\s+Regulations", re.I)


def survey_selpa() -> dict:
    data = ROOT / "selpa-aion" / "data"
    files = sorted(data.glob("*.json"))
    paras, with_cfr, with_usc, lens = 0, 0, 0, []
    cited_cfr: Counter = Counter()
    sections_with_fed = set()
    examples = []

    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        for p in d.get("paragraphs", []):
            text = p.get("text", "")
            paras += 1
            lens.append(len(text))
            cfr = CFR_CITE.findall(text) + (
                [("34", "part")] if TITLE34.search(text) else []
            )
            usc = USC_CITE.findall(text)
            if cfr:
                with_cfr += 1
                sections_with_fed.add(f.stem)
                for t, s in cfr:
                    cited_cfr[f"{t} CFR {s}"] += 1
                if len(examples) < 4:
                    examples.append({"citation": p.get("citation"),
                                     "text": text[:200]})
            if usc:
                with_usc += 1

    return {
        "sections": len(files),
        "paragraphs": paras,
        "mean_paragraph_chars": round(statistics.mean(lens)) if lens else 0,
        "median_paragraph_chars": round(statistics.median(lens)) if lens else 0,
        "paragraphs_citing_cfr": with_cfr,
        "paragraphs_citing_usc": with_usc,
        "sections_citing_federal": len(sections_with_fed),
        "top_cfr_targets": cited_cfr.most_common(8),
        "examples": examples,
    }


def survey_doe() -> dict:
    data = ROOT / "aion-doe" / "data"
    parts = sorted(p for p in data.glob("part-*.json"))
    total_sections, lens = 0, []
    sample_sections = []

    for f in parts[:60]:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        secs = d.get("sections") or d.get("paragraphs") or []
        if isinstance(secs, list):
            total_sections += len(secs)
            for s in secs[:2]:
                t = s.get("text") or ""
                if t:
                    lens.append(len(t))
                    if len(sample_sections) < 3:
                        sample_sections.append({
                            "id": s.get("citation") or s.get("id") or f.stem,
                            "chars": len(t), "head": t[:160],
                        })

    ledger = data / "ledger.json"
    ledger_info = {}
    if ledger.exists():
        L = json.loads(ledger.read_text())
        if isinstance(L, list):
            ledger_info = {"entries": len(L), "sample": L[:2]}
        elif isinstance(L, dict):
            ledger_info = {"keys": list(L)[:10],
                           "entries": sum(len(v) for v in L.values()
                                          if isinstance(v, list))}

    return {
        "parts": len(parts),
        "sections_sampled": total_sections,
        "mean_section_chars": round(statistics.mean(lens)) if lens else 0,
        "amendment_ledger": ledger_info,
        "samples": sample_sections,
    }


def survey_fedramp() -> dict:
    data = ROOT / "fedramp-aion" / "data"
    out = {}
    rules_f = data / "rules.json"
    if rules_f.exists():
        R = json.loads(rules_f.read_text())
        out["rules_top_keys"] = list(R)[:12] if isinstance(R, dict) else "list"
        if isinstance(R, dict):
            for k, v in R.items():
                if isinstance(v, list):
                    out[f"count_{k}"] = len(v)
    kev_f = data / "kev.json"
    if kev_f.exists():
        K = json.loads(kev_f.read_text())
        if isinstance(K, dict):
            vulns = K.get("vulnerabilities", [])
            out["kev_cves"] = len(vulns)
            if vulns:
                out["kev_sample"] = {k: str(vulns[0].get(k))[:100]
                                     for k in list(vulns[0])[:7]}
    prov = data / "provenance.json"
    if prov.exists():
        P = json.loads(prov.read_text())
        out["provenance_keys"] = list(P)[:10]
    return out


if __name__ == "__main__":
    report = {
        "selpa": survey_selpa(),
        "doe": survey_doe(),
        "fedramp": survey_fedramp(),
    }
    print(json.dumps(report, indent=2, default=str)[:6000])
