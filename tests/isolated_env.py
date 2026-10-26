"""Drop a private profile's settings before any test module imports the package.

The workflow script loads data/private.env before its test step. Left in place,
those settings point the CLI at the real registry, subscription history, and
NetNewsWire subscriptions, so tests would read private files and could write to
them. Every test module imports this first; the removal lasts for the test
process, and tests that need a setting still patch it in with patch.dict.
"""

import os

# The settings examples/private.env.example documents, by prefix or exact name.
PRIVATE_PREFIXES = ("RSS_", "NETNEWSWIRE_", "BANDCAMP_", "MODAL_")
PRIVATE_NAMES = frozenset({"FULL_FAN_SOURCE_IDS"})

for name in [name for name in os.environ if name.startswith(PRIVATE_PREFIXES) or name in PRIVATE_NAMES]:
    del os.environ[name]
