"""gait_prior.py · mmw_logic 사전분포 경로 · 대시보드 API 테스트.
하드웨어·운영 DB·API 키·유료 호출을 쓰지 않는다(AI는 가짜 호출 함수로 대체).
'Codex #n'은 2026-09-28 Codex 1차 코드 리뷰, 'Codex2'는 2차 리뷰 지적 사항의 회귀 테스트다."""
import copy
import json
import math
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from flask import Flask

import dashboard_api
import db
import gait_prior as gp
import integrated_analysis
import mmw_logic as L

TODAY = date(2026, 9, 28)
S0 = gp.EVIDENCE["radar_within_person_window_sd"]["speed"]


def fake_ai(payload, success: bool = True, seen: list | None = None):
    def caller(system_prompt, user_content, json_mode):
        assert json_mode is True
        assert "숫자를 새로 만들거나" in system_prompt
        if seen is not None:
            seen.append(user_content)
        if not success:
            return {"success": False, "error_type": "missing_key", "message": "키 없음"}
        return {"success": True, "text": json.dumps(payload, ensure_ascii=False), "model": "fake"}
    return caller


class NormTests(unittest.TestCase):
    def test_regression_midpoint_reproduces_kim_table2(self):
        ev = gp.EVIDENCE["kim2025_table2_by_decade"]
        seventies = gp.healthy_norm(75.61, 160.01, "velocity")
        self.assertAlmostEqual(seventies["mean"] * 100, ev["70s"]["velocity"][0], delta=3.0)
        self.assertAlmostEqual(seventies["sd"] * 100, ev["70s"]["velocity"][1], delta=0.2 * ev["70s"]["velocity"][1])
        eighties = gp.healthy_norm(83.01, 159.75, "velocity")
        # P10·P90 중간값은 느린 쪽으로 치우친 분포에서 평균보다 약간 크다(표 2 평균 77.99)
        self.assertAlmostEqual(eighties["mean"] * 100, ev["80s"]["velocity"][0], delta=6.0)
        step = gp.healthy_norm(75.61, 160.01, "step_length")
        self.assertAlmostEqual(step["mean"] * 100, ev["70s"]["step_length"][0], delta=2.0)

    def test_out_of_range_inputs_are_clamped_and_flagged(self):
        norm = gp.healthy_norm(95, 130, "velocity")
        self.assertEqual(len(norm["flags"]), 2)
        self.assertIsNone(gp.healthy_norm(45, 160, "velocity"))
        self.assertIsNone(gp.healthy_norm(None, 160, "velocity"))
        self.assertIsNone(gp.healthy_norm(float("inf"), 160, "velocity"))

    def test_missing_height_uses_decade_table_without_sex(self):
        norm = gp.healthy_norm(84, None, "velocity")
        self.assertAlmostEqual(norm["mean"], 0.7799)
        self.assertAlmostEqual(norm["sd"], 0.2021)
        self.assertIn("표 2", norm["method"])

    def test_cognition_and_parkinsonism_reductions_follow_evidence(self):
        self.assertAlmostEqual(gp.condition_reduction("mild", False)["mean"], 0.20)
        self.assertAlmostEqual(gp.condition_reduction("moderate", False)["mean"], 0.41)
        severe = gp.condition_reduction("severe", False)
        self.assertAlmostEqual(severe["mean"], 0.41)
        self.assertTrue(severe["flags"])
        unknown = gp.condition_reduction("unknown", False)
        vals = [0.11, 0.20, 0.41]
        m = sum(vals) / 3
        self.assertAlmostEqual(unknown["mean"], m)
        self.assertAlmostEqual(unknown["sd"], math.sqrt(sum(v * v for v in vals) / 3 - m * m))
        both = gp.condition_reduction("moderate", True)
        self.assertAlmostEqual(both["mean"], 0.41 + 0.17 / 2)
        self.assertAlmostEqual(both["sd"], 0.17 / math.sqrt(12))
        self.assertEqual(gp.condition_reduction("none", False)["mean"], 0.0)
        self.assertAlmostEqual(gp.condition_reduction("none", True)["mean"], 0.17)

    def test_unknown_severity_with_parkinsonism_mixes_branches_first(self):   # Codex #11
        r = gp.condition_reduction("unknown", True)
        self.assertAlmostEqual(r["mean"], 0.335, places=6)
        self.assertAlmostEqual(r["sd"], 0.12386, places=4)

    def test_mixture_and_single_most_recent_measurement(self):
        mean, var = gp.mixture(0.4, 0.01, 0.6, 0.04, 0.5)
        self.assertAlmostEqual(mean, 0.5)
        self.assertAlmostEqual(var, 0.025 + 0.25 * 0.04)
        items = [{"value_mps": 0.70, "date": "2026-09-18"}, {"value_mps": 0.60, "date": "2026-08-01"},
                 {"value_mps": 0.30, "date": "2024-01-01"}, {"value_mps": 5.0, "date": "2026-09-01"},
                 {"value_mps": 0.70, "date": "2026-09-18"}]
        chosen, skipped = gp.select_measurement(items, TODAY)
        self.assertEqual(chosen["value_mps"], 0.70)
        self.assertEqual(len(skipped), 4)          # 더 오래된 것·1년 초과·범위 밖·중복
        post_mean, post_var, used = gp.apply_measurement(0.45, 0.03, chosen, TODAY)
        expected = (0.45 / 0.03 + 0.70 / 0.05 ** 2) / (1 / 0.03 + 1 / 0.05 ** 2)
        self.assertAlmostEqual(post_mean, expected)
        self.assertEqual(used["sd"], 0.05)


