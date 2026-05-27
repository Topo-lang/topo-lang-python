"""Acceptance for two innermost-band mechanisms:

* code-layer inlined / hidden TOML (the embedded ``b`` default): the
  product needs no scattered external file for those defaults, the
  embedded block restores to equivalent TOML (round-trip), and the
  items still enumerate normally — embedding hides the *file*, not the
  *items*; ``a``/``c`` still override ``b``.
* the pure-internal band: declarable only in code, discoverable only in
  a dev-phase registry that the runtime store never consults, promoted
  to a plain constant with zero runtime config footprint.

Run: ``python3 -m unittest discover -s topo-lang-python/runtime/test``.
"""

import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from topo._config_model import (  # noqa: E402
    BuildConfigKeyError,
    ConfigStore,
    DevInternalRegistry,
    ItemPolicy,
    Layer,
    LayeredConfig,
    UnbridgedValueError,
)
from topo.config import ProductConfig  # noqa: E402


_TOML_SRC = """\
log_level = "info"
retries = 3
ratio = 0.5
enabled = true

[net]
host = "example.com"
ports = [80, 443]
"""


class InlineDeclareNoExternalFileNeeded(unittest.TestCase):
    def test_inline_declared_defaults_need_no_external_file(self):
        # No path given => no scattered external TOML file at all, yet
        # the embedded defaults are fully resolvable.
        pc = ProductConfig()  # path is None: nothing on disk
        pc.declare_inlined_toml(_TOML_SRC)
        self.assertIsNone(pc._path)
        self.assertEqual(pc.get("log_level"), "info")
        self.assertEqual(pc.get("net.host"), "example.com")
        self.assertEqual(pc.get("net.ports"), [80, 443])
        # Provenance: every value comes from the inlined (b) layer.
        for key in pc.keys():
            self.assertIs(pc.resolve(key).layer, Layer.B)

    def test_accepts_already_decoded_mapping_too(self):
        pc = ProductConfig()
        pc.declare_inlined_toml({"a": 1, "nested": {"b": 2}})
        self.assertEqual(pc.get("a"), 1)
        self.assertEqual(pc.get("nested.b"), 2)


class InlineRoundTrip(unittest.TestCase):
    def test_restore_yields_toml_reparsing_to_identical_data(self):
        pc = ProductConfig()
        pc.declare_inlined_toml(_TOML_SRC)

        restored = pc.restore_inlined_toml()
        # The restored text is real, re-parseable TOML.
        reparsed = tomllib.loads(restored)

        # Equivalent == re-parsing yields the same decoded data as the
        # original source decoded.
        self.assertEqual(reparsed, tomllib.loads(_TOML_SRC))

    def test_restore_is_idempotent_under_reparse(self):
        pc = ProductConfig()
        pc.declare_inlined_toml(_TOML_SRC)
        once = pc.restore_inlined_toml()
        # Feed the restored text back in and restore again: stable.
        pc2 = ProductConfig()
        pc2.declare_inlined_toml(once)
        self.assertEqual(tomllib.loads(pc2.restore_inlined_toml()),
                         tomllib.loads(once))

    def test_empty_inline_restores_to_empty(self):
        pc = ProductConfig()
        pc.declare_inlined_toml({})
        self.assertEqual(pc.restore_inlined_toml(), "")
        self.assertEqual(tomllib.loads(pc.restore_inlined_toml()), {})


class FileHiddenNotItemHidden(unittest.TestCase):
    def test_inlined_items_still_enumerate_under_normal_rules(self):
        pc = ProductConfig()
        pc.declare_inlined_toml(_TOML_SRC)
        keys = pc.keys()
        for k in ("log_level", "retries", "ratio", "enabled",
                  "net.host", "net.ports"):
            self.assertIn(k, keys)
        # Tag and read-tier rules still apply to inlined items normally.
        pc.declare("retries", ItemPolicy(tags=["tuning"]))
        pc.declare("net.host", ItemPolicy(read_level=2))
        # Tag filter selects the tagged inlined item.
        self.assertEqual(pc.query(tags=["tuning"]), ["retries"])
        # A read-gated inlined item hides by default but the top tier
        # still enumerates it (tiered-transparency holds for b too).
        self.assertNotIn("net.host", pc.query())
        self.assertIn("net.host", pc.query(credential_level=2))
        rv = pc.query_resolved()
        self.assertIn("log_level", rv)
        self.assertEqual(rv["log_level"].value, "info")

    def test_a_and_c_still_override_inlined_b(self):
        pc = ProductConfig(
            inlined={},  # will be replaced by the inline declaration
            injected={"retries": 99},  # c
        )
        pc.declare_inlined_toml(_TOML_SRC)
        # c overrides b.
        self.assertEqual(pc.get("retries"), 99)
        self.assertIs(pc.resolve("retries").layer, Layer.C)
        # a overrides b: write lands in the external (a) layer.
        pc.set("log_level", "debug")
        self.assertEqual(pc.get("log_level"), "debug")
        self.assertIs(pc.resolve("log_level").layer, Layer.A)
        # Untouched inlined value still resolves from b.
        self.assertEqual(pc.get("ratio"), 0.5)
        self.assertIs(pc.resolve("ratio").layer, Layer.B)

    def test_inline_layer_rejects_build_toolchain_key(self):
        pc = ProductConfig()
        with self.assertRaises(BuildConfigKeyError):
            pc.declare_inlined_toml({"build": {"language": "python"}})


