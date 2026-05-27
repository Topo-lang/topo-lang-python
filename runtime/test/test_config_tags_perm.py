"""Acceptance for the tag system, the tag-query API, and the two
orthogonal multi-level permission roles (read-visibility tiering vs the
write mis-operation gate), plus the tiered-transparency invariant.

Run: ``python3 -m unittest topo-lang-python.runtime.test.test_config_tags_perm``
(or ``python3 -m unittest discover -s topo-lang-python/runtime/test``).
"""

import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from topo._config_model import (  # noqa: E402
    ConfigStore,
    ImpactLevel,
    ItemPolicy,
    LayeredConfig,
    WriteProtectionError,
    authorize_write,
    required_read_level,
)
from topo.config import ProductConfig  # noqa: E402


def _tagged_store():
    """A store where items carry freely-combinable tags and a mix of
    read tiers, so tag AND, the no-tag default, and the read tier can
    each be exercised independently."""

    store = ConfigStore(
        LayeredConfig(
            inlined={
                "log.level": "warn",
                "net.timeout_ms": 5000,
                "net.retries": 3,
                "cache.size": 256,
                "db.dsn": "postgres://local",
                "secret.api_key": "k-xxx",
            }
        )
    )
    store.declare("log.level", ItemPolicy(tags=["obs"]))
    store.declare("net.timeout_ms", ItemPolicy(tags=["network", "tuning"]))
    store.declare("net.retries", ItemPolicy(tags=["network"]))
    store.declare("cache.size", ItemPolicy(tags=["tuning"]))
    # Permission-gated: needs read level 2 to be enumerated/read.
    store.declare(
        "db.dsn",
        ItemPolicy(tags=["network"], read_level=2, impact=ImpactLevel.HIGH),
    )
    # Higher tier still: read level 3.
    store.declare(
        "secret.api_key",
        ItemPolicy(tags=["network"], read_level=3, impact=ImpactLevel.HIGH),
    )
    return store


class TagQuery(unittest.TestCase):
    def test_single_tag_returns_exact_subset(self):
        store = _tagged_store()
        # network items, but the two permission-gated ones are hidden
        # without a credential.
        self.assertEqual(
            store.query(tags=["network"]),
            ["net.retries", "net.timeout_ms"],
        )

    def test_multi_tag_is_AND_combination(self):
        store = _tagged_store()
        # Only net.timeout_ms carries BOTH network and tuning.
        self.assertEqual(
            store.query(tags=["network", "tuning"]),
            ["net.timeout_ms"],
        )
        # Order of the requested tags must not matter.
        self.assertEqual(
            store.query(tags=["tuning", "network"]),
            ["net.timeout_ms"],
        )

    def test_no_tag_returns_all_non_permission_items(self):
        store = _tagged_store()
        # No tag, no credential: every non-permission item, and the
        # read-gated db.dsn / secret.api_key are excluded by default.
        self.assertEqual(
            store.query(),
            ["cache.size", "log.level", "net.retries", "net.timeout_ms"],
        )

    def test_tag_with_no_match_returns_empty(self):
        store = _tagged_store()
        self.assertEqual(store.query(tags=["does-not-exist"]), [])

    def test_query_resolved_carries_values_and_provenance(self):
        store = _tagged_store()
        rv = store.query_resolved(tags=["tuning"])
        self.assertEqual(set(rv), {"net.timeout_ms", "cache.size"})
        self.assertEqual(rv["cache.size"].value, 256)


class ReadVisibilityTiering(unittest.TestCase):
    """Role (i): an item can require a permission level to be enumerated
    or read; below that level it is not listed at all."""

    def test_gated_item_hidden_without_credential(self):
        store = _tagged_store()
        self.assertNotIn("db.dsn", store.query())
        self.assertNotIn("secret.api_key", store.query())
        with self.assertRaises(WriteProtectionError):
            store.read("db.dsn")  # no credential -> not reachable

    def test_each_level_sees_that_levels_complete_range(self):
        store = _tagged_store()
        # Level 2 admits db.dsn (read_level 2) but still not the
        # level-3 secret — each tier sees its own complete range.
        keys_l2 = store.query(credential_level=2)
        self.assertIn("db.dsn", keys_l2)
        self.assertNotIn("secret.api_key", keys_l2)
        self.assertEqual(store.read("db.dsn", credential_level=2),
                         "postgres://local")
        with self.assertRaises(WriteProtectionError):
            store.read("secret.api_key", credential_level=2)

    def test_tiered_transparency_highest_level_enumerates_everything(self):
        # The invariant: the highest read level can always enumerate
        # EVERY runtime item — no fragment is invisible at every level.
        store = _tagged_store()
        top = store.max_read_level()
        self.assertEqual(top, 3)
        enumerated = set(store.query(credential_level=top))
        self.assertEqual(enumerated, set(store.keys()))
        # And every item is actually readable at the top level.
        for key in store.keys():
            store.read(key, credential_level=top)

    def test_tag_filter_and_read_tier_are_orthogonal(self):
        store = _tagged_store()
        # tag=network at the top level reveals the gated network items
        # too; the tag axis did not change, the permission axis did.
        top = store.max_read_level()
        self.assertEqual(
            store.query(tags=["network"], credential_level=top),
            ["db.dsn", "net.retries", "net.timeout_ms", "secret.api_key"],
        )
        # Same tag, no credential: only the non-gated network items.
        self.assertEqual(
            store.query(tags=["network"]),
            ["net.retries", "net.timeout_ms"],
        )


