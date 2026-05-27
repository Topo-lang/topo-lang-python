"""Acceptance for the layered product-config model: frozen a/b/c merge
precedence, per-value provenance, and the Topo.toml boundary guard.

Run: ``python3 -m unittest topo-lang-python.runtime.test.test_config_model``
(or ``python3 -m unittest discover -s topo-lang-python/runtime/test``).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from topo._config_model import (  # noqa: E402
    PRODUCT_CONFIG_FILENAME,
    BuildConfigKeyError,
    Layer,
    LayeredConfig,
    iter_provenance,
    merge_layers,
    reject_if_build_config_key,
)


class MergePrecedence(unittest.TestCase):
    """`inlined (b) ◁ external (a) ◁ in-code (c)` — more explicit wins,
    and every value carries the layer it came from."""

    def setUp(self):
        # A sample where each layer is the sole winner of at least one
        # key, plus a key all three set so precedence is unambiguous.
        self.cfg = LayeredConfig(
            inlined={
                "log.level": "warn",      # only b -> b wins
                "cache.size": 64,         # b, overridden by a
                "retry.count": 1,         # b, overridden by a and c
            },
            external={
                "cache.size": 256,        # a beats b
                "retry.count": 3,         # a beats b, lost to c
                "feature.flag": True,     # only a -> a wins
            },
            injected={
                "retry.count": 9,         # c beats a and b
                "tracing.enabled": False,  # only c -> c wins
            },
        )

    def test_each_key_has_unique_effective_value_and_provenance(self):
        resolved = self.cfg.resolve_all()

        self.assertEqual(resolved["log.level"].value, "warn")
        self.assertEqual(resolved["log.level"].layer, Layer.B)

        self.assertEqual(resolved["cache.size"].value, 256)
        self.assertEqual(resolved["cache.size"].layer, Layer.A)

        self.assertEqual(resolved["feature.flag"].value, True)
        self.assertEqual(resolved["feature.flag"].layer, Layer.A)

        # Set by all three layers: c (most explicit) must win.
        self.assertEqual(resolved["retry.count"].value, 9)
        self.assertEqual(resolved["retry.count"].layer, Layer.C)

        self.assertEqual(resolved["tracing.enabled"].value, False)
        self.assertEqual(resolved["tracing.enabled"].layer, Layer.C)

    def test_keys_enumerates_every_layer_once_sorted(self):
        self.assertEqual(
            self.cfg.keys(),
            [
                "cache.size",
                "feature.flag",
                "log.level",
                "retry.count",
                "tracing.enabled",
            ],
        )

    def test_iter_provenance_triples(self):
        triples = list(iter_provenance(self.cfg.resolve_all()))
        self.assertEqual(
            triples,
            [
                ("cache.size", 256, Layer.A),
                ("feature.flag", True, Layer.A),
                ("log.level", "warn", Layer.B),
                ("retry.count", 9, Layer.C),
                ("tracing.enabled", False, Layer.C),
            ],
        )

    def test_merge_layers_helper_matches(self):
        resolved = merge_layers(
            inlined={"x": 1},
            external={"x": 2},
            injected={"x": 3},
        )
        self.assertEqual(resolved["x"].value, 3)
        self.assertEqual(resolved["x"].layer, Layer.C)

    def test_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            self.cfg.resolve("does.not.exist")

    def test_d_layer_is_not_a_runtime_merge_layer(self):
        # d exists in the vocabulary but is promoted to code, never
        # merged at runtime — asking the model to read it as a layer is
        # an explicit construction error, not a silent empty result.
        with self.assertRaises(AssertionError):
            LayeredConfig()._layer_map(Layer.D)


class TopoTomlBoundary(unittest.TestCase):
    """A build-toolchain key is rejected, and the error names
    Topo.toml so the user knows where it actually belongs."""

    def test_build_section_key_rejected_and_points_to_topo_toml(self):
        with self.assertRaises(BuildConfigKeyError) as ctx:
            reject_if_build_config_key("build.language")
        self.assertIn("Topo.toml", str(ctx.exception))
        self.assertIn(PRODUCT_CONFIG_FILENAME, str(ctx.exception))

    def test_feature_mode_section_key_rejected(self):
        # [parallel]/[adaptive]/etc. are build feature-mode sections.
        for key in ("parallel.mode", "adaptive.min_trigger_ns",
                    "optimize.indirection", "check.jobs", "topo.root"):
            with self.assertRaises(BuildConfigKeyError):
                reject_if_build_config_key(key)

    def test_build_key_in_a_layer_rejected_on_resolve_all(self):
        cfg = LayeredConfig(external={"build.standard": "c++20"})
        with self.assertRaises(BuildConfigKeyError) as ctx:
            cfg.resolve_all()
        self.assertIn("Topo.toml", str(ctx.exception))

    def test_product_key_with_similar_name_is_not_rejected(self):
        # Only the exact build sections are off-limits; product keys
        # that merely look related are fine.
        reject_if_build_config_key("checkout.timeout_ms")  # not [check]
        reject_if_build_config_key("testing_endpoint.url")  # not [test]
        cfg = LayeredConfig(inlined={"checkout.timeout_ms": 5000})
        self.assertEqual(
            cfg.resolve("checkout.timeout_ms").value, 5000
        )


if __name__ == "__main__":
    unittest.main()
