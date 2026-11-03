"""Recording session: timestamped PNG frames + JSONL event timeline."""
import json
from pathlib import Path
import queue
import threading
import time
from datetime import datetime, timezone

def record(hwnd, output, goal, completion, fps=2, duration=300, stop=None, status=None):
    from . import win32
    if not 0.2 <= fps <= 10 or duration <= 0:
        raise ValueError('FPS must be 0.2..10 and duration must be positive')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'frames').mkdir()
    stop = stop or threading.Event()
    status = status or (lambda message: None)
    start = time.perf_counter_ns()
    events = queue.SimpleQueue()
    state, ready = {}, threading.Event()
    def emit(kind, **fields):
        events.put(dict(t_ns=time.perf_counter_ns()-start, kind=kind, **fields))
    metadata = dict(schema_version=1, source='recorded', started_utc=datetime.now(timezone.utc).isoformat(),
                    clock='perf_counter_ns relative to session start', target_hwnd=hwnd,
                    goal=goal, completion_condition=completion, fps=fps, max_duration_s=duration,
                    capture='visible client area; occlusions appear in frames', status='recording')
    manifest = output / 'session.json'
    manifest.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')
    hooks = threading.Thread(target=win32.listen, args=(hwnd, emit, stop, ready, state), daemon=True)
    count, event_count, error = 0, 0, None
    hooks.start()
    with (output / 'events.jsonl').open('w', encoding='utf-8') as log:
        def flush():
            nonlocal event_count
            while not events.empty():
                log.write(json.dumps(dict(id=f'e{event_count:06d}', **events.get()), ensure_ascii=False)+'\n')
                event_count += 1
            log.flush()
        try:
            if not ready.wait(5): raise RuntimeError('Input hook startup timed out')
            if state.get('error'): raise RuntimeError(state['error'])
            next_frame = time.monotonic()
            was_focused = None
            while not stop.is_set() and (time.perf_counter_ns()-start)/1e9 < duration:
                if not win32.user.IsWindow(hwnd): raise RuntimeError('Target window closed')
                focused = win32.user.GetForegroundWindow() == hwnd and not win32.user.IsIconic(hwnd)
                if focused != was_focused:
                    emit('focus', active=focused)
                    status('기록 중' if focused else '일시 대기: 대상 창을 선택하세요')
                    was_focused = focused
                if focused and time.monotonic() >= next_frame:
                    before = time.perf_counter_ns()-start
                    name = f'frames/{count:06d}.png'
                    bounds = win32.capture(hwnd, output / name)
                    emit('frame', path=name, capture_start_ns=before, rect=bounds)
                    count += 1
                    next_frame = time.monotonic() + 1/fps
                flush()
                stop.wait(0.01)
        except Exception as exc:
            error = str(exc)
            emit('error', message=error)
        finally:
            stop.set()
            if state.get('thread_id'):
                win32.user.PostThreadMessageW(state['thread_id'], 0x12, 0, 0)
            hooks.join(2)
            if hooks.is_alive(): error = error or 'Input hook did not stop'
            emit('stop', reason='error' if error else 'user_or_duration_limit')
            flush()
            metadata.update(status='failed' if error else 'completed', error=error, frame_count=count,
                            event_count=event_count, duration_ns=time.perf_counter_ns()-start)
            manifest.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')
    if error: raise RuntimeError(error)
    return metadata