class SameQueryDifferentSites(unittest.TestCase):
    """The same query API yields different visibility purely from the
    arguments each call-site passes — it reads no ambient identity."""

    def test_two_callsites_different_args_different_visibility(self):
        store = _tagged_store()

        # Call-site one: a restricted surface, no credential.
        site_one = store.query(tags=["network"])
        # Call-site two: a privileged surface, top credential.
        site_two = store.query(tags=["network"],
                               credential_level=store.max_read_level())

        self.assertNotEqual(site_one, site_two)
        self.assertNotIn("db.dsn", site_one)
        self.assertIn("db.dsn", site_two)

    def test_query_signature_takes_no_identity(self):
        for fn in (ConfigStore.query, ConfigStore.query_resolved,
                   ConfigStore.read, ConfigStore.max_read_level):
            params = set(inspect.signature(fn).parameters)
            for forbidden in ("identity", "principal", "user", "agent"):
                self.assertNotIn(forbidden, params)


class WriteGateGeneralizedMultiLevel(unittest.TestCase):
    """Role (ii): the write mis-operation gate generalized from binary
    to the multi-level scale, without regressing the prior behaviour."""

    def test_mid_level_threshold_via_required_credential_table(self):
        # The write gate is the orthogonal twin of read tiering. Insert
        # a mid threshold by extending the explicit table, not by
        # rewriting logic — proves the scale is multi-level.
        from topo import _config_model as m

        original = dict(m._REQUIRED_CREDENTIAL_LEVEL)
        try:
            # A new mid impact level mapped to level 2 — a table edit.
            mid = m.ImpactLevel.HIGH  # reuse enum slot; map it to 2
            m._REQUIRED_CREDENTIAL_LEVEL[mid] = 2
            store = ConfigStore()
            store.declare("db.dsn", ItemPolicy(impact=mid))
            with self.assertRaises(WriteProtectionError):
                store.set("db.dsn", "x", credential_level=1)  # below 2
            store.set("db.dsn", "ok", credential_level=2)  # meets 2
            self.assertEqual(store.get("db.dsn"), "ok")
        finally:
            m._REQUIRED_CREDENTIAL_LEVEL.clear()
            m._REQUIRED_CREDENTIAL_LEVEL.update(original)

    def test_read_level_and_write_gate_are_independent_fields(self):
        # An item freely readable but write-guarded, and one read-gated
        # but cheap to write — proves the two roles do not collapse.
        store = ConfigStore()
        store.declare("public.but.guarded",
                      ItemPolicy(read_level=0, impact=ImpactLevel.HIGH))
        store.declare("gated.but.cheap",
                      ItemPolicy(read_level=2, impact=ImpactLevel.LOW))

        # Freely readable, but a write needs a credential.
        self.assertEqual(required_read_level(
            store.policy_of("public.but.guarded")), 0)
        with self.assertRaises(WriteProtectionError):
            store.set("public.but.guarded", 1)  # impact gate bites

        # Read-gated, but writing it needs no credential.
        self.assertEqual(required_read_level(
            store.policy_of("gated.but.cheap")), 2)
        store.set("gated.but.cheap", 1)  # write gate does not bite
        with self.assertRaises(WriteProtectionError):
            store.read("gated.but.cheap")  # read tier still bites

    def test_authorize_write_still_identity_independent(self):
        params = set(inspect.signature(authorize_write).parameters)
        for forbidden in ("identity", "principal", "user", "agent"):
            self.assertNotIn(forbidden, params)


class BridgeExposesOneQueryAPI(unittest.TestCase):
    """The Python bridge exposes the same single query API; it adds no
    filtering of its own."""

    def test_product_config_query_passthrough(self):
        pc = ProductConfig(inlined={"a": 1, "b": 2})
        pc.declare("b", ItemPolicy(tags=["x"]))
        pc.declare("a", ItemPolicy(read_level=2))
        self.assertEqual(pc.query(), ["b"])  # a is read-gated
        self.assertEqual(pc.query(tags=["x"]), ["b"])
        self.assertEqual(pc.max_read_level(), 2)
        self.assertEqual(pc.query(credential_level=2), ["a", "b"])
        self.assertEqual(pc.read("a", credential_level=2), 1)


if __name__ == "__main__":
    unittest.main()
