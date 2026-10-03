# Python API

Python では snake_case、immutable dataclass、bytes を基本にします。JavaScript と同じ機能を提供しますが、戻り値は Python 向けに統一しています。

## 生成と結果

`generate(input, options=None, **overrides)` と `generate_segments(segments, options=None, **overrides)` は `QRResult` を返します。入力は str / bytes / bytearray / memoryview、手動入力は `Segment` または mode と data を持つ mapping の有限 sequence です。リストの整数列を一般の binary 入力として暗黙変換しません。`bytes(values)` を使ってください。

`Options` は frozen dataclass です。keyword override は指定済み `Options` より優先します。不明な option を無視しません。bool を整数値として受け付けません。

主要 option:

- `error_correction_level='M'`: L / M / Q / H
- `version=None`: 自動。明示値 1..40 は min/max より優先
- `min_version=1`, `max_version=40`
- `mask_pattern=None`: 自動。明示値 0..7
- `mode='auto'`: numeric / alphanumeric / byte / kanji も指定可能
- `optimize_segments=True`, `boost_error_correction=False`
- `eci=None`: 0..999999、True は UTF-8 の 26、False は無効
- `gs1=False`, `fnc1_second=None`: second は ASCII 数字 2 桁または英字 1 字
- `structured_append=None`: `Segment.structured_append(index,total,parity)` または同名の 3 項目の mapping
- `margin=4`, `scale=8`, `foreground='#000000'`, `background='#ffffff'`, `print_dpi=None`

ECI / GS1 / FNC1 second / Structured Append は、この版では互いに組み合わせられません。手動 ECI を複数指定することは可能です。FNC1 / FNC1 second / Structured Append は option と手動 control の重複も拒否します。ECI option は手動 ECI の前に追加され、同じ assignment の複数 header も許容します。

`QRResult` の主な属性は matrix、version、mask_pattern、error_correction_level、data_codewords、codewords、error_correction_codewords、segments、diagnostics、options です。data_codewords は pad 済み・block 分割前、codewords は interleaved data + ECC、error_correction_codewords は interleaved ECC 部分です。

描画関数の引数は margin / scale / foreground / background。結果に保存した既定値を上書きできます。独立関数 `to_svg(matrix,...)` なども利用できます。`to_pixels()` は width / height / pixels を持つ `Pixels` を返し、pixels は row-major RGBA bytes です。matrix を外部から渡す場合は 1..177 の正方形 bool sequence に限定します。

SVG は XML-escape した CSS color string を許容します。PNG / pixels は black / white / transparent と #RGB / #RGBA / #RRGGBB / #RRGGBBAA に限定します。PNG は deterministic stored-DEFLATE、RGBA、filter 0 です。

## セグメント

`Segment.numeric(text)`、`alphanumeric(text)`、`byte(text_or_bytes)`、`kanji(text)`、`eci(number)`、`fnc1()`、`fnc1_second(indicator)`、`structured_append(index,total,parity)` を使えます。

手動 segment は境界を保持します。ECI は自動 Kanji 選択を無効にします。手動 Kanji は明示的な選択として残ります。Kanji は、指定した QR 範囲の CP932 二重バイトを復号して first mapping を選ぶため、JavaScript の WHATWG Shift_JIS mapping と対応します。

FNC1 と alphanumeric を手動で組み合わせる場合、単独 `%` は group separator、`%%` は literal percent の意味です。この low-level escaping は呼び出し側の責任です。通常の文字列を high-level `gs1=True` / `fnc1_second=...` に渡す場合は、literal percent を失わない byte fallback を適用します。

## Planning / capacity / diagnostics

`estimate(input,...)` と `analyze_segments(segments,...)` は `Plan` を返し、matrix・RS・mask 探索を行いません。容量不足は例外ではなく `ok=False`、`remaining_bits<0`、`overflow_bits>0` で表します。不正な入力・option は例外です。

自動範囲で不足したとき `version=None`、`capacity_version=max_version` です。明示 version の不足では version を保持します。容量不足で `CAPACITY_NEAR_LIMIT` を返しません。巨大な auto 入力で最良の numeric 下限さえ入らない場合、bounded preflight 後の単一 mode 計画を返すため、これは実行可能な最適化計画ではありません。

`get_capacity(version, error_correction_level='M', mode=None, control_bits=0)` は `Capacity` を返します。max_characters / max_bytes / maximum は単一 segment の上限です。UTF-8 文字数と bytes は同じではありません。FNC1、ECI 等の header 分を含めるには control_bits を指定します。

diagnostics は mode/control、segment ごとの bit count、version/ECC、capacity、mask penalty、quiet zone、color contrast、print geometry、warnings を含みます。結果を変更して encode 状態を壊せないよう、mapping と sequence は読み取り専用です。`dict(qr.diagnostics)` は外側だけのコピーです。JSON が必要なら CLI `--diagnostics` を利用するか、mapping/tuple を再帰変換してください。

描画は生成後の操作なので、diagnostics の色・print 項目は生成時 Options を示します。`to_png(scale=...)` の override は診断を書き換えません。raster を拡大縮小して印刷・表示した結果や実機 scanner の読み取りを保証するものではありません。

## エラー

`SpecQRError` は `ValueError` の派生です。`code` に安定識別子があります。派生は DataTooLongError / InvalidInputError / InvalidVersionError / InvalidModeError / InvalidColorError / InvalidEciError / InvalidGs1Error / InvalidOutputError。容量不足の生成は DataTooLongError、planning は上記の `ok=False` です。

str 中の surrogate、非 ASCII の numeric、曖昧な payload key、未対応の mapping field、範囲外や bool の整数 option は拒否します。エラーを隠して別の encoding に切り替えません。
