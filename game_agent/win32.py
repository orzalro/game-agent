"""Windows-only capture and input hooks. No third-party dependencies."""
import ctypes as C
from ctypes import wintypes as W
import struct
import zlib

user = C.WinDLL('user32', use_last_error=True)
gdi = C.WinDLL('gdi32', use_last_error=True)
kernel = C.WinDLL('kernel32', use_last_error=True)
LRESULT = C.c_ssize_t
HOOKPROC = C.WINFUNCTYPE(LRESULT, C.c_int, W.WPARAM, W.LPARAM)
ENUMPROC = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

def signature(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn

signature(user, 'GetForegroundWindow', W.HWND)
signature(user, 'GetDC', W.HDC, W.HWND)
signature(user, 'ReleaseDC', C.c_int, W.HWND, W.HDC)
signature(user, 'GetClientRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
signature(user, 'ClientToScreen', W.BOOL, W.HWND, C.POINTER(W.POINT))
signature(user, 'IsWindow', W.BOOL, W.HWND)
signature(user, 'IsIconic', W.BOOL, W.HWND)
signature(user, 'IsWindowVisible', W.BOOL, W.HWND)
signature(user, 'EnumWindows', W.BOOL, ENUMPROC, W.LPARAM)
signature(user, 'GetWindowTextW', C.c_int, W.HWND, W.LPWSTR, C.c_int)
signature(user, 'SetWindowsHookExW', W.HANDLE, C.c_int, HOOKPROC, W.HINSTANCE, W.DWORD)
signature(user, 'CallNextHookEx', LRESULT, W.HANDLE, C.c_int, W.WPARAM, W.LPARAM)
signature(user, 'UnhookWindowsHookEx', W.BOOL, W.HANDLE)
signature(user, 'PostThreadMessageW', W.BOOL, W.DWORD, W.UINT, W.WPARAM, W.LPARAM)
signature(user, 'GetMessageW', C.c_int, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
signature(user, 'TranslateMessage', W.BOOL, C.POINTER(W.MSG))
signature(user, 'DispatchMessageW', LRESULT, C.POINTER(W.MSG))
signature(kernel, 'GetCurrentThreadId', W.DWORD)
signature(kernel, 'GetModuleHandleW', W.HMODULE, W.LPCWSTR)
signature(gdi, 'CreateCompatibleDC', W.HDC, W.HDC)
signature(gdi, 'CreateCompatibleBitmap', W.HBITMAP, W.HDC, C.c_int, C.c_int)
signature(gdi, 'SelectObject', W.HANDLE, W.HDC, W.HANDLE)
signature(gdi, 'DeleteObject', W.BOOL, W.HANDLE)
signature(gdi, 'DeleteDC', W.BOOL, W.HDC)
signature(gdi, 'BitBlt', W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD)
signature(gdi, 'GetDIBits', C.c_int, W.HDC, W.HBITMAP, W.UINT, W.UINT, W.LPVOID, W.LPVOID, W.UINT)

def dpi_aware():
    try:
        user.SetProcessDpiAwarenessContext(C.c_void_p(-4))
    except AttributeError:
        user.SetProcessDPIAware()

def windows():
    result = []
    @ENUMPROC
    def visit(hwnd, _):
        title = C.create_unicode_buffer(1024)
        user.GetWindowTextW(hwnd, title, len(title))
        if title.value and user.IsWindowVisible(hwnd):
            result.append((int(hwnd), title.value))
        return True
    user.EnumWindows(visit, 0)
    return result

def rect(hwnd):
    r, p = W.RECT(), W.POINT()
    if not user.IsWindow(hwnd) or user.IsIconic(hwnd):
        raise RuntimeError('Target window closed or minimized')
    if not user.GetClientRect(hwnd, C.byref(r)) or not user.ClientToScreen(hwnd, C.byref(p)):
        raise C.WinError(C.get_last_error())
    if r.right <= 0 or r.bottom <= 0:
        raise RuntimeError('Empty client area')
    return [p.x, p.y, r.right, r.bottom]

def capture(hwnd, path):
    x, y, width, height = rect(hwnd)
    screen = user.GetDC(None)
    dc = gdi.CreateCompatibleDC(screen)
    bitmap = gdi.CreateCompatibleBitmap(screen, width, height)
    old = gdi.SelectObject(dc, bitmap) if bitmap else None
    try:
        if not all((screen, dc, bitmap, old)):
            raise RuntimeError('Could not allocate capture resources')
        if not gdi.BitBlt(dc, 0, 0, width, height, screen, x, y, 0x40CC0020):
            raise C.WinError(C.get_last_error())
        gdi.SelectObject(dc, old)
        size = width * height * 4
        header = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 32, 0, size, 0, 0, 0, 0)
        info = C.create_string_buffer(header)
        pixels = C.create_string_buffer(size)
        if gdi.GetDIBits(dc, bitmap, 0, height, pixels, info, 0) != height:
            raise RuntimeError('Incomplete capture')
        # Convert bottom-up BGRA scanlines to RGB PNG using the standard library.
        raw, rows = pixels.raw, []
        for row in range(height-1, -1, -1):
            bgra = raw[row*width*4:(row+1)*width*4]
            rgb = bytearray(width*3)
            rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
            rows.append(b'\x00'+rgb)
        def chunk(kind, content):
            return struct.pack('>I', len(content))+kind+content+struct.pack('>I', zlib.crc32(kind+content))
        path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', width,height,8,2,0,0,0))+
                         chunk(b'IDAT', zlib.compress(b''.join(rows), 3))+chunk(b'IEND', b''))
    finally:
        if old: gdi.SelectObject(dc, old)
        if bitmap: gdi.DeleteObject(bitmap)
        if dc: gdi.DeleteDC(dc)
        if screen: user.ReleaseDC(None, screen)
    return [x, y, width, height]

class Mouse(C.Structure):
    _fields_ = [('pt', W.POINT), ('data', W.DWORD), ('flags', W.DWORD), ('time', W.DWORD), ('extra', C.c_size_t)]

class Keyboard(C.Structure):
    _fields_ = [('vk', W.DWORD), ('scan', W.DWORD), ('flags', W.DWORD), ('time', W.DWORD), ('extra', C.c_size_t)]

def listen(hwnd, emit, stop, ready, state):
    """Pump low-level hooks; enqueue only, never write files inside callbacks."""
    held_keys, held_buttons = set(), set()
    messages = {0x201: ('left', 'down'), 0x202: ('left', 'up'), 0x204: ('right', 'down'),
                0x205: ('right', 'up'), 0x207: ('middle', 'down'), 0x208: ('middle', 'up')}
    def focused():
        return user.GetForegroundWindow() == hwnd
    @HOOKPROC
    def keyboard(code, msg, ptr):
        if code >= 0:
            k = C.cast(ptr, C.POINTER(Keyboard)).contents
            down = msg in (0x100, 0x104)
            if k.vk == 119 and down:  # F8: stop, regardless of focus
                stop.set()
            elif k.vk == 120 and down and focused():
                emit('marker', label='User checkpoint')
            elif focused() or (not down and k.vk in held_keys):
                if down: held_keys.add(k.vk)
                else: held_keys.discard(k.vk)
                emit('key', vk=k.vk, scan=k.scan, action='down' if down else 'up', foreground=focused())
        return user.CallNextHookEx(None, code, msg, ptr)
    @HOOKPROC
    def mouse(code, msg, ptr):
        if code >= 0:
            m = C.cast(ptr, C.POINTER(Mouse)).contents
            pair = messages.get(msg)
            releasing = pair and pair[1] == 'up' and pair[0] in held_buttons
            if focused() or releasing:
                try:
                    x, y, w, h = rect(hwnd)
                    fields = dict(screen=[m.pt.x, m.pt.y], client=[m.pt.x-x, m.pt.y-y],
                                  normalized=[(m.pt.x-x)/w, (m.pt.y-y)/h], rect=[x,y,w,h], foreground=focused())
                    if pair:
                        button, action = pair
                        if action == 'down': held_buttons.add(button)
                        else: held_buttons.discard(button)
                        emit('mouse_button', button=button, action=action, **fields)
                    elif msg in (0x20A, 0x20E):
                        emit('wheel', delta=C.c_short(m.data >> 16).value, axis='vertical' if msg == 0x20A else 'horizontal', **fields)
                    elif msg == 0x200:
                        emit('mouse_move', **fields)
                except Exception as exc:
                    emit('input_error', message=str(exc))
        return user.CallNextHookEx(None, code, msg, ptr)
    hooks = []
    try:
        state['thread_id'] = kernel.GetCurrentThreadId()
        module = kernel.GetModuleHandleW(None)
        for kind, callback in ((13, keyboard), (14, mouse)):
            hook = user.SetWindowsHookExW(kind, callback, module, 0)
            if not hook: raise C.WinError(C.get_last_error())
            hooks.append(hook)
        # Ensure the message queue exists before advertising readiness.
        message = W.MSG()
        user.PeekMessageW(C.byref(message), None, 0, 0, 0)
        ready.set()
        while True:
            result = user.GetMessageW(C.byref(message), None, 0, 0)
            if result == -1: raise C.WinError(C.get_last_error())
            if result == 0: break
            user.TranslateMessage(C.byref(message))
            user.DispatchMessageW(C.byref(message))
    except Exception as exc:
        state['error'] = str(exc)
        emit('input_error', message=str(exc))
        stop.set()
        ready.set()
    finally:
        for hook in hooks: user.UnhookWindowsHookEx(hook)
