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

    # sdd slice
    args = parser.parse_args(["sdd", "slice", "--task", "IPLAN-03", "--depth", "4"])
    assert args.command == "sdd"
    assert args.sdd_command == "slice"
    assert args.task == "IPLAN-03"
    assert args.depth == 4

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
