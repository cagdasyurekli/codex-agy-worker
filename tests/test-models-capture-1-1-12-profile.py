#!/usr/bin/env python3
"""Offline profile-builder slice of the 1.1.12 capture bridge tests."""
from __future__ import annotations

import pathlib
import runpy
import sys
import unittest


SOURCE = pathlib.Path(__file__).with_name("test-models-capture-1-1-12.py")
MODULE = runpy.run_path(str(SOURCE))
PROFILE_TESTS = frozenset({
    "test_01_profile_source_contract", "test_03_profile_accepts_canonical_shape",
    "test_05_profile_rejects_extra_field", "test_07_profile_rejects_noncanonical_bytes",
    "test_09_builder_request_has_explicit_capture_parent", "test_12_builder_has_no_process_import",
    "test_13_builder_does_not_list_account_home", "test_22_profile_rejects_wrong_output_basename_before_authority",
    "test_24_profile_publisher_has_exact_identity_cleanup", "test_30_profile_validate_reopens_final_path",
    "test_33_profile_rolls_back_provisional_output_on_pending_signal", "test_36_source_contract_rejects_profile_process_import",
    "test_42_profile_guard_rejects_dynamic_importfrom", "test_43_profile_guard_rejects_reachable_helper_mutation",
    "test_45_profile_hardlink_normalizes_before_poll_or_fsync",
    "test_50_prepare_publishes_canonical_profile", "test_51_validate_rejects_post_derivation_profile_replacement",
    "test_54_profile_cleanup_preserves_post_normalization_mode_drift",
    "test_58_profile_staging_unlink_failure_removes_both_owned_names",
    "test_60_profile_completion_write_failure_rolls_back_profile",
    "test_61_profile_completion_flush_failure_rolls_back_profile",
    "test_62_profile_completion_primitive_failure_rolls_back_and_restores_mask",
    "test_63_profile_completion_drift_preserves_residual",
    "test_67_profile_completion_short_write_rolls_back_profile",
})


if __name__ == "__main__":
    suite = unittest.TestSuite(
        MODULE["ModelsCapture112Tests"](name) for name in sorted(PROFILE_TESTS)
    )
    sys.exit(0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1)
