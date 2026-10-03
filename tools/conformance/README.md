# Development-only conformance tools

Nothing in this directory is imported by the Python runtime. The normal package
and its unit tests need only the standard library. Independent encoders and
decoders are pinned test tools, installed only when these checks are requested.

From the repository root:

```sh
npm ci --prefix tools/conformance --ignore-scripts
git clone https://github.com/SpecQR/SpecQR.git ../SpecQR-baseline
git -C ../SpecQR-baseline checkout 15ad15e5c770ea0e39072f8f88b2733018f02ffd
python tools/conformance/verify_conformance.py --baseline ../SpecQR-baseline
python tools/conformance/verify_jsqr.py
python tools/conformance/verify_decoders.py --decoder cpp --dependency-dir .tools/zxing-cpp
python tools/conformance/verify_decoders.py --decoder java --dependency-dir .tools/zxing-java
```

Node 18+, Python 3.11+, and a Java 17+ source launcher are required for the relevant
checks. Decoder wheels must support the selected Python/OS. `--no-install` uses
only already hash-verified decoder files and fails when they are missing.
Nayuki/jsQR can also be resolved from a separate prepared test package directory
with `SPECQR_DEV_NODE_MODULES=/absolute/package/directory`.

Use `--candidate PATH` to target another source checkout. For a separately built
and installed wheel, use `--python /absolute/venv/bin/python --installed`. The
driver then runs Python with `-I`, removes `PYTHONPATH`, and never inserts a source
checkout. Its imported module path, interpreter, nonce, and PID are recorded in
the conformance report. The package build and wheel installation are separate
checks; merely using `--installed` does not claim that a wheel was audited.

## What each tool verifies

- `verify_conformance.py`: live public API matrices, padded data codewords, and
  complete interleaved data/ECC against the pinned owner JavaScript checkout.
  Both the checkout commit and tracked source cleanliness are checked. Public
  fixed-condition cases include every version 1–40, ECC L/M/Q/H, and mask 0–7.
  An independent Nayuki lane covers every one of those 1,280 tuples without
  auto-mode, automatic masking, or ECC boost; additional manual/mixed/ECI cases
  preserve segment boundaries. The comparison uses exact hexadecimal codewords
  and SHA-256 over the complete canonical row-major matrix.
- The default conformance run also checks 4,320 raw matrices: three distinct data
  patterns × 40 versions × four ECC levels × automatic and eight explicit masks.
  It checks all mask penalties, all 65,536 GF products, and RS generator/remainder
  degrees 1–255. Public coverage includes 640 capacity cases, 1,920 at/below/above
  boundary estimates, deterministic fuzz, malformed input, Structured Append,
  and shared-process eight-thread differential replay.
- Six negative controls actually launch Python and reach the candidate before
  corrupting a matrix digest, a data byte, or an interleaved ECC/data byte, or
  forcing exit, a missing response, or an error. Any undetected control fails the
  run. This tests the live comparison and process route, not a fabricated local
  dictionary comparison.
- `verify_jsqr.py`: independent matrix rasterization and actual PNG-pixel
  detection through jsQR 1.4.0. Checks supported Numeric/Alphanumeric/Byte/Kanji,
  binary bytes, ECI 26 chunks, all versions, and an all-white negative control.
  jsQR does not support FNC1/Structured Append; those checks are never claimed
  here and are performed through ZXing instead.
- `verify_decoders.py`: independent ZXing-C++ 3.1.1 or ZXing Java 3.5.4 decoding,
  exact bytes/text and decoder-exposed metadata, all versions, ECI, FNC1-first,
  all 135 legal Structured Append header index/total combinations, and complete
  high-level set reconstruction. C++ checks all 152 FNC1-second indicators through
  both manual and high-level routes. Java checks SA sequence/parity metadata and
  three corrupted codewords corrected in each ECC/mask combination.

The PNG routes verify every real PNG pixel and quiet-zone pixel before passing
those pixels to a decoder. Java uses scale 3 without `PURE_BARCODE` and without
matrix fallback after failed PNG detection. Its known default-scale-8 case is
reported separately beside an independent identical-pixel control, including
any rejection; it never increments the strict success count. C++ checks the
default scale 8. ECI API differences in ZXing-C++ are checked against independent
Nayuki controls, including both raw-byte and ECI-transcoded APIs.

Reports distinguish failures from the selected scope. `--suite public` or
`--suite internal` is explicitly a scoped run, never a full conformance claim.
No missing dependency, failed decode, omitted version, output truncation, or
changed candidate source is silently counted as a pass. Synthetic finite tests
are not ISO/GS1 certification, camera/print testing, or proof for arbitrary input
and damage.

## Test-tool provenance

`oracle.mjs`, `structured-append-cases.json`, `eci-control.mjs`, and the ZXing
decoder workflow are adapted from MIT-licensed SpecQR-CPP at
`e91cd8fe4434fd6ef10d1126bb2fd17b0b19321f`. `DecodeSymbols.java` and pinned decoder
requirements are from MIT-licensed SpecQR-CSharp at
`057c4b3f25e52c4786a8f94c744ff884eedcecfa`; the Java adapter retains its original
SpecQR-Swift attribution. JavaScript baseline:
`15ad15e5c770ea0e39072f8f88b2733018f02ffd`.

The npm lock records test-only package integrity. The downloaded ZXing Java JAR
and official PyPI ZXing-C++ wheels are hash-pinned. No third-party encoder or
decoder source, binary, dependency, or import is part of the Python runtime.
