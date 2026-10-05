# GS1 / Digital Link

SpecQR-Python は、SpecQR と同じ範囲に限定した **50-AI カタログ**を扱う、外部依存のないヘルパーを提供します。GS1 全仕様の検証器、Digital Link resolver、圧縮実装、GS1 canonicalizer ではありません。GS1 ヘルパーはネットワーク接続を行いません。

```python
from specqr.gs1 import (
    GS1Element, create_gs1_element_string, parse_gs1_human_readable,
    parse_gs1_element_string, create_gs1_digital_link,
    parse_gs1_digital_link, validate_gs1_digital_link, normalize_gs1_digital_link,
)

elements = parse_gs1_human_readable("(01)04912345678904(10)100%(17)251231")
raw = create_gs1_element_string(elements)
assert raw == "010491234567890410100%\x1d17251231"
assert parse_gs1_element_string(raw).elements == elements

uri = create_gs1_digital_link(elements, base_url="https://example.com/products")
assert uri == "https://example.com/products/01/04912345678904/10/100%25?17=251231"
assert parse_gs1_digital_link(uri).elements == elements
assert normalize_gs1_digital_link(uri) == uri
```

公開ヘルパーは `specqr` からもエクスポートされます。要素、メタデータ、診断、結果は、snake_case フィールドを持つ frozen dataclass です。これらの API が返すコレクションは変更不可の tuple です。要素の入力には、`GS1Element` または文字列の `ai` / `value` フィールドを持つ mapping の iterable を指定でき、先頭のゼロを保持します。`create_gs1_digital_link` は `GS1ElementStringParseResult` も受け付けます。

## 対応 AI カタログ

`get_supported_gs1_ais()` は、固定した上流版と同じ順序で具体的な AI エントリを返します。`get_gs1_ai_info(ai)` はメタデータを返し、未対応 AI では `None` を返します。メタデータには、長さ、値の種類、チェックデジット規則、Digital Link の役割、区切り規則、qualifier に対して許可される primary AI が含まれます。

| AI | 値の種類と長さ |
| --- | --- |
| 00 | 18 桁の数字、SSCC チェックデジット |
| 01, 02 | 14 桁の数字、GTIN チェックデジット |
| 10, 21, 22 | 印字可能 ASCII、1–20 文字 |
| 11, 12, 13, 15, 16, 17 | 6 桁の数字 |
| 20 | 2 桁の数字 |
| 30, 37 | 1–8 桁の数字 |
| 240, 241, 400 | 印字可能 ASCII、1–30 文字 |
| 410–415 | 13 桁の数字 |
| 420 | 印字可能 ASCII、1–20 文字 |
| 422, 424, 425, 426 | 3 桁の数字 |
| 3100–3105, 3200–3205 | 6 桁の数字 |
| 91–99 | 印字可能 ASCII、1–90 文字 |

すべての値で括弧と ASCII GS（`U+001D`）を禁止します。日付は 6 桁の数字として検証し、実在する暦日かどうかは判定しません。GLN のチェックデジット、国コードの実在、用途・業種別の組み合わせ規則も検証しません。AI 90、3106、3206 など、この表にない AI は未対応です。

## 要素文字列と検証

- `parse_gs1_human_readable(input)` は `(AI)value` 表記を明示的に解析します。
- `create_gs1_element_string(elements)` は、最後以外の可変長要素の直後に ASCII GS を挿入します。
- `parse_gs1_element_string(input)` は `.elements` と `.has_separators` を返します。固定長要素に区切りは不要です。不正な位置の区切りや末尾の区切りは拒否します。
- `validate_gs1_elements(elements, *, context="element-string", collect_all_errors=True, allow_unsupported_ai=False)` は `.ok`、`.elements`、`.errors`、`.warnings` を返します。
- `validate_gs1_element_string(input, ...)` は、成功時に `.has_separators` も返します。解析は最初のエラーで停止します。上流と同様に、`context` と `collect_all_errors` は raw 解析の意味を変更しません。

