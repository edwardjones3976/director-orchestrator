import unittest
from fractions import Fraction

from dev_selection import (
    evaluate_cell_counts,
    evaluate_primary_score,
    primary_fraction,
    is_selectable,
    select_checkpoint,
    evaluate_confirmation,
    whole_group_counts,
    confirm_whole_groups,
)


def _full_cand(non_goals=None, constraints=None, acceptance_checks=None, evidence_requirements=None):
    return {
        "non_goals": non_goals if non_goals is not None else [],
        "constraints": constraints if constraints is not None else [],
        "acceptance_checks": acceptance_checks if acceptance_checks is not None else [],
        "evidence_requirements": evidence_requirements if evidence_requirements is not None else [],
    }


def _ref_row(row_id, source_group, non_goals=None, constraints=None, acceptance_checks=None, evidence_requirements=None):
    return {
        "row_id": row_id,
        "source_group": source_group,
        "target": {
            "non_goals": non_goals if non_goals is not None else [],
            "constraints": constraints if constraints is not None else [],
            "acceptance_checks": acceptance_checks if acceptance_checks is not None else [],
            "evidence_requirements": evidence_requirements if evidence_requirements is not None else [],
        },
    }


class TestLiteralMultiset(unittest.TestCase):
    def test_toy_tokens_tp_fp_fn(self):
        # Ref[A,B], cand[B,A]: order does not matter, TP2 FP0 FN0 F1=1
        tp, fp, fn = evaluate_cell_counts(["A", "B"], ["B", "A"])
        self.assertEqual((tp, fp, fn), (2, 0, 0))

        # Ref[A,B], cand[A,A,B]: excess candidate duplicate -> 2/1/0 F1=4/5
        tp, fp, fn = evaluate_cell_counts(["A", "B"], ["A", "A", "B"])
        self.assertEqual((tp, fp, fn), (2, 1, 0))

        # Ref[A,A], cand[A]: omitted reference duplicate -> 1/0/1 F1=2/3
        tp, fp, fn = evaluate_cell_counts(["A", "A"], ["A"])
        self.assertEqual((tp, fp, fn), (1, 0, 1))

    def test_no_normalization_case_punctuation_unicode_whitespace(self):
        cases = [
            (["A"], ["a"]),           # case
            (["x-y"], ["x y"]),       # punctuation
            (["\uff21"], ["A"]),      # fullwidth A vs ASCII A (unicode fold)
            (["A "], ["A"]),          # trailing whitespace
            (["A"], [" A"]),          # leading whitespace
        ]
        for ref, cand in cases:
            tp, fp, fn = evaluate_cell_counts(ref, cand)
            self.assertEqual((tp, fp, fn), (0, 1, 1), f"ref={ref!r} cand={cand!r}")

    def test_both_empty_is_perfect(self):
        tp, fp, fn = evaluate_cell_counts([], [])
        self.assertEqual((tp, fp, fn), (0, 0, 0))

    def test_one_sided_empty(self):
        tp, fp, fn = evaluate_cell_counts(["A"], [])
        self.assertEqual((tp, fp, fn), (0, 0, 1))
        tp, fp, fn = evaluate_cell_counts([], ["A"])
        self.assertEqual((tp, fp, fn), (0, 1, 0))


