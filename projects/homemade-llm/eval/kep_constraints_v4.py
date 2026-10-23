"""Frozen WO-C1 A2 scoped-unit selection and v3 allocation, without corpus I/O.

Behaviour ported from v4a2_funnel.py, SHA-256
538b22adb847484b331d9e7073c0dd9b8691fd43efe6d1f1d166f54cc2a7d718.
Selection is lexical evidence, not semantic grading or token admission.
"""
import re

import prepare_kep_inputs as pk

SOURCE_SHA256 = "538b22adb847484b331d9e7073c0dd9b8691fd43efe6d1f1d166f54cc2a7d718"

I = re.IGNORECASE
REQ = re.compile(r"\b(must|shall|required|requires?|requiring|needs? to be|has to|have to|only|cannot|can ?not|can't|unsupported|"
                 r"not (?:be )?supported|does not support|do not support|(?:is|are) not allowed|never|prerequisites?|depends on|"
                 r"at most|at least|no more than|no fewer than|no less than|limited to|up to \d|maximum of|minimum of)\b", I)
RISK = re.compile(r"\b(risks?|mitigat\w*|might|may|could|possibl\w*|potential\w*|likel\w*|unlikely|in case)\b", I)
PROSPECTIVE = re.compile(r"\b(will|won't|would|should|shouldn't|going to|plan(?:s|ned)? to|intend(?:s|ed)? to|recommend\w*|suggest\w*|"
                         r"consider\w*|careful|ideally|preferabl[ey]|encourag\w*|in the future|eventually|TODO|TBD)\b", I)
BULLET = re.compile(r"^( *)(?:[-*+]|\d+[.)])[ \t]+")
CHECKBOX = re.compile(r"^\s*[-*+]\s+\[[ xX]\]")
CONSTRAINTS = "Notes/Constraints/Caveats"
MIN_CHARS, MAX_CHARS = 30, 600


def headings(text):
    out = []
    for offset, visible, fenced in pk.visible_lines(text):
        m = None if fenced else re.match(r"^ {0,3}(#{1,6})[ \t]+(.*)$", visible.rstrip("\r\n"))
        if m:
            out.append((len(m[1]), pk.heading_name(re.sub(r"[ \t]+#+[ \t]*$", "", m[2]).strip()), offset, offset + len(visible)))
    return out


def lead_source(heads, name):
    """The heading line plus LEAD text (up to the first subheading or next heading) of a uniquely named heading, as a section dict."""
    hits = [i for i, h in enumerate(heads) if h[1] == name]
    if len(hits) != 1:
        return None
    i = hits[0]
    return {"start": heads[i][2], "body_start": heads[i][3], "end": heads[i + 1][2] if i + 1 < len(heads) else None}


def _blocks(text, lines, removed, start, end):
    """Consecutive content lines between boundaries (blank, heading, fence, template line, table row, checkbox, comment-only)."""
    blocks, cur = [], []
    for idx, ln in enumerate(lines):
        if ln["start"] < start or ln["start"] >= end:
            continue
        vis, raw = ln["visible"], ln["raw"]
        boundary = (ln["fenced"] or not vis.strip() or re.match(r"^ {0,3}#", vis) or removed[idx]
                    or vis.lstrip().startswith("|") or CHECKBOX.match(vis))
        if boundary:
            if cur:
                blocks.append(cur)
            cur = []
            if not ln["fenced"] and re.match(r"^ {0,3}#", vis):
                blocks.append(None)  # a subheading: no lead-in scope crosses it
            continue
        cur.append(dict(ln, tainted=raw.rstrip() != vis.rstrip()))
    if cur:
        blocks.append(cur)
    return blocks


def _span(recs):
    return {"start": recs[0]["start"], "end": recs[-1]["end"]}


