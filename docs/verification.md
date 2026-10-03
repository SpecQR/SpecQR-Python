# 検証・互換性・resource budget

## 方針

有限のテスト結果を、ISO / GS1 certification、任意入力の正しさ、camera / print / 実機 scanner の保証として扱いません。missing dependency、skip、decoder failure、未実行 platform は成功に数えません。

主な基準は SpecQR JavaScript `15ad15e5c770ea0e39072f8f88b2733018f02ffd`。GS1 fixture は CSharp `057c4b3f25e52c4786a8f94c744ff884eedcecfa` から取得し、元の内容を保持しています。出典は [NOTICE](../NOTICE) に記載します。

## Gate

1. stdlib `unittest`: QR core、全 capacity、GF / RS、golden、segments、planning、GS1、Structured Append、rendering、concurrency、invalid / boundary / resource
2. clean package: AST runtime-import audit、dependency metadata、deterministic wheel、RECORD hash、clean venv installed consumer、CLI
3. live owner baseline: matrix、pad 済み data、interleaved data/ECC、mask penalties、capacity / overflow、GF / RS、fuzz / concurrent replay
4. independent Nayuki: forced version/ECC/mask/manual mode による比較。自動最適化や auto mask の同一性を独立性と取り違えない
5. jsQR: matrix raster と actual PNG の独立 detection。ECI metadata を含む。FNC1 / SA は対応していないので主張しない
6. ZXing-C++ / ZXing Java: exact payload、FNC1、ECI、Structured Append metadata と reassembly、actual PNG、error correction
7. Linux / macOS / Windows × CPython 3.11 / 3.12 / 3.13 / 3.14 CI

実行手順は [tools/conformance](../tools/conformance/README.md)。CI の独立検証レポートは該当 run の artifact から確認できます。source snapshot と test tool / fixture hash を記録し、途中で候補が変わった run は pass としません。installed lane は `-I` と空の PYTHONPATH を使い、読み込んだ package を確認します。

不正な Python 応答（matrix / data / ECC の改変、process exit、response 欠落、error）を注入する六つの negative control を別 interpreter で実行し、比較器が実際に Python 結果を検査していることを確認します。

## 既知の互換境界

- JS の lone-surrogate replacement を Python へ持ち込まず、Unicode scalar でない str を拒否
- `str` の長さ・診断位置は Python code point 単位。UTF-16 code unit ではない
- GS1 `%` と dot path の安全差分、および IDNA2003 の境界は [GS1](gs1.md)
- manual mapping の不明・曖昧な field、bool-as-int は早期エラー
- source-only 初期候補。PyPI install や stable tag を確認したという意味ではない
- Java decoder の default scale 8 の一部画像への rejection は、同じ画素の独立 PNG control と並べて報告。strict Java gate は scale 3、PURE_BARCODE や failure 後の matrix fallback なし。scale 8 の failure を pass に含めない

## Resource budget

- text / binary payload: 1,000,000 source units を上限として、copy / encode 前に検査
- manual segment sequence: 16,384 segments、aggregate 1,000,000 source units
- 自動 single-symbol traceback: QR 最大容量から 7,089 scalar まで。大きい input は容量 preflight / 非最適化 overflow 計画へ
- QR version は 1..40、matrix は最大 177×177、single-symbol bit expansion は最大 23,648 data bits
- GS1: 1,000,000 characters、16,384 elements、および aggregate work budget。iterator は上限までしか materialize しない
- SA: 2..16 symbols。生成前に最大 payload capacity を確認。full split details は opt-in
- raster: 4 Mi pixels、RGBA 16 MiB、PNG raw は 17 MiB 未満
- SVG: 8 Mi characters、data URL: 32 Mi characters
- render geometry は非負の 53-bit safe integer を上限とし、allocation 前に検査

これらは個々の処理の deterministic limit です。プロセス全体の peak RSS 上限や同時接続数の制限ではありません。PNG の raw / encoded buffer など複数の buffer が同時に存在することがあるため、untrusted service では外側にも resource limit を置いてください。

## Python version

[Python の公式 lifecycle](https://devguide.python.org/versions/) を確認し、3.11 を最小版にしています。3.10 は対応対象外です。CI matrix に書いたことだけでは実行済みを意味しません。各 run の terminal status を確認してください。
