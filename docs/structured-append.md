# Structured Append（分割 QR）

SpecQR-Python は、SpecQR のコミット
`15ad15e5c770ea0e39072f8f88b2733018f02ffd` における高レベル生成、手動セグメント、
パリティ計算、再結合の仕様を実装しています。エンコーダーとすべての実行時ヘルパーは、
Python 標準ライブラリだけで動作します。

## テキスト・バイナリを高レベル API で分割する

```python
from specqr import generate_structured_append

result = generate_structured_append(
    "A" * 31,
    version=1,
    error_correction_level="L",
    mode="alphanumeric",
    mask_pattern=0,
)
assert result.total == 2
assert result.parity == 65
assert [p["input_length"] for p in result.diagnostics["symbols"]] == [21, 10]
first_png = result.symbols[0].to_png(scale=6)
```

凍結された `SAResult` は、`symbols`、`total`、`parity`、`input_length`、
`byte_length`、`diagnostics` を保持します。`symbols` は通常の `QRResult`
オブジェクトのタプルです。各結果から、マトリクス、パディング済みデータコードワード、
インターリーブ済みコードワード、選択されたマスク、バージョン、セグメント、診断情報、
描画メソッドを利用できます。Structured Append の入れ子の診断情報も、変更不能な
マッピングとタプルです。

テキストは Unicode スカラー値の境界でのみ分割します。結合文字列や絵文字の並びが
複数シンボルにまたがることはありますが、再結合すると元の文字列を正確に復元します。
バイト列入力は生バイトの境界で分割します。memoryview は、スライスされたビューを
含め、選択された範囲のバイトだけを使用します。テキストのパリティとバイト位置は、
元の文字列の UTF-8 表現に基づきます。サロゲートコードポイントを含む Python 文字列は
暗黙に置換せず、拒否します。Unicode 正規化は行いません。

`version=None` は、`min_version..max_version`（既定値 `1..40`）の中から、
データを貪欲法で `2..max_symbols` 個に分割できる最小バージョンを選びます。
`max_symbols` は 2〜16 の整数で、既定値は 16 です。すべてのシンボルは、同じ
バージョンと誤り訂正レベルを使用します。各シンボルには、残りのデータの先頭から
格納可能な最長部分を割り当てます。シンボル数や印刷面積の合計を最小化する探索では
ありません。容量判定には、20 ビットの Structured Append ヘッダーを含めます。

Structured Append ヘッダーを含めても 1 シンボルに収まるペイロードは拒否します。
その場合は `generate()` を使用してください。このため、同じ短いペイロードでも、
自動選択ではバージョン 1 の有効な分割セットとなり、より大きいバージョンを明示すると
1 シンボルに収まるため拒否されることがあります。指定されたバージョン範囲と
シンボル数の上限で分割できない場合は、`DataTooLongError` を送出します。

省略可能な `Options` オブジェクトを第 2 位置引数に渡せます。キーワード引数は、
その設定を上書きします。Python の `version` と `mask_pattern` の自動選択には、
JavaScript の文字列 `"auto"` ではなく `None` を使用します。

## 手動セグメントを分割する

```python
from specqr import Segment, generate_segments_structured_append

result = generate_segments_structured_append(
    [
        Segment.alphanumeric("ABCDEFGHIJKLMNOPQRSTU"),
        Segment.numeric("12345678901234567890"),
        Segment.byte(bytes([0, 1, 2, 255])),
    ],
    version=1,
    error_correction_level="L",
    mask_pattern=0,
    diagnostics={"split_units": "full"},
)
assert result.total == 2
assert result.parity == 189
assert result.diagnostics["split_unit_count"] == 6
```

手動指定では、呼び出し側が与えたセグメント境界をすべて保持します。数字、英数字、
漢字の各セグメントは、途中で分割すれば収まる場合でも分割しない単位として扱います。
バイトモードのテキストは Unicode スカラー値の境界で、バイナリはバイトの境界で
分割できます。分割しないセグメントが、許可されたどのバージョンにも収まらない場合は
容量エラーになります。元の各セグメントには、空でないデータが必要です。手動入力は、
`Segment` インスタンスまたはセグメントのマッピングを格納した有限のリストかタプルです。
マッピングには、`data` を含む通常の Python セグメントのキーを使用します。

