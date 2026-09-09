"""Regression coverage for the PostgreSQL-backed application state.

These tests deliberately cross a process boundary.  A successful write in the
process running pytest is not enough to catch the old local-file behavior:
the child process must be able to reload every record from PostgreSQL.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest


def test_postgres_persistence_survives_fresh_process_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round-trip each migrated store and exercise proposal reopen/download.

    The ``finally`` block is intentional: this test must leave the shared
    development database unchanged even if the child-process assertions fail.
    """
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for PostgreSQL persistence tests")

    proposals_dir = tmp_path / "proposals"
    monkeypatch.setenv("PROPOSALS_DIR", str(proposals_dir))

    from app.catalog import (
        CATALOG,
        add_custom_product,
        delete_custom_product,
        load_custom_products,
        load_rate_overrides,
        save_rate_overrides,
    )
    from app.db import execute
    from app.main import _save_proposal_metadata
    from app.market_config import delete_market_entry, set_market_entry

    suffix = uuid.uuid4().hex
    custom_name = f"__pytest_persistence_product_{suffix}"
    market_key = f"__pytest_persistence_market_{suffix}"
    proposal_id = f"__pytest_persistence_proposal_{suffix}"
    proposal_filename = f"{proposal_id}.xlsx"
    original_overrides = load_rate_overrides()
    builtin = CATALOG[0]
    proposal_path = proposals_dir / proposal_filename

    try:
        # Rate overrides: use a built-in product so this covers the normal
        # admin rate-override path, then verify the value in a fresh process.
        overrides = {
            name: dict(fields) for name, fields in original_overrides.items()
        }
        overrides[builtin.name] = {
            **overrides.get(builtin.name, {}),
            "minimum_spend": float(builtin.minimum_spend) + 123.45,
            "notes": f"pytest persistence marker {suffix}",
        }
        save_rate_overrides(overrides)

        # Custom products: create a complete record with non-default values so
        # numeric, boolean, tuple, and text fields all have to round-trip.
        add_custom_product(
            {
                "name": custom_name,
                "family": "Pytest Persistence",
                "short_label": "Persistence Check",
                "buying_model": "CPM",
                "base_rate": 37.5,
                "minimum_spend": 875.25,
                "minimum_flight_days": (11, 44),
                "estimated_impressions": True,
                "discloses_impressions": False,
                "proposal_description": "Temporary persistence regression product",
                "sizes": "15s, 30s",
                "notes": f"custom marker {suffix}",
                "estimated_cpm_for_imps": 18.25,
                "tech_platform": "pytest-platform",
                "wide_orbit_code": f"pytest-code-{suffix}",
                "is_addon": True,
            }
        )

        # Market configuration: set and read a uniquely named market entry.
        set_market_entry(
            market_key,
            {
                "address_line1": "99 Persistence Way",
                "address_line2": "Testville, CA 90000",
                "ccs": [f"market-{suffix}@example.test"],
            },
        )

        # Proposal metadata stays in PostgreSQL while generated files remain
        # in the configured proposal directory, matching production behavior.
        proposal_path.parent.mkdir(parents=True, exist_ok=True)
        proposal_path.write_bytes(b"pytest proposal download fixture")
        reopen_state = {
            "request": {
                "client_name": "Persistence Test Client",
                "salesperson_email": "seller@example.test",
            },
            "line_items": [
                {
                    "id": "line-1",
                    "product_name": custom_name,
                    "monthly_budget": 875.25,
                    "months": 3,
                    "rate_override": 39.0,
                    "notes_override": "round-trip note",
                    "target_override": "A21+",
                }
            ],
            "avails_data": {
                custom_name: {
                    "max_imps": 12000,
                    "max_spend": 875.25,
                    "frequency": 5.5,
                    "basis": "imps",
                }
            },
            "tiers": None,
            "strategy_brief": {"recommended_tactics": ["persistence-test"]},
            "force_tabs": {"net": True},
            "addons": [],
        }
        _save_proposal_metadata(
            proposal_id=proposal_id,
            client_name="Persistence Test Client",
            seller_email="seller@example.test",
            requested_by="Pytest",
            notion_id=f"notion-{suffix}",
            proposal_title="Persistence Test Proposal",
            filename=proposal_filename,
            email_doc_filename=None,
            pptx_net_filename=None,
            pptx_gross_filename=None,
            generated_at=datetime.now(timezone.utc),
            requester_ip="127.0.0.1",
            requester_user_agent="pytest",
            summary={"total_net": 1234.5, "tabs_built": ["Net"]},
            reopen_state=reopen_state,
        )

        worker = textwrap.dedent(
            f"""
            import asyncio
            from pathlib import Path

            from app.catalog import load_custom_products, load_rate_overrides
            from app.main import _get_proposal_metadata, download, reopen_proposal
            from app.market_config import load_market_config

            overrides = load_rate_overrides()
            assert overrides[{builtin.name!r}]["minimum_spend"] == {float(builtin.minimum_spend) + 123.45!r}
            assert overrides[{builtin.name!r}]["notes"] == {"pytest persistence marker " + suffix!r}

            custom = next(p for p in load_custom_products() if p.name == {custom_name!r})
            assert custom.family == "Pytest Persistence"
            assert custom.minimum_flight_days == (11, 44)
            assert custom.base_rate == 37.5
            assert custom.estimated_impressions is True
            assert custom.discloses_impressions is False
            assert custom.is_addon is True
            assert custom.wide_orbit_code == {"pytest-code-" + suffix!r}

            market = load_market_config()[{market_key!r}]
            assert market == {{
                "address_line1": "99 Persistence Way",
                "address_line2": "Testville, CA 90000",
                "ccs": [{"market-" + suffix + "@example.test"!r}],
            }}

            metadata = _get_proposal_metadata({proposal_id!r})
            assert metadata["summary"] == {{"total_net": 1234.5, "tabs_built": ["Net"]}}
            assert metadata["notion_id"] == {"notion-" + suffix!r}

            reopened = asyncio.run(reopen_proposal({proposal_id!r}))
            assert reopened["proposal_title"] == "Persistence Test Proposal"
            assert reopened["request"] == {{
                "client_name": "Persistence Test Client",
                "salesperson_email": "seller@example.test",
            }}
            assert reopened["line_items"][0]["rate_override"] == 39.0
            assert reopened["avails_data"][{custom_name!r}]["frequency"] == 5.5
            assert reopened["strategy_brief"] == {{"recommended_tactics": ["persistence-test"]}}

            response = asyncio.run(download({proposal_id!r}))
            assert response.filename == {proposal_filename!r}
            assert Path(response.path).read_bytes() == b"pytest proposal download fixture"
            print("PostgreSQL persistence child-process checks passed")
            """
        )
        child_env = os.environ.copy()
        child_env["PROPOSALS_DIR"] = str(proposals_dir)
        subprocess.run(
            [sys.executable, "-c", worker],
            cwd=Path(__file__).resolve().parents[1],
            env=child_env,
            check=True,
            text=True,
        )
    finally:
        # Restore every store touched above, including when any assertion or
        # subprocess call raises.  The unique rows are also removed explicitly
        # so a partially completed setup cannot leak into a later run.
        execute("DELETE FROM proposals WHERE proposal_id = %s", (proposal_id,))
        delete_market_entry(market_key)
        delete_custom_product(custom_name)
        save_rate_overrides(original_overrides)
        proposal_path.unlink(missing_ok=True)