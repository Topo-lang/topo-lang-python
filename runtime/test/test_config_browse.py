"""Acceptance for the unified browse + agent-introspection entry: one
call that returns, within the caller's read tier, a self-describing row
per runtime config item, plus a structurally separate dev-phase listing
of the pure-internal (d) band.

The central correctness risk this file guards is that the browse routes
through the tier-aware door (``query_resolved``/``query``/``policy_of``)
and never the tier-blind ``resolve_all``: a permission-gated item must
not leak into a lower-level caller's browse. The tiered-transparency
invariant (the top level sees every runtime item; each level sees
exactly that level's complete range) is asserted explicitly.

Run: ``python3 -m unittest topo-lang-python.runtime.test.test_config_browse``
(or ``python3 -m unittest discover -s topo-lang-python/runtime/test``).
"""

import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from topo._config_model import (  # noqa: E402
    _NO_DEFAULT,
    BrowseEntry,
    ConfigStore,
    ImpactLevel,
    ItemPolicy,
    Layer,
    LayeredConfig,
)
from topo.config import ProductConfig  # noqa: E402


def _layered_store():
    """A store with values arriving from all three runtime layers
    (b inlined default, a external override, c in-code injection), a
    mix of impact levels, read tiers, and freely-combinable tags — so
    every documented row field can be checked against a hand-built
    sample with known provenance."""

    store = ConfigStore(
        LayeredConfig(
            # b — inlined defaults (also the "default" column source).
            inlined={
                "log.level": "warn",
                "net.timeout_ms": 1000,
                "cache.size": 256,
                "db.dsn": "postgres://default",
            },
            # a — external file overrides one default.
            external={"net.timeout_ms": 5000},
            # c — in-code injection overrides, and adds a key with no
            # inlined default.
            injected={"cache.size": 512, "feature.flag": True},
        )
    )
    store.declare("log.level", ItemPolicy(tags=["obs"]))
    store.declare(
        "net.timeout_ms",
        ItemPolicy(tags=["network", "tuning"], impact=ImpactLevel.HIGH),
    )
    store.declare("cache.size", ItemPolicy(tags=["tuning"]))
    store.declare("feature.flag", ItemPolicy(tags=["features"]))
    # Permission-gated: hidden below read level 2.
    store.declare(
        "db.dsn",
        ItemPolicy(tags=["network"], read_level=2, impact=ImpactLevel.HIGH),
    )
    return store


def _row_by_key(rows):
    return {r.key: r for r in rows}


class FullPerItemSchema(unittest.TestCase):
    def test_every_documented_field_present_and_correct(self):
        store = _layered_store()
        # Browse at the top tier so every item, including the gated one,
        # is in range and can be schema-checked.
        rows = store.browse(credential_level=store.max_read_level())
        by = _row_by_key(rows)

        # Every row is the frozen BrowseEntry with the full field set.
        for r in rows:
            self.assertIsInstance(r, BrowseEntry)
            for fld in (
                "key",
                "type",
                "default",
                "effective",
                "layer",
                "impact",
                "required_write_level",
                "required_read_level",
                "tags",
            ):
                self.assertTrue(hasattr(r, fld), f"missing field {fld}")

        # b-sourced: log.level. Default == effective == the inlined b
        # value; provenance is layer B; low impact, open read tier.
        log = by["log.level"]
        self.assertEqual(log.type, "str")
        self.assertEqual(log.default, "warn")
        self.assertEqual(log.effective, "warn")
        self.assertIs(log.layer, Layer.B)
        self.assertIs(log.impact, ImpactLevel.LOW)
        self.assertEqual(log.required_write_level, 0)
        self.assertEqual(log.required_read_level, 0)
        self.assertEqual(log.tags, frozenset({"obs"}))

        # a-sourced: external overrode the inlined default. default is
        # still the b value, effective is the a value, provenance A.
        net = by["net.timeout_ms"]
        self.assertEqual(net.type, "int")
        self.assertEqual(net.default, 1000)
        self.assertEqual(net.effective, 5000)
        self.assertIs(net.layer, Layer.A)
        self.assertIs(net.impact, ImpactLevel.HIGH)
        self.assertEqual(net.required_write_level, 1)
        self.assertEqual(net.required_read_level, 0)
        self.assertEqual(net.tags, frozenset({"network", "tuning"}))

        # c-sourced over a b default.
        cache = by["cache.size"]
        self.assertEqual(cache.type, "int")
        self.assertEqual(cache.default, 256)
        self.assertEqual(cache.effective, 512)
        self.assertIs(cache.layer, Layer.C)

        # c-sourced with NO inlined default -> the no-default sentinel,
        # never a fabricated value, and still typed from the effective.
        flag = by["feature.flag"]
        self.assertEqual(flag.type, "bool")
        self.assertIs(flag.default, _NO_DEFAULT)
        self.assertEqual(flag.effective, True)
        self.assertIs(flag.layer, Layer.C)

        # Gated item, visible only at/above its tier; both permission
        # roles are exposed: read tier 2, write gate 1 (HIGH impact).
        dsn = by["db.dsn"]
        self.assertEqual(dsn.required_read_level, 2)
        self.assertEqual(dsn.required_write_level, 1)
        self.assertEqual(dsn.type, "str")


