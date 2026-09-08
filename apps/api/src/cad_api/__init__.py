"""HTTP transport for the CAD application service.

A thin adapter. It contains **no CAD business logic**: no validation rules, no
geometry, no exporter, no cache logic, no process management, no document
canonicalization, no build-key computation and no artifact checksums. Every
one of those lives in ``cad_core`` and stays there.

```
HTTP request  ->  transport contract  ->  application service  ->  infrastructure
HTTP response <-  transport contract  <-  application result
```

See ``docs/http-api.md``.
"""

from cad_api.config import ApiConfig, config_from_environment
from cad_api.status import FAILURE_STATUS, INTERNAL_STATUS, TRANSPORT_STATUS

__all__ = [
    "FAILURE_STATUS",
    "INTERNAL_STATUS",
    "TRANSPORT_STATUS",
    "ApiConfig",
    "config_from_environment",
]
