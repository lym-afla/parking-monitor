import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import state_store


class _NoopStore:
    def create_event(self, *args, **kwargs):
        raise AssertionError("No transition event is expected in this fixture")


def _monitor_process(state_path, entered, release):
    import monitor

    original = monitor.apply_check_result

    def paused_apply(state, enabled, store, checked_at=None):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test did not release monitor mutation")
        return original(state, enabled, store, checked_at=checked_at)

    with (
        patch.object(monitor, "STATE_FILE", state_path),
        patch.object(monitor, "apply_check_result", side_effect=paused_apply),
    ):
        monitor.record_check_result(False, _NoopStore())


def _command_process(state_path, started, finished):
    from command_service import CommandService

    started.set()
    CommandService(state_path, health_provider=lambda: {}).set_normal_interval(900)
    finished.set()


class StateContentionTests(unittest.TestCase):
    def test_new_state_file_is_created_with_shared_runtime_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"

            with patch.object(state_store.os, "chmod", wraps=state_store.os.chmod) as chmod:
                state_store.replace_json_state(state_path, {"checks": 1})

            self.assertTrue(
                any(call.args[1] == 0o660 for call in chmod.call_args_list),
                chmod.call_args_list,
            )

    def test_monitor_and_command_processes_serialize_read_modify_write(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "checks": 12,
                        "hits": 3,
                        "last_check": "2026-08-27T11:30:00+03:00",
                        "last_enabled": True,
                        "interval": 1800,
                        "error": "old error",
                    }
                ),
                encoding="utf-8",
            )
            context = multiprocessing.get_context("spawn")
            entered = context.Event()
            release = context.Event()
            command_started = context.Event()
            command_finished = context.Event()
            monitor_process = context.Process(
                target=_monitor_process,
                args=(str(state_path), entered, release),
            )
            command_process = context.Process(
                target=_command_process,
                args=(str(state_path), command_started, command_finished),
            )

            monitor_process.start()
            self.assertTrue(entered.wait(10), "monitor never entered locked mutation")
            command_process.start()
            self.assertTrue(command_started.wait(10), "command process never started")
            self.assertFalse(
                command_finished.wait(0.3),
                "command mutation was not blocked by the monitor lock",
            )
            release.set()
            monitor_process.join(10)
            command_process.join(10)

            self.assertEqual(monitor_process.exitcode, 0)
            self.assertEqual(command_process.exitcode, 0)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["interval"], 900)
            self.assertEqual(state["checks"], 13)
            self.assertEqual(state["hits"], 3)
            self.assertIs(state["last_enabled"], False)
            self.assertIsNone(state["error"])
            self.assertNotEqual(state["last_check"], "2026-08-27T11:30:00+03:00")


if __name__ == "__main__":
    unittest.main()
