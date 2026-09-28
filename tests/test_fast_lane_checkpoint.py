import json
import tempfile
import unittest
import sys
import types
from collections import defaultdict, deque
from pathlib import Path
from unittest.mock import patch

sys.modules.setdefault("requests", types.SimpleNamespace())

import fast_market_lane as lane


class FastLaneCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.old_history = lane._HISTORY
        self.old_last = lane._LAST_CHECKPOINT
        lane._HISTORY = defaultdict(lambda: deque(maxlen=24))
        lane._LAST_CHECKPOINT = 0.0

    def tearDown(self):
        lane._HISTORY = self.old_history
        lane._LAST_CHECKPOINT = self.old_last

    def test_checkpoint_round_trip_restores_recent_samples(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "fast.json"
            lane._HISTORY["TEST.IS"].append({
                "ts": 995.0, "price": 123.4, "volume": 1000.0
            })
            with patch.object(lane, "FAST_STATE_PATH", state), \
                 patch.object(lane.time, "time", return_value=1_000.0):
                self.assertTrue(lane.checkpoint_fast_state(force=True))
                lane._HISTORY.clear()
                lane._load_checkpoint()

            self.assertEqual(len(lane._HISTORY["TEST.IS"]), 1)
            self.assertEqual(lane._HISTORY["TEST.IS"][0]["price"], 123.4)

    def test_expired_checkpoint_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "fast.json"
            state.write_text(json.dumps({
                "saved_at": 1.0,
                "history": {"TEST.IS": [{"ts": 1.0, "price": 10, "volume": 1}]},
            }))
            with patch.object(lane, "FAST_STATE_PATH", state), \
                 patch.object(lane.time, "time", return_value=10_000.0):
                lane._load_checkpoint()
            self.assertFalse(lane._HISTORY)


if __name__ == "__main__":
    unittest.main()
