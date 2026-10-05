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


def _ids_match_expected(ids: List[Any], expected: Sequence[str]) -> bool:
    """Exact roster match: same length, no duplicates, no substitutions."""
    if len(set(ids)) != len(ids):
        return False
    return sorted(ids) == sorted(expected)


def _manifest_is_well_formed(
    expected_packets: Any,
    frozen_roster: Sequence[str],
) -> bool:
    """True only for a *given* (non-None) manifest that exactly binds every
    roster group to a non-empty, duplicate-free list of non-empty str packet
    ids, with no missing/extra group keys. The frozen roster itself must be
    non-empty and duplicate-free for an exact key match to mean anything."""
    if not isinstance(expected_packets, dict):
        return False
    roster_list = list(frozen_roster)
    if not roster_list or len(set(roster_list)) != len(roster_list):
        return False
    if set(expected_packets.keys()) != set(roster_list):
        return False
    for group in roster_list:
        ids = expected_packets[group]
        if not isinstance(ids, list) or not ids:
            return False
        if not all(isinstance(pid, str) and pid for pid in ids):
            return False
        if len(set(ids)) != len(ids):
            return False
    return True


def _roster_is_bound(
    expected_packets: Optional[Dict[str, Sequence[str]]],
    frozen_roster: Sequence[str],
) -> bool:
    """A roster is bound only by a fully valid manifest (see
    `_manifest_is_well_formed`); an omitted (`None`) manifest is a diagnostic
    call, and a given-but-malformed manifest is unbound, never both."""
    if expected_packets is None:
        return False
    return _manifest_is_well_formed(expected_packets, frozen_roster)


def evaluate_primary_score(
    reference_rows: List[Dict[str, Any]],
    predictions: Dict[str, Any],
    frozen_roster: Sequence[str],
    schema_invalid_row_ids: Iterable[str] = (),
    expected_packets: Optional[Dict[str, Sequence[str]]] = None,
) -> Dict[str, Any]:
    invalid_ids = set(schema_invalid_row_ids)

    rows_by_group: Dict[str, List[Dict[str, Any]]] = {group: [] for group in frozen_roster}
    malformed_row = False
    for row in reference_rows:
        if not isinstance(row, dict):
            malformed_row = True
            continue
        sg = row.get("source_group")
        if sg in rows_by_group:
            rows_by_group[sg].append(row)

    diagnostic_only = expected_packets is None
    roster_bound = _roster_is_bound(expected_packets, frozen_roster)
    # Given (non-None) but not fully valid: the manifest cannot be trusted at
    # all, so every roster group is forced incomplete rather than partially
    # trusting whichever entries happen to look valid.
    manifest_malformed = expected_packets is not None and not roster_bound

    cell_fractions: List[Fraction] = []
    field_tp = {field: 0 for field in TARGET_FIELDS}
    field_fp = {field: 0 for field in TARGET_FIELDS}
    field_fn = {field: 0 for field in TARGET_FIELDS}
    incomplete_groups: List[str] = []

    for group in frozen_roster:
        packets = rows_by_group[group]
        if manifest_malformed or malformed_row:
            broken = True
        else:
            broken = not packets
            if not broken and roster_bound:
                ref_ids = [row.get("row_id") for row in packets]
                if not _ids_match_expected(ref_ids, expected_packets[group]):
                    broken = True
        if not broken:
            for row in packets:
                row_id = row.get("row_id")
                if row_id not in predictions or row_id in invalid_ids:
                    broken = True
                    break
                if (not _is_valid_prediction(row.get("target"))
                        or not _is_valid_prediction(predictions[row_id])):
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
                ref_items.extend(row["target"][field])
                cand_items.extend(predictions[row["row_id"]][field])
            tp, fp, fn = evaluate_cell_counts(ref_items, cand_items)
            cell_fractions.append(_fraction_f1(tp, fp, fn))
            field_tp[field] += tp
            field_fp[field] += fp
            field_fn[field] += fn

    denom = 4 * len(frozen_roster)
    if denom:
        primary_frac = sum(cell_fractions, Fraction(0)) / denom
    else:
        primary_frac = Fraction(0)

    total_tp = sum(field_tp.values())
    total_fp = sum(field_fp.values())
    total_fn = sum(field_fn.values())
    micro_fraction = _fraction_f1(total_tp, total_fp, total_fn)

    per_field = {}
    per_field_exact = {}
    for field in TARGET_FIELDS:
        f = _fraction_f1(field_tp[field], field_fp[field], field_fn[field])
        per_field[field] = float(f)
        per_field_exact[field] = {"numerator": f.numerator, "denominator": f.denominator}

    # Without a fully bound manifest, completeness can never be certified,
    # regardless of whether any individual group looked complete.
    incomplete = (not roster_bound) or bool(incomplete_groups)

    return {
        "primary": float(primary_frac),
        "primary_exact": {
            "numerator": primary_frac.numerator,
            "denominator": primary_frac.denominator,
        },
        "micro": float(micro_fraction),
        "per_field": per_field,
        "per_field_exact": per_field_exact,
        "incomplete": incomplete,
        "incomplete_groups": incomplete_groups,
        "groups": len(frozen_roster),
        "roster_bound": roster_bound,
        "diagnostic_only": diagnostic_only,
    }


