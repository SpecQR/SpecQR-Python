"""Bounded URL-prefix caret correction and exact independent native contracts."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from specqr import gs1

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('url_contract_verifier', ROOT/'tools/url_serialization/verify.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)

class URLSerializationTests(unittest.TestCase):
    def test_all_current_and_native_contracts(self):
        with tempfile.TemporaryDirectory(prefix='specqr-url-tests-') as directory:
            report = verifier.verify(Path(directory)/'report.json')
            self.assertEqual(report['cases'], 1499)
            self.assertEqual(report['positive80'], 80)
            self.assertEqual(report['restoredOutputIds'], [1460,1461,1465])

    def test_fail_closed_harness(self):
        self.assertEqual(len(verifier.negative_controls()), 16)

    def test_caret_and_query_preservation(self):
        elements = [{'ai':'01','value':'04912345678904'}]
        uri = 'https://example.com/a%5Eb/01/04912345678904'
        self.assertEqual(gs1.create_gs1_digital_link(elements,base_url='https://example.com/a^b'),uri)
        self.assertEqual(gs1.create_gs1_digital_link(elements,base_url='https://example.com/a%5Eb'),uri)
        self.assertEqual(gs1.create_gs1_digital_link(elements,base_url='https://user:p@ss@example.com/a^b'),'https://user:p%40ss@example.com/a%5Eb/01/04912345678904')
        self.assertEqual(gs1.normalize_gs1_digital_link('https://example.com/a^b/01/04912345678904'),uri)
        self.assertEqual(gs1.normalize_gs1_digital_link(uri),uri)
        self.assertEqual(gs1.normalize_gs1_digital_link('https://example.com/01/04912345678904?x=a^b'),'https://example.com/01/04912345678904?x=a%5Eb')