要素検証の `context="digital-link"` は、AI 00、01、414 のいずれかが存在することを確認します。重複や URI 内の配置は検証しないため、それらには `validate_gs1_digital_link` を使用してください。未対応 AI を有効にすることはできず、`allow_unsupported_ai=True` は不正なオプションとして報告されます。

raw パーサは、曖昧な入力を保守的に拒否する SpecQR のヒューリスティックを継承しています。最後の可変長値が、対応する固定長 AI とその値の形で終わる場合、区切り欠落の可能性があるとして拒否します。疑わしい末尾部分の値自体は検証しません。たとえば `10ABC17251231` と `10ABC17XXXXXX` はどちらも拒否します。このため、任意の要素列について、生成した raw 文字列を解析して元に戻せる保証はありません。構造化された要素列を保持できる場合は、そのまま保持してください。

例外を投げるヘルパーは、`ValueError` のサブクラスで `.code == "INVALID_GS1"` を持つ `InvalidGs1Error` を送出します。結果を返す validator は検証失敗を捕捉しますが、呼び出し元の iterator やメモリ確保に由来する無関係な実行時エラーは捕捉しません。診断の `.code` と `.reason` は分岐に利用できます。英語メッセージと任意フィールドについて、言語間で同一になる保証はありません。offset は Python 文字列のコードポイント位置であり、JavaScript / C# の UTF-16 コード単位や C++ の UTF-8 バイト位置とは異なります。

### チェックデジット

`calculate_gs1_check_digit` と `validate_gs1_check_digit` は modulo-10 を実装します。GTIN 用は `calculate_gtin_check_digit`、`append_gtin_check_digit`、`validate_gtin_check_digit`、SSCC 用は名前の対応部分が `sscc` の各関数です。計算結果は 1 文字の文字列です。GTIN 本体は 7・11・12・13 桁、SSCC 本体は 17 桁を受け付けます。検証はチェックデジットだけが一致しない場合に `False` を返し、形式が不正な入力には `InvalidGs1Error` を送出します。

## Digital Link API

```python
uri = create_gs1_digital_link(
    elements,
    base_url="https://example.com/items",  # Required keyword
    primary_ai="01",                      # Also supports 00 or 414
    path_ais=None,                         # Automatic eligible qualifiers
)
parsed = parse_gs1_digital_link(uri, unknown_query="preserve")
checked = validate_gs1_digital_link(uri, unknown_query="reject")
normalized = normalize_gs1_digital_link(uri, mode="specqr-deterministic")
```

primary はパス要素です。primary 01 の後には、qualifier AI 10、21、22 を入力順に配置できます。primary 00、414 に対応するパス qualifier はありません。他の要素は AI 順に並べたクエリパラメータになります。`path_ais=()` は primary 以外をすべてクエリに配置し、明示的な部分集合を指定した場合は、その集合に含まれる配置可能な qualifier だけをパスに置きます。GS1 AI の重複はパスとクエリを通じて拒否します。未知の 2–4 桁の数値クエリキーは、通常の未知クエリパラメータとして扱わず、未対応 AI として拒否します。

解析結果には `elements`、`primary`、`path_elements`、`query_elements`、`unknown_query` が含まれます。既定では、AI ではない未知クエリの pair は順序と重複を保持します。`unknown_query="reject"` はそれらを拒否します。クエリの `+` は空白へ復号し、未知の値に含まれる percent-encoded NUL は Python 文字列内に保持します。正規化は、配置可能な qualifier をパスへ移し、GS1 クエリを並べ、未知の pair を元の相対順序で末尾へ追加します。HTTP の使用と未知クエリの保持は警告で通知します。validator の `normalize=True` は未対応です。正規化関数を明示的に呼び出してください。

### リテラル percent と dot segment の安全性

