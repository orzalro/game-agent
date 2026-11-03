import copy
import json
from pathlib import Path
import tempfile
import unittest
import shutil
from game_agent.scenario import validate

FIXTURE = Path(__file__).resolve().parents[1] / 'examples/menu_demo'

class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'fixture'
        shutil.copytree(FIXTURE, self.root)
        self.path = self.root / 'scenario.json'
        self.data = json.loads(self.path.read_text())

    def check(self):
        self.path.write_text(json.dumps(self.data))
        return validate(self.path)

    def test_fixture_and_success_path(self):
        self.assertEqual(self.check(), [])
        state = self.data['initial']
        for edge in self.data['transitions']:
            self.assertEqual(edge['from'], state)
            state = edge['to']
        self.assertEqual(state, self.data['success']['node'])

    def test_missing_evidence_rejected(self):
        self.data['transitions'][0]['evidence'] = ['nonexistent']
        self.assertTrue(any('unknown event' in e for e in self.check()))

    def test_unreachable_success_rejected(self):
        self.data['transitions'].pop()
        self.assertIn('Success node is unreachable', self.check())

    def test_out_of_bounds_hitbox_rejected(self):
        self.data['transitions'][0]['rect'] = [950, 0, 30, 20]
        self.assertIn('Invalid click rectangle', self.check())

    def test_inference_requires_explanation(self):
        self.data['transitions'][0]['certainty'] = 'inferred'
        self.assertIn('Inference requires uncertainty', self.check())

    def test_asset_escape_rejected(self):
        self.data['nodes'][0]['image'] = '../outside.svg'
        self.assertTrue(any('escapes' in e for e in self.check()))

if __name__ == '__main__': unittest.main()
