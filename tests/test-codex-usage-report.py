#!/usr/bin/env python3
"""Offline unit and adversary tests for codex-usage-report.sh and Codex usage observer."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import runpy
import stat
import sys
import tempfile
import time
import unittest
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
USAGE_REPORT_PATH = ROOT / "scripts" / "codex_usage_report.py"

usage_module = runpy.run_path(str(USAGE_REPORT_PATH))
build_usage_report = usage_module["build_usage_report"]
format_text_report = usage_module["format_text_report"]
parse_session_file = usage_module["parse_session_file"]
parse_thread_usage_dict = usage_module["parse_thread_usage_dict"]
parse_rate_limits_dict = usage_module["parse_rate_limits_dict"]
query_app_server = usage_module["query_app_server"]
preflight_codex_schema = usage_module["preflight_codex_schema"]
UsageObservationError = usage_module["UsageObservationError"]
PINNED_CODEX_VERSION = usage_module["PINNED_CODEX_VERSION"]
EXPERIMENTAL_APP_SERVER_SCHEMA_SHA256 = usage_module["EXPERIMENTAL_APP_SERVER_SCHEMA_SHA256"]
main_func = usage_module["main"]


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        + b"\n"
    )


class CodexUsageReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="agy-test-usage-")
        self.root = pathlib.Path(os.path.realpath(self.temp_dir.name))
        os.chmod(str(self.root), 0o700)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_mock_codex_cli_script(
        self,
        cli_version: str = "0.150.1",
        schema_bytes: bytes = b"synthetic-codex-0.150.1-schema",
        user_agent: str = "Codex Desktop/0.150.1 (macOS; arm64)",
        thread_usages: Optional[dict[str, Any]] = None,
        rate_limits: Optional[dict[str, Any]] = None,
        explicit_null_usage_for_thread: Optional[str] = None,
        include_usage_summary: bool = True,
    ) -> pathlib.Path:
        if thread_usages is None:
            thread_usages = {
                "thread_main": {
                    "threadId": "thread_main",
                    "groups": [
                        {
                            "inputTokens": 1200,
                            "cachedInputTokens": 300,
                            "netNewInputTokens": 900,
                            "outputTokens": 250,
                            "estimatedUsageCreditsMicros": 150000,
                        }
                    ],
                    "estimatedUsageCreditsMicros": 150000,
                }
            }
        if rate_limits is None:
            rate_limits = {
                "primary": {
                    "windowDurationMins": 60,
                    "usedPercent": 15,
                    "resetsAt": 1724800000,
                },
                "planType": "team",
            }

        script_path = self.root / "mock_codex.py"
        script_code = f"""#!/usr/bin/env python3
import sys, json, os, pathlib

args = sys.argv[1:]

if args == ["--version"]:
    sys.stdout.write("codex-cli {cli_version}\\n")
    sys.exit(0)

if len(args) == 5 and args[:3] == ["app-server", "generate-json-schema", "--experimental"] and args[3] == "--out":
    out_path = pathlib.Path(args[4]) / "codex_app_server_protocol.schemas.json"
    out_path.write_bytes({schema_bytes!r})
    sys.exit(0)

if args and args[0] == "app-server":
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method = msg.get("method")
        req_id = msg.get("id")

        if method == "initialize":
            send({{
                "id": req_id,
                "result": {{
                    "userAgent": "{user_agent}",
                    "codexHome": "/Users/test/.codex",
                    "platformFamily": "unix",
                    "platformOs": "macos",
                }},
            }})
        elif method == "initialized":
            pass
        elif method == "account/usage/read":
            params = msg.get("params")
            if params and "threadId" in params:
                tid = params["threadId"]
                if tid == "{explicit_null_usage_for_thread}":
                    result_obj = {{"threadUsage": None}}
                    if {include_usage_summary!r}:
                        result_obj["summary"] = {{}}
                    send({{"id": req_id, "result": result_obj}})
                elif tid in {json.dumps(thread_usages)}:
                    usage = dict({json.dumps(thread_usages)}[tid])
                    usage["threadId"] = tid
                    result_obj = {{"threadUsage": usage}}
                    if {include_usage_summary!r}:
                        result_obj["summary"] = {{}}
                    send({{"id": req_id, "result": result_obj}})
                else:
                    result_obj = {{}}
                    if {include_usage_summary!r}:
                        result_obj["summary"] = {{}}
                    send({{"id": req_id, "result": result_obj}})
        elif method == "account/rateLimits/read":
            send({{"id": req_id, "result": {{"rateLimits": {json.dumps(rate_limits)}}}}})
    sys.exit(0)

