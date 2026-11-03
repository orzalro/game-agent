import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from game_agent.recorder import record

class RecorderTests(unittest.TestCase):
    def fake(self, fail=False):
        def listen(hwnd, emit, stop, ready, state):
            emit('key', vk=65, action='down')
            ready.set()
            stop.wait()
        def capture(hwnd, path):
            if fail: raise RuntimeError('capture failed')
            path.write_bytes(b'fixture')
            return [10,20,100,100]
        return types.SimpleNamespace(listen=listen, capture=capture,
            user=types.SimpleNamespace(IsWindow=lambda h: True, IsIconic=lambda h: False,
                                      GetForegroundWindow=lambda: 1, PostThreadMessageW=lambda *a: True))

    def test_timeline_manifest_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('sys.modules', {'game_agent.win32': self.fake()}):
            import game_agent
            with patch.object(game_agent, 'win32', self.fake(), create=True):
                path = Path(temp)/'session'
                result = record(1, path, 'goal', 'done', duration=0.05)
                self.assertEqual(result['status'], 'completed')
                events = [json.loads(x) for x in (path/'events.jsonl').read_text().splitlines()]
                self.assertTrue(any(e['kind']=='frame' for e in events))
                self.assertTrue(any(e['kind']=='key' for e in events))
                self.assertEqual(len({e['id'] for e in events}), len(events))
                self.assertTrue(all(e['t_ns']>=0 for e in events))
                with self.assertRaises(FileExistsError): record(1, path, 'goal', 'done', duration=.01)

    def test_capture_failure_finalizes_session(self):
        import game_agent
        fake = self.fake(fail=True)
        with tempfile.TemporaryDirectory() as temp, patch.dict('sys.modules', {'game_agent.win32': fake}), patch.object(game_agent, 'win32', fake, create=True):
            path = Path(temp)/'session'
            with self.assertRaisesRegex(RuntimeError, 'capture failed'):
                record(1, path, 'goal', 'done', duration=.05)
            metadata = json.loads((path/'session.json').read_text())
            self.assertEqual(metadata['status'], 'failed')
            self.assertEqual(metadata['frame_count'], 0)

if __name__ == '__main__': unittest.main()
