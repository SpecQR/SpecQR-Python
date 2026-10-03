# Security

SpecQR-Python は QR を生成するライブラリです。入力の意味・URL の安全性・相手の本人性を保証しません。機密情報を QR に入れる前に、利用者側で公開範囲を確認してください。

この版は初期候補です。input / renderer に resource budget を設けていますが、untrusted input を受け付ける service では request size・CPU time・concurrency も制限してください。SVG を描画する環境の CSS color policy は利用側で管理してください。

脆弱性は公開 issue に秘密情報や exploit 対象の利用者データを載せず、GitHub の private vulnerability reporting が利用可能ならそちらを使用してください。未提供の場合は影響を一般化した issue で安全な連絡方法を相談してください。