raw GS1 値の `100%` は `100%` のまま保持します。高水準 QR API は、GS1 / FNC1 が有効な場合でもリテラル percent を保持するため、安全なモードを選ぶか、安全でない alphanumeric の明示指定を拒否します。低水準の FNC1 alphanumeric セグメントは QR 自体の規則に従い、`%` が GS、`%%` がリテラル percent を表します。そのエスケープは呼び出し元が管理します。

URL のパス値 `.` / `..` はブラウザに削除される可能性があるため、生成時にパス配置が選ばれていれば拒否します。`path_ais=()` でクエリに置いてください。正規化でも、そのような qualifier はクエリに残します。リテラル値 `%2e` は別のデータであり、`%252e` に変換され、元の値へ復元できます。

入力 URI の primary AI パスセグメント以後にある、リテラルまたは percent-encoded の dot segment は、正規化によって消される**前に**拒否します。最初の primary AI より前にある通常の base / prefix の dot segment は正規化します。prefix 自体に `00`、`01`、`414` があり、その後に dot segment が続く場合は、曖昧な入力として保守的に解析を拒否します。生成時に明示した base URL は通常の prefix として扱います。

### URL の挙動と適用範囲

専用の小さな URL adapter を使用し、`urllib.parse.urlsplit` を検証器として頼ることなく、試験済みの上流 HTTP(S) の挙動を保持します。

- scheme / host の大小文字を正規化し、既定ポートを除去します。
- HTTP(S) の slash 補完、前後の C0 制御文字・空白除去、tab / CR / LF 除去、authority / path 内の backslash 変換を行います。クエリ内の backslash はデータとして保持します。
- 埋め込まれた `@` を含む userinfo をエスケープします。認証やネットワーク通信は行いません。
- 短縮形、16 進、8 進、単一整数の IPv4 表記を受け付け、4 つの 10 進数へ正規化します。
- IPv4-mapped address を含む角括弧付き IPv6 を受け付け、16 進グループ表記にそろえて最長のゼロ列を圧縮します。zone identifier は拒否します。
- 非空 fragment は拒否します。空の `#` は受け付け、生成では保持し、正規化では除去します。base URL の空の `?` は受け付けて置き換えますが、非空クエリは拒否します。
- パス値は percent 構文と UTF-8 を厳密に復号します。クエリ解析は `URLSearchParams` と同様の寛容な form decoding を使い、不正な UTF-8 を置換します。`validate_gs1_digital_link` と `normalize_gs1_digital_link` は、未知クエリの pair も含め、不正な `%HH` 構文を追加で拒否します。ただし、percent escape の構文が正しければ、form decoding が不正な UTF-8 を置換するという理由だけでは拒否しません。
- 一般的な DNS の妥当性、到達可能性、WHATWG URL への完全準拠、GS1 canonicalization は保証しません。

Unicode のホストラベルには、WHATWG の新しい UTS #46 処理ではなく、Python 標準の **IDNA2003** を使用します。`é.com`、`例え.テスト` などの一般的な名前と全角の IPv4 数字は対応します。具体的には次の差分があります。

| 入力ホスト | Python の出力 | Node WHATWG との比較 |
| --- | --- | --- |
| `faß.de` | `fass.de` | `xn--fa-hia.de` |
| `ς.gr` | `xn--4xa.gr` | `xn--3xa.gr` |
| `a` + U+200C + `b.com` | `ab.com` | 拒否 |
| `a` + U+200D + `b.com` | `ab.com` | 拒否 |

これらの mapping は別のホストを指す場合があります。新しい IDNA 処理と厳密に同じホストを指定する必要がある場合は、意図した ASCII / punycode ホスト名を渡してください。ASCII ラベルには IDNA2003 の DNS 長制限を適用しないため、途中の空ラベル、underscore、64 文字の ASCII ラベルなど、受理される URL 表記を保持します。非 ASCII ラベルには、codec の長さ、双方向テキスト、禁止文字、Unicode バージョンによる制限が残ります。ASCII の ACE / punycode ラベルについては、完全な IDNA 検証を行いません。たとえば、この adapter は ASCII の `xn--` を受け付けますが、Node は拒否します。Unicode ホストを一律に拒否する方針ではありません。