class ExtractionTests(unittest.TestCase):
    def test_korean_and_english_records(self):
        text = "\n".join([
            "[주진단] 알츠하이머병(중등도)",
            "[위험요인] 보행 보조기 사용",
            "[기록 2026-05-02 · 전원소견서] 10m 보행검사 0.62 m/s",
            "[기록 2026-05-02 · 전원소견서] 기립성 저혈압 있음, 백내장 수술 전",
        ])
        p = gp.extract_profile_rules(text)
        self.assertEqual(p["dementia_severity"]["value"], "moderate")
        self.assertEqual(p["ambulation"]["value"], "walker")
        self.assertEqual(p["measured_gait_speeds"][0]["value_mps"], 0.62)
        self.assertEqual(p["measured_gait_speeds"][0]["date"], "2026-05-02")
        self.assertEqual(p["measured_gait_speeds"][0]["date_from"], "record")
        cats = {(f["category"], f["factor"]) for f in p["out_of_scope_factors"]}
        self.assertIn(("collapse", "기립성 저혈압"), cats)
        self.assertIn(("trip", "시력 저하"), cats)
        self.assertEqual(next(f for f in p["out_of_scope_factors"] if f["factor"] == "기립성 저혈압")["evidence"], "MOL2019")

    def test_severity_variants(self):
        self.assertEqual(gp.extract_profile_rules("경도인지장애 진단")["dementia_severity"]["value"], "cognitive_impairment")
        self.assertEqual(gp.extract_profile_rules("혈관성 치매(경도)")["dementia_severity"]["value"], "mild")
        self.assertEqual(gp.extract_profile_rules("치매로 입원")["dementia_severity"]["value"], "unknown")
        self.assertEqual(gp.extract_profile_rules("Alzheimer dementia CDR 2")["dementia_severity"]["value"], "moderate")
        self.assertEqual(gp.extract_profile_rules("Alzheimer's disease, moderate stage")["dementia_severity"]["value"], "moderate")
        self.assertEqual(gp.extract_profile_rules("고혈압")["dementia_severity"]["value"], "none")
        self.assertTrue(gp.extract_profile_rules("루이소체 치매")["parkinsonism"]["value"])
        self.assertEqual(gp.extract_profile_rules("보행속도 55 cm/s")["measured_gait_speeds"][0]["value_mps"], 0.55)

    def test_severity_needs_dementia_context(self):   # Codex #2
        self.assertEqual(gp.extract_profile_rules("고혈압 경도")["dementia_severity"]["value"], "none")
        self.assertEqual(gp.extract_profile_rules("경도인지장애에서 치매(중등도)로 진행")["dementia_severity"]["value"],
                         "moderate")

    def test_negation_is_scoped_to_the_clause(self):   # Codex #8, Codex2 #8
        p = gp.extract_profile_rules("파킨슨병 있음, 실신 없음")
        self.assertTrue(p["parkinsonism"]["value"])
        self.assertEqual(p["out_of_scope_factors"], [])
        self.assertFalse(gp.extract_profile_rules("파킨슨병 없습니다")["parkinsonism"]["value"])
        self.assertFalse(gp.extract_profile_rules("파킨슨병(없음)")["parkinsonism"]["value"])
        self.assertFalse(gp.extract_profile_rules("파킨슨병 (-)")["parkinsonism"]["value"])
        self.assertTrue(gp.extract_profile_rules("파킨슨병(치매 동반), 실신 없음")["parkinsonism"]["value"])
        self.assertTrue(gp.extract_profile_rules("파킨슨병으로 걸음이 좋지 않음")["parkinsonism"]["value"])
        p = gp.extract_profile_rules("파킨슨증 의심 소견 없음\nno orthostatic hypotension\n보행기 없이 독립 보행")
        self.assertFalse(p["parkinsonism"]["value"])
        self.assertEqual(p["out_of_scope_factors"], [])
        self.assertEqual(p["ambulation"]["value"], "independent")
        self.assertEqual(gp.extract_profile_rules("치매 아님, 섬망")["dementia_severity"]["value"], "none")

    def test_family_history_is_not_the_patients_diagnosis(self):
        cases = {
            "기립성 저혈압 있음, 파킨슨병 없음. 부친은 파킨슨병.": (False, "none", ["기립성 저혈압"]),
            "가족력: 파킨슨병, 치매": (False, "none", []),
            "모친 알츠하이머 치매(중등도)": (False, "none", []),
            "부친 부정맥": (False, "none", []),
            "파킨슨병(부친)": (False, "none", []),
            "가족력: 모친 치매. 본인은 알츠하이머 치매 중등도": (False, "moderate", []),
            "가족력 없음, 알츠하이머 치매": (False, "unknown", []),       # 부정된 가족력 뒤는 본인 진단
            "가족력(-), 파킨슨병 진단": (True, "none", []),
            "아들이 보호자로 상주, 치매 진행 중": (False, "unknown", []),  # 다른 절의 친족 언급은 무관
            "어머님 파킨슨병으로 보행 느림": (True, "none", []),          # 간병 호칭은 친족으로 보지 않음
            "Parkinson disease, father with dementia": (True, "none", []),
            "딸꾹질, 파킨슨병": (True, "none", []),
        }
        for text, expected in cases.items():
            p = gp.extract_profile_rules(text)
            got = (p["parkinsonism"]["value"], p["dementia_severity"]["value"],
                   [f["factor"] for f in p["out_of_scope_factors"]])
            self.assertEqual(got, expected, text)

    def test_conditional_inability_means_walking_with_help(self):
        cases = {
            "보행기 없이는 걷지 못함": "walker", "보행기 없이는 보행 불가": "walker",
            "지팡이 없이는 걷지 못함": "assisted", "부축 없이는 보행 불가": "assisted",
            "혼자서는 걷지 못함": "assisted", "혼자 걸을 수 없음": "assisted",
            "독립 보행 불가": "assisted", "독립 보행 어려움": "assisted", "독립 보행하지 못함": "assisted",
            "독립 보행은 아직 어려움": "unknown",
            "보행기로 걷지 못함": "non_ambulatory", "와상 상태로 이동 어려움": "non_ambulatory",
            "보행기 없이 독립 보행 가능": "independent", "와상 아님, 보행기로 걸음": "walker",
        }
        for text, expected in cases.items():
            self.assertEqual(gp.extract_profile_rules(text)["ambulation"]["value"], expected, text)

    def test_negation_of_another_predicate_does_not_negate_the_diagnosis(self):
        cases = {
            "파킨슨병으로 걷지 못함": (True, "none"), "파킨슨병으로 약 복용하지 않음": (True, "none"),
            "파킨슨병 있고 치매 없음": (True, "none"), "치매로 대화하지 못함": (False, "unknown"),
            "치매로 인해 식사하지 못함": (False, "unknown"),
            "알츠하이머병으로 진단되지 않음": (False, "none"), "파킨슨병 진단은 받지 않음": (False, "none"),
            "치매 증상 보이지 않음": (False, "none"),
        }
        for text, expected in cases.items():
            p = gp.extract_profile_rules(text)
            self.assertEqual((p["parkinsonism"]["value"], p["dementia_severity"]["value"]), expected, text)

    def test_wheelchair_use_is_not_inability_to_walk(self):   # Codex #9, Codex2 #9
        mixed = gp.extract_profile_rules("장거리에는 휠체어 사용. 병실에서는 독립 보행")["ambulation"]
        self.assertEqual(mixed["value"], "independent")
        self.assertTrue(mixed["flags"])
        self.assertEqual(gp.extract_profile_rules("휠체어로 이동")["ambulation"]["value"], "wheelchair_user")
        self.assertEqual(gp.extract_profile_rules("와상 상태")["ambulation"]["value"], "non_ambulatory")
        self.assertEqual(gp.extract_profile_rules("휠체어 사용하지 않음")["ambulation"]["value"], "unknown")
        wheel = gp.build_prior({"age": 85, "height_cm": 150}, "휠체어로 이동", today=TODAY)
        self.assertIsNone(wheel["not_applicable"])       # 걷는지 모를 뿐 보행 불가는 아님

    def test_stored_walker_does_not_override_explicit_non_ambulation(self):   # Codex2 new
        self.assertEqual(gp.extract_profile_rules("와상, 보행 불가. 침상 옆 보행기 보관")["ambulation"]["value"],
                         "non_ambulatory")
        self.assertEqual(gp.extract_profile_rules("과거 와상. 현재 보행기로 병실 안을 걸음")["ambulation"]["value"],
                         "walker")

    def test_goals_references_and_untested_values_are_not_measurements(self):   # Codex #5, Codex2 #5
        text = "\n".join(["재활 목표 보행속도 0.8 m/s; 보행검사 미시행", "정상 기준치 보행속도 1.0 m/s",
                          "보행속도 0.9 m/s (목표)", "보행검사 0.50 m/s (=50 cm/s)"])
        speeds = gp.extract_profile_rules(text)["measured_gait_speeds"]
        self.assertEqual([s["value_mps"] for s in speeds], [0.5])        # Codex #7: 괄호 안 환산값은 따로 세지 않음

    def test_measurement_date_beats_document_date(self):   # Codex #6, Codex2 #6
        text = "[기록 2026-09-28 · 전원 소견서] 2024-01-01 보행검사 0.50 m/s"
        m = gp.extract_profile_rules(text)["measured_gait_speeds"][0]
        self.assertEqual((m["date"], m["date_from"]), ("2024-01-01", "clause"))
        prior = gp.build_prior({"age": 81, "height_cm": 156}, text, today=TODAY)
        step = next(t for t in prior["trace"] if t["step"] == "과거 측정 보행속도 반영")
        self.assertIsNone(step["used"])
        self.assertIn("범위를 벗어남", step["skipped"][0]["why"])
        multi = "[기록 2026-09-28 · 소견서 · #7] 2024-01-01 검사 결과\n[기록 2026-09-28 · 소견서 · #7] 보행속도 0.50 m/s"
        m = gp.extract_profile_rules(multi)["measured_gait_speeds"][0]
        self.assertEqual((m["date"], m["date_from"]), ("2024-01-01", "previous_line"))

    def test_copied_measurements_are_counted_once(self):   # Codex #7
        text = "\n".join(["[기록 2026-09-10 · 소견서] 보행검사 0.50 m/s"] * 3)
        prior = gp.build_prior({"age": 81, "height_cm": 156}, text, today=TODAY)
        single = gp.build_prior({"age": 81, "height_cm": 156}, text.splitlines()[0], today=TODAY)
        self.assertEqual(prior["metrics"]["speed"], single["metrics"]["speed"])