class TieredTransparencyInvariant(unittest.TestCase):
    def test_gated_item_absent_below_tier_present_at_and_above(self):
        store = _layered_store()
        # db.dsn requires read level 2.
        below = {r.key for r in store.browse(credential_level=0)}
        self.assertNotIn("db.dsn", below)
        at = {r.key for r in store.browse(credential_level=2)}
        self.assertIn("db.dsn", at)
        above = {r.key for r in store.browse(credential_level=5)}
        self.assertIn("db.dsn", above)

    def test_top_level_browse_equals_complete_runtime_key_set(self):
        store = _layered_store()
        top = store.max_read_level()
        browsed = sorted(r.key for r in store.browse(credential_level=top))
        # The invariant: nothing in the runtime key set is invisible at
        # every level — the top of the scale enumerates them all.
        self.assertEqual(browsed, store.keys())

    def test_each_level_is_exactly_that_levels_complete_range(self):
        store = _layered_store()
        # Level 0 = every non-gated item (its complete range), the gated
        # one excluded — no more, no less.
        zero = sorted(r.key for r in store.browse(credential_level=0))
        expected_zero = sorted(
            k
            for k in store.keys()
            if store.policy_of(k).read_level == 0
        )
        self.assertEqual(zero, expected_zero)


class RoutesThroughTierAwareDoor(unittest.TestCase):
    def test_browse_does_not_use_resolve_all_to_leak(self):
        store = _layered_store()
        # resolve_all is tier-blind: it would surface the gated key at
        # any level. The browse at level 0 must NOT — proving it goes
        # through the tier-aware door, not resolve_all. This assertion
        # FAILS if someone swaps the browse onto resolve_all/resolve.
        tier_blind = set(store.resolve_all())
        self.assertIn("db.dsn", tier_blind)

        browsed_keys = {r.key for r in store.browse(credential_level=0)}
        self.assertNotIn("db.dsn", browsed_keys)
        self.assertNotEqual(browsed_keys, tier_blind)
        # And the value is unreachable too, not just the name.
        for r in store.browse(credential_level=0):
            self.assertNotEqual(r.key, "db.dsn")


class IdentityIndependence(unittest.TestCase):
    def test_signature_has_no_principal_identity_param(self):
        for fn in (ConfigStore.browse, ProductConfig.browse):
            params = set(inspect.signature(fn).parameters)
            for forbidden in ("principal", "identity", "user", "agent"):
                self.assertNotIn(
                    forbidden,
                    params,
                    f"{fn.__qualname__} must not take a {forbidden} arg",
                )

    def test_same_level_yields_identical_browse(self):
        store = _layered_store()
        a = store.browse(credential_level=1)
        b = store.browse(credential_level=1)
        # Same level -> byte-identical rows regardless of any caller
        # notion; BrowseEntry is frozen so equality is structural.
        self.assertEqual(a, b)


