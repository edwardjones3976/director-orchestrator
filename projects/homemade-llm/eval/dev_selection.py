import math
from fractions import Fraction
from typing import Any, Dict, List, Tuple, Sequence, Optional, Iterable

TARGET_FIELDS = ("non_goals", "constraints", "acceptance_checks", "evidence_requirements")


def evaluate_cell_counts(ref_items: List[str], cand_items: List[str]) -> Tuple[int, int, int]:
    """Literal multiset TP/FP/FN: no normalization, no folding, no dedup."""
    ref_counts: Dict[str, int] = {}
    for item in ref_items:
        ref_counts[item] = ref_counts.get(item, 0) + 1
    cand_counts: Dict[str, int] = {}
    for item in cand_items:
        cand_counts[item] = cand_counts.get(item, 0) + 1

    tp = 0
    for item, count in cand_counts.items():
        if item in ref_counts:
            tp += min(count, ref_counts[item])

    fp = len(cand_items) - tp
    fn = len(ref_items) - tp
    return tp, fp, fn


def _fraction_f1(tp: int, fp: int, fn: int) -> Fraction:
    if tp or fp or fn:
        return Fraction(2 * tp, 2 * tp + fp + fn)
    return Fraction(1)


def _is_valid_prediction(cand: Any) -> bool:
    if not isinstance(cand, dict):
        return False
    if set(cand.keys()) != set(TARGET_FIELDS):
        return False
    for field in TARGET_FIELDS:
        items = cand[field]
        if not isinstance(items, list):
            return False
        if not all(isinstance(x, str) for x in items):
            return False
    return True


def evaluate_primary_score(
    reference_rows: List[Dict[str, Any]],
    predictions: Dict[str, Any],
    frozen_roster: Sequence[str],
    schema_invalid_row_ids: Iterable[str] = (),
) -> Dict[str, Any]:
    invalid_ids = set(schema_invalid_row_ids)

    rows_by_group: Dict[str, List[Dict[str, Any]]] = {group: [] for group in frozen_roster}
    for row in reference_rows:
        sg = row.get("source_group")
        if sg in rows_by_group:
            rows_by_group[sg].append(row)

    cell_fractions: List[Fraction] = []
    field_tp = {field: 0 for field in TARGET_FIELDS}
    field_fp = {field: 0 for field in TARGET_FIELDS}
    field_fn = {field: 0 for field in TARGET_FIELDS}
    incomplete_groups: List[str] = []

    for group in frozen_roster:
        packets = rows_by_group[group]
        broken = not packets
        if not broken:
            for row in packets:
                row_id = row.get("row_id")
                if row_id not in predictions or row_id in invalid_ids:
                    broken = True
                    break
                if not _is_valid_prediction(predictions[row_id]):
                    broken = True
                    break

        if broken:
            incomplete_groups.append(group)
            cell_fractions.extend(Fraction(0) for _ in TARGET_FIELDS)
            continue

        for field in TARGET_FIELDS:
            ref_items: List[str] = []
            cand_items: List[str] = []
            for row in packets:
                ref_items.extend(row.get("target", {}).get(field, []))
                cand_items.extend(predictions[row["row_id"]][field])
            tp, fp, fn = evaluate_cell_counts(ref_items, cand_items)
            cell_fractions.append(_fraction_f1(tp, fp, fn))
            field_tp[field] += tp
            field_fp[field] += fp
            field_fn[field] += fn

    denom = 4 * len(frozen_roster)
    if denom:
        primary_fraction = sum(cell_fractions, Fraction(0)) / denom
    else:
        primary_fraction = Fraction(0)

    total_tp = sum(field_tp.values())
    total_fp = sum(field_fp.values())
    total_fn = sum(field_fn.values())
    micro_fraction = _fraction_f1(total_tp, total_fp, total_fn)

    per_field = {
        field: float(_fraction_f1(field_tp[field], field_fp[field], field_fn[field]))
        for field in TARGET_FIELDS
    }

    return {
        "primary": float(primary_fraction),
        "micro": float(micro_fraction),
        "per_field": per_field,
        "incomplete": bool(incomplete_groups),
        "incomplete_groups": incomplete_groups,
        "groups": len(frozen_roster),
    }


def select_checkpoint(checkpoints: List[Tuple[str, int, Any]]) -> str:
    if not checkpoints:
        raise ValueError("select_checkpoint requires at least one checkpoint")

    seen_orders = set()
    parsed: List[Tuple[str, int, Fraction]] = []
    for name, order, score in checkpoints:
        if order in seen_orders:
            raise ValueError(f"duplicate checkpoint order: {order!r}")
        seen_orders.add(order)
        try:
            frac_score = Fraction(score)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"invalid score for checkpoint {name!r}: {score!r}") from exc
        parsed.append((name, order, frac_score))

    best_name, best_order, best_score = parsed[0]
    for name, order, frac_score in parsed[1:]:
        if frac_score > best_score or (frac_score == best_score and order < best_order):
            best_name, best_order, best_score = name, order, frac_score
    return best_name


def _exact_p_fraction(b: int, c: int) -> Fraction:
    n = b + c
    if n == 0:
        return Fraction(1)
    min_bc = min(b, c)
    numerator = 2 * sum(math.comb(n, i) for i in range(min_bc + 1))
    p = Fraction(numerator, 2 ** n)
    return p if p < Fraction(1) else Fraction(1)


def evaluate_confirmation(b: int, c: int) -> Dict[str, Any]:
    margin = b - c
    p_fraction = _exact_p_fraction(b, c)
    passes = margin >= 6 and p_fraction <= Fraction(1, 20)
    return {
        "b": b,
        "c": c,
        "margin": margin,
        "p_value": float(p_fraction),
        "passes": passes,
    }


def _group_arm_status(packets: Optional[List[Optional[Dict[str, Any]]]]) -> Tuple[bool, bool]:
    """Returns (passes, complete) for a single group's packet list in one arm."""
    if not packets:
        return False, False
    if any(p is None for p in packets):
        return False, False
    all_accepted = all(
        p.get("schema_valid") and p.get("grok_as_is") and p.get("zai_as_is")
        for p in packets
    )
    return all_accepted, True


def whole_group_counts(
    frozen_roster: Sequence[str],
    before: Dict[str, List[Optional[Dict[str, Any]]]],
    after: Dict[str, List[Optional[Dict[str, Any]]]],
) -> Dict[str, Any]:
    b = 0
    c = 0
    complete = True
    group_results: List[Dict[str, Any]] = []

    for group in frozen_roster:
        before_pass, before_complete = _group_arm_status(before.get(group))
        after_pass, after_complete = _group_arm_status(after.get(group))
        if not (before_complete and after_complete):
            complete = False
        if after_pass and not before_pass:
            b += 1
        if before_pass and not after_pass:
            c += 1
        group_results.append({"group": group, "before": before_pass, "after": after_pass})

    return {
        "b": b,
        "c": c,
        "groups": len(frozen_roster),
        "complete": complete,
        "group_results": group_results,
    }


def confirm_whole_groups(
    frozen_roster: Sequence[str],
    before: Dict[str, List[Optional[Dict[str, Any]]]],
    after: Dict[str, List[Optional[Dict[str, Any]]]],
) -> Dict[str, Any]:
    counts = whole_group_counts(frozen_roster, before, after)
    confirmation = evaluate_confirmation(counts["b"], counts["c"])
    result: Dict[str, Any] = dict(counts)
    result.update(confirmation)
    if not counts["complete"]:
        result["passes"] = False
    return result
