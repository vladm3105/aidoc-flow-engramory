"""Unit tests for SDD CLI commands (SPEC-07 / ADR-10)."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from engramory.cli import (
    _build_parser,
    _cmd_sdd_blast_radius,
    _cmd_sdd_slice,
)


def test_parser_has_sdd_subcommands() -> None:
    parser = _build_parser()

    # sdd ingest
    args = parser.parse_args(["sdd", "ingest", "--sdd-root", "sdd", "--project-id", "myproj"])
    assert args.command == "sdd"
    assert args.sdd_command == "ingest"
    assert args.sdd_root == "sdd"
    assert args.project_id == "myproj"
    assert args.no_reconcile is False

    # sdd ingest with --no-reconcile
    args_nr = parser.parse_args(["sdd", "ingest", "--no-reconcile"])
    assert args_nr.no_reconcile is True

    # sdd slice
    args = parser.parse_args(
        [
            "sdd",
            "slice",
            "--task",
            "IPLAN-03",
            "--depth",
            "4",
            "--layers",
            "SPEC,ADR",
            "--max-tokens",
            "1500",
            "--markdown",
        ]
    )
    assert args.command == "sdd"
    assert args.sdd_command == "slice"
    assert args.task == "IPLAN-03"
    assert args.depth == 4
    assert args.layers == "SPEC,ADR"
    assert args.max_tokens == 1500
    assert args.markdown is True

    # mcp serve sse
    args_mcp = parser.parse_args(
        ["mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "9000"]
    )
    assert args_mcp.command == "mcp"
    assert args_mcp.transport == "sse"
    assert args_mcp.host == "0.0.0.0"
    assert args_mcp.port == 9000

    # sdd blast-radius
    args = parser.parse_args(["sdd", "blast-radius", "--doc", "ADR-03", "--depth", "3"])
    assert args.command == "sdd"
    assert args.sdd_command == "blast-radius"
    assert args.doc == "ADR-03"
    assert args.depth == 3


@patch("engramory.cli._run_with_surface")
def test_cmd_sdd_slice_invokes_run_with_surface(mock_run: MagicMock) -> None:
    args = argparse.Namespace(task="IPLAN-03", depth=5, scope="project", json=False)
    _cmd_sdd_slice(args)
    mock_run.assert_called_once()


@patch("engramory.cli._run_with_surface")
def test_cmd_sdd_blast_radius_invokes_run_with_surface(mock_run: MagicMock) -> None:
    args = argparse.Namespace(doc="ADR-03", depth=4, scope="project", json=False)
    _cmd_sdd_blast_radius(args)
    mock_run.assert_called_once()