class AiValidationTests(unittest.TestCase):
    TEXT = "[주진단] 알츠하이머병(중등도)\n[간호기록] 복도를 혼자 자주 걸어 다님\n[기록 2026-09-01] 파킨슨증 의심 소견 없음"

    def test_fabricated_quotes_are_dropped_and_numbers_never_come_from_ai(self):   # Codex #1, #4
        ai = {
            "dementia_severity": {"value": "mild", "quote": "치매 경도로 진단됨"},          # 기록에 없음
            "ambulation": {"value": "independent", "quote": "복도를 혼자 자주 걸어 다님"},
            "parkinsonism": {"value": False, "quote": "파킨슨증 의심 소견 없음"},
            "measured_gait_speeds": [{"value": 1, "unit": "m/s", "quote": "CDR 1"}],       # 스키마 밖 → 무시
            "out_of_scope_factors": [{"category": "collapse", "factor": "뇌전증", "quote": "파킨슨증 의심 소견 없음"}],
            "community_path_weight": 0.8, "weight_quotes": ["복도를 혼자 자주 걸어 다님"],
        }
        kept, dropped = gp.validate_ai_profile(ai, self.TEXT)
        self.assertNotIn("dementia_severity", kept)
        self.assertEqual(kept["ambulation"]["value"], "independent")
        self.assertFalse(kept["parkinsonism"]["value"])
        self.assertEqual(kept["community_path_weight"]["value"], 0.8)
        self.assertEqual(len(dropped), 1)
        prior = gp.build_prior({"age": 81, "height_cm": 156}, self.TEXT, use_ai=True, ai_caller=fake_ai(ai), today=TODAY)
        self.assertEqual(prior["profile"]["measured_gait_speeds"], [])
        self.assertNotIn("뇌전증", [f["factor"] for f in prior["out_of_scope"]["patient_factors"]])

    def test_polarity_is_checked_where_the_quote_is(self):   # Codex #2, Codex2 #2
        text = "[진단] 파킨슨병 진단\n[기록] 휠체어 사용하지 않음\n[기록] 파킨슨병 없음. 부친은 파킨슨병 있음"
        for claim in ({"parkinsonism": {"value": False, "quote": "파킨슨병 진단"}},
                      {"ambulation": {"value": "wheelchair_user", "quote": "휠체어 사용하지 않음"}},
                      {"parkinsonism": {"value": True, "quote": "파킨슨병 없음"}}):
            kept, _ = gp.validate_ai_profile(claim, text)
            self.assertEqual(kept, {}, claim)
        kept, _ = gp.validate_ai_profile({"parkinsonism": {"value": True, "quote": "파킨슨병"}}, "[기록] 파킨슨병 없음")
        self.assertEqual(kept, {})
        kept, _ = gp.validate_ai_profile({"parkinsonism": {"value": False, "quote": "파킨슨병 없음"}}, text)
        self.assertFalse(kept["parkinsonism"]["value"])
        # 줄 전체를 인용해도 부친의 언급은 환자의 파킨슨증 근거가 아님
        kept, _ = gp.validate_ai_profile(
            {"parkinsonism": {"value": True, "quote": "파킨슨병 없음. 부친은 파킨슨병 있음"}}, text)
        self.assertEqual(kept, {})

    def test_header_plus_fragment_quotes_are_checked_inside_that_line(self):
        # gpt-4o-mini가 실제로 낸 형태: 줄 머리 + 문장 일부
        text = "\n".join(["[기록 2026-09-01 · 전원 소견서 · #1] 기립성 저혈압 있음, 파킨슨병 없음. 부친은 파킨슨병.",
                          "[간호기록 2026-09-20] 환자 식사 양호. 오후에 복도를 보행기로 산책함.",
                          "[기록 2026-09-02 · 경과 · #2] 파킨슨병 진단"])
        head1 = "[기록 2026-09-01 · 전원 소견서 · #1] "
        cases = [
            ({"parkinsonism": {"value": False, "quote": head1 + "파킨슨병 없음."}}, True),
            ({"parkinsonism": {"value": True, "quote": head1 + "파킨슨병."}}, False),                 # 부친 언급
            ({"parkinsonism": {"value": False, "quote": "[기록 2026-09-02 · 경과 · #2] 파킨슨병 없음."}}, False),
            ({"community_path_weight": 0.4, "weight_quotes": ["[간호기록 2026-09-20] 오후에 복도를 보행기로 산책함."]}, True),
            ({"community_path_weight": 0.4, "weight_quotes": ["[간호기록 2026-09-20] 환자 식사 양호."]}, False),
        ]
        for ai, accepted in cases:
            kept, _ = gp.validate_ai_profile(ai, text)
            self.assertEqual(bool(kept), accepted, ai)

    def test_family_history_negation_cannot_erase_dementia(self):
        text = "[진단] 알츠하이머병(중등도)\n[기록] 치매 가족력 없음"
        kept, _ = gp.validate_ai_profile({"dementia_severity": {"value": "none", "quote": "치매 가족력 없음"}}, text)
        self.assertEqual(kept, {})
        kept, _ = gp.validate_ai_profile({"dementia_severity": {"value": "none", "quote": "치매 아님"}}, "[기록] 치매 아님")
        self.assertEqual(kept["dementia_severity"]["value"], "none")

    def test_severity_needs_dementia_context_not_just_a_word(self):   # Codex #2
        text = "[진단] 고혈압 경도\n[기록] CDR 1\n[진단] 알츠하이머 치매"
        kept, _ = gp.validate_ai_profile({"dementia_severity": {"value": "mild", "quote": "고혈압 경도"}}, text)
        self.assertEqual(kept, {})
        kept, _ = gp.validate_ai_profile({"dementia_severity": {"value": "mild", "quote": "CDR 1"}}, text)
        self.assertEqual(kept["dementia_severity"]["value"], "mild")

    def test_weight_needs_mobility_words_inside_the_quote(self):   # Codex #3, Codex2 #3
        text = "[간호기록] 식사 양호. 독립 보행\n[간호기록] 복도를 혼자 자주 걸어 다님"
        kept, _ = gp.validate_ai_profile({"community_path_weight": 1.0, "weight_quotes": ["식사 양호"]}, text)
        self.assertNotIn("community_path_weight", kept)
        kept, _ = gp.validate_ai_profile({"community_path_weight": 0.9, "weight_quotes": ["독립 보행"]}, text)
        self.assertEqual(kept["community_path_weight"]["value"], 0.9)
        self.assertEqual(gp.policy_weight(None, "independent"), (0.5, "default"))
        self.assertEqual(gp.policy_weight({"value": 0.9}, "unknown")[0], 0.5)
        self.assertEqual(gp.policy_weight({"value": 0.9}, "wheelchair_user")[0], 0.5)
        w, note = gp.policy_weight({"value": 0.1}, "independent")
        self.assertEqual(w, 0.5)
        self.assertIn("제한", note)
        self.assertEqual(gp.policy_weight({"value": 0.9}, "walker")[0], 0.5)
        self.assertEqual(gp.policy_weight({"value": 0.3}, "walker"), (0.3, "ai"))

    def test_ai_cannot_claim_non_ambulation_against_walking_records(self):
        text = "[기록] 와상, 보행 불가\n[기록] 오후에는 보행기로 복도를 걸음"
        kept, dropped = gp.validate_ai_profile({"ambulation": {"value": "non_ambulatory", "quote": "와상, 보행 불가"}}, text)
        self.assertEqual(kept, {})
        self.assertIn("충돌", dropped[0])
        # 조건부 불가는 보행 불가의 근거가 아니라 보조 보행의 근거
        cond = "[기록] 보행기 없이는 걷지 못함"
        kept, _ = gp.validate_ai_profile({"ambulation": {"value": "non_ambulatory", "quote": "보행기 없이는 걷지 못함"}}, cond)
        self.assertEqual(kept, {})
        kept, _ = gp.validate_ai_profile({"ambulation": {"value": "walker", "quote": "보행기 없이는 걷지 못함"}}, cond)
        self.assertEqual(kept["ambulation"]["value"], "walker")
        kept, _ = gp.validate_ai_profile({"ambulation": {"value": "independent", "quote": "독립 보행 불가"}},
                                         "[기록] 독립 보행 불가")
        self.assertEqual(kept, {})

    def test_malformed_ai_fields_fall_back_without_error(self):   # Codex #14
        bad = {"dementia_severity": "moderate", "ambulation": 7, "parkinsonism": [True],
               "community_path_weight": "0.9", "weight_quotes": "복도", "measured_gait_speeds": 7}
        kept, _ = gp.validate_ai_profile(bad, self.TEXT)
        self.assertEqual(kept, {})
        for payload in (bad, [1, 2], "text", None):
            prior = gp.build_prior({"age": 81, "height_cm": 156}, self.TEXT, use_ai=True,
                                   ai_caller=fake_ai(payload), today=TODAY)
            self.assertIsNotNone(prior["metrics"]["speed"])
            self.assertFalse(prior["ai"]["used"])

    def test_registered_patient_name_is_redacted_before_ai(self):   # Codex2 new
        seen = []
        gp.build_prior({"age": 81, "height_cm": 156, "name": "홍길동"}, "[기록] 홍길동 환자, 보행기 사용",
                       use_ai=True, ai_caller=fake_ai({}, seen=seen), today=TODAY)
        self.assertNotIn("홍길동", seen[0])
        self.assertNotIn("환자 환자", seen[0])
        self.assertIn("등록된 환자 이름은 '환자'로 가렸습니다", seen[0])
        self.assertEqual(gp.redact_names("홍길동님, 홍길동씨, 보호자 김철수", ["홍길동", None, "x"]),
                         "환자, 환자, 보호자 김철수")

    def test_unquoted_negation_does_not_erase_rule_finding(self):
        rule = gp.extract_profile_rules("루이소체 치매")
        kept, _ = gp.validate_ai_profile({"parkinsonism": {"value": False, "quote": None}}, "루이소체 치매")
        self.assertTrue(gp.merge_profiles(rule, kept)["parkinsonism"]["value"])

    def test_ai_weight_moves_estimate_and_failure_falls_back_honestly(self):
        patient = {"age": 81, "sex": "F", "height_cm": 156}
        ai = {"community_path_weight": 1.0, "weight_quotes": ["복도를 혼자 자주 걸어 다님"], "weight_rationale": "독립 보행"}
        with_ai = gp.build_prior(patient, self.TEXT, use_ai=True, ai_caller=fake_ai(ai), today=TODAY)
        self.assertTrue(with_ai["ai"]["used"])
        norm = gp.healthy_norm(81, 156, "velocity")["mean"]
        self.assertAlmostEqual(with_ai["metrics"]["speed"]["mean"], norm - 0.41, places=3)
        failed = gp.build_prior(patient, self.TEXT, use_ai=True, ai_caller=fake_ai({}, success=False), today=TODAY)
        self.assertFalse(failed["ai"]["used"])
        self.assertEqual(failed["ai"]["error_type"], "missing_key")
        default = gp.build_prior(patient, self.TEXT, today=TODAY)
        self.assertEqual(failed["metrics"]["speed"], default["metrics"]["speed"])