参照: [Python の IDNA codec](https://docs.python.org/3.12/library/codecs.html#module-encodings.idna)、[Python の URL 解析の制限](https://docs.python.org/3.12/library/urllib.parse.html#url-parsing-security)。

## リソース制限と並行利用

`GS1_MAX_INPUT_CHARACTERS = 1_000_000` は Python 文字列のコードポイント数を数え、`GS1_MAX_ELEMENTS = 16_384` は iterable の読み取り、解析済み要素、パスの component、クエリ pair の数を制限します。要素 iterable には、文字列である AI / value の長さの合計について、共有の 1,000,000 文字の処理量上限も設け、個別要素の検証前に確認します。同じオブジェクトを繰り返し参照していても、その回数分を数えます。これにより、大量の不正な値が文字走査の処理量を増幅することを防ぎます。上限を超えた validator は、構造化された `GS1_INVALID_INPUT` の結果を返します。同じ iterable の処理量上限は、`path_ais` 内の文字列エントリにも適用します。リソース検証は個別要素の診断より先に行われる場合があります。無限 iterable の読み取りも、適用される上限を 1 要素超えて確認するところまでで停止します。テキスト出力にも上限があります。これらは QR シンボルの格納容量とは別のリソース制限です。返されるメタデータと結果は変更不可で、ヘルパーに操作ごとの共有可変状態はありません。

## 検証と出典

`tests/fixtures/gs1-upstream.json` は SpecQR-CSharp commit `057c4b3` から変更せずにコピーしたもので、JavaScript SpecQR commit `15ad15e5c770ea0e39072f8f88b2733018f02ffd` を記録しています。標準ライブラリの unittest suite で、**1,411** 件の fixture をすべて実行します。

- **1,405** 件は、成功時の値、メタデータ、解析済みコレクション、検証結果、診断カテゴリを保持します。
- **6** 件の dot-path 安全性差分を明示的に記録しています。元の受理から拒否へ変わる 5 件と、診断だけが変わる 1 件です。case ID、入力、上流の結果、Python の結果を保持した `tests/fixtures/gs1-deltas.json` を参照してください。黙ってスキップする case はありません。
- 診断の文言と任意フィールドは、言語間の一致を判定する条件にしません。
- 追加試験は、dot / percent の保持、クエリの Unicode と NUL、IDNA2003 の差分、不正入力、リソース制限、無限 iterable・呼び出し元で例外を起こす iterable、メタデータの変更不可性、受理する URL 表記、固定 seed による 250 回のランダムな往復試験、200 回の並行呼び出しを対象にします。
- さらに 512 件の ASCII URL oracle は、U+0000–U+007F の各文字を、パス値、未知クエリ値、credentials、ホスト名に含めた場合を網羅します。固定した JavaScript 実装から直接生成し、試験実行時は Node に依存せず照合します。

パッケージをインストールした環境で `python -m unittest discover -s tests -p test_gs1.py` を実行してください。チェックアウトしたソースを使う場合は `PYTHONPATH=src` を設定します。

### 2026-10-05 prefix caret compatibility

生成時の base path と正規化時の prefix にある `^` を `%5E` として出力します。
既存の `%5E` は二重 encoding せず保持します。通常の QR encoding、GS1 payload、
query / credentials の escaping と実行時依存関係は変更しません。

元の 1,411 request、80 positive、49 shared case と 39 追加 case は、固定した
TypeScript `16efc6c0a8e397c9df3d051d20fce6c1eebdfad7` と公開済み Python
`69bc9d3257f7e86d5b07328437f4da9f95b4c843` の独立出力に結び付けています。
三つの caret 出力だけを更新し、既存の native diagnostic と上記 IDNA2003 差分を保持します。
この範囲は WHATWG / UTS46 完全準拠の追加ではありません。