手動セットの `input_length` は、元のセグメント数です。`byte_length` は、元メッセージの
規定のバイト表現を数えます。漢字テキストについても UTF-8 を使用し、QR の漢字モードで
使う 1 文字 2 バイトの Shift_JIS 表現は数えません。

高レベル API は、ヘッダーを自ら計算して挿入します。手動の制御セグメント、ヘッダーや
パリティの上書き、ECI、GS1/FNC1 第 1 位置、FNC1 第 2 位置、誤り訂正レベルの自動引き上げは
拒否します。手動セグメント生成では、`mode`、`encoding`、`optimize_segments` の
キーワード引数による上書きも拒否します。共通の `Options` オブジェクトを使用する場合、
手動生成ではモードと最適化の設定を既定値のままにする必要があります。

## コンパクトな診断情報と全分割単位の診断情報

セット全体の診断情報は常に存在します。手動セグメント API の既定設定と
`diagnostics=True` は、v3 のコンパクトな形式を返します。

- `split_unit_count`: セット全体の論理的な分割単位数
- `split_units_detail`: `"summary"`
- `symbols`: 最大 16 個のシンボル別レコード。元セグメント、分割単位、規定の
  バイト表現における位置を含みます
- `split_units` キーは存在せず、単位ごとの診断オブジェクトも作成しません

`diagnostics={"split_units": "full"}` を明示した場合だけ、シンボル生成が成功した後に
`split_units` を作成します。この場合、`split_units_detail` は `"full"` になります。
各レコードは、`source_segment_index`、`mode`、`unit_start`、`unit_length`、
`byte_start`、`byte_length` を持ちます。バイナリ・テキストのバイトモードでは
`unit_length=1` です。それ以外のモードでは、分割しないセグメント全体を記述します。
レコードは元の入力順を保持します。位置は各フィールドの意味に従って Unicode スカラー値
または規定のバイト表現で数え、UTF-16 の位置は使用しません。

`diagnostics` のマッピングでは、`symbol_results="output"|"diagnostics"` も指定できます。
これは JavaScript の警告選択の仕様を保持するための設定ですが、Python は常に診断情報を
持つ `QRResult` を返します。Python の描画は各結果のメソッドで明示的に行い、出力形式に
応じて戻り値の型が変わることはありません。`diagnostics=True` とマッピングの既定設定は、
デコーダーの対応状況に関する注意を含めます。`diagnostics=False` と
`symbol_results="output"` は、その注意を省略します。どちらの設定も、符号化データ、
分割位置、パリティ、マスク、マトリクスには影響しません。

テキスト・バイナリ向け高レベル API の `diagnostics` は、真偽値だけを受け付けます。
全分割単位の詳細を指定できるのは、手動セグメント API です。

## 低レベルのヘッダー指定とパリティ

あらかじめ分割したデータには、`Segment.structured_append(index, total, parity)` を
構築するか、`generate()` / `generate_segments()` に
`structured_append={"index": ..., "total": ..., "parity": ...}` を渡します。
公開 API のインデックスは 1 始まりです。符号化される順序の 4 ビット値は、それぞれ
`index-1` と `total-1` です。低レベルのヘッダー指定は、パリティを計算せず、
残りのシンボルが存在することも確認しません。

```python
from specqr import (
    calculate_structured_append_parity,
    calculate_structured_append_segments_parity,
    generate,
)

parity = calculate_structured_append_parity("HELLO WORLD")
first = generate(
    "HELLO ",
    structured_append={"index": 1, "total": 2, "parity": parity},
)
```

パリティは、元のテキストの UTF-8 バイト列、または元の生バイト列全体に対する XOR です。
手動セグメント用ヘルパーも、漢字を含むすべてのデータセグメントに同じ規則を適用します。
高レベルのパリティ用ヘルパーは空のテキスト・バイナリを受け付け、0 を返します。
手動セグメント用ヘルパーには、少なくとも 1 個の空でないデータセグメントが必要で、
制御セグメントは拒否します。

## 再結合

