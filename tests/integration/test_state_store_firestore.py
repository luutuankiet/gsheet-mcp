# Copied from google-docs-mcp@2fb4652 tests/integration/test_state_store_firestore.py; renamed.
"""The Firestore state store passes the same plan behaviour tests as the
in-memory store, against the Firestore emulator. Run through
tests/integration/compose.yaml; skipped when no emulator is configured.
"""
from __future__ import annotations

import os

import pytest

from gsheet_mcp import state_store
from tests.unit import test_state_store as behaviour

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="needs the Firestore emulator: see tests/integration/compose.yaml")

clock = behaviour.clock
store = behaviour.store

# Every plan behaviour test; the object tests drive their own fakes.
globals().update({name: test for name, test in vars(behaviour).items()
                  if name.startswith("test_") and "plan" in name})


@pytest.fixture
def make_store(clock):
    # No bucket: the plan tests must pass without one, as on a deployment
    # that never sets GSHEET_MCP_BUCKET.
    return lambda: state_store.HostedStateStore(
        database="gsheet-mcp-test", bucket=None, clock=clock)


def test_plan_is_visible_from_another_instance(make_store):
    # Two store objects stand in for two Cloud Run instances.
    plan_id = make_store().save_plan(behaviour.PLAN, spreadsheet_id="sheet-a", caller="a@example.com")
    assert make_store().load_plan(
        plan_id, spreadsheet_id="sheet-a", caller="a@example.com") == behaviour.PLAN