def is_selectable(result: Dict[str, Any]) -> bool:
    """True only if `result` is roster-bound, not diagnostic-only, and has no
    incompleteness: for a primary result (one with `primary_exact`) that
    means `incomplete` is False; for a whole-group
    (`whole_group_counts`/`confirm_whole_groups`) result (one with
    `group_results`) that means `evidence_complete` is True. An omitted or
    malformed manifest (`roster_bound` False) or a diagnostic-only call is
    never selectable, and neither is a result of neither kind. Every status
    flag must be the literal boolean: a missing key or a stand-in like
    "yes"/1/0/[] never qualifies."""
    if not isinstance(result, dict) or result.get("roster_bound") is not True:
        return False
    if result.get("diagnostic_only") is not False:
        return False
    if "primary_exact" in result:
        return result.get("incomplete") is False
    if "group_results" in result:
        return result.get("evidence_complete") is True
    return False


def exact_score(pair: Any, summary: Any, label: str) -> Fraction:
    """Validate canonical score evidence shared by primary and per-field gates."""
    if not isinstance(pair, dict) or set(pair) != {"numerator", "denominator"}:
        raise ValueError("malformed exact " + label)
    numerator, denominator = pair["numerator"], pair["denominator"]
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise ValueError("invalid exact " + label)
    value = Fraction(numerator, denominator)
    if value < 0 or value > 1 or (value.numerator, value.denominator) != (numerator, denominator):
        raise ValueError("out of range or unreduced " + label)
    if (type(summary) not in (int, float) or not 0 <= summary <= 1
            or not math.isfinite(summary) or abs(float(value) - summary) > 1e-12):
        raise ValueError("invalid or inconsistent summary " + label)
    return value


def primary_fraction(result: Dict[str, Any]) -> Fraction:
    """Exact primary-score Fraction for a selectable primary result. Raises
    `ValueError` for anything not selectable (unbound/malformed/omitted
    manifest, diagnostic-only call, or any remaining incompleteness). Later
    slices must only feed `select_checkpoint` scores obtained from this
    function (or, for non-selectable-gated callers, plain Fraction/int/float
    values); it does not itself call `select_checkpoint`."""
    if not is_selectable(result):
        raise ValueError(
            "primary_fraction requires a selectable result: roster_bound "
            "must be True, diagnostic_only must be False, and the result "
            "must be complete"
        )
    return exact_score(result.get("primary_exact"), result.get("primary"), "primary")


def select_checkpoint(checkpoints: List[Tuple[str, int, Any]]) -> str:
    """Selects the best-scoring checkpoint. Accepts plain Fraction/int/float
    scores unchanged; later slices must only feed this `primary_fraction`
    values (never a raw, unvalidated `result["primary"]`/`primary_exact`)."""
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


_FLAG_KEYS = ("schema_valid", "grok_as_is", "zai_as_is")


def _packet_flags_real(packet: Any) -> bool:
    return isinstance(packet, dict) and all(type(packet.get(key)) is bool for key in _FLAG_KEYS)


def _group_arm_status(
    packets: Optional[List[Optional[Dict[str, Any]]]],
    expected: Optional[Sequence[str]] = None,
) -> Tuple[bool, bool]:
    """Returns (passes, complete) for a single group's packet list in one arm."""
    if not packets:
        return False, False
    if any(p is None for p in packets):
        return False, False
    if any(not _packet_flags_real(p) for p in packets):
        return False, False
    if expected is not None:
        ids = []
        for p in packets:
            packet_id = p.get("packet_id")
            if not isinstance(packet_id, str):
                return False, False
            ids.append(packet_id)
        if not _ids_match_expected(ids, expected):
            return False, False
    all_accepted = all(
        p["schema_valid"] and p["grok_as_is"] and p["zai_as_is"]
        for p in packets
    )
    return all_accepted, True


def whole_group_counts(
    frozen_roster: Sequence[str],
    before: Dict[str, List[Optional[Dict[str, Any]]]],
    after: Dict[str, List[Optional[Dict[str, Any]]]],
    expected_packets: Optional[Dict[str, Sequence[str]]] = None,
) -> Dict[str, Any]:
    diagnostic_only = expected_packets is None
    roster_bound = _roster_is_bound(expected_packets, frozen_roster)
    # Given (non-None) but not fully valid: do not trust any single group's
    # entry either; every group is forced to neither pass nor be complete.
    manifest_malformed = expected_packets is not None and not roster_bound
    arms_malformed = not isinstance(before, dict) or not isinstance(after, dict)

    b = 0
    c = 0
    complete = not arms_malformed
    group_results: List[Dict[str, Any]] = []

    for group in frozen_roster:
        if manifest_malformed or arms_malformed:
            before_pass = after_pass = False
            before_complete = after_complete = False
        else:
            expected = expected_packets.get(group) if roster_bound else None
            before_pass, before_complete = _group_arm_status(before.get(group), expected)
            after_pass, after_complete = _group_arm_status(after.get(group), expected)
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
        "roster_bound": roster_bound,
        "diagnostic_only": diagnostic_only,
        "evidence_complete": complete and roster_bound,
        "group_results": group_results,
    }


def confirm_whole_groups(
    frozen_roster: Sequence[str],
    before: Dict[str, List[Optional[Dict[str, Any]]]],
    after: Dict[str, List[Optional[Dict[str, Any]]]],
    expected_packets: Optional[Dict[str, Sequence[str]]] = None,
) -> Dict[str, Any]:
    counts = whole_group_counts(frozen_roster, before, after, expected_packets)
    confirmation = evaluate_confirmation(counts["b"], counts["c"])
    result: Dict[str, Any] = dict(counts)
    result.update(confirmation)
    # The terminal "complete" here means fully certified evidence (arm data
    # complete AND roster-bound); a malformed or omitted manifest can never
    # pass, even if the arithmetic alone would have met margin/p-value.
    result["complete"] = counts["evidence_complete"]
    if not counts["evidence_complete"]:
        result["passes"] = False
    return result