```python
from specqr import calculate_structured_append_parity, merge_structured_append_parts

parity = calculate_structured_append_parity("HELLO WORLD")
merged = merge_structured_append_parts([
    {"index": 2, "total": 2, "parity": parity, "data": "WORLD"},
    {"index": 1, "total": 2, "parity": parity, "data": "HELLO "},
])
assert merged.data == "HELLO WORLD"
```

凍結された `MergeResult` は、順序をそろえた各部分のメタデータとパリティ検証の
診断情報を保持します。再結合には、総数とパリティが一致する 2〜16 個の重複のない部分が
必要です。インデックスは `1..total` で、欠番があってはいけません。再結合したメッセージの
規定のバイト表現に対して XOR を検証します。各部分のデータは、すべて文字列、または
すべてバイト列である必要があります。データ型の混在、不正な整数メタデータ（bool を含む）、
未対応のデータ、不一致のメタデータ、重複、欠番、パリティ検証の失敗は拒否します。
バイナリ出力は、入力と所有権を共有しない変更不能な `bytes` です。

このヘルパーは、すでにデコードされた各部分のデータを受け取ります。QR スキャナーでは
ありません。また、Structured Append のメタデータを取得できるかどうかは、スキャナーの
API によって異なります。8 ビットの XOR は弱い破損検出であり、認証や同一セットの証明には
なりません。総数とパリティが一致するだけで、信頼できない部分を混ぜてはいけません。

## リソース上限

- パリティ用ヘルパーと再結合は、入力全体で最大 1,000,000 単位を許可します。
  テキストは Unicode スカラー値 1 個、バイナリは生バイト 1 個を 1 単位と数えます。
- 手動セグメント用ヘルパーは、元セグメントを最大 16,384 個まで受け付けます。
  ジェネレーターや要素数に上限のない反復可能オブジェクトは消費しません。
- 生成時は、入力サイズに比例するコピー、セグメントの正規化、最適化を行う前に、
  バージョン・誤り訂正レベル・シンボル数から求めた、さらに小さい容量上限を適用します。
- 自動モードの先頭部分の最適化は、一定サイズのコスト状態を保持します。UTF-8 の位置を
  求める索引は、64 スカラー値ごとに 1 個のチェックポイントを持ちます。分割候補の評価で、
  QR マトリクスやコードワード配列を構築することはありません。
- 全分割単位の詳細は明示的な指定が必要で、容量による上限があります。バージョン 40-L の
  バイナリ最大ペイロードでは、16 シンボルのセットが 47,216 バイト単位を含みます。
  標準の診断形式では、この 47,216 個の単位別レコードを作成しません。
- 生成するマトリクスは最大 16 個です。各シンボルを画像に変換する際には、描画側の
  リソース上限も別途適用されます。

リソース上限を超えると `DataTooLongError` を送出します。これは、通常の QR 容量自体を
判定しないパリティ・再結合ヘルパーにも適用されます。これらの上限、厳格な Unicode の
取り扱い、変更不能な結果、snake_case の名前は、JavaScript 基準実装に対する意図的な
Python API の違いです。

## 再現可能な検証

`tests/test_structured_append.py` は、固定した JavaScript 基準実装の 22 セット、
計 112 シンボルと比較します。保存済みフィクスチャは、正確な分割サマリー、
マトリクスの全行、パディング済みデータコードワード、インターリーブ済みコードワードを
含みます。固定・自動のバージョンとマスク、数字・英数字・漢字、Unicode・バイナリ入力、
混在モードの最適化、手動の非分割単位とバイト境界、16 シンボルのセットを検証します。
フィクスチャを使うテストには Python だけが必要です。

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_structured_append.py -v
```

開発時のみ使用する Node と、指定コミットをチェックアウトした変更のない基準実装を使い、
ゴールデンフィクスチャを再生成するには、次を実行します。

```sh
node tools/conformance/structured-append-golden.mjs \
  ../baselines/SpecQR \
  tools/conformance/structured-append-cases.json \
  tests/fixtures/structured_append.json
```

生成スクリプトは、基準実装のコミットを検証します。Node と元の JavaScript 実装は、
テストの比較対象としてのみ使用します。Python の実行時コードから呼び出すことはありません。
