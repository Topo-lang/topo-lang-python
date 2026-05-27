# Fixture for stdlib-bridge default summaries.
#
# Binds one host value of each stdlib-bridge shape (uuid / decimal128 /
# ndarray / time_ns) then stops on the `sentinel` line so the adapter
# can read them at a stable frame. numpy is optional — the ndarray case
# is configure-gated on its importability; the other three use only the
# stdlib so they always run.

import decimal
import uuid

u = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
d = decimal.Decimal("-12345.67890")
# Nanoseconds since the Unix epoch for 2026-05-16T00:00:00Z
# (1_747_~ ... a value squarely inside the [1e17, 1e19) ns window).
t = 1747353600000000000

try:
    import numpy as _np
    arr = _np.arange(1, 13, dtype=_np.float64).reshape(3, 4)
except Exception:  # numpy absent — keep a placeholder so the name binds
    arr = None

sentinel = 0  # breakpoint line
print(u, d, t, arr is not None)