class PureInternalDevPhaseOnly(unittest.TestCase):
    def test_declared_internal_is_dev_searchable_by_name_and_tag(self):
        pc = ProductConfig()
        value = pc.declare_internal(
            "MAX_BUF", 4096, tags=["perf", "memory"]
        )
        # The call returns the plain value to bind as a constant.
        self.assertEqual(value, 4096)
        # Dev-phase: present in the side registry, tag-searchable there.
        self.assertIn("MAX_BUF", pc.dev_internal.names())
        self.assertEqual(pc.dev_internal.search(["perf"]), ["MAX_BUF"])
        self.assertEqual(
            pc.dev_internal.search(["perf", "memory"]), ["MAX_BUF"]
        )
        self.assertEqual(pc.dev_internal.search(["unrelated"]), [])
        self.assertEqual(pc.dev_internal.get("MAX_BUF").value, 4096)

    def test_internal_absent_from_every_runtime_surface(self):
        pc = ProductConfig(inlined={"public.k": 1})
        pc.declare_internal("SECRET_TUNING", 7, tags=["internal"])
        # Not in keys / query / query_resolved / resolve_all.
        self.assertNotIn("SECRET_TUNING", pc.keys())
        self.assertNotIn("SECRET_TUNING", pc.query())
        self.assertNotIn("SECRET_TUNING",
                         pc.query(credential_level=999))
        self.assertNotIn("SECRET_TUNING", pc.store.resolve_all())
        self.assertNotIn("SECRET_TUNING",
                         pc.query_resolved(credential_level=999))
        with self.assertRaises(KeyError):
            pc.get("SECRET_TUNING")

    def test_promoted_value_is_a_plain_constant_no_config_reference(self):
        pc = ProductConfig()
        v = pc.declare_internal("RATE", 0.25)
        # Byte/identity-equivalent to a hand-written constant: the
        # returned object IS the value passed in, a bare float, with no
        # wrapper carrying a config-system back-reference.
        self.assertIs(type(v), float)
        self.assertEqual(v, 0.25)
        # The runtime store object holds no reference to the d registry.
        store_state = vars(pc.store)
        for attr in store_state.values():
            self.assertNotIsInstance(attr, DevInternalRegistry)

    def test_layer_d_stays_out_of_runtime_merge(self):
        # The model already excludes Layer.D from the merge order; this
        # pins that contract so a d band can never be merged at runtime.
        from topo import _config_model as m

        self.assertNotIn(Layer.D, m.RUNTIME_MERGE_ORDER)
        cfg = LayeredConfig(inlined={"k": 1})
        with self.assertRaises(AssertionError):
            cfg._layer_map(Layer.D)

    def test_internal_value_still_honours_stdlib_contract(self):
        pc = ProductConfig()
        from datetime import datetime
        with self.assertRaises(UnbridgedValueError):
            pc.declare_internal("WHEN", datetime(2026, 5, 16))

    def test_dev_registry_is_disjoint_from_store_type(self):
        # The registry is a free-standing type; ConfigStore neither
        # constructs nor stores one — the structural separation that
        # makes "no runtime presence" true by construction.
        reg = DevInternalRegistry()
        reg.declare("X", 1, tags=["t"])
        store = ConfigStore(LayeredConfig(inlined={"X": 2}))
        # Same name in both is a coincidence, not a link: the store
        # resolves its own b value and knows nothing of the registry.
        self.assertEqual(store.get("X"), 2)
        self.assertEqual(reg.get("X").value, 1)


if __name__ == "__main__":
    unittest.main()