class TestPrimaryScore(unittest.TestCase):
    def test_malformed_reference_target_zeroes_bound_group(self):
        for target in ({}, None, [], 7, "text", {"non_goals": []},
                       dict(_full_cand(), extra=[]), _full_cand(non_goals=""),
                       _full_cand(non_goals=[1])):
            with self.subTest(target=target):
                ref = dict(_ref_row("r1", "g1"), target=target)
                res = evaluate_primary_score([ref], {"r1": _full_cand()}, ["g1"],
                                             expected_packets={"g1": ["r1"]})
                self.assertEqual(res["primary"], 0.0)
                self.assertEqual(res["incomplete_groups"], ["g1"])
                self.assertFalse(is_selectable(res))
                with self.assertRaises(ValueError):
                    primary_fraction(res)
        ref = _ref_row("r1", "g1")
        del ref["target"]
        missing = evaluate_primary_score([ref], {"r1": _full_cand()}, ["g1"],
                                         expected_packets={"g1": ["r1"]})
        self.assertFalse(is_selectable(missing))
        valid = evaluate_primary_score([_ref_row("r1", "g1")], {"r1": _full_cand()},
                                       ["g1"], expected_packets={"g1": ["r1"]})
        self.assertEqual(primary_fraction(valid), Fraction(1))

    def test_onegroup4field_counts_a(self):
        # primary 1/4, micro 200/203
        refs = [_ref_row("r1", "g1", acceptance_checks=["A"] * 100, constraints=["B"],
                          evidence_requirements=["C"], non_goals=["D"])]
        cands = {"r1": _full_cand(acceptance_checks=["A"] * 100)}
        res = evaluate_primary_score(refs, cands, ["g1"], (), {"g1": ["r1"]})
        self.assertAlmostEqual(res["primary"], 0.25)
        self.assertAlmostEqual(res["micro"], 200 / 203)
        self.assertFalse(res["incomplete"])
        self.assertEqual(res["incomplete_groups"], [])

    def test_onegroup4field_counts_b(self):
        # primary 3/4. TP=3, FP=0, FN=100 -> micro = 2*3 / (2*3 + 0 + 100) = 6/106 = 3/53.
        refs = [_ref_row("r1", "g1", acceptance_checks=["A"] * 100, constraints=["B"],
                          evidence_requirements=["C"], non_goals=["D"])]
        cands = {"r1": _full_cand(constraints=["B"], evidence_requirements=["C"], non_goals=["D"])}
        res = evaluate_primary_score(refs, cands, ["g1"])
        self.assertAlmostEqual(res["primary"], 0.75)
        self.assertAlmostEqual(res["micro"], 3 / 53)

    def test_missing_candidate_incomplete(self):
        refs = [_ref_row("r1", "g1")]
        cands = {}
        res = evaluate_primary_score(refs, cands, ["g1"])
        self.assertTrue(res["incomplete"])
        self.assertEqual(res["primary"], 0.0)
        self.assertEqual(res["incomplete_groups"], ["g1"])

    def test_pooled_not_packet_average(self):
        # Two packets in the same group: pooling the literal items across the
        # whole group must NOT equal the average of each packet's own F1.
        refs = [
            _ref_row("r1", "g1", non_goals=["A", "B"]),
            _ref_row("r2", "g1", non_goals=["A"]),
        ]
        cands = {
            "r1": _full_cand(non_goals=["A"]),
            "r2": _full_cand(non_goals=["A", "B"]),
        }
        res = evaluate_primary_score(refs, cands, ["g1"])
        # Pooled: ref={A:2,B:1}, cand={A:2,B:1} -> tp=3,fp=0,fn=0 -> f1=1.0
        # (a naive per-packet average would give 2/3 instead)
        self.assertEqual(res["per_field"]["non_goals"], 1.0)
        self.assertEqual(res["primary"], 1.0)

    def test_cross_field_no_credit(self):
        # Candidate places the reference item in the wrong field: neither
        # field gets credit for it.
        refs = [_ref_row("r1", "g1", non_goals=["X"])]
        cands = {"r1": _full_cand(constraints=["X"])}
        res = evaluate_primary_score(refs, cands, ["g1"])
        self.assertEqual(res["per_field"]["non_goals"], 0.0)
        self.assertEqual(res["per_field"]["constraints"], 0.0)
        # The other two fields are both-empty (perfect), so primary = 2/4
        self.assertAlmostEqual(res["primary"], 0.5)

    def test_frozen_second_group_absent_gives_half_and_incomplete(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"], constraints=["B"],
                          acceptance_checks=["C"], evidence_requirements=["D"])]
        cands = {"r1": _full_cand(non_goals=["A"], constraints=["B"],
                                   acceptance_checks=["C"], evidence_requirements=["D"])}
        res = evaluate_primary_score(refs, cands, ["g1", "g2"])
        self.assertAlmostEqual(res["primary"], 0.5)
        self.assertTrue(res["incomplete"])
        self.assertEqual(res["incomplete_groups"], ["g2"])
        self.assertEqual(res["groups"], 2)

    def test_missing_key_zeroes_whole_group(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"])]
        bad_cand = _full_cand(non_goals=["A"])
        del bad_cand["constraints"]
        cands = {"r1": bad_cand}
        res = evaluate_primary_score(refs, cands, ["g1"])
        self.assertEqual(res["primary"], 0.0)
        self.assertTrue(res["incomplete"])
        self.assertEqual(res["incomplete_groups"], ["g1"])

    def test_wrong_type_zeroes_whole_group(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"])]
        bad_cand = _full_cand(non_goals=["A"])
        bad_cand["constraints"] = "not-a-list"
        cands = {"r1": bad_cand}
        res = evaluate_primary_score(refs, cands, ["g1"])
        self.assertEqual(res["primary"], 0.0)
        self.assertTrue(res["incomplete"])
        self.assertEqual(res["incomplete_groups"], ["g1"])

    def test_extra_property_zeroes_whole_group(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"])]
        bad_cand = _full_cand(non_goals=["A"])
        bad_cand["extra_field"] = []
        cands = {"r1": bad_cand}
        res = evaluate_primary_score(refs, cands, ["g1"])
        self.assertEqual(res["primary"], 0.0)
        self.assertTrue(res["incomplete"])
        self.assertEqual(res["incomplete_groups"], ["g1"])

    def test_externally_invalid_row_id_zeroes_whole_group(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"])]
        cands = {"r1": _full_cand(non_goals=["A"])}
        res = evaluate_primary_score(refs, cands, ["g1"], schema_invalid_row_ids=["r1"])
        self.assertEqual(res["primary"], 0.0)
        self.assertTrue(res["incomplete"])
        self.assertEqual(res["incomplete_groups"], ["g1"])

    def test_per_field_exact(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"], constraints=[], acceptance_checks=["B"])]
        cands = {"r1": _full_cand(non_goals=["A"], constraints=["C"], acceptance_checks=[])}
        res = evaluate_primary_score(refs, cands, ["g1"])
        
        self.assertIn("per_field_exact", res)
        exact = res["per_field_exact"]
        
        self.assertEqual(set(exact.keys()), {"non_goals", "constraints", "acceptance_checks", "evidence_requirements"})
        
        for f, pair in exact.items():
            self.assertIs(type(pair["numerator"]), int)
            self.assertIs(type(pair["denominator"]), int)
            self.assertGreater(pair["denominator"], 0)
            self.assertAlmostEqual(pair["numerator"] / pair["denominator"], res["per_field"][f])
            
        self.assertEqual(exact["non_goals"], {"numerator": 1, "denominator": 1})
        self.assertEqual(exact["constraints"], {"numerator": 0, "denominator": 1})
        self.assertEqual(exact["acceptance_checks"], {"numerator": 0, "denominator": 1})
        self.assertEqual(exact["evidence_requirements"], {"numerator": 1, "denominator": 1})
        
        refs_inc = [_ref_row("r1", "g1")]
        cands_inc = {}
        res_inc = evaluate_primary_score(refs_inc, cands_inc, ["g1"])
        self.assertTrue(res_inc["incomplete"])
        self.assertIn("per_field_exact", res_inc)
        self.assertEqual(res_inc["per_field_exact"]["non_goals"], {"numerator": 1, "denominator": 1})


class TestSelectCheckpoint(unittest.TestCase):
    def test_highest_score_wins(self):
        checkpoints = [("a", 1, Fraction(1, 2)), ("b", 2, Fraction(3, 4))]
        self.assertEqual(select_checkpoint(checkpoints), "b")

    def test_tie_goes_to_earlier_checkpoint(self):
        checkpoints = [("a", 2, Fraction(1, 2)), ("b", 1, Fraction(1, 2))]
        self.assertEqual(select_checkpoint(checkpoints), "b")

    def test_tie_in_different_rational_forms(self):
        checkpoints = [("a", 1, Fraction(2, 3)), ("b", 2, Fraction(4, 6))]
        self.assertEqual(select_checkpoint(checkpoints), "a")

    def test_empty_list_refused(self):
        with self.assertRaises(ValueError):
            select_checkpoint([])

    def test_nan_score_refused(self):
        with self.assertRaises(ValueError):
            select_checkpoint([("a", 1, float("nan"))])

    def test_duplicate_orders_refused(self):
        with self.assertRaises(ValueError):
            select_checkpoint([("a", 1, 0.5), ("b", 1, 0.6)])


class TestConfirmation(unittest.TestCase):
    def test_b6_c0_passes_p_1_32(self):
        res = evaluate_confirmation(6, 0)
        self.assertTrue(res["passes"])
        self.assertAlmostEqual(res["p_value"], 1 / 32)
        self.assertEqual(res["margin"], 6)

    def test_b8_c2_fails_on_p_despite_margin_6(self):
        res = evaluate_confirmation(8, 2)
        self.assertEqual(res["margin"], 6)
        self.assertAlmostEqual(res["p_value"], 7 / 64)
        self.assertFalse(res["passes"])

    def test_b0_c6_fails_on_direction(self):
        res = evaluate_confirmation(0, 6)
        self.assertEqual(res["margin"], -6)
        self.assertFalse(res["passes"])

    def test_b5_c0_fails_on_margin(self):
        res = evaluate_confirmation(5, 0)
        self.assertEqual(res["margin"], 5)
        self.assertFalse(res["passes"])

    def test_b13_c7(self):
        res = evaluate_confirmation(13, 7)
        self.assertFalse(res["passes"])
        self.assertAlmostEqual(res["p_value"], 34495 / 131072)

    def test_b7_c1(self):
        res = evaluate_confirmation(7, 1)
        self.assertFalse(res["passes"])
        self.assertAlmostEqual(res["p_value"], 9 / 128)

    def test_b0_c0(self):
        res = evaluate_confirmation(0, 0)
        self.assertFalse(res["passes"])
        self.assertEqual(res["p_value"], 1.0)


class TestWholeGroupConfirmation(unittest.TestCase):
    def _packet(self, packet_id="p", schema_valid=True, grok_as_is=True, zai_as_is=True):
        return {"packet_id": packet_id, "schema_valid": schema_valid,
                "grok_as_is": grok_as_is, "zai_as_is": zai_as_is}

    def test_failing_packet_keeps_group_from_passing(self):
        roster = ["g1"]
        before = {"g1": [self._packet("p1"), self._packet("p2")]}
        after = {"g1": [self._packet("p1"), self._packet("p2", grok_as_is=False)]}
        # Bind a valid, complete manifest so "complete" reflects certified
        # (roster-bound) evidence, not just arm data presence.
        res = confirm_whole_groups(roster, before, after, {"g1": ["p1", "p2"]})
        self.assertEqual(res["b"], 0)
        self.assertEqual(res["c"], 1)
        self.assertTrue(res["complete"])
        self.assertTrue(res["roster_bound"])

    def test_missing_packet_keeps_group_from_passing(self):
        roster = ["g1"]
        before = {"g1": [self._packet()]}
        after = {"g1": [self._packet(), None]}
        res = confirm_whole_groups(roster, before, after)
        self.assertFalse(res["complete"])
        self.assertEqual(res["b"], 0)

    def test_absent_group_is_incomplete(self):
        roster = ["g1", "g2"]
        before = {"g1": [self._packet()], "g2": [self._packet()]}
        after = {"g1": [self._packet()]}  # g2 entirely absent from "after"
        res = whole_group_counts(roster, before, after)
        self.assertFalse(res["complete"])

    def test_incomplete_roster_never_passes_confirm_whole_groups(self):
        # Build a roster where 6 groups flip from failing to passing (B=6, C=0,
        # which would otherwise pass both margin and p-value), but one group
        # has a missing (None) packet in "after" making the roster incomplete.
        roster = [f"g{i}" for i in range(1, 8)]
        before = {g: [self._packet(schema_valid=False, grok_as_is=False, zai_as_is=False)] for g in roster}
        after = {g: [self._packet()] for g in roster[:6]}
        after["g7"] = [None]
        before["g7"] = []

        counts = whole_group_counts(roster, before, after)
        self.assertEqual(counts["b"], 6)
        self.assertEqual(counts["c"], 0)
        self.assertFalse(counts["complete"])

        res = confirm_whole_groups(roster, before, after)
        self.assertFalse(res["complete"])
        self.assertFalse(res["passes"])


class TestExpectedPacketRoster(unittest.TestCase):
    def test_nonobject_outer_arms_are_incomplete_even_with_empty_roster(self):
        for roster in ([f"g{i}" for i in range(1, 21)], []):
            for bad in (7, "text", [1], None):
                for arm in ("before", "after", "both"):
                    for evaluate in (whole_group_counts, confirm_whole_groups):
                        for manifest in (self._expected(roster), None):
                            with self.subTest(roster=roster, bad=bad, arm=arm,
                                              entrypoint=evaluate.__name__, manifest=manifest):
                                before, after = self._arms(roster)
                                if arm in ("before", "both"):
                                    before = bad
                                if arm in ("after", "both"):
                                    after = bad
                                result = evaluate(roster, before, after, manifest)
                                self.assertFalse(result["complete"])
                                self.assertFalse(result["evidence_complete"])
                                self.assertFalse(is_selectable(result))
                                self.assertEqual(result["groups"], len(roster))
                                self.assertEqual((result["b"], result["c"]), (0, 0))
                                if evaluate is confirm_whole_groups:
                                    self.assertFalse(result["passes"])

    def test_valid_outer_arms_preserve_b_c_direction(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        for evaluate in (whole_group_counts, confirm_whole_groups):
            for left, right, expected in ((before, after, (6, 0)), (after, before, (0, 6))):
                with self.subTest(entrypoint=evaluate.__name__, expected=expected):
                    result = evaluate(roster, left, right, self._expected(roster))
                    self.assertTrue(result["complete"])
                    self.assertTrue(result["evidence_complete"])
                    self.assertTrue(is_selectable(result))
                    self.assertEqual((result["b"], result["c"]), expected)
                    if evaluate is confirm_whole_groups:
                        self.assertEqual(result["passes"], expected == (6, 0))

    def test_nonobject_packets_block_bound_counts_and_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        for bad in (7, [], "text", None):
            for arm in ("before", "after"):
                with self.subTest(packet=bad, arm=arm):
                    before, after = self._arms(roster)
                    (before if arm == "before" else after)["g7"][1] = bad
                    for evaluate in (whole_group_counts, confirm_whole_groups):
                        res = evaluate(roster, before, after, self._expected(roster))
                        self.assertEqual((res["b"], res["c"]), (6, 0))
                        self.assertFalse(res["complete"])
                        self.assertFalse(res["evidence_complete"])
                        self.assertFalse(is_selectable(res))
                        if evaluate is confirm_whole_groups:
                            self.assertFalse(res["passes"])

    def _packet(self, packet_id, schema_valid=True, grok_as_is=True, zai_as_is=True):
        return {"packet_id": packet_id, "schema_valid": schema_valid,
                "grok_as_is": grok_as_is, "zai_as_is": zai_as_is}

    def _arms(self, roster, after_override=None):
        """20 groups; g1..g6 flip to passing, the rest fail in both arms."""
        fail = dict(schema_valid=False, grok_as_is=False, zai_as_is=False)
        before = {g: [self._packet(f"{g}-p1", **fail), self._packet(f"{g}-p2", **fail)]
                  for g in roster}
        after = {g: [self._packet(f"{g}-p1"), self._packet(f"{g}-p2")] for g in roster[:6]}
        for g in roster[6:]:
            after[g] = [self._packet(f"{g}-p1", **fail), self._packet(f"{g}-p2", **fail)]
        if after_override:
            after.update(after_override)
        return before, after

    def _expected(self, roster):
        return {g: [f"{g}-p1", f"{g}-p2"] for g in roster}

    def test_clean_six_flips_pass_with_expected_roster(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        res = confirm_whole_groups(roster, before, after, self._expected(roster))
        self.assertEqual((res["b"], res["c"]), (6, 0))
        self.assertTrue(res["complete"])
        self.assertTrue(res["roster_bound"])
        self.assertTrue(res["passes"])

    def test_omitted_packet_in_passing_group_blocks_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster, {"g3": [self._packet("g3-p1")]})
        res = whole_group_counts(roster, before, after, self._expected(roster))
        self.assertTrue(res["roster_bound"])
        self.assertFalse(res["complete"])
        self.assertEqual(res["b"], 5)
        confirm = confirm_whole_groups(roster, before, after, self._expected(roster))
        self.assertFalse(confirm["passes"])

    def test_substituted_packet_id_blocks_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(
            roster, {"g5": [self._packet("g5-p1"), self._packet("g5-pX")]})
        res = whole_group_counts(roster, before, after, self._expected(roster))
        self.assertFalse(res["complete"])
        self.assertEqual(res["b"], 5)
        confirm = confirm_whole_groups(roster, before, after, self._expected(roster))
        self.assertFalse(confirm["passes"])

    def test_without_expected_roster_never_passes(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        # Arm data itself is complete (every packet present and real-boolean)
        # even with no manifest at all.
        counts = whole_group_counts(roster, before, after)
        self.assertTrue(counts["complete"])
        self.assertFalse(counts["roster_bound"])
        self.assertTrue(counts["diagnostic_only"])
        self.assertFalse(counts["evidence_complete"])
        # But confirm_whole_groups' terminal "complete" is evidence_complete:
        # an omitted manifest can never be a confirmed PASS.
        res = confirm_whole_groups(roster, before, after)
        self.assertFalse(res["complete"])
        self.assertFalse(res["roster_bound"])
        self.assertTrue(res["diagnostic_only"])
        self.assertFalse(res["passes"])
        self.assertFalse(is_selectable(res))

    def test_string_false_flags_are_incomplete_not_a_pass(self):
        roster = ["g1"]
        before = {"g1": [self._packet("g1-p1", "false", "false", "false")]}
        after = {"g1": [self._packet("g1-p1")]}
        res = whole_group_counts(roster, before, after, {"g1": ["g1-p1"]})
        self.assertFalse(res["complete"])
        self.assertFalse(confirm_whole_groups(roster, before, after, {"g1": ["g1-p1"]})["passes"])
        # Real-boolean enforcement applies even without an expected roster.
        res = whole_group_counts(roster, before, after)
        self.assertFalse(res["complete"])

    def test_missing_flag_key_is_incomplete(self):
        roster = ["g1"]
        bad = {"packet_id": "g1-p1", "schema_valid": True, "grok_as_is": True}
        res = whole_group_counts(roster, {"g1": [bad]}, {"g1": [self._packet("g1-p1")]},
                                 {"g1": ["g1-p1"]})
        self.assertFalse(res["complete"])

    def test_real_false_flag_is_complete_not_a_pass(self):
        roster = ["g1"]
        before = {"g1": [self._packet("g1-p1", False, False, False)]}
        after = {"g1": [self._packet("g1-p1")]}
        res = whole_group_counts(roster, before, after, {"g1": ["g1-p1"]})
        self.assertTrue(res["complete"])
        self.assertEqual(res["b"], 1)
        self.assertFalse(res["group_results"][0]["before"])

    def test_packet_id_must_be_a_present_string(self):
        roster = ["g1"]
        no_id = {"schema_valid": True, "grok_as_is": True, "zai_as_is": True}
        res = whole_group_counts(roster, {"g1": [self._packet("g1-p1")]},
                                 {"g1": [no_id]}, {"g1": ["g1-p1"]})
        self.assertFalse(res["complete"])
        non_str = dict(no_id, packet_id=7)
        res = whole_group_counts(roster, {"g1": [self._packet("g1-p1")]},
                                 {"g1": [non_str]}, {"g1": ["g1-p1"]})
        self.assertFalse(res["complete"])


class TestExpectedPacketsPrimaryScore(unittest.TestCase):
    def test_dropped_reference_id_zeroes_group(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"])]
        cands = {"r1": _full_cand(non_goals=["A"])}
        res = evaluate_primary_score(refs, cands, ["g1"],
                                     expected_packets={"g1": ["r1", "r2"]})
        self.assertEqual(res["primary"], 0.0)
        self.assertTrue(res["incomplete"])
        self.assertEqual(res["incomplete_groups"], ["g1"])
        self.assertEqual(res["groups"], 1)  # denominator is never trimmed

    def test_duplicated_reference_id_zeroes_group(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"]),
                _ref_row("r1", "g1", non_goals=["B"])]
        cands = {"r1": _full_cand(non_goals=["A"])}
        res = evaluate_primary_score(refs, cands, ["g1"],
                                     expected_packets={"g1": ["r1", "r2"]})
        self.assertEqual(res["primary"], 0.0)
        self.assertTrue(res["incomplete"])
        self.assertEqual(res["incomplete_groups"], ["g1"])

    def test_substituted_reference_id_zeroes_group(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"]),
                _ref_row("r3", "g1", non_goals=["B"])]
        cands = {"r1": _full_cand(), "r3": _full_cand()}
        res = evaluate_primary_score(refs, cands, ["g1"],
                                     expected_packets={"g1": ["r1", "r2"]})
        self.assertEqual(res["primary"], 0.0)
        self.assertTrue(res["incomplete"])
        self.assertEqual(res["incomplete_groups"], ["g1"])

    def test_matching_roster_scores_normally(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"]),
                _ref_row("r2", "g1", non_goals=["B"])]
        cands = {"r1": _full_cand(non_goals=["A"]), "r2": _full_cand(non_goals=["B"])}
        res = evaluate_primary_score(refs, cands, ["g1"],
                                     expected_packets={"g1": ["r1", "r2"]})
        self.assertEqual(res["primary"], 1.0)
        self.assertFalse(res["incomplete"])
        self.assertTrue(res["roster_bound"])

    def test_roster_bound_false_when_partial_or_absent(self):
        refs = [_ref_row("r1", "g1")]
        cands = {"r1": _full_cand()}
        res = evaluate_primary_score(refs, cands, ["g1", "g2"])
        self.assertFalse(res["roster_bound"])
        res = evaluate_primary_score(refs, cands, ["g1", "g2"],
                                     expected_packets={"g1": ["r1"]})
        self.assertFalse(res["roster_bound"])

    def test_primary_exact_one_third_and_fraction_selection(self):
        # Unequal groups: g1 perfect (4 cells), g2/g3 zero in all four cells
        # (each reference field is non-empty and missed), so the pooled sum is
        # 4 over 12 cells -> primary = 1/3 exactly.
        refs = [
            _ref_row("r1", "g1", non_goals=["A"], constraints=["B"],
                     acceptance_checks=["C"], evidence_requirements=["D"]),
            _ref_row("r2", "g2", non_goals=["X"], constraints=["Y"],
                     acceptance_checks=["Z"], evidence_requirements=["W"]),
            _ref_row("r3", "g3", non_goals=["X"], constraints=["Y"],
                     acceptance_checks=["Z"], evidence_requirements=["W"]),
        ]
        cands = {
            "r1": _full_cand(non_goals=["A"], constraints=["B"],
                             acceptance_checks=["C"], evidence_requirements=["D"]),
            "r2": _full_cand(),
            "r3": _full_cand(),
        }
        expected = {"g1": ["r1"], "g2": ["r2"], "g3": ["r3"]}
        res = evaluate_primary_score(refs, cands, ["g1", "g2", "g3"],
                                     expected_packets=expected)
        self.assertFalse(res["incomplete"])
        self.assertTrue(res["roster_bound"])
        self.assertEqual(res["primary_exact"], {"numerator": 1, "denominator": 3})
        frac = primary_fraction(res)
        self.assertEqual(frac, Fraction(1, 3))
        self.assertAlmostEqual(res["primary"], float(Fraction(1, 3)))
        # select_checkpoint accepts the Fraction unchanged; equal exact values
        # (1/3 vs 2/6) tie-break to the earlier checkpoint.
        checkpoints = [("later", 2, frac), ("earlier", 1, Fraction(2, 6))]
        self.assertEqual(select_checkpoint(checkpoints), "earlier")


class TestManifestValidationGate(unittest.TestCase):
    """Contract (issue 299 comment 5980052003): a manifest is valid only if
    it is a dict whose keys exactly equal the frozen roster, with every value
    a non-empty, duplicate-free list of non-empty str ids. Anything else
    (given but malformed) is unbound, exactly like an omitted manifest, and
    neither can ever yield a confirmed PASS or a selectable score."""

    def _packet(self, packet_id, schema_valid=True, grok_as_is=True, zai_as_is=True):
        return {"packet_id": packet_id, "schema_valid": schema_valid,
                "grok_as_is": grok_as_is, "zai_as_is": zai_as_is}

    def _arms(self, roster):
        """20 groups; g1..g6 flip to passing, the rest fail in both arms."""
        fail = dict(schema_valid=False, grok_as_is=False, zai_as_is=False)
        before = {g: [self._packet(f"{g}-p1", **fail), self._packet(f"{g}-p2", **fail)]
                  for g in roster}
        after = {g: [self._packet(f"{g}-p1"), self._packet(f"{g}-p2")] for g in roster[:6]}
        for g in roster[6:]:
            after[g] = [self._packet(f"{g}-p1", **fail), self._packet(f"{g}-p2", **fail)]
        return before, after

    def _valid_manifest(self, roster):
        return {g: [f"{g}-p1", f"{g}-p2"] for g in roster}

    def _assert_blocked(self, roster, before, after, manifest):
        res = confirm_whole_groups(roster, before, after, manifest)
        self.assertFalse(res["passes"])
        self.assertFalse(res["roster_bound"])
        self.assertFalse(is_selectable(res))
        return res

    def test_omitted_manifest_blocks_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        res = confirm_whole_groups(roster, before, after)
        self.assertFalse(res["passes"])
        self.assertFalse(res["roster_bound"])
        self.assertTrue(res["diagnostic_only"])
        self.assertFalse(is_selectable(res))

    def test_twenty_group_none_value_matches_defect_repro(self):
        # Exact repro of the verified defect: six groups flip to passing
        # (B=6, C=0), but every manifest value is None -- confirm_whole_groups
        # must neither report roster_bound/complete nor pass.
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        manifest = {g: None for g in roster}
        res = self._assert_blocked(roster, before, after, manifest)
        self.assertEqual((res["b"], res["c"]), (0, 0))

    def test_empty_list_value_blocks_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        manifest = {g: [] for g in roster}
        self._assert_blocked(roster, before, after, manifest)

    def test_duplicate_ids_block_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        manifest = self._valid_manifest(roster)
        manifest["g1"] = ["g1-p1", "g1-p1"]
        self._assert_blocked(roster, before, after, manifest)

    def test_non_string_id_blocks_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        manifest = self._valid_manifest(roster)
        manifest["g1"] = [7, "g1-p2"]
        self._assert_blocked(roster, before, after, manifest)

    def test_empty_string_id_blocks_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        manifest = self._valid_manifest(roster)
        manifest["g1"] = ["", "g1-p2"]
        self._assert_blocked(roster, before, after, manifest)

    def test_missing_group_key_blocks_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        manifest = self._valid_manifest(roster)
        del manifest["g1"]
        self._assert_blocked(roster, before, after, manifest)

    def test_extra_group_key_blocks_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        manifest = self._valid_manifest(roster)
        manifest["g21"] = ["g21-p1"]
        self._assert_blocked(roster, before, after, manifest)

    def test_non_dict_manifest_blocks_confirmation(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        manifest = list(self._valid_manifest(roster).values())
        self._assert_blocked(roster, before, after, manifest)

    def test_valid_manifest_positive_control_is_selectable(self):
        roster = [f"g{i}" for i in range(1, 21)]
        before, after = self._arms(roster)
        manifest = self._valid_manifest(roster)
        res = confirm_whole_groups(roster, before, after, manifest)
        self.assertEqual((res["b"], res["c"]), (6, 0))
        self.assertAlmostEqual(res["p_value"], 1 / 32)
        self.assertTrue(res["passes"])
        self.assertTrue(res["roster_bound"])
        self.assertTrue(is_selectable(res))


class TestManifestValidationPrimaryScore(unittest.TestCase):
    def test_malformed_manifest_zeroes_every_group(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"]),
                _ref_row("r2", "g2", non_goals=["B"])]
        cands = {"r1": _full_cand(non_goals=["A"]), "r2": _full_cand(non_goals=["B"])}
        manifest = {"g1": None, "g2": ["r2"]}  # g1 malformed -> whole manifest invalid
        res = evaluate_primary_score(refs, cands, ["g1", "g2"], expected_packets=manifest)
        self.assertEqual(res["primary"], 0.0)
        self.assertTrue(res["incomplete"])
        self.assertEqual(sorted(res["incomplete_groups"]), ["g1", "g2"])
        self.assertFalse(res["roster_bound"])
        self.assertFalse(res["diagnostic_only"])
        self.assertFalse(is_selectable(res))
        with self.assertRaises(ValueError):
            primary_fraction(res)

    def test_omitted_manifest_is_diagnostic_only_and_not_selectable(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"])]
        cands = {"r1": _full_cand(non_goals=["A"])}
        res = evaluate_primary_score(refs, cands, ["g1"])
        self.assertFalse(res["incomplete_groups"])  # arithmetic unchanged
        self.assertTrue(res["incomplete"])
        self.assertTrue(res["diagnostic_only"])
        self.assertFalse(res["roster_bound"])
        self.assertFalse(is_selectable(res))
        with self.assertRaises(ValueError):
            primary_fraction(res)

    def test_bound_complete_result_is_selectable_and_fraction_exact(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"])]
        cands = {"r1": _full_cand(non_goals=["A"])}
        res = evaluate_primary_score(refs, cands, ["g1"], expected_packets={"g1": ["r1"]})
        self.assertFalse(res["incomplete"])
        self.assertTrue(res["roster_bound"])
        self.assertTrue(is_selectable(res))
        self.assertEqual(primary_fraction(res), Fraction(1, 1))


class TestStrictStatusBooleans(unittest.TestCase):
    def test_primary_fraction_rejects_tampered_evidence(self):
        for numerator, denominator, summary in (
            (2, 1, 2.0), (-1, 2, -0.5), (2, 4, 0.5), (0, 2, 0.0),
            (2, 2, 1.0), (1, True, 1.0), (1, 2, 0.75),
            (1, 2, float("nan")), (1, 2, float("inf")),
            (1, 1, True), (1, 2, "0.5"), (1, 2, None),
        ):
            with self.subTest(pair=(numerator, denominator), summary=summary):
                res = self._primary_result()
                res.update(primary_exact={"numerator": numerator, "denominator": denominator},
                           primary=summary)
                with self.assertRaises(ValueError):
                    primary_fraction(res)
        for fault in ("extra_exact_key", "missing_summary", "nonobject"):
            with self.subTest(fault=fault):
                res = self._primary_result()
                if fault == "extra_exact_key":
                    res["primary_exact"]["extra"] = 0
                elif fault == "missing_summary":
                    del res["primary"]
                else:
                    res = []
                with self.assertRaises(ValueError):
                    primary_fraction(res)
        for value in (Fraction(0), Fraction(1), Fraction(1, 3), Fraction(19, 20)):
            res = self._primary_result()
            res.update(primary_exact={"numerator": value.numerator, "denominator": value.denominator},
                       primary=float(value))
            self.assertEqual(primary_fraction(res), value)

    """Every status flag must be the literal boolean: a missing key or a
    truthy/falsy stand-in ("yes", 1, 0, [], "False") never makes a result
    selectable, and `primary_fraction` refuses exactly what `is_selectable`
    refuses (plus a malformed `primary_exact`)."""

    def _primary_result(self):
        refs = [_ref_row("r1", "g1", non_goals=["A"])]
        cands = {"r1": _full_cand(non_goals=["A"])}
        return evaluate_primary_score(refs, cands, ["g1"],
                                      expected_packets={"g1": ["r1"]})

    def _whole_group_result(self):
        packet = {"packet_id": "p1", "schema_valid": True,
                  "grok_as_is": True, "zai_as_is": True}
        return confirm_whole_groups(
            ["g1"], {"g1": [packet]}, {"g1": [packet]},
            expected_packets={"g1": ["p1"]})

    def _assert_refused(self, result):
        self.assertFalse(is_selectable(result))
        with self.assertRaises(ValueError):
            primary_fraction(result)

    def test_incomplete_must_be_literal_false(self):
        for bad in ([], "False", 0):
            res = self._primary_result()
            res["incomplete"] = bad
            self._assert_refused(res)

    def test_roster_bound_must_be_literal_true(self):
        for bad in ("yes", 1):
            res = self._primary_result()
            res["roster_bound"] = bad
            self._assert_refused(res)

    def test_diagnostic_only_must_be_literal_false(self):
        res = self._primary_result()
        res["diagnostic_only"] = 0
        self._assert_refused(res)
        res = self._primary_result()
        del res["diagnostic_only"]
        self._assert_refused(res)

    def test_evidence_complete_must_be_literal_true(self):
        for bad in ("yes", 1):
            res = self._whole_group_result()
            res["evidence_complete"] = bad
            self._assert_refused(res)

    def test_missing_roster_bound_key_refused(self):
        res = self._primary_result()
        del res["roster_bound"]
        self._assert_refused(res)

    def test_unrelated_dict_refused(self):
        self._assert_refused({})
        self._assert_refused({"roster_bound": True, "diagnostic_only": False})

    def test_primary_exact_must_be_two_ints_positive_denominator(self):
        for exact in (
            {"numerator": 1, "denominator": 0},
            {"numerator": "1", "denominator": 2},
            {"numerator": 1.0, "denominator": 2},
            {"numerator": True, "denominator": 2},
            {"numerator": 1},
        ):
            res = self._primary_result()
            res["primary_exact"] = exact
            self.assertTrue(is_selectable(res))  # tampering is invisible here
            with self.assertRaises(ValueError):
                primary_fraction(res)

    def test_real_bound_complete_primary_result_accepted(self):
        res = self._primary_result()
        self.assertTrue(is_selectable(res))
        self.assertEqual(primary_fraction(res), Fraction(1, 1))

    def test_real_bound_complete_whole_group_result_accepted(self):
        res = self._whole_group_result()
        self.assertTrue(res["evidence_complete"])
        self.assertTrue(is_selectable(res))



class TestPerFieldExactAdditional(unittest.TestCase):
    def test_reduced_hand_count_two_thirds_and_all_four_fields(self):
        refs = [_ref_row("r1", "g1", constraints=["A", "B"])]
        res = evaluate_primary_score(refs, {"r1": _full_cand(constraints=["A"])}, ["g1"],
                                     expected_packets={"g1": ["r1"]})
        self.assertEqual(res["per_field_exact"]["constraints"], {"numerator": 2, "denominator": 3})
        self.assertEqual(set(res["per_field_exact"]), {"non_goals", "constraints", "acceptance_checks", "evidence_requirements"})
        for field, pair in res["per_field_exact"].items():
            self.assertIs(type(pair["numerator"]), int)
            self.assertIs(type(pair["denominator"]), int)
            self.assertGreater(pair["denominator"], 0)
            self.assertEqual(pair["numerator"] / pair["denominator"], res["per_field"][field])

    def test_empty_policy_both_empty_one_sided_and_incomplete_are_distinct(self):
        for reference, candidate, expected in (([], [], 1), (["A"], [], 0), ([], ["A"], 0)):
            with self.subTest(reference=reference, candidate=candidate):
                res = evaluate_primary_score([_ref_row("r1", "g1", non_goals=reference)],
                    {"r1": _full_cand(non_goals=candidate)}, ["g1"], expected_packets={"g1": ["r1"]})
                self.assertEqual(res["per_field_exact"]["non_goals"], {"numerator": expected, "denominator": 1})
        missing = evaluate_primary_score([_ref_row("r1", "g1")], {}, ["g1"], expected_packets={"g1": ["r1"]})
        self.assertTrue(missing["incomplete"])
        self.assertFalse(is_selectable(missing))
        self.assertEqual(missing["primary"], 0.0)
        self.assertEqual(missing["per_field_exact"]["non_goals"], {"numerator": 1, "denominator": 1})

if __name__ == '__main__':
    unittest.main()
