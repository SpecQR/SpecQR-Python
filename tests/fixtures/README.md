# Fixture provenance

`specqr-js-golden.json` is an unchanged copy of `fixtures/golden-cases.json`
from the SpecQR JavaScript repository at commit
`15ad15e5c770ea0e39072f8f88b2733018f02ffd`.

Copyright (c) 2026 SpecQR contributors. MIT license; see the repository's
`LICENSE`.

fixture は、version・ECC・mask を固定した 15 ケースを含み、正確な data/interleaved
codewords、matrix、hash、penalty、format/version 情報、remainder-module 数を保持します。
これらは関連する SpecQR 実装の回帰テスト用 vectors であり、独立 decoder の証拠ではありません。

`gs1-upstream.json` は、SpecQR-CSharp の
`tests/SpecQR.Tests/Fixtures/gs1-upstream.json` を、commit
`057c4b3f25e52c4786a8f94c744ff884eedcecfa` (MIT) から変更せずにコピーしたものです。
`gs1-deltas.json` は、dot-path の安全上の差分 6 件を明示的に記録します。
`gs1-url-ascii-upstream.json` は、決定的な Node WHATWG URL 比較の追加 512 件を記録します。

`structured_append.json` は、同じ固定済み SpecQR JavaScript baseline から生成した
22 sets と 112 symbols を記録します。再現可能な generator は
`tools/conformance/structured-append-golden.mjs` で、fixture の生成前に baseline の
commit を確認し、source に変更がないことを検証します。同系統の実装に由来するこれらの
fixtures は、独立 decoder gate を補うものであり、置き換えるものではありません。
