# Contributing

README / docs / commit message は日本語を基本にし、API 名・型名・command は英語を使います。

実装は Python 標準ライブラリだけに限定します。第三者 QR 実装・画像ライブラリ・native wrapper は runtime に追加しません。独立検証の dependency は tools/conformance に隔離します。

変更後は次を実行してください。

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python tools/verify_package.py
```

core / segment / rendering / GS1 / Structured Append の変更では独立 conformance と decoder gate も必須です。手順は tools/conformance/README.md を参照してください。失敗を expected 値の自動更新で隠さず、missing dependency を成功扱いしないでください。

public API を変える場合は docs と negative tests を揃え、三 OS・四 Python 版の CI を確認してください。PyPI 公開、stable tag、release はこの source-only workflow に含めません。