class BuildPriorTests(unittest.TestCase):
    def test_ltc_patient_mixes_paths_and_documents_sources(self):
        prior = gp.build_prior({"age": 81, "sex": "F", "height_cm": 156}, "[주진단] 알츠하이머병(중등도)", today=TODAY)
        speed = prior["metrics"]["speed"]
        self.assertIsNone(prior["not_applicable"])
        self.assertLess(speed["p10"], speed["mean"])
        self.assertLess(speed["mean"], speed["p90"])
        community = gp.healthy_norm(81, 156, "velocity")["mean"] - 0.41
        self.assertAlmostEqual(speed["mean"], 0.5 * community + 0.5 * 0.475, places=3)
        self.assertTrue(speed["use_for_provisional"])
        self.assertFalse(prior["metrics"]["stride_length"]["use_for_provisional"])
        sources = json.dumps(prior["trace"], ensure_ascii=False)
        for sid in ("KIM2025_KR", "PEEL2019", "KUYS2014", "THIMYLIEN2026"):
            self.assertIn(sid, sources)
        self.assertEqual(set(prior["unsupported_metrics"]), {"speed_cv", "sway", "freeze_ratio", "stride_cv"})
        shares = prior["out_of_scope"]["mechanism_share_ltc"]["not_predictable_by_sensors"]
        self.assertAlmostEqual(sum(shares.values()), 0.57, places=2)

    def test_community_setting_does_not_mix_with_facility_average(self):
        prior = gp.build_prior({"age": 70, "height_cm": 165}, "혈관성 치매(경도)", care_setting="community", today=TODAY)
        self.assertAlmostEqual(prior["metrics"]["speed"]["mean"], gp.healthy_norm(70, 165, "velocity")["mean"] - 0.20, places=3)
        self.assertNotIn("환경 평균과 섞기", [t["step"] for t in prior["trace"]])

    def test_non_ambulatory_and_missing_age_do_not_invent_values(self):
        bed = gp.build_prior({"age": 88, "height_cm": 150}, "와상 상태", today=TODAY)
        self.assertEqual(bed["not_applicable"], gp.NOT_AMBULATORY_NOTE)
        self.assertEqual(bed["metrics"], {})
        no_age = gp.build_prior({"age": None}, "치매", today=TODAY)
        self.assertEqual(no_age["metrics"], {})
        self.assertIsNotNone(no_age["not_applicable"])


