import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.process_digitizing_queue import (
    discover,
    normalized_statuses,
    run,
    sidecar_settings,
)


def options(directory: Path, output: Path, **overrides) -> argparse.Namespace:
    values = {
        "directory": directory,
        "batch_size": 10,
        "recursive": False,
        "settle_seconds": 0.0,
        "width_mm": 110.0,
        "colors": 8,
        "profile": "standard",
        "density_multiplier": 1.0,
        "pixels_per_mm": 2,
        "statuses": None,
        "output": output,
        "retry_failed": False,
        "force": False,
        "drain": False,
        "watch": False,
        "poll_seconds": 1.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def completed_result(name: str, output: Path) -> dict:
    fixture_root = output / name
    fixture_root.mkdir(parents=True, exist_ok=True)
    (fixture_root / "index.html").write_text("fixture")
    return {
        "name": name,
        "source": f"{name}.png",
        "source_format": "png",
        "report_status": "pass",
        "report_summary": {
            "semantic_violations": 0,
            "budget_violations": 0,
            "stitch_memory_exceeded": False,
        },
        "drc": {"quality_score": 97},
        "metrics": {
            "stitches": 125,
            "objects": 2,
            "brackets": 3,
            "all_brackets": 3,
            "brackets_by_status": {"pass": 3},
        },
        "outputs": {"gallery": f"{name}/index.html"},
        "urls": {
            "gallery": f"/artifacts/test/{name}/index.html",
            "dst": f"/artifacts/test/{name}/{name}.dst",
        },
    }


class QueueProcessorTests(unittest.TestCase):
    def test_discovery_is_content_addressed(self):
        with tempfile.TemporaryDirectory() as temporary:
            queue = Path(temporary)
            source = queue / "mark.png"
            source.write_bytes(b"first")
            state = {"jobs": {}, "batches": []}

            first = discover(
                queue,
                state,
                recursive=False,
                settle_seconds=0,
                retry_failed=False,
                force=False,
            )
            self.assertEqual(len(first), 1)
            state["jobs"][first[0]["key"]] = {"status": "completed"}
            self.assertEqual(
                discover(
                    queue,
                    state,
                    recursive=False,
                    settle_seconds=0,
                    retry_failed=False,
                    force=False,
                ),
                [],
            )

            source.write_bytes(b"second")
            changed = discover(
                queue,
                state,
                recursive=False,
                settle_seconds=0,
                retry_failed=False,
                force=False,
            )
            self.assertEqual(len(changed), 1)
            self.assertNotEqual(changed[0]["key"], first[0]["key"])

    def test_sidecars_and_status_filters_are_normalized(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "mark.jpg"
            source.write_bytes(b"source")
            source.with_name("mark.jpg.json").write_text(json.dumps({
                "width_mm": 88,
                "statuses": ["warning", "blocker"],
            }))
            self.assertEqual(sidecar_settings(source)["width_mm"], 88)
            self.assertEqual(
                normalized_statuses("warning, blocker"),
                {"warning", "blocker"},
            )
            self.assertEqual(
                normalized_statuses(["pass", "near_limit"]),
                {"pass", "near_limit"},
            )

    def test_drain_processes_each_job_once_and_writes_batch_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue = root / "inbox"
            output = root / "reports"
            queue.mkdir()
            (queue / "one.png").write_bytes(b"one")
            (queue / "two.jpg").write_bytes(b"two")
            calls = []

            def fake_write(fixture, batch_output, **_kwargs):
                calls.append(fixture["path"])
                return completed_result(fixture["name"], batch_output)

            with patch(
                "tools.process_digitizing_queue.write_fixture",
                side_effect=fake_write,
            ):
                result = run(options(
                    queue,
                    output,
                    batch_size=1,
                    drain=True,
                ))

            self.assertEqual(result, 0)
            self.assertEqual(calls, ["one.png", "two.jpg"])
            state = json.loads(
                (queue / ".digitizer-queue" / "state.json").read_text()
            )
            self.assertEqual(len(state["jobs"]), 2)
            self.assertEqual(len(state["batches"]), 2)
            for batch in state["batches"]:
                batch_root = output / batch["report"]
                self.assertTrue((batch_root / "batch-report.json").exists())
                self.assertTrue((batch_root / "batch-report.csv").exists())
                self.assertTrue((batch_root / "batch-report.html").exists())
                self.assertTrue((batch_root / "training-records.jsonl").exists())
            self.assertTrue((output / "index.html").exists())
            self.assertTrue((output / "latest.json").exists())

    def test_retry_failed_drain_does_not_hot_loop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue = root / "inbox"
            output = root / "reports"
            queue.mkdir()
            (queue / "broken.svg").write_text("<svg")

            with patch(
                "tools.process_digitizing_queue.write_fixture",
                side_effect=ValueError("invalid artwork"),
            ) as mocked:
                result = run(options(
                    queue,
                    output,
                    drain=True,
                    retry_failed=True,
                ))

            self.assertEqual(result, 0)
            self.assertEqual(mocked.call_count, 1)
            state = json.loads(
                (queue / ".digitizer-queue" / "state.json").read_text()
            )
            job = next(iter(state["jobs"].values()))
            self.assertEqual(job["status"], "failed")

    def test_output_inside_queue_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            queue = Path(temporary)
            with self.assertRaisesRegex(ValueError, "outside the input queue"):
                run(options(queue, queue / "generated"))


if __name__ == "__main__":
    unittest.main()
