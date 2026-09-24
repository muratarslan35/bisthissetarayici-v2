import os
import unittest
from pathlib import Path
from unittest.mock import patch

import resource_guard


ROOT = Path(__file__).resolve().parents[1]


class ResourceGuardTests(unittest.TestCase):
    def test_low_memory_yields(self):
        with patch("resource_guard._memory_state", return_value=(300.0, 0.08)), \
             patch("resource_guard._load_per_cpu", return_value=0.20), \
             patch.dict(os.environ, {
                 "BIST_MIN_AVAILABLE_MEMORY_MB": "512",
                 "BIST_MIN_AVAILABLE_MEMORY_RATIO": "0.15",
                 "BIST_MAX_LOAD_PER_CPU": "0.90",
             }, clear=False):
            state = resource_guard.host_pressure_state()

        self.assertTrue(state.should_yield)
        self.assertEqual(state.reason, "memory")

    def test_high_cpu_yields(self):
        with patch("resource_guard._memory_state", return_value=(4096.0, 0.70)), \
             patch("resource_guard._load_per_cpu", return_value=1.15), \
             patch.dict(os.environ, {
                 "BIST_MAX_LOAD_PER_CPU": "0.90",
             }, clear=False):
            state = resource_guard.host_pressure_state()

        self.assertTrue(state.should_yield)
        self.assertEqual(state.reason, "cpu")

    def test_normal_host_does_not_yield(self):
        with patch("resource_guard._memory_state", return_value=(4096.0, 0.65)), \
             patch("resource_guard._load_per_cpu", return_value=0.35):
            state = resource_guard.host_pressure_state()

        self.assertFalse(state.should_yield)
        self.assertEqual(state.reason, "ok")


class SharedHostServiceContractTests(unittest.TestCase):
    def test_slice_caps_total_bist_resources(self):
        text = (ROOT / "deploy" / "bist-trading.slice.in").read_text()
        self.assertIn("CPUQuota=80%", text)
        self.assertIn("MemoryHigh=30%", text)
        self.assertIn("MemoryMax=40%", text)
        self.assertIn("IOWeight=10", text)

    def test_worker_is_low_priority_and_single_threaded_math(self):
        text = (ROOT / "deploy" / "bist-trading-worker.service.in").read_text()
        self.assertIn("Slice=bist-trading.slice", text)
        self.assertIn("Nice=12", text)
        self.assertIn("CPUQuota=70%", text)
        self.assertIn("MemoryMax=32%", text)
        self.assertIn("OOMScoreAdjust=800", text)
        self.assertIn("OPENBLAS_NUM_THREADS=1", text)
        self.assertIn("OMP_NUM_THREADS=1", text)
        self.assertIn("IOSchedulingClass=idle", text)

    def test_web_is_small_and_separate_from_worker(self):
        text = (ROOT / "deploy" / "bist-trading-web.service.in").read_text()
        self.assertIn("gunicorn", text)
        self.assertIn("app:app", text)
        self.assertIn("CPUQuota=20%", text)
        self.assertIn("MemoryMax=12%", text)
        self.assertNotIn("worker.py", text)

    def test_installer_never_manages_ims_units(self):
        text = (ROOT / "deploy" / "install_shared_host_services.sh").read_text()
        # IMS names may only be inspected in the read-only status footer.
        self.assertNotIn('systemctl restart "$svc"', text)
        self.assertNotIn('systemctl stop "$svc"', text)
        self.assertNotIn('systemctl enable "$svc"', text)
        self.assertIn("IMS services (read-only status check", text)


if __name__ == "__main__":
    unittest.main()