class MmwPriorPathTests(unittest.TestCase):
    PRIOR = {"id": 3, "metrics": {
        "speed": {"mean": 0.45, "tau": 0.28, "sigma_within_prior": S0, "use_for_provisional": True},
        "stride_length": {"mean": 0.47, "tau": 0.23, "sigma_within_prior": 0.0411, "use_for_provisional": False}}}

    def payload(self, speed=0.45, stride=0.45, drop=0.0):
        return {"target_id": "room_x", "raw": {"speed": speed, "speed_cv": 0.15, "sway": 0.05, "freeze_ratio": 0.1,
                                               "stride_length": stride, "stride_cv": 0.09, "height_drop": drop},
                "quality": {"reliable": True, "walking": True}, "presence": {"n_targets": 1}}

    def test_provisional_judgement_from_first_window(self):
        r = L.evaluate(self.payload(speed=0.45), {}, persist=False, prior=self.PRIOR)
        self.assertTrue(r["provisional"])
        self.assertEqual(r["baseline_source"], "population_prior")
        self.assertEqual(r["alert_level"], "normal")
        self.assertFalse(r["alarm"])

    def test_running_mean_far_below_expectation_escalates_but_never_alarms(self):
        fast = {"id": 4, "metrics": {"speed": {"mean": 0.9, "tau": 0.28, "sigma_within_prior": S0,
                                               "use_for_provisional": True}}}
        store, levels = {}, []
        for _ in range(5):
            r = L.evaluate(self.payload(speed=0.3), store, persist=False, prior=fast)
            levels.append(r["alert_level"])
            self.assertFalse(r["alarm"])
            self.assertTrue(r["provisional"])
        # 손 계산(자유도 ν0+n−1로 합친 σ_w): z = −1.37, −1.63, −1.82, −1.92, −1.99
        self.assertEqual(levels, ["caution", "caution", "caution", "caution", "warning"])
        pooled = math.sqrt(4 * S0 ** 2 / 7)
        self.assertAlmostEqual(r["zscore"]["speed"], round(-0.6 / math.sqrt(0.28 ** 2 + pooled ** 2 / 5), 2))

    def test_pooled_within_sd_uses_n_minus_one_degrees_of_freedom(self):   # Codex #10
        self.assertAlmostEqual(L._within_sd(4, 0.0, S0, 0.05), math.sqrt(4 * S0 ** 2 / 7))
        self.assertAlmostEqual(L._within_sd(2, 0.1, S0, 0.05), math.sqrt((4 * S0 ** 2 + 2 * 0.01) / 5))
        self.assertEqual(L._within_sd(5, 0.2, S0, 0.05), 0.2)
        self.assertEqual(L._within_sd(1, None, S0, 0.05), S0)

    def test_height_drop_is_critical_even_before_personal_baseline(self):
        r = L.evaluate(self.payload(drop=1.5), {}, persist=False, prior=self.PRIOR)
        self.assertEqual(r["alert_level"], "critical")
        self.assertTrue(r["alarm"])

    def test_very_fast_walking_adds_identity_note_without_changing_level(self):
        r = L.evaluate(self.payload(speed=2.0), {}, persist=False, prior=self.PRIOR)
        self.assertIn("identity_note", r)
        self.assertEqual(r["alert_level"], "normal")

    def test_failed_stride_is_missing_in_valid_totals_but_kept_in_legacy_totals(self):   # Codex #12, Codex2 #12
        store = {}
        for i in range(10):
            L.evaluate(self.payload(stride=0.5 if i < 2 else 0.0), store, persist=False, prior=self.PRIOR)
        bed = store["room_x"]
        self.assertEqual(bed["n"], 10)
        self.assertEqual(bed["stride_length"]["v_n"], 2)
        self.assertAlmostEqual(bed["stride_length"]["v_sum"], 1.0)
        self.assertEqual(bed[L.VALID_KEY], 10)
        # 기존 합계는 이전 버전과 같은 방식(보폭 실패 0.0 포함) → 이전 버전 코드가 읽어도 원래와 같은 결과
        self.assertAlmostEqual(bed["stride_length"]["sum"], 1.0)
        self.assertEqual(L.baseline_stats(bed)["stride_length_mu"], round(1.0 / 10, 3))
        # 보폭은 구했지만 변동계수를 못 구한 구간(stride_cv 0.0)도 변동계수에서는 결측
        cv_fail = self.payload(stride=0.5)
        cv_fail["raw"]["stride_cv"] = 0.0
        L.evaluate(cv_fail, store, persist=False, prior=self.PRIOR)
        self.assertEqual((bed["stride_length"]["v_n"], bed["stride_cv"]["v_n"]), (3, 2))

    def test_legacy_updates_after_rollback_are_detected(self):   # Codex #12
        store = {}
        for _ in range(3):
            L.evaluate(self.payload(), store, persist=False, prior=self.PRIOR)
        stale = copy.deepcopy(store)
        stale["room_x"]["n"] += 1                              # 이전 버전 코드는 기존 합계와 n만 올린다
        for m in L.METRICS:
            stale["room_x"][m]["sum"] += 0.1
        r = L.evaluate(self.payload(), stale, persist=False, prior=self.PRIOR)
        self.assertIn("이전 버전", " ".join(r["reasons"]))
        self.assertEqual(stale["room_x"][L.VALID_KEY], stale["room_x"]["n"])
        self.assertEqual(stale["room_x"]["speed"]["v_n"], 5)

    def test_non_finite_and_absurd_values_never_enter_the_baseline(self):   # Codex #13, Codex2 #13
        store = {}
        L.evaluate(self.payload(speed=float("inf")), store, persist=False, prior=self.PRIOR)
        L.evaluate(self.payload(stride=float("nan")), store, persist=False, prior=self.PRIOR)
        L.evaluate(self.payload(speed=1e308), store, persist=False, prior=self.PRIOR)
        bed = store["room_x"]
        for m in L.METRICS:
            self.assertTrue(math.isfinite(bed[m]["sum"]) and math.isfinite(bed[m]["sumsq"]))
            self.assertTrue(math.isfinite(bed[m]["v_sum"]) and math.isfinite(bed[m]["v_sumsq"]))
        self.assertEqual(bed["speed"]["v_n"], 1)

    def test_switches_to_personal_phase_and_compares_before_adding_current(self):
        store = {}
        speeds = [0.40, 0.50, 0.45, 0.55, 0.35]
        for s in speeds:
            L.evaluate(self.payload(speed=s), store, persist=False, prior=self.PRIOR)
        r = L.evaluate(self.payload(speed=0.45), store, persist=False, prior=self.PRIOR)
        self.assertFalse(r["provisional"])
        self.assertEqual(r["baseline_source"], "blended")
        mean = sum(speeds) / 5
        sd = math.sqrt(sum(s * s for s in speeds) / 5 - mean * mean)
        s_w = max(sd, L.SIGMA_FLOOR["speed"])
        prec = 1 / 0.28 ** 2 + 5 / s_w ** 2
        post = (0.45 / 0.28 ** 2 + 5 * mean / s_w ** 2) / prec
        self.assertAlmostEqual(r["zscore"]["speed"], round((0.45 - post) / s_w, 2), places=2)
        self.assertEqual(store["room_x"]["n"], 6)
        self.assertFalse(r["alarm"])   # 14일 전이라 정식 알람 보류

    def test_without_prior_behaviour_is_unchanged(self):
        r = L.evaluate(self.payload(), {}, persist=False)
        self.assertNotIn("provisional", r)
        self.assertEqual(r["alert_level"], "normal")
        self.assertIn("베이스라인 학습 중", " ".join(r["reasons"]))

    def test_walking_in_non_ambulatory_record_is_flagged(self):
        prior = {"id": 9, "metrics": {}, "not_applicable": gp.NOT_AMBULATORY_NOTE}
        r = L.evaluate(self.payload(), {}, persist=False, prior=prior)
        self.assertIn("identity_note", r)
        self.assertNotIn("provisional", r)


