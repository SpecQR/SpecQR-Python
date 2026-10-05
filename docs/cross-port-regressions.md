# TypeScript 不具合の移植版監査

TypeScript の修正対象 4 件を既存 Python API の契約で確認しました。runtime の同じ不具合は再現せず、runtime は変更していません。回帰テストを追加しました。

- **FNC1 の literal `%`**: high-level の auto は既存どおり byte に退避します。forced alphanumeric は明示的な `INVALID_MODE` です。低水準の手動 FNC1 segment は書き換えません。`%` は GS、`%%` は literal `%` という呼出側管理の規則を保持します。通常の非 FNC1 `%` は literal のままです。
- **容量差**: byte 退避は TypeScript の escaped-alphanumeric 最適化と同じ matrix、segment、最小 version を保証しません。version 1 / ECC L の FNC1 first-position byte payload は 17 bytes まで、18 bytes は安全に容量超過となり、auto version は拡大します。TypeScript より大きな symbol や明示的な失敗はデータ破損ではありません。
- **Digital Link の dot 値**: Python は path の `.` / `..` を明示的に拒否します。query に置いた値は保持します。 raw input の最初の不正 primary を dot 正規化で消して後続 primary を採用するケースは拒否します。安全な base URL の dot 正規化、query の dot 値、重複した未知 query、literal `%2e` 値を保持します。
- **print DPI**: 既存の immutable options 検証は最大 version 40 を使い、module と全 symbol の物理寸法が有限になることを要求します。固定 version 1 でも `1e-304` は拒否されます。`1e-303` と通常の 300 DPI は有限で、QR data は DPI に左右されません。API は常に診断を返し、TypeScript の `diagnostics: false` に相当する公開 option はありません。この既存契約を変更しません。
- **ECC キー**: JavaScript prototype chain の影響はありません。不正な文字列や null/None は既存の型付き入力エラーで拒否します。L/M/Q/H の通常入力は維持します。

追加した自己テストは payload、forced mode、低水準 segment、count-width 境界、容量・資源上限、dot path/query、非有限 print geometry、不正 ECC を対象にしています。依存追加はありません。既存の独立検証・package 検証コマンドは CONTRIBUTING.md を参照してください。別 OS・compiler の実行結果をローカル成功から推定しません。
