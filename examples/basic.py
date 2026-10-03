from pathlib import Path
from specqr import generate

qr = generate("SpecQR Python / こんにちは", eci=26)
Path("qr.svg").write_text(qr.to_svg(), encoding="utf-8")
Path("qr.png").write_bytes(qr.to_png())
print(qr.version, qr.mask_pattern)
