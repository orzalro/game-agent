"""Exercise callback decoding with synthetic structs, without OS input injection."""
import ctypes as C
import sys
import threading
import unittest
from unittest.mock import patch
from contextlib import ExitStack

@unittest.skipUnless(sys.platform == 'win32', 'Windows callback ABI')
class HookTests(unittest.TestCase):
    def test_mouse_key_marker_stop_and_cleanup(self):
        from game_agent import win32 as w
        callbacks, events, removed = {}, [], []
        stop, ready = threading.Event(), threading.Event()
        def install(kind, callback, module, thread):
            callbacks[kind] = callback
            return kind
        def pump(*args):
            m = w.Mouse()
            m.pt.x, m.pt.y = 70, 80
            callbacks[14](0, 0x201, C.addressof(m))
            callbacks[14](0, 0x202, C.addressof(m))
            k = w.Keyboard()
            k.vk = 65
            callbacks[13](0, 0x100, C.addressof(k))
            callbacks[13](0, 0x101, C.addressof(k))
            k.vk = 120
            callbacks[13](0, 0x100, C.addressof(k))
            k.vk = 119
            callbacks[13](0, 0x100, C.addressof(k))
            return 0
        with ExitStack() as stack:
            for name, value in {'SetWindowsHookExW':install, 'GetMessageW':pump, 'PeekMessageW':lambda *a:0,
                                'CallNextHookEx':lambda *a:0, 'GetForegroundWindow':lambda:1,
                                'UnhookWindowsHookEx':lambda h:removed.append(h)}.items():
                stack.enter_context(patch.object(w.user,name,value))
            stack.enter_context(patch.object(w,'rect',lambda h:[20,30,100,100]))
            w.listen(1,lambda kind,**fields:events.append(dict(kind=kind,**fields)),stop,ready,{})
        self.assertEqual([e['kind'] for e in events],['mouse_button','mouse_button','key','key','marker'])
        self.assertEqual(events[0]['client'],[50,50])
        self.assertEqual(events[0]['normalized'],[.5,.5])
        self.assertTrue(stop.is_set())
        self.assertTrue(ready.is_set())
        self.assertEqual(removed,[13,14])

if __name__ == '__main__': unittest.main()
