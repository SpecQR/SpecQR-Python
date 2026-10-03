# Fixture provenance

`specqr-js-golden.json` is an unchanged copy of `fixtures/golden-cases.json`
from the SpecQR JavaScript repository at commit
`15ad15e5c770ea0e39072f8f88b2733018f02ffd`.

Copyright (c) 2026 SpecQR contributors. MIT license; see the repository's
`LICENSE`. The fixture contains fifteen fixed-version, fixed-ECC, fixed-mask
cases with exact data/interleaved codewords, matrices, hashes, penalties,
format/version information, and remainder-module counts. These are regression
vectors for the related SpecQR implementation, not independent decoder evidence.

`gs1-upstream.json` is an unchanged copy of
`tests/SpecQR.Tests/Fixtures/gs1-upstream.json` from SpecQR-CSharp
`057c4b3f25e52c4786a8f94c744ff884eedcecfa` (MIT). `gs1-deltas.json` records
six explicit dot-path safety differences. `gs1-url-ascii-upstream.json`
records 512 additional deterministic Node WHATWG URL comparisons.

`structured_append.json` records 22 sets and 112 symbols generated from the
same pinned SpecQR JavaScript baseline. The reproducible generator is
`tools/conformance/structured-append-golden.mjs`; it verifies the baseline
commit and source cleanliness before generating fixtures. These owner-family
fixtures supplement, rather than replace, the independent decoder gates.
