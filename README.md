# SpecQR-Python

SpecQR-Python is a dependency-free, typed QR Code Model 2 generator for Python, including Kanji, ECI, GS1, Structured Append, SVG and PNG.

Python 標準ライブラリだけで QR Code Model 2 を生成する SpecQR の Python 版です。QR エンコーダーや画像処理ライブラリのラッパーではありません。実装・ビルドとも外部依存はなく、外部の QR 実装は独立検証にだけ使います。

この版は `0.1.0rc1` の初期候補です。GitHub ソースからの利用を対象とし、PyPI 公開や stable release は行っていません。

## インストール

Python 3.11 以上が必要です。検証対象は CPython 3.11 / 3.12 / 3.13 / 3.14、Linux / macOS / Windows です。各環境の実行結果は [CI](https://github.com/SpecQR/SpecQR-Python/actions) で確認してください。新しい Python 版は、検証が完了するまでは対応確認済みとは扱いません。

```sh
git clone https://github.com/SpecQR/SpecQR-Python.git
cd SpecQR-Python
python -m pip install --no-deps .
```

オフラインでのビルドもできます。PEP 517 backend 自体に依存はありません。

```sh
python -c "import build_backend; print(build_backend.build_wheel('dist'))"
python -m pip install --no-index --no-deps dist/specqr_python-0.1.0rc1-py3-none-any.whl
```

## 基本例

```python
from pathlib import Path
from specqr import generate

qr = generate("https://example.com/", error_correction_level="M")
Path("qr.svg").write_text(qr.to_svg(), encoding="utf-8")
Path("qr.png").write_bytes(qr.to_png(scale=8))
print(qr.version, qr.mask_pattern)
print(qr.diagnostics["warnings"])
```

`generate()` は常に immutable な `QRResult` を返します。matrix は bool の tuple、codewords は bytes、diagnostics は再帰的に読み取り専用です。描画は結果の `to_svg()` / `to_png()` / `to_pixels()` / `to_svg_data_url()` / `to_png_data_url()`、または `render("svg")` で明示します。

## 対応範囲

- QR Code Model 2、Version 1–40、ECC L / M / Q / H、全 8 mask
- numeric / alphanumeric / UTF-8 byte / binary / Kanji、手動・自動 mixed segmentation
- ECI、FNC1 first / second、low-level Structured Append header
- 生成前の `estimate()` / `analyze_segments()`、`get_capacity()`、ECC boosting、診断
- 対応範囲を限定した 50 個の GS1 AI、element string、HRI、check digit、Digital Link
- text / binary / manual segment の Structured Append 分割、parity、merge
- SVG、PNG、RGBA pixels、data URL、CLI

Micro QR、rMQR、QR 読み取り、logo overlay、canvas / browser object は対象外です。

## Typed API と手動セグメント

```python
from specqr import Options, Segment, generate_segments, estimate, get_capacity

options = Options(error_correction_level="Q", min_version=1, max_version=10)
plan = estimate("注文1234567890", options)
if plan.ok:
    print(plan.version, plan.remaining_bits)

qr = generate_segments([
    Segment.eci(26),
    Segment.numeric("1234567890"),
    Segment.byte("こんにちは"),
], mask_pattern=3)

print(get_capacity(1, "M", mode="byte").max_bytes)  # 14
```

詳しい引数・error・差分は [API](docs/api.md)、GS1 は [GS1](docs/gs1.md)、分割は [Structured Append](docs/structured-append.md) を参照してください。

## CLI

```sh
python -m specqr 'Hello, QR!' --format svg --output hello.svg
specqr '漢字とUTF-8' --format png --output hello.png --diagnostics
specqr --binary payload.bin --format png --output binary.png
specqr --segments examples/segments.json --format matrix
```

標準出力へ PNG を出す場合もバイナリのまま出力します。diagnostics は標準エラーです。失敗時の終了コードは 2 です。

## 安全上の差分

- Python 文字列に含まれる孤立 surrogate は置換せず拒否します。
- high-level FNC1 入力の `%` は byte mode で保存します。明示 alphanumeric は拒否します。low-level 手動 alphanumeric では FNC1 の `%` / `%%` escaping を呼び出し側が指定します。
- GS1 Digital Link の path 値 `.` / `..` は、URL 正規化でデータが消えないよう拒否します。query 値は保存します。
- URL 処理は HTTP(S) 向け互換アダプターですが、Python 標準 IDNA2003 と WHATWG/UTS46 の差は残ります。具体的な境界は [GS1](docs/gs1.md) に記載します。
- 入力・描画・分割に明示的な resource budget を設けます。[検証と制限](docs/verification.md) を参照してください。

## 検証

基本テストは外部依存なしで実行できます。

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python tools/verify_package.py
```

PowerShell では `PYTHONPATH=src` の代わりに `$env:PYTHONPATH='src'` を設定してください。
独立 oracle / decoder と実行結果の区別は [検証](docs/verification.md) に記載します。

## 出典・ライセンス

MIT。SpecQR JavaScript `15ad15e5c770ea0e39072f8f88b2733018f02ffd`（3.0.0-rc.2）を主な基準として移植しています。SpecQR CSharp / CPP 版の検証資産も参照しています。[NOTICE](NOTICE) と [LICENSE](LICENSE) を参照してください。
