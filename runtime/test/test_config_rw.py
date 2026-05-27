"""Acceptance for the layered product-config read/write API: get/set
over the frozen b ◁ a ◁ c precedence, stdlib-contract value validation,
and the identity-independent high-impact write gate.

Run: ``python3 -m unittest topo-lang-python.runtime.test.test_config_rw``
(or ``python3 -m unittest discover -s topo-lang-python/runtime/test``).
"""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tomllib  # noqa: E402

from topo._config_model import (  # noqa: E402
    BuildConfigKeyError,
    ConfigStore,
    ImpactLevel,
    ItemPolicy,
    Layer,
    LayeredConfig,
    UnbridgedValueError,
)
from topo.config import ProductConfig  # noqa: E402


class ReadWriteRoundTrip(unittest.TestCase):
    """set lands in the external layer (a), get reflects b ◁ a ◁ c, and
    the new value shows up in the serialised topo-app.toml."""

    def test_set_then_get_through_store(self):
        store = ConfigStore(LayeredConfig(inlined={"log.level": "warn"}))
        # An external write must override the inlined default for the
        # same key, and report A as the provenance.
        store.set("log.level", "debug")
        self.assertEqual(store.get("log.level"), "debug")
        self.assertEqual(store.resolve("log.level").layer, Layer.A)
        # An injected (c) value still wins over the external write.
        store._cfg.injected["log.level"] = "trace"
        self.assertEqual(store.get("log.level"), "trace")
        self.assertEqual(store.resolve("log.level").layer, Layer.C)

    def test_get_default_only_when_no_layer_sets_key(self):
        store = ConfigStore(LayeredConfig(inlined={"present": 1}))
        self.assertEqual(store.get("absent", 42), 42)
        with self.assertRaises(KeyError):
            store.get("absent")  # no default -> no silent None
        self.assertEqual(store.get("present", 99), 1)

    def test_set_reflected_in_serialized_external_toml(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "topo-app.toml")
            pc = ProductConfig(path)
            pc.set("cache.size", 256)
            pc.set("log.level", "debug")
            pc.set("feature.flags", ["a", "b"])

            # Round-trips through the real stdlib TOML parser.
            with open(path, "rb") as fh:
                reloaded = tomllib.load(fh)
            self.assertEqual(reloaded["cache"]["size"], 256)
            self.assertEqual(reloaded["log"]["level"], "debug")
            self.assertEqual(reloaded["feature"]["flags"], ["a", "b"])

            # A fresh ProductConfig over the same file reads it back.
            pc2 = ProductConfig(path)
            self.assertEqual(pc2.get("cache.size"), 256)
            self.assertEqual(pc2.get("feature.flags"), ["a", "b"])

    def test_keys_enumerates_all_layers(self):
        store = ConfigStore(
            LayeredConfig(inlined={"a.x": 1}, injected={"c.z": 3})
        )
        store.set("b.y", 2)
        self.assertEqual(store.keys(), ["a.x", "b.y", "c.z"])


class ValueTypeContract(unittest.TestCase):
    """Only stdlib-bridged values are accepted; a value with no contract
    (notably TOML datetime) is rejected, naming the key and the gap."""

    def test_stdlib_scalars_accepted(self):
        store = ConfigStore()
        store.set("s", "str")
        store.set("i", 7)
        store.set("f", 1.5)
        store.set("b", True)
        store.set("arr", [1, 2, 3])
        store.set("rec", {"id": 1, "amount": 2.0})
        self.assertEqual(store.get("rec"), {"id": 1, "amount": 2.0})

    def test_datetime_rejected_points_to_bridging_gap(self):
        store = ConfigStore()
        with self.assertRaises(UnbridgedValueError) as ctx:
            store.set("event.at", datetime(2026, 5, 16, 12, 0, 0))
        msg = str(ctx.exception)
        self.assertIn("event.at", msg)                       # locates the key
        self.assertIn("stdlib-bridging-types", msg)          # names the gap
        self.assertIn("time_*", msg)                         # names the missing family

    def test_datetime_nested_in_array_rejected(self):
        store = ConfigStore()
        with self.assertRaises(UnbridgedValueError) as ctx:
            store.set("schedule.points", [datetime(2026, 1, 1)])
        self.assertIn("schedule.points", str(ctx.exception))

    def test_non_stdlib_object_rejected_and_located(self):
        store = ConfigStore()

        class Custom:
            pass

        with self.assertRaises(UnbridgedValueError) as ctx:
            store.set("weird.value", Custom())
        msg = str(ctx.exception)
        self.assertIn("weird.value", msg)
        self.assertIn("stdlib-bridging-types", msg)

    def test_build_toolchain_key_still_rejected_on_write(self):
        store = ConfigStore()
        with self.assertRaises(BuildConfigKeyError) as ctx:
            store.set("build.standard", "c++20")
        self.assertIn("Topo.toml", str(ctx.exception))


class WriteProtectionGate(unittest.TestCase):
    """High-impact items need a credential to write; low-impact items do
    not. The gate is identity-independent: same rule for human or agent,
    it only inspects credential level."""

    def _store(self):
        store = ConfigStore()
        store.declare("db.dsn", ItemPolicy(impact=ImpactLevel.HIGH))
        store.declare("ui.theme", ItemPolicy(impact=ImpactLevel.LOW))
        return store

    def test_high_impact_write_without_credential_rejected(self):
        store = self._store()
        with self.assertRaises(PermissionError) as ctx:
            store.set("db.dsn", "postgres://prod")
        msg = str(ctx.exception)
        self.assertIn("db.dsn", msg)
        self.assertIn("HIGH", msg)
        # The guard message is about credentials, never about identity.
        self.assertNotIn("human", msg.lower())
        self.assertNotIn("agent", msg.lower())

    def test_high_impact_write_with_credential_succeeds(self):
        store = self._store()
        store.set("db.dsn", "postgres://prod", credential_level=1)
        self.assertEqual(store.get("db.dsn"), "postgres://prod")

    def test_low_impact_write_needs_no_credential(self):
        store = self._store()
        store.set("ui.theme", "dark")  # no credential argument at all
        self.assertEqual(store.get("ui.theme"), "dark")

    def test_undeclared_item_defaults_to_low_impact(self):
        store = ConfigStore()
        store.set("anything.unlisted", 1)  # no declare(), no credential
        self.assertEqual(store.get("anything.unlisted"), 1)

    def test_gate_is_identity_independent(self):
        # The authorize/set surface takes a credential *level* and no
        # principal: a "human" and an "agent" presenting the same level
        # get the exact same outcome. We assert that by behaviour and by
        # the absence of any identity parameter in the signatures.
        import inspect

        from topo._config_model import authorize_write

        for fn in (authorize_write, ConfigStore.set):
            params = set(inspect.signature(fn).parameters)
            self.assertNotIn("identity", params)
            self.assertNotIn("principal", params)
            self.assertNotIn("user", params)
            self.assertNotIn("agent", params)

        # Behavioural equivalence: two callers, same level, same result.
        store_a = self._store()
        store_b = self._store()
        with self.assertRaises(PermissionError):
            store_a.set("db.dsn", "x", credential_level=0)  # "the human"
        with self.assertRaises(PermissionError):
            store_b.set("db.dsn", "x", credential_level=0)  # "the agent"
        store_a.set("db.dsn", "ok", credential_level=1)
        store_b.set("db.dsn", "ok", credential_level=1)
        self.assertEqual(store_a.get("db.dsn"), store_b.get("db.dsn"))


if __name__ == "__main__":
    unittest.main()