sys.exit(1)
"""
        script_path.write_bytes(script_code.encode("utf-8"))
        os.chmod(str(script_path), 0o700)
        return script_path

    def test_01_schema_preflight_execution_and_digest_validation(self) -> None:
        self.assertEqual(PINNED_CODEX_VERSION, "0.150.1")
        self.assertEqual(
            EXPERIMENTAL_APP_SERVER_SCHEMA_SHA256,
            "e9bad0a20736e7d3aba18c0f04bef59856fb212ae21049fe17d786682203cfae",
        )
        schema_bytes = b"synthetic-codex-0.150.1-schema"
        mock = self._create_mock_codex_cli_script(schema_bytes=schema_bytes)
        digest = hashlib.sha256(schema_bytes).hexdigest()
        self.assertEqual(
            preflight_codex_schema(codex_bin=str(mock), expected_digest=digest),
            digest,
        )
        with self.assertRaises(UsageObservationError):
            preflight_codex_schema(codex_bin=str(mock), expected_digest="0" * 64)

    def test_02_thread_usage_groups_mirroring_actual_schema(self) -> None:
        raw = {
            "threadId": "thread_main",
            "groups": [
                {
                    "inputTokens": 800,
                    "cachedInputTokens": 300,
                    "netNewInputTokens": 500,
                    "outputTokens": 150,
                    "estimatedUsageCreditsMicros": 125000,
                },
                {
                    "inputTokens": 400,
                    "cachedInputTokens": 100,
                    "netNewInputTokens": 300,
                    "outputTokens": 100,
                    "estimatedUsageCreditsMicros": 75000,
                },
            ],
            "estimatedUsageCreditsMicros": 200000,
        }
        res = parse_thread_usage_dict(raw, expected_thread_id="thread_main")
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res["status"], "available")
        self.assertEqual(res["input_tokens"], 1200)
        self.assertEqual(res["cached_input_tokens"], 400)
        self.assertEqual(res["net_new_input_tokens"], 800)
        self.assertIsNone(res["cache_write_input_tokens"])
        self.assertEqual(res["output_tokens"], 250)
        self.assertIsNone(res["reasoning_output_tokens"])
        self.assertEqual(res["estimated_credits_micros"], 200000)
        self.assertTrue(res["reasoning_is_subset_of_output"])
        with self.assertRaises(UsageObservationError):
            parse_thread_usage_dict(raw, expected_thread_id="different_thread")

    def test_03_thread_usage_nullable_group_fields_remain_unavailable(self) -> None:
        raw = {
            "threadId": "thread_nullable",
            "groups": [
                {
                    "inputTokens": None,
                    "cachedInputTokens": None,
                    "netNewInputTokens": None,
                    "outputTokens": None,
                    "estimatedUsageCreditsMicros": 0,
                }
            ],
            "estimatedUsageCreditsMicros": 0,
        }
        res = parse_thread_usage_dict(raw)
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res["status"], "available")
        self.assertIsNone(res["input_tokens"])

    def test_04_thread_usage_parsing_rejects_cached_exceeding_input(self) -> None:
        raw = {
            "threadId": "thread_bad",
            "groups": [
                {
                    "inputTokens": 500,
                    "cachedInputTokens": 600,
                    "netNewInputTokens": 0,
                    "outputTokens": 100,
                    "estimatedUsageCreditsMicros": 0,
                }
            ],
            "estimatedUsageCreditsMicros": 0,
        }
        with self.assertRaises(UsageObservationError):
            parse_thread_usage_dict(raw)

    def test_05_rate_limits_parsing_actual_windowDurationMins_and_resetsAt(self) -> None:
        raw = {
            "primary": {
                "windowDurationMins": 60,
                "usedPercent": 18,
                "resetsAt": 1724800000,
            },
            "secondary": {
                "windowDurationMins": 1440,
                "usedPercent": 45,
                "resetsAt": 1724850000,
            },
            "planType": "enterprise",
        }
        res = parse_rate_limits_dict(raw)
        self.assertEqual(res["primary"]["window_duration_mins"], 60)
        self.assertEqual(res["primary"]["used_percent"], 18)
        self.assertEqual(res["primary"]["resets_at"], 1724800000)
        self.assertIsNotNone(res["secondary"])
        assert res["secondary"] is not None
        self.assertEqual(res["secondary"]["window_duration_mins"], 1440)
        self.assertEqual(res["secondary"]["used_percent"], 45)
        zero = parse_rate_limits_dict({"primary": {"windowDurationMins": 60, "usedPercent": 0, "resetsAt": None}})
        self.assertEqual(zero["primary"]["used_percent"], 0)

    def test_06_rate_limits_allow_missing_primary_and_reject_invalid_windows(self) -> None:
        self.assertIsNone(parse_rate_limits_dict({})["primary"])
        with self.assertRaises(UsageObservationError):
            parse_rate_limits_dict({"primary": {"windowDurationMins": 60}})
        with self.assertRaises(UsageObservationError):
            parse_rate_limits_dict({"primary": {"windowDurationMins": -1, "usedPercent": 10}})

    def test_07_session_file_parsing_exact_topology_with_large_line(self) -> None:
        session_file = self.root / "session.jsonl"
        large_padding = "x" * 230000
        lines = [
            json.dumps({"timestamp": "2026-08-28T10:00:00.000Z", "type": "session_meta", "payload": {"cli_version": "0.150.1", "padding": large_padding}}),
            json.dumps({"timestamp": "2026-08-28T10:00:01.000Z", "type": "response_item", "payload": {"type": "function_call", "name": "exec"}}),
            json.dumps({"timestamp": "2026-08-28T10:00:02.000Z", "type": "response_item", "payload": {"type": "custom_tool_call", "name": "spawn_agent"}}),
            json.dumps({"timestamp": "2026-08-28T10:00:03.000Z", "type": "response_item", "payload": {"type": "custom_tool_call", "name": "wait"}}),
            json.dumps({
                "timestamp": "2026-08-28T10:00:04.250Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 1500,
                            "cached_input_tokens": 500,
                            "cache_write_input_tokens": 200,
                            "output_tokens": 400,
                            "reasoning_output_tokens": 150,
                            "total_tokens": 1900,
                        },
                        "last_token_usage": {
                            "input_tokens": 200,
                            "cached_input_tokens": 50,
                            "cache_write_input_tokens": 10,
                            "output_tokens": 80,
                            "reasoning_output_tokens": 20,
                            "total_tokens": 280,
                        },
                    },
                },
            }),
        ]
        session_file.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        os.chmod(str(session_file), 0o600)

        parsed = parse_session_file(str(session_file))
        self.assertEqual(parsed["status"], "available")
        self.assertEqual(parsed["cli_version"], "0.150.1")
        self.assertEqual(parsed["wait_count"], 1)
        self.assertEqual(parsed["tool_calls"]["exec"], 1)
        self.assertEqual(parsed["tool_calls"]["spawn_agent"], 1)
        self.assertEqual(parsed["token_count"]["input_tokens"], 1500)
        self.assertEqual(parsed["token_count"]["cached_input_tokens"], 500)
        self.assertEqual(parsed["token_count"]["net_new_input_tokens"], 1000)
        self.assertEqual(parsed["token_count"]["cache_write_input_tokens"], 200)
        self.assertEqual(parsed["token_count"]["output_tokens"], 400)
        self.assertEqual(parsed["token_count"]["reasoning_output_tokens"], 150)
        self.assertEqual(parsed["last_phase_token_count"]["input_tokens"], 200)
        self.assertEqual(parsed["last_phase_token_count"]["cached_input_tokens"], 50)
        self.assertEqual(parsed["last_phase_token_count"]["net_new_input_tokens"], 150)
        self.assertEqual(parsed["last_phase_token_count"]["output_tokens"], 80)
        self.assertEqual(parsed["last_phase_token_count"]["reasoning_output_tokens"], 20)
        self.assertEqual(parsed["measurement_window"]["basis"], "explicit_session_records")
        self.assertEqual(parsed["measurement_window"]["records_observed"], 5)
        self.assertEqual(parsed["measurement_window"]["token_snapshots_observed"], 1)
        self.assertEqual(parsed["measurement_window"]["duration_ms"], 4250)

    def test_08_session_file_rejects_counter_regression_and_duplicates(self) -> None:
        session_file = self.root / "regressed_session.jsonl"
        lines = [
            json.dumps({"type": "session_meta", "payload": {"cli_version": "0.150.1"}}),
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 100, "cache_write_input_tokens": 20, "output_tokens": 200, "reasoning_output_tokens": 50, "total_tokens": 1200}}}}),
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 100, "cache_write_input_tokens": 10, "output_tokens": 200, "reasoning_output_tokens": 50, "total_tokens": 1200}}}}),
        ]
        session_file.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        os.chmod(str(session_file), 0o600)

        with self.assertRaises(UsageObservationError):
            parse_session_file(str(session_file))

        missing_counter_file = self.root / "missing_counter_session.jsonl"
        missing_counter_lines = [
            json.dumps({"type": "session_meta", "payload": {"cli_version": "0.150.1"}}),
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 100, "output_tokens": 20}}}}),
        ]
        missing_counter_file.write_bytes(("\n".join(missing_counter_lines) + "\n").encode("utf-8"))
        os.chmod(str(missing_counter_file), 0o600)
        with self.assertRaises(UsageObservationError):
            parse_session_file(str(missing_counter_file))

        incomplete_time_file = self.root / "incomplete_time_session.jsonl"
        incomplete_time_lines = [
            json.dumps({"timestamp": "2026-08-28T10:00:00.000Z", "type": "session_meta", "payload": {"cli_version": "0.150.1"}}),
            json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": "exec"}}),
        ]
        incomplete_time_file.write_bytes(("\n".join(incomplete_time_lines) + "\n").encode("utf-8"))
        os.chmod(str(incomplete_time_file), 0o600)
        with self.assertRaises(UsageObservationError):
            parse_session_file(str(incomplete_time_file))

        regressed_time_file = self.root / "regressed_time_session.jsonl"
        regressed_time_lines = [
            json.dumps({"timestamp": "2026-08-28T10:00:01.000Z", "type": "session_meta", "payload": {"cli_version": "0.150.1"}}),
            json.dumps({"timestamp": "2026-08-28T10:00:00.000Z", "type": "response_item", "payload": {"type": "function_call", "name": "exec"}}),
        ]
        regressed_time_file.write_bytes(("\n".join(regressed_time_lines) + "\n").encode("utf-8"))
        os.chmod(str(regressed_time_file), 0o600)
        with self.assertRaises(UsageObservationError):
            parse_session_file(str(regressed_time_file))

        session_file2 = self.root / "dup_id_session.jsonl"
        lines2 = [
            json.dumps({"type": "session_meta", "payload": {"cli_version": "0.150.1"}}),
            json.dumps({"id": "evt_1", "type": "response_item", "payload": {"name": "exec"}}),
            json.dumps({"id": "evt_1", "type": "response_item", "payload": {"name": "read_file"}}),
        ]
        session_file2.write_bytes(("\n".join(lines2) + "\n").encode("utf-8"))
        os.chmod(str(session_file2), 0o600)

        with self.assertRaises(UsageObservationError):
            parse_session_file(str(session_file2))

        session_file3 = self.root / "dup_ordinal_session.jsonl"
        lines3 = [
            json.dumps({"ordinal": 1, "type": "session_meta", "payload": {"cli_version": "0.150.1"}}),
            json.dumps({"ordinal": 2, "type": "response_item", "payload": {"type": "function_call", "name": "exec"}}),
            json.dumps({"ordinal": 2, "type": "response_item", "payload": {"type": "function_call", "name": "wait"}}),
        ]
        session_file3.write_bytes(("\n".join(lines3) + "\n").encode("utf-8"))
        os.chmod(str(session_file3), 0o600)
        with self.assertRaises(UsageObservationError):
            parse_session_file(str(session_file3))

    def test_09_session_file_rejects_version_drift(self) -> None:
        session_file = self.root / "drift_session.jsonl"
        lines = [json.dumps({"type": "session_meta", "payload": {"cli_version": "0.149.0"}})]
        session_file.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        os.chmod(str(session_file), 0o600)

        with self.assertRaises(UsageObservationError):
            parse_session_file(str(session_file))

    def test_10_session_file_rejects_non_private_mode_or_symlinks(self) -> None:
        session_file = self.root / "open_session.jsonl"
        lines = [json.dumps({"type": "session_meta", "payload": {"cli_version": "0.150.1"}})]
        session_file.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        os.chmod(str(session_file), 0o644)

        with self.assertRaises(UsageObservationError):
            parse_session_file(str(session_file))

        os.chmod(str(session_file), 0o600)
        link_file = self.root / "link_session.jsonl"
        os.symlink(str(session_file), str(link_file))

        with self.assertRaises(UsageObservationError):
            parse_session_file(str(link_file))

    def test_11_real_smoke_coverage_with_codex_desktop_user_agent(self) -> None:
        mock_cli = self._create_mock_codex_cli_script(
            user_agent="Codex Desktop/0.150.1 (macOS; arm64)",
        )
        cmd = [sys.executable, str(mock_cli), "app-server"]

        report = build_usage_report(
            tasks=[("main", "thread_main")],
            sessions=[],
            query_account_usage=True,
            app_server_cmd=cmd,
            skip_schema_preflight=True,
        )

        self.assertEqual(report["codex_version"], "0.150.1")
        self.assertEqual(report["schema_digest"], "e9bad0a20736e7d3aba18c0f04bef59856fb212ae21049fe17d786682203cfae")
        self.assertEqual(report["tasks"]["main"]["status"], "available")
        self.assertEqual(report["tasks"]["main"]["input_tokens"], 1200)
        self.assertEqual(report["tasks"]["main"]["cached_input_tokens"], 300)
        self.assertEqual(report["tasks"]["main"]["net_new_input_tokens"], 900)
        self.assertEqual(report["tasks"]["main"]["output_tokens"], 250)
        self.assertIsNone(report["tasks"]["main"]["reasoning_output_tokens"])
        self.assertEqual(report["tasks"]["main"]["estimated_credits_micros"], 150000)

        # Aggregate arithmetic with nullables
        self.assertEqual(report["aggregates"]["total_input_tokens"], 1200)
        self.assertEqual(report["aggregates"]["total_net_new_input_tokens"], 900)
        self.assertEqual(report["aggregates"]["total_output_tokens"], 250)
        self.assertIsNone(report["aggregates"]["total_reasoning_output_tokens"])

        # Text formatting with nullables
        text_out = format_text_report(report)
        self.assertIn("Codex Version: 0.150.1", text_out)
        self.assertIn("Task [main]:", text_out)
        self.assertIn("Reasoning Output:  unavailable", text_out)
        self.assertIn("Total Reasoning Output:  unavailable", text_out)
        self.assertIn("Primary:   15.0% used (60m window", text_out)

    def test_12_mock_app_server_version_drift_fails_closed(self) -> None:
        mock_cli = self._create_mock_codex_cli_script(user_agent="Codex Desktop/0.149.0 (macOS; arm64)")
        cmd = [sys.executable, str(mock_cli), "app-server"]

        with self.assertRaises(UsageObservationError):
            build_usage_report(
                tasks=[("main", "thread_main")],
                sessions=[],
                query_account_usage=False,
                app_server_cmd=cmd,
                skip_schema_preflight=True,
            )

    def test_13_mock_app_server_explicit_null_usage_fails_closed(self) -> None:
        mock_cli = self._create_mock_codex_cli_script(explicit_null_usage_for_thread="thread_null")
        cmd = [sys.executable, str(mock_cli), "app-server"]

        with self.assertRaises(UsageObservationError):
            build_usage_report(
                tasks=[("failing_task", "thread_null")],
                sessions=[],
                query_account_usage=False,
                app_server_cmd=cmd,
                skip_schema_preflight=True,
            )

        missing_summary = self._create_mock_codex_cli_script(include_usage_summary=False)
        with self.assertRaises(UsageObservationError):
            build_usage_report(
                tasks=[("missing_summary", "thread_main")],
                sessions=[],
                query_account_usage=False,
                app_server_cmd=[sys.executable, str(missing_summary), "app-server"],
                skip_schema_preflight=True,
            )

    def test_14_session_smoke_on_private_copy_observes_nonzero_tokens_tools_waits(self) -> None:
        session_file = self.root / "private_session_smoke.jsonl"
        lines = [
            json.dumps({"type": "session_meta", "payload": {"cli_version": "0.150.1"}}),
            json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": "exec"}}),
            json.dumps({"type": "response_item", "payload": {"type": "custom_tool_call", "name": "spawn_agent"}}),
            json.dumps({"type": "response_item", "payload": {"type": "custom_tool_call", "name": "wait"}}),
            json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 5000,
                            "cached_input_tokens": 1200,
                            "cache_write_input_tokens": 300,
                            "output_tokens": 800,
                            "reasoning_output_tokens": 250,
                            "total_tokens": 5800,
                        },
                        "last_token_usage": {
                            "input_tokens": 600,
                            "cached_input_tokens": 100,
                            "cache_write_input_tokens": 25,
                            "output_tokens": 120,
                            "reasoning_output_tokens": 40,
                            "total_tokens": 720,
                        },
                    },
                },
            }),
        ]
        session_file.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        os.chmod(str(session_file), 0o600)

        parsed = parse_session_file(str(session_file))
        self.assertEqual(parsed["status"], "available")
        self.assertGreater(parsed["token_count"]["input_tokens"], 0)
        self.assertGreater(parsed["token_count"]["cached_input_tokens"], 0)
        self.assertGreater(parsed["token_count"]["output_tokens"], 0)
        self.assertGreater(parsed["token_count"]["reasoning_output_tokens"], 0)
        self.assertGreater(parsed["tool_calls"]["exec"], 0)
        self.assertGreater(parsed["tool_calls"]["spawn_agent"], 0)
        self.assertGreater(parsed["wait_count"], 0)

    def test_15_report_privacy_and_redaction(self) -> None:
        session_file = self.root / "private_session.jsonl"
        lines = [
            json.dumps({"type": "session_meta", "payload": {"cli_version": "0.150.1"}}),
            json.dumps({"cwd": "/Users/secret/repo", "prompt": "secret task content", "user_id": "user@example.com"}),
            json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": "exec"}}),
        ]
        session_file.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        os.chmod(str(session_file), 0o600)

        mock_cli = self._create_mock_codex_cli_script()
        cmd = [sys.executable, str(mock_cli), "app-server"]

        report = build_usage_report(
            tasks=[("main_task", "secret_thread_id_999")],
            sessions=[("main_session", str(session_file))],
            query_account_usage=True,
            app_server_cmd=cmd,
            skip_schema_preflight=True,
        )

        json_bytes = canonical(report)
        json_str = json_bytes.decode("utf-8")
        text_str = format_text_report(report)

        for rendered in (json_str, text_str):
            self.assertNotIn("secret_thread_id_999", rendered)
            self.assertNotIn("/Users/secret/repo", rendered)
            self.assertNotIn("secret task content", rendered)
            self.assertNotIn("user@example.com", rendered)
            self.assertNotIn("private_session.jsonl", rendered)

        self.assertTrue(report["limitations"]["directional_only"])
        self.assertFalse(report["limitations"]["money_inferred"])
        self.assertFalse(report["limitations"]["quota_inferred"])

    def test_16_cli_argument_parsing(self) -> None:
        rc = main_func([])
        self.assertEqual(rc, 64)

        rc = main_func(["--task", "invalid_no_equals"])
        self.assertEqual(rc, 64)

    def test_17_multiple_explicit_tasks_aggregate_only_complete_values(self) -> None:
        thread_usages = {
            "thread_a": {
                "threadId": "thread_a",
                "groups": [{
                    "inputTokens": 100,
                    "cachedInputTokens": 25,
                    "netNewInputTokens": 75,
                    "outputTokens": 20,
                    "estimatedUsageCreditsMicros": 10,
                }],
                "estimatedUsageCreditsMicros": 10,
            },
            "thread_b": {
                "threadId": "thread_b",
                "groups": [{
                    "inputTokens": 300,
                    "cachedInputTokens": 100,
                    "netNewInputTokens": 200,
                    "outputTokens": 60,
                    "estimatedUsageCreditsMicros": 30,
                }],
                "estimatedUsageCreditsMicros": 30,
            },
        }
        mock_cli = self._create_mock_codex_cli_script(thread_usages=thread_usages)
        report = build_usage_report(
            tasks=[("main", "thread_a"), ("subagent", "thread_b")],
            sessions=[],
            app_server_cmd=[sys.executable, str(mock_cli), "app-server"],
            skip_schema_preflight=True,
        )
        self.assertEqual(report["aggregates"]["tasks_requested"], 2)
        self.assertEqual(report["aggregates"]["tasks_available"], 2)
        self.assertEqual(report["aggregates"]["total_input_tokens"], 400)
        self.assertEqual(report["aggregates"]["total_cached_input_tokens"], 125)
        self.assertEqual(report["aggregates"]["total_net_new_input_tokens"], 275)
        self.assertEqual(report["aggregates"]["total_output_tokens"], 80)
        with self.assertRaises(UsageObservationError):
            build_usage_report(
                tasks=[("first", "thread_a"), ("duplicate", "thread_a")],
                sessions=[],
                app_server_cmd=[sys.executable, str(mock_cli), "app-server"],
                skip_schema_preflight=True,
            )


if __name__ == "__main__":
    unittest.main()
