"""Our manifest_digest must equal the server's (prospect-og modkit).

The server compares the digest the launcher reports with its own
`expected_attestation`, which is `manifest_digest` over the offered mods'
integrity values. A mismatch only shows up in-game, as matchmaking refusing
the player, so it is pinned here against the server's own source.
"""

import importlib.util
from pathlib import Path

import pytest

from tclauncher.verify import manifest_digest

# Sibling checkout next to this repo (see CLAUDE.md); absent on CI runners.
MODKIT_VERIFY = (
    Path(__file__).resolve().parents[2] / "prospect-og" / "modkit" / "modkit" / "verify.py"
)

requires_prospect_og = pytest.mark.skipif(
    not MODKIT_VERIFY.exists(),
    reason="prospect-og sibling repo not present (attestation reference)",
)


def load_modkit_verify():
    spec = importlib.util.spec_from_file_location("_modkit_verify", MODKIT_VERIFY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@requires_prospect_og
@pytest.mark.parametrize("entries", [
    {},
    {"tc_update": "0123456789abcdef0123456789abcdef"},
    {"b": "2", "a": "1", "c": ""},
    {"mod-ü": "ä", "z": "1"},
])
def test_manifest_digest_matches_modkit(entries):
    assert manifest_digest(entries) == load_modkit_verify().manifest_digest(entries)
