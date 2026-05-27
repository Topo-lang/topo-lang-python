// PythonSafePatterns::loadDefault regression test.
//
// Pins the catalog-resolution contract: `loadDefault` returns true and
// populates the safe/unsafe sets when called from a built tree without
// any environment override, by resolving the in-source catalog at
// `topo-lang-python/topo-check/analysis/catalog/PythonSafePatterns.toml`.
//
// History: a now-removed second candidate path
// (`topo-lang-python/analysis/catalog/PythonSafePatterns.toml`,
// pre-restructure layout) was kept in the candidate array "for older
// worktrees". No active worktree carries that path; the legacy fallback
// added a stat call and a misleading comment, and was deleted along
// with this regression test landing to prevent the dead path from being
// re-added.

#include "analysis/catalog/PythonSafePatterns.h"

#include <gtest/gtest.h>

using topo::check::PythonSafePatterns;

TEST(PythonSafePatternsLoad, LoadDefaultResolvesCurrentInSourceCatalog) {
    PythonSafePatterns patterns;
    ASSERT_TRUE(patterns.loadDefault())
        << "loadDefault must locate the catalog at "
           "topo-lang-python/topo-check/analysis/catalog/"
           "PythonSafePatterns.toml when invoked from a built tree.";

    // Sanity: a known whitelisted construct must come back as safe.
    // `if` is the canonical positive-control: it appears in every
    // Python catalog revision since the file's introduction.
    EXPECT_TRUE(patterns.isConstructSafe("if"))
        << "Expected 'if' to be in the safe-constructs set; if this "
           "fails the catalog file was located but is empty or "
           "structurally unexpected.";

    // A nonsense construct must NOT be classified as either safe or
    // unsafe — guards against an over-broad default.
    EXPECT_FALSE(patterns.isConstructSafe("__never_a_real_construct__"));
    EXPECT_FALSE(patterns.isConstructUnsafe("__never_a_real_construct__"));
}
