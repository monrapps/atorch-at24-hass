"""Prove seven behavior assertions fail with unchanged baseline integration code.

Set ATORCH_SOURCE_ROOT to a separate checkout of master 297075d. Tests/doubles
remain those of the fix. Missing APIs/imports, teardown errors and unexpected
passes are NOT accepted as evidence of the original defect.
"""
import os
from pathlib import Path
import sys
import unittest

if "ATORCH_SOURCE_ROOT" not in os.environ:
    raise SystemExit("Set ATORCH_SOURCE_ROOT to the baseline checkout")
if Path(os.environ["ATORCH_SOURCE_ROOT"]).resolve() == Path(__file__).resolve().parents[1]:
    raise SystemExit("The negative control must not point at the current checkout")

from test_ble_stale import AvailabilityTests  # noqa: E402

CASES = (
    "test_P01_initial_state",
    "test_P04_disconnect_invalidates_before_reconnect",
    "test_P05_reconnection_without_frame_never_restores_cache",
    "test_P06_recovery_inside_previous_throttle_publishes_once",
    "test_P08_exact_timeout_publishes_once_without_BLE",
    "test_P20_flush_latest_sample_without_another_frame",
    "test_P34_coordinator_error_is_respected_and_idempotent",
)
suite = unittest.TestSuite(AvailabilityTests(case) for case in CASES)
result = unittest.TextTestRunner(verbosity=2).run(suite)
failed = {test._testMethodName for test, _ in result.failures}
expected = set(CASES)
if result.errors or failed != expected or result.testsRun != len(CASES):
    raise SystemExit("Negative control failed: expected exactly seven assertion failures")
print(f"Negative control verified: {len(failed)} expected assertion failures, no errors")
sys.exit(0)