def region_units(text, lines, removed, source):
    """Candidate units of one section body, each {'spans': [scope..., item], 'item': span, 'recs': [...]} in source order."""
    end = source["end"] if source["end"] is not None else len(text)
    units, prev, prev_recs = [], None, []
    for block in _blocks(text, lines, removed, source["body_start"], end):
        if block is None:
            prev, prev_recs = None, []
            continue
        first_bullet = next((i for i, r in enumerate(block) if BULLET.match(r["raw"])), None)
        lead = None
        if first_bullet is None:
            scope = [prev] if prev is not None and text[prev["start"]:prev["end"]].rstrip().endswith(":") else []
            units.append({"scope": scope, "item": _span(block), "recs": block, "scope_recs": prev_recs if scope else []})
        else:
            if first_bullet > 0:
                lead_recs = block[:first_bullet]
                lead = (_span(lead_recs), lead_recs)
            elif prev is not None and text[prev["start"]:prev["end"]].rstrip().endswith(":"):
                lead = (prev, prev_recs)
            items, stack = [], []  # stack of (indent, item index)
            for r in block[first_bullet:]:
                m = BULLET.match(r["raw"])
                if m:
                    ind = len(m[1])
                    while stack and stack[-1][0] >= ind:
                        stack.pop()
                    items.append({"recs": [r], "parent": stack[-1][1] if stack else None, "children": []})
                    if stack:
                        items[stack[-1][1]]["children"].append(len(items) - 1)
                    stack.append((ind, len(items) - 1))
                else:
                    items[-1]["recs"].append(r)  # continuation (incl. lazy) belongs to the most recent item
            for k, it in enumerate(items):
                sub, j = list(it["recs"]), k + 1
                while j < len(items):  # whole subtree = following items whose ancestor chain reaches k
                    a = items[j]["parent"]
                    while a is not None and a != k:
                        a = items[a]["parent"]
                    if a != k:
                        break
                    sub += items[j]["recs"]; j += 1
                chain, a = [], it["parent"]
                while a is not None:
                    chain.insert(0, items[a]); a = items[a]["parent"]
                scope = ([lead[0]] if lead else []) + [_span(c["recs"]) for c in chain]
                scope_recs = (lead[1] if lead else []) + [r for c in chain for r in c["recs"]]
                units.append({"scope": scope, "item": _span(sub), "recs": sub, "scope_recs": scope_recs})
        prev, prev_recs = _span(block), block
    for u in units:
        u["spans"] = u["scope"] + [u["item"]]
        u["text"] = "".join(text[s["start"]:s["end"]] for s in u["spans"])
    return units


def classify(text, u):
    item_vis = " ".join(r["visible"] for r in u["recs"])
    all_vis = " ".join(r["visible"] for r in u["scope_recs"] + u["recs"])
    if not REQ.search(item_vis) or RISK.search(all_vis) or PROSPECTIVE.search(all_vis) or item_vis.rstrip().endswith("?"):
        return "NOT_LEXICAL"
    if any(r["tainted"] for r in u["scope_recs"] + u["recs"]):
        return "COMMENT_IN_UNIT"
    if pk._spans_overlap(u["spans"]):
        return "SCOPE_OVERLAP"
    n = len(u["text"])
    return "TOO_SHORT" if n < MIN_CHARS else ("TOO_LONG" if n > MAX_CHARS else "OK")


