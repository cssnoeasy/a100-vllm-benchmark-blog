import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "runner"))
from unittest.mock import patch

from runner_core import atomic_write_json, choose_port, heartbeat, inspect_run, port_candidates, update_manifest, validate_config


class RunnerCoreTests(unittest.TestCase):
    def test_atomic_manifest_events_and_stale_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            atomic_write_json(run_dir / "manifest.json", {"run_id": "demo", "status": "created"})
            heartbeat(run_dir, stage="starting")
            update_manifest(run_dir, "starting", started_at="now")
            update_manifest(run_dir, "ready", ready_at="now")
            manifest = json.loads((run_dir / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "ready")
            events = (run_dir / "events.jsonl").read_text().splitlines()
            self.assertGreaterEqual(len(events), 2)
            self.assertFalse(inspect_run(run_dir)["stale"])

    def test_invalid_transition_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            atomic_write_json(run_dir / "manifest.json", {"run_id": "demo", "status": "created"})
            with self.assertRaises(ValueError):
                update_manifest(run_dir, "benchmarking")

    def test_corrupt_manifest_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text("{")
            info = inspect_run(run_dir)
            self.assertEqual(info["status"], "corrupt")

    def test_extended_resource_states_are_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            atomic_write_json(run_dir / "manifest.json", {"run_id": "demo", "status": "created"})
            update_manifest(run_dir, "queued")
            update_manifest(run_dir, "waiting_resources")
            update_manifest(run_dir, "starting")
            update_manifest(run_dir, "recovering")
            update_manifest(run_dir, "queued")
            self.assertEqual(load_status(run_dir), "queued")

    def test_port_candidates_and_fallback(self):
        self.assertEqual(port_candidates({"port": 8000, "port_candidates": [8001, 8001], "port_range": [8002, 8003]}), [8000, 8001, 8002, 8003])
        with patch("runner_core.listening_ports", return_value={8000}):
            self.assertEqual(choose_port(8000, [8001, 8002]), 8001)
        with patch("runner_core.listening_ports", return_value={8000, 8001}):
            with self.assertRaises(RuntimeError):
                choose_port(8000, [8001])

    def test_port_config_validation(self):
        base = {
            "experiment": "demo", "service_mode": "single_gpu", "feature": "baseline", "feature_variant": "default",
            "model_path": ".", "model_name": "demo", "port": 8000, "cuda_visible_devices": "0",
            "tensor_parallel_size": 1, "max_model_len": 128, "gpu_memory_utilization": 0.8, "dataset": "random",
            "input_len": 1, "output_len": 1, "num_prompts": 1, "max_concurrency": 1, "request_rate": "inf", "warmup_requests": 1, "trial": "t1",
        }
        self.assertFalse(validate_config({**base, "port_candidates": [8001], "port_range": [8002, 8003]}, Path.cwd()))
        self.assertTrue(validate_config({**base, "port_range": [9000]}, Path.cwd()))


def load_status(run_dir):
    return json.loads((run_dir / "manifest.json").read_text())["status"]


if __name__ == "__main__":
    unittest.main()