class RiskScoreTests(unittest.TestCase):
    def test_provisional_gait_level_is_reported_but_not_scored(self):   # Codex2 new
        r = integrated_analysis.compute_risk_score({"mmw_latest": {"alert_level": "warning", "provisional": True}})
        self.assertIsNone(r["combined_risk_score"])
        self.assertEqual(r["provisional_levels"], {"mmwave_gait": "warning"})
        fall = integrated_analysis.compute_risk_score({"mmw_latest": {"alert_level": "critical", "provisional": True}})
        self.assertEqual(fall["combined_risk_score"], integrated_analysis.RISK_LEVEL_SCORE["critical"])
        formal = integrated_analysis.compute_risk_score({"mmw_latest": {"alert_level": "warning"}})
        self.assertEqual(formal["combined_risk_score"], integrated_analysis.RISK_LEVEL_SCORE["warning"])


class DashboardGaitPriorApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = patch.object(db, "DB_PATH", os.path.join(self.temp.name, "test.db"))
        p.start()
        self.addCleanup(p.stop)
        dashboard_api.init_dashboard_schema()
        app = Flask(__name__)
        app.register_blueprint(dashboard_api.create_blueprint(lambda bed: {"success": True},
                                                              lambda: {"configured": False, "available": False}))
        self.client = app.test_client()

    def test_emr_ingest_then_prior_build_and_patient_detail(self):
        base = "/api/dashboard/patients/bed_01"
        self.assertEqual(self.client.post(base + "/emr", json={"profile": {"age": 200}}).status_code, 400)
        self.assertEqual(self.client.post(base + "/emr", json={"records": [{"text": ""}]}).status_code, 400)
        self.assertEqual(self.client.post("/api/dashboard/patients/nope/emr", json={}).status_code, 404)
        r = self.client.post(base + "/emr", json={
            "profile": {"age": 81, "sex": "F", "height_cm": 156, "diagnoses": ["알츠하이머병(중등도)"]},
            "records": [{"record_date": "2026-09-01", "source": "전원 소견서", "text": "10m 보행검사 0.52 m/s\n기립성 저혈압"}]})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["updated_fields"], ["age", "diagnosis_json", "height_cm", "sex"])
        self.assertIsNone(self.client.get(base + "/gait-prior").get_json()["prior"])
        self.assertEqual(self.client.post(base + "/gait-prior", json={"care_setting": "moon"}).status_code, 400)
        built = self.client.post(base + "/gait-prior", json={}).get_json()
        self.assertTrue(built["success"])
        self.assertEqual(built["prior"]["target_id"], "room_01")
        self.assertEqual(built["summary"]["dementia_severity"], "moderate")
        self.assertIn("기립성 저혈압", built["summary"]["out_of_scope_factors"])
        steps = [t["step"] for t in built["prior"]["trace"]]
        self.assertIn("과거 측정 보행속도 반영", steps)
        detail = self.client.get(base).get_json()
        self.assertEqual(detail["gait_prior"]["id"], built["prior"]["id"])
        with db.get_conn() as conn:
            loaded = gp.get_active_prior_for_target(conn, "room_01")
        self.assertEqual(loaded["id"], built["prior"]["id"])
        again = self.client.post(base + "/gait-prior").get_json()     # 본문 없음 → 기본값
        with db.get_conn() as conn:
            active = conn.execute("SELECT COUNT(*) FROM gait_priors WHERE bed_id='bed_01' AND active=1").fetchone()[0]
        self.assertEqual(active, 1)
        self.assertNotEqual(again["prior"]["id"], built["prior"]["id"])

    def test_malformed_bodies_are_rejected_without_replacing_the_prior(self):   # Codex #15
        base = "/api/dashboard/patients/bed_01/gait-prior"
        self.client.post("/api/dashboard/patients/bed_01/emr", json={"profile": {"age": 80, "height_cm": 160}})
        first = self.client.post(base, json={}).get_json()["prior"]["id"]
        for body in ("{bad json", "null", "false", "[]", '{"use_ai": "yes"}'):
            r = self.client.post(base, data=body, content_type="application/json")
            self.assertEqual(r.status_code, 400, body)
        self.assertEqual(self.client.get(base).get_json()["prior"]["id"], first)

    def test_report_payload_carries_prior_and_out_of_scope_facts(self):
        self.client.post("/api/dashboard/patients/bed_01/emr",
                         json={"profile": {"age": 80, "height_cm": 160}, "records": [{"text": "치매(경도), 실신 병력"}]})
        self.client.post("/api/dashboard/patients/bed_01/gait-prior", json={})
        investigation = {"patient_state": {}, "final_results": []}
        payload = integrated_analysis._build_payload("bed_01", investigation, {}, None, None)
        self.assertIsNotNone(payload["보행_기대범위_문헌기반"]["speed_expected_mps"])
        factors = [f["factor"] for f in payload["센서_예측범위_밖_위험"]["patient_factors"]]
        self.assertIn("실신 병력", factors)
        self.assertIsNone(integrated_analysis._build_payload("bed_zz", investigation, {}, None, None)["보행_기대범위_문헌기반"])