def analyse(text, template_lines):
    """Funnel stages for one README: returns {'stage': 'L'|'W'|'B' reached or refusal code, ...}."""
    try:
        title_span, sections, alias = pk.source_sections_v3(text)
    except pk.Refusal as exc:
        return {"stage": "REFUSE_SOURCE", "role": "source", "reason": str(exc)}
    lines = pk._document_lines(text)
    removed = [bool(r["raw"].strip()) and r["raw"] in template_lines for r in lines]
    heads = headings(text)
    tier1 = []
    if CONSTRAINTS in sections and pk.visibly_nonempty(text[sections[CONSTRAINTS]["body_start"]:sections[CONSTRAINTS]["end"]]):
        tier1 = [(sections[CONSTRAINTS], u) for u in region_units(text, lines, removed, sections[CONSTRAINTS])]
    tier2 = []
    for src in ([alias[CONSTRAINTS]] if CONSTRAINTS in alias else []) + [s for s in (lead_source(heads, "proposal"),
                                                                                    lead_source(heads, "design details")) if s]:
        tier2 += [(src, u) for u in region_units(text, lines, removed, src)]
    tier2.sort(key=lambda su: su[1]["item"]["start"])
    res = {}
    for name, cands in (("tier1", tier1), ("tier2", tier2)):
        res[name] = [(s, u, classify(text, u)) for s, u in cands]
    chosen_tier = next((t for t in ("tier1", "tier2") if any(c != "NOT_LEXICAL" for _, _, c in res[t])), None)
    out = {"lexical_units": sum(c != "NOT_LEXICAL" for t in res for _, _, c in res[t]),
           "whole_units": sum(c == "OK" for t in res for _, _, c in res[t]),
           "unit_refusals": sorted({c for t in res for _, _, c in res[t] if c not in ("OK", "NOT_LEXICAL")})}
    if chosen_tier is None:
        return dict(out, stage="REFUSE_NO_LEXICAL_UNIT", role=CONSTRAINTS, reason="NO_LEXICAL_UNIT")
    ok = [(s, u) for s, u, c in res[chosen_tier] if c == "OK"]
    out["tier"] = chosen_tier
    if not ok:
        return dict(out, stage="L_ONLY", role=CONSTRAINTS, reason="NO_WHOLE_SOURCE_BOUND_UNIT")
    # --- the constructor's v3 allocation (lines 1039-1127 of 3b53e7f2), constraints role replaced by the units above ---
    role = "source"
    try:
        chosen = {}
        for role in pk.SUPPORT_SECTIONS:
            if role == CONSTRAINTS:
                continue
            canonical, al = sections.get(role), alias.get(role)
            if canonical is not None and pk.visibly_nonempty(text[canonical["body_start"]:canonical["end"]]):
                chosen[role] = canonical
            elif al is not None and pk.visibly_nonempty(text[al["body_start"]:al["end"]]):
                chosen[role] = al
            else:
                raise pk.Refusal("MISSING_OR_EMPTY_SUPPORT_SECTION")
        role = "title"
        if title_span is None:
            raise pk.Refusal("INPUT_TITLE_MISSING")
        role = "objective"
        if "Goals" not in sections or not pk.visibly_nonempty(text[sections["Goals"]["body_start"]:sections["Goals"]["end"]]):
            raise pk.Refusal("INPUT_GOALS_MISSING")
        goals = {"start": sections["Goals"]["body_start"], "end": sections["Goals"]["end"]}
        objective = None
        for paragraph in pk._source_paragraphs(lines, goals["start"], goals["end"]):
            runs = [r for r in pk._surviving_spans(lines, removed, paragraph["start"], paragraph["end"])
                    if pk.visibly_nonempty(text[r["start"]:r["end"]])]
            if runs:
                objective = (paragraph, runs)
                break
        if objective is None:
            raise pk.Refusal("NO_WHOLE_PARAGRAPH_WITHIN_BUDGET")
        paragraph, runs = objective
        if len(runs) > 1 or runs[0]["start"] != paragraph["start"] or runs[0]["end"] != paragraph["end"]:
            raise pk.Refusal("OBJECTIVE_SPAN_NOT_CONTIGUOUS")
        objective_span = pk.trim_span(text, runs[0])
        minima, rests = {}, {}
        for role in pk.SUPPORT_SECTIONS:
            if role == CONSTRAINTS:
                # v2: each unit is atomic; its section heading is emitted once, before its first unit. A span an earlier unit already emits
                # (a shared lead-in, or a parent subtree that contains this child) is not emitted again; a unit with nothing new is
                # dropped; a unit that would PARTIALLY overlap emitted text is skipped whole (never clipped). v1 emitted shared spans twice,
                # which the constructor's own CONTEXT_SPAN_OVERLAP check refused (6 groups).
                groups, seen, emitted = [], set(), []
                for src, u in ok:
                    head = (src["start"], src["body_start"])
                    spans = [s for s in u["spans"] if not any(e["start"] <= s["start"] and s["end"] <= e["end"] for e in emitted)]
                    if not spans or pk._spans_overlap(spans + emitted):
                        continue
                    groups.append(([{"start": head[0], "end": head[1]}] if head not in seen else []) + spans)
                    seen.add(head)
                    emitted += spans
                minima[role], rests[role] = groups[0], groups[1:]
                continue
            qualifying = pk._qualifying_paragraph_runs(text, lines, removed, chosen[role])
            if not qualifying:
                raise pk.Refusal("EMPTY_AFTER_TEMPLATE_REMOVAL")
            minima[role] = [{"start": chosen[role]["start"], "end": chosen[role]["body_start"]}] + qualifying[0]
            rests[role] = qualifying[1:]
        role = "allocation"
        used = pk._span_cost(text, objective_span) + sum(pk._span_cost(text, i) for role in pk.SUPPORT_SECTIONS for i in minima[role])
        if used > pk.V3_MANDATORY_BUDGET:
            raise pk.Refusal("MANDATORY_MINIMA_OVER_BUDGET")
        selected = {role: list(minima[role]) for role in pk.SUPPORT_SECTIONS}
        emitted = 1
        for role in pk.SUPPORT_SECTIONS:
            for group in rests[role]:
                cost = sum(pk._span_cost(text, r) for r in group)
                if used + cost > pk.V3_MANDATORY_BUDGET:
                    break
                used += cost
                selected[role].extend(group)
                emitted += role == CONSTRAINTS
        items = [i for role in pk.SUPPORT_SECTIONS for i in selected[role]]
        optional_used = {}
        for role in ("Summary", "Motivation"):
            source = sections.get(role)
            if source is None or not pk.visibly_nonempty(text[source["body_start"]:source["end"]]):
                continue
            got = pk._select_budgeted_items(text, lines, removed, source, pk.V3_OPTIONAL_BUDGETS[role])
            optional_used[role] = sum(pk._span_cost(text, i) for i in got)
            items.extend(got)
        role = "allocation"
        if pk._spans_overlap([objective_span] + items):
            raise pk.Refusal("CONTEXT_SPAN_OVERLAP")
        mandatory_total = len(text[objective_span["start"]:objective_span["end"]]) + sum(
            len(text[i["start"]:i["end"]]) for role in pk.SUPPORT_SECTIONS for i in selected[role])
        if (mandatory_total != used or mandatory_total > pk.V3_MANDATORY_BUDGET
                or any(optional_used[r] > pk.V3_OPTIONAL_BUDGETS[r] for r in optional_used)
                or mandatory_total + sum(optional_used.values()) > pk.V3_TOTAL_BUDGET
                or len({(i["start"], i["end"]) for i in items}) != len(items)):
            raise pk.Refusal("ALLOCATION_ACCOUNTING_MISMATCH")
    except pk.Refusal as e:
        return dict(out, stage="W_ONLY", allocation_refusal=str(e), role=role, reason=str(e))
    # minima[CONSTRAINTS][0] is always the first unit's own section heading; the rest are that unit's exact source spans.
    return dict(out, stage="B", title_span=title_span, objective_span=objective_span,
                context_spans=sorted(items, key=lambda span: span["start"]),
                constraints_units_emitted=emitted, mandatory_chars=mandatory_total,
                total_chars=mandatory_total + sum(optional_used.values()), first_unit_spans=minima[CONSTRAINTS][1:],
                first_unit_text="".join(text[s["start"]:s["end"]] for s in minima[CONSTRAINTS][1:]))
