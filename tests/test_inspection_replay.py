import importlib.util
import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKLOAD_ROOT = PROJECT_ROOT / "scripts" / "workloads" / "inspection-replay"
SPEC = importlib.util.spec_from_file_location("replay", WORKLOAD_ROOT / "run_inspection_replay.py")
replay = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(replay)


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = replay.load_scenarios(WORKLOAD_ROOT / "scenarios.jsonl")
        cls.by_id = {item["scenario_id"]: item for item in cls.scenarios}

    def test_dataset_has_planned_distribution(self):
        counts = {}
        for item in self.scenarios:
            counts[item["scenario_type"]] = counts.get(item["scenario_type"], 0) + 1
        self.assertEqual(
            counts,
            {
                "hazard_decision": 16,
                "fault_diagnosis": 8,
                "inspection_summary": 4,
                "invalid_or_edge": 2,
            },
        )

    def test_all_configs_resolve(self):
        for name in ("inspection-smoke.yaml", "inspection-preflight-5m.yaml", "inspection-soak-60m.yaml"):
            config = replay.resolve_config(PROJECT_ROOT / "configs" / "experiments" / name)
            self.assertEqual(config["model"], "Qwen2.5-7B-Instruct")
            self.assertTrue(Path(config["scenarios_file"]).is_absolute())

    def test_action_validation(self):
        scenario = self.by_id["hazard_decision_001"]
        ok, error = replay.validate_output(
            scenario, json.dumps({"thought": "高危容器需立即后退", "actionCode": 2}, ensure_ascii=False)
        )
        self.assertTrue(ok, error)
        ok, error = replay.validate_output(
            scenario, json.dumps({"thought": "错误动作", "actionCode": 14}, ensure_ascii=False)
        )
        self.assertFalse(ok)
        self.assertIn("expected=2", error)

    def test_repairs_escaped_field_quote(self):
        scenario = self.by_id["hazard_decision_009"]
        output = '{"thought":"确认无残留符合安全规程。",\\"actionCode\\":14}'
        ok, error = replay.validate_output(scenario, output)
        self.assertTrue(ok, error)

    def test_action_thought_length_validation(self):
        scenario = self.by_id["hazard_decision_001"]
        output = {"thought": "过" * 51, "actionCode": 2}
        ok, error = replay.validate_output(scenario, json.dumps(output, ensure_ascii=False))
        self.assertFalse(ok)
        self.assertIn("50", error)

    def test_stream_payload_requests_usage(self):
        config = replay.resolve_config(PROJECT_ROOT / "configs" / "experiments" / "inspection-smoke.yaml")
        payload = replay.build_payload(config, self.by_id["hazard_decision_001"])
        self.assertEqual(payload["stream_options"], {"include_usage": True})

    def test_diagnosis_validation(self):
        scenario = self.by_id["fault_diagnosis_001"]
        output = {
            "probableCauses": ["过滤器堵塞"],
            "inspectionSteps": ["停机并检查过滤器"],
            "safetyAction": "保持设备隔离",
        }
        self.assertEqual(replay.validate_output(scenario, json.dumps(output, ensure_ascii=False)), (True, None))

        output["inspectionSteps"] = [""]
        ok, error = replay.validate_output(scenario, json.dumps(output, ensure_ascii=False))
        self.assertFalse(ok)
        self.assertIn("non-empty strings", error)

    def test_markdown_validation(self):
        scenario = self.by_id["inspection_summary_001"]
        output = "\n".join(scenario["expected"]["required_sections"])
        self.assertEqual(replay.validate_output(scenario, output), (True, None))

    def test_clarification_validation(self):
        scenario = self.by_id["invalid_or_edge_001"]
        output = {
            "status": "NEEDS_CLARIFICATION",
            "missingOrConflictingFields": ["危险等级", "坐标"],
            "safeAction": "停止并等待人工确认",
        }
        self.assertEqual(replay.validate_output(scenario, json.dumps(output, ensure_ascii=False)), (True, None))

        output["safeAction"] = "继续低速运行"
        ok, error = replay.validate_output(scenario, json.dumps(output, ensure_ascii=False))
        self.assertFalse(ok)
        self.assertIn("stop", error)

    def test_quality_gate(self):
        config = replay.resolve_config(PROJECT_ROOT / "configs" / "experiments" / "inspection-smoke.yaml")
        results = [
            {
                "status": "success",
                "scenario_type": "hazard_decision",
                "output_parse_ok": True,
                "error_type": None,
                "parse_error": None,
                "e2e_latency_ms": 120.0,
                "ttft_ms": 20.0,
                "input_tokens": 10,
                "output_tokens": 5,
            }
        ]
        summary = replay.summarize(results, 1000.0, 1001.0, config)
        self.assertTrue(summary["quality_gate"]["passed"])

    def test_smoke_selector_covers_all_types(self):
        config = replay.resolve_config(PROJECT_ROOT / "configs" / "experiments" / "inspection-smoke.yaml")
        selector = replay.ScenarioSelector(self.scenarios, config)
        selected = [selector.next()["scenario_type"] for _ in range(6)]
        self.assertEqual(set(selected[:4]), replay.SCENARIO_TYPES)

    def test_percentile(self):
        self.assertEqual(replay.percentile([1, 2, 3, 4], 50), 2.5)
        self.assertIsNone(replay.percentile([], 99))


if __name__ == "__main__":
    unittest.main()