class MmwServerInputTests(unittest.TestCase):
    """운영 DB·기준선 파일에 쓰지 않도록 임시 DB와 메모리 기준선으로 바꿔 실행한다."""

    def setUp(self):
        import mmw_server
        self.server = mmw_server
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, attr, value in ((db, "DB_PATH", os.path.join(self.temp.name, "test.db")),
                                    (mmw_server, "baseline_store", {}),
                                    (mmw_server.L, "save_baseline", lambda *a, **k: None),
                                    (mmw_server, "_prior_for", lambda target_id: None)):
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        db.init_db()
        self.client = mmw_server.app.test_client()

    def test_invalid_gait_fields_do_not_block_the_fall_rule(self):   # Codex2 P1
        body = ('{"target_id": "t", "raw": {"speed": Infinity, "stride_cv": null, "height_drop": 1.5},'
                ' "quality": {"reliable": true, "walking": true}, "presence": {"n_targets": 1}}')
        r = self.client.post("/mmw", data=body, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["alert_level"], "critical")
        latest = self.server.latest_by_target["t"]
        self.assertEqual(latest["quality"]["invalid_fields"], ["speed", "stride_cv"])
        self.assertEqual(self.server.baseline_store, {})         # 보행 기준선에는 넣지 않음

    def test_missing_raw_is_still_rejected(self):
        self.assertEqual(self.client.post("/mmw", json={"raw": {}}).status_code, 400)

    def test_malformed_envelopes_get_400_or_safe_defaults_instead_of_500(self):
        post = lambda body: self.client.post("/mmw", data=body, content_type="application/json")
        self.assertEqual(post("[1, 2]").status_code, 400)
        self.assertEqual(post('{"target_id": 5, "raw": {"speed": 0.5}}').status_code, 400)
        self.assertEqual(post('{"target_id": ["a"], "raw": {"speed": 0.5}}').status_code, 400)
        r = post('{"raw": {"speed": 0.5, "height_drop": 1.5}, "quality": "ok", "presence": 3}')
        self.assertEqual((r.status_code, r.get_json()["alert_level"]), (200, "critical"))
        huge = post('{"target_id": "h", "raw": {"speed": 1' + "0" * 400 + ', "height_drop": 1.5}}')
        self.assertEqual((huge.status_code, huge.get_json()["alert_level"]), (200, "critical"))
        self.assertEqual(self.server.latest_by_target["h"]["quality"]["invalid_fields"], ["speed"])


if __name__ == "__main__":
    unittest.main()
