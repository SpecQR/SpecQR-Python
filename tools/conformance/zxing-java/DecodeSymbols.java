/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 SpecQR contributors
 * Test adapter adapted from SpecQR-Swift at commit
 * 0ef9613fe8f1ecd687da76ce797b4ef896afa477:
 * https://github.com/SpecQR/SpecQR-Swift/blob/0ef9613fe8f1ecd687da76ce797b4ef896afa477/Scripts/zxing-java/DecodeSymbols.java
 * Licensed under the repository's MIT LICENSE. This adapter imports a separately
 * downloaded, development-only ZXing JAR; it contains no ZXing runtime source.
 */
/* Test-only ZXing adapter. SpecQR's encoder is never used to decode its output. */
import com.google.zxing.*;
import com.google.zxing.common.*;
import com.google.zxing.qrcode.QRCodeReader;
import com.google.zxing.qrcode.decoder.Decoder;
import java.awt.image.BufferedImage;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import javax.imageio.ImageIO;

class DecodeSymbols {
  static String b64(byte[] bytes) { return Base64.getEncoder().encodeToString(bytes); }
  static String quoted(String safeASCII) { return "\"" + safeASCII + "\""; }
  static String byteSegments(List<byte[]> segments) {
    if (segments == null) return "[]";
    return "[" + String.join(",", segments.stream().map(bytes -> quoted(b64(bytes))).toList()) + "]";
  }
  @SuppressWarnings("unchecked")
  public static void main(String[] args) throws Exception {
    Map<DecodeHintType, Object> hints = new EnumMap<>(DecodeHintType.class);
    hints.put(DecodeHintType.TRY_HARDER, true);
    // Deliberately no PURE_BARCODE hint: rendered PNGs exercise detection, too.
    int ordinal = 0;
    for (String line : Files.readAllLines(Path.of(args[0]), StandardCharsets.UTF_8)) {
      String[] input = line.split("\t", 2);
      try {
        String text, ecc, identifier; byte[] raw; List<byte[]> segments;
        Object sequence, parity, corrected;
        if (input[0].equals("png")) {
          BufferedImage image = ImageIO.read(Path.of(input[1]).toFile());
          int width = image.getWidth(), height = image.getHeight();
          int[] rgb = image.getRGB(0, 0, width, height, null, 0, width);
          BinaryBitmap bitmap = new BinaryBitmap(new HybridBinarizer(new RGBLuminanceSource(width, height, rgb)));
          Result decoded = new QRCodeReader().decode(bitmap, hints);
          Map<ResultMetadataType, Object> metadata = decoded.getResultMetadata();
          text = decoded.getText(); raw = decoded.getRawBytes();
          segments = (List<byte[]>) metadata.get(ResultMetadataType.BYTE_SEGMENTS);
          sequence = metadata.get(ResultMetadataType.STRUCTURED_APPEND_SEQUENCE);
          parity = metadata.get(ResultMetadataType.STRUCTURED_APPEND_PARITY);
          corrected = metadata.get(ResultMetadataType.ERRORS_CORRECTED);
          ecc = String.valueOf(metadata.get(ResultMetadataType.ERROR_CORRECTION_LEVEL));
          identifier = String.valueOf(metadata.get(ResultMetadataType.SYMBOLOGY_IDENTIFIER));
        } else {
          String[] rows = input[1].split(","); BitMatrix matrix = new BitMatrix(rows.length);
          for (int y = 0; y < rows.length; y++)
            for (int x = 0; x < rows.length; x++) if (rows[y].charAt(x) == '1') matrix.set(x, y);
          DecoderResult decoded = new Decoder().decode(matrix, hints);
          text = decoded.getText(); raw = decoded.getRawBytes(); segments = decoded.getByteSegments();
          sequence = decoded.hasStructuredAppend() ? decoded.getStructuredAppendSequenceNumber() : null;
          parity = decoded.hasStructuredAppend() ? decoded.getStructuredAppendParity() : null;
          corrected = decoded.getErrorsCorrected(); ecc = decoded.getECLevel();
          identifier = "]Q" + decoded.getSymbologyModifier();
        }
        System.out.println("{\"ordinal\":" + ordinal + ",\"textBase64\":" + quoted(b64(text.getBytes(StandardCharsets.UTF_8)))
          + ",\"rawBytesBase64\":" + quoted(b64(raw)) + ",\"byteSegments\":" + byteSegments(segments)
          + ",\"sequence\":" + sequence + ",\"parity\":" + parity + ",\"errorsCorrected\":" + corrected
          + ",\"ecc\":" + quoted(ecc) + ",\"symbologyIdentifier\":" + quoted(identifier) + "}");
      } catch (ReaderException error) {
        System.out.println("{\"ordinal\":" + ordinal + ",\"error\":" + quoted(error.getClass().getSimpleName()) + "}");
      }
      ordinal++;
    }
  }
}