class LiveDerivedNoStaticList(unittest.TestCase):
    def test_key_added_after_construction_auto_appears(self):
        store = _layered_store()
        before = {r.key for r in store.browse(credential_level=0)}
        self.assertNotIn("late.added", before)
        store.set("late.added", "hi")  # lands in the external (a) layer
        after = {r.key for r in store.browse(credential_level=0)}
        self.assertIn("late.added", after)


class DevPhaseDListing(unittest.TestCase):
    def test_d_absent_from_runtime_browse_at_every_level(self):
        cfg = ProductConfig(
            inlined={"log.level": "warn"},
        )
        cfg.declare_internal("BUILD_SALT", "abc123", tags=["crypto"])
        cfg.declare_internal("MAX_WIDGETS", 64, tags=["limits"])
        for level in (0, 1, 99):
            keys = {r.key for r in cfg.browse(credential_level=level)}
            self.assertNotIn("BUILD_SALT", keys)
            self.assertNotIn("MAX_WIDGETS", keys)
        # And not in the raw runtime key set either.
        self.assertNotIn("BUILD_SALT", cfg.keys())

    def test_d_present_only_in_dev_listing_and_tag_searchable(self):
        cfg = ProductConfig()
        cfg.declare_internal("BUILD_SALT", "abc123", tags=["crypto"])
        cfg.declare_internal("MAX_WIDGETS", 64, tags=["limits"])

        listed = {r["name"] for r in cfg.dev_browse()}
        self.assertEqual(listed, {"BUILD_SALT", "MAX_WIDGETS"})

        crypto = cfg.dev_browse(tags=["crypto"])
        self.assertEqual([r["name"] for r in crypto], ["BUILD_SALT"])
        self.assertEqual(crypto[0]["value"], "abc123")
        self.assertEqual(crypto[0]["tags"], frozenset({"crypto"}))

    def test_dev_browse_shape_is_distinct_from_runtime_entry(self):
        cfg = ProductConfig()
        cfg.declare_internal("BUILD_SALT", "abc123", tags=["crypto"])
        rec = cfg.dev_browse()[0]
        # A dev record is a plain dict, never a BrowseEntry — the two
        # ranges are kept structurally disjoint.
        self.assertIsInstance(rec, dict)
        self.assertNotIsInstance(rec, BrowseEntry)
        self.assertEqual(set(rec), {"name", "value", "tags"})

    def test_no_d_declared_yields_empty_listing_without_registry(self):
        cfg = ProductConfig()
        # Browsing the empty dev band must not even create the side
        # registry (a runtime-only build never builds it).
        self.assertEqual(cfg.dev_browse(), [])
        self.assertIsNone(cfg._dev_internal)


class ProductConfigBrowseParity(unittest.TestCase):
    def test_bridge_browse_is_passthrough_to_model(self):
        cfg = ProductConfig(
            inlined={"a.x": 1, "b.y": "two"},
            injected={"a.x": 9},
        )
        cfg.declare("b.y", ItemPolicy(tags=["t"], read_level=1))
        # Below tier: gated key absent.
        low = {r.key for r in cfg.browse(credential_level=0)}
        self.assertEqual(low, {"a.x"})
        # At tier: full range, rows are model BrowseEntry objects.
        full = cfg.browse(credential_level=cfg.max_read_level())
        self.assertTrue(all(isinstance(r, BrowseEntry) for r in full))
        self.assertEqual({r.key for r in full}, {"a.x", "b.y"})
        ax = _row_by_key(full)["a.x"]
        self.assertEqual(ax.default, 1)
        self.assertEqual(ax.effective, 9)
        self.assertIs(ax.layer, Layer.C)


if __name__ == "__main__":
    unittest.main()
