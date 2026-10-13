# Development-only conformance tools

Nothing in this directory is imported by the Python runtime.

通常の package と unit tests に必要なのは標準ライブラリだけです。独立した encoders と
decoders は固定したテスト用 tools で、これらの検証を実施するときだけインストールします。

repository のルートから実行します。

```sh
npm ci --prefix tools/conformance --ignore-scripts
git clone https://github.com/SpecQR/SpecQR.git ../SpecQR-baseline
git -C ../SpecQR-baseline checkout 15ad15e5c770ea0e39072f8f88b2733018f02ffd
python tools/conformance/verify_conformance.py --baseline ../SpecQR-baseline
python tools/conformance/verify_jsqr.py
python tools/conformance/verify_decoders.py --decoder cpp --dependency-dir .tools/zxing-cpp
python tools/conformance/verify_decoders.py --decoder java --dependency-dir .tools/zxing-java
```

該当する検証には、Node 18+、Python 3.11+、Java 17+ の source launcher が必要です。
decoder wheels は、選択した Python/OS に対応している必要があります。`--no-install` は、
hash 検証済みの decoder files だけを使い、それらがなければ失敗します。Nayuki/jsQR は、
別途用意したテスト用 package directory からも解決でき、その場合は
`SPECQR_DEV_NODE_MODULES=/absolute/package/directory` を指定します。

別の source checkout を対象にするには `--candidate PATH` を使います。別途 build して
インストールした wheel には `--python /absolute/venv/bin/python --installed` を使います。
その場合、driver は Python を `-I` 付きで実行し、`PYTHONPATH` を削除し、source checkout を
挿入しません。import した module の path、interpreter、nonce、PID は conformance report に
記録します。package の build と wheel のインストールは別の検証です。`--installed` を
使っただけで、wheel を監査したと主張するものではありません。

## What each tool verifies

- `verify_conformance.py`: 実行中の公開 API が返す matrices、pad 済み data codewords、
  完全な interleaved data/ECC を、固定した開発元の JavaScript checkout と比較します。
  checkout の commit と、追跡対象 source に変更がないことの両方を検査します。公開 API の
  固定条件のケースは、version 1–40、ECC L/M/Q/H、mask 0–7 をすべて含みます。
  独立した Nayuki lane は、auto-mode・自動 mask 選択・ECC boost を使わず、これらの
  1,280 tuples すべてを検査します。追加の manual/mixed/ECI ケースは segment 境界を保持します。
  比較には正確な十六進 codewords と、canonical な row-major matrix 全体の SHA-256 を使います。
- 既定の conformance run は、次の構成で 4,320 個の raw matrices も検査します。
  three distinct data
  patterns × 40 versions × four ECC levels × automatic and eight explicit masks.
  すべての mask penalties、全 65,536 GF products、次数 1–255 の RS generator/remainder を
  検査します。公開 API の検証範囲には、640 capacity cases、境界上・境界未満・境界超過の
  1,920 estimates、決定的な fuzz、不正な入力、Structured Append、同一 process を共有する
  8 threads による差分再実行を含みます。
- 6 つの異常対照は、実際に Python を起動して候補へ到達した後、matrix digest、data byte、
  interleaved ECC/data byte のいずれかを改変するか、終了・応答欠落・エラーを強制します。
  検出できない対照が一つでもあれば、その run は失敗します。これは実際の比較と process の
  経路を検証するもので、作り物のローカル dictionary 同士を比較するものではありません。
- `verify_jsqr.py`: 独立した matrix の raster 化と、jsQR 1.4.0 による実際の PNG pixels からの
  検出を行います。対応する Numeric/Alphanumeric/Byte/Kanji、binary bytes、ECI 26 chunks、
  全 version、全白画像の異常対照を検査します。jsQR は FNC1/Structured Append に未対応です。
  ここでそれらを検証したとは主張せず、ZXing で別途検証します。
- `verify_decoders.py`: ZXing-C++ 3.1.1 または ZXing Java 3.5.4 による独立 decode、正確な
  bytes/text と decoder が公開する metadata、全 version、ECI、FNC1-first、Structured Append の
  合法な header index/total の全 135 組合せ、高レベル set の完全な再構成を検証します。
  C++ は全 152 種類の FNC1-second indicators を、manual と high-level の両経路で検査します。
  Java は SA sequence/parity metadata と、各 ECC/mask の組合せで破損した 3 codewords が
  訂正されることを検査します。

PNG の経路は、実際の PNG pixels と quiet-zone pixels のすべてを検証してから decoder に
渡します。Java は scale 3 を使い、`PURE_BARCODE` を使わず、PNG 検出失敗後の matrix fallback も
行いません。既知の default-scale-8 ケースは、拒否も含めて、同じ画素を持つ独立した対照と
並べて別途報告し、strict な成功数には加えません。C++ は既定の scale 8 を検査します。
ZXing-C++ の ECI API の違いは、raw-byte と ECI-transcoded の両 API を含め、
独立した Nayuki の対照と照合します。

report は、失敗と選択した検証範囲を区別します。`--suite public` または
`--suite internal` は範囲を限定した実行であることを明示し、全 conformance の検証完了とは
主張しません。依存の欠落、decode の失敗、version の省略、出力の切捨て、候補 source の変更を
黙って成功に数えることはありません。合成した有限のテストは、ISO/GS1 の認証、camera や
印刷の試験、任意の入力や破損に対する証明ではありません。

## Test-tool provenance

`oracle.mjs`、`structured-append-cases.json`、`eci-control.mjs` と ZXing decoder workflow は、
MIT ライセンスの SpecQR-CPP の次の commit を基に調整したものです。
`e91cd8fe4434fd6ef10d1126bb2fd17b0b19321f`。`DecodeSymbols.java` と固定した decoder の
requirements は、MIT ライセンスの SpecQR-CSharp の次の commit に由来します。
`057c4b3f25e52c4786a8f94c744ff884eedcecfa`。Java adapter は元の SpecQR-Swift の出典表記を
保持します。JavaScript baseline:
`15ad15e5c770ea0e39072f8f88b2733018f02ffd`.

npm lock は、テスト専用 package の integrity を記録します。ダウンロードする ZXing Java JAR と
公式 PyPI の ZXing-C++ wheels は、hash で固定しています。第三者の encoder や decoder の
source、binary、dependency、import を Python runtime に含めることはありません。
