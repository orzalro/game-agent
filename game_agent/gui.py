import queue
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
from . import win32
from .recorder import record

def run():
    win32.dpi_aware()
    root = tk.Tk()
    root.title('Game Agent — 화면·입력 기록기')
    root.geometry('720x490')
    box = ttk.Frame(root, padding=18)
    box.pack(fill='both', expand=True)
    ttk.Label(box, text='대상 창을 선택하세요. 기록은 대상 창이 활성화되어 있을 때 진행됩니다.').pack(anchor='w')
    targets = ttk.Combobox(box, state='readonly', width=90)
    targets.pack(fill='x', pady=8)
    options = []
    def refresh():
        options[:] = win32.windows()
        targets['values'] = [f'{title} [{hwnd}]' for hwnd, title in options]
        if options: targets.current(0)
    ttk.Button(box, text='창 목록 새로고침', command=refresh).pack(anchor='w')
    fields = {}
    for key, label, default in [('goal', '작업 목표', ''), ('completion', '완료 판정 기준', ''),
                                ('fps', '초당 화면 수 (0.2~10)', '2'), ('duration', '최대 기록 시간 (초)', '300')]:
        ttk.Label(box, text=label).pack(anchor='w', pady=(8,0))
        value = tk.StringVar(value=default)
        ttk.Entry(box, textvariable=value).pack(fill='x')
        fields[key] = value
    ttk.Label(box, text='F8: 기록 중단 · F9: 주요 단계 표시 (대상 창에서)\n화면은 PNG로 저장됩니다. 창을 가리지 마세요. 게임에도 F8/F9 입력이 전달됩니다.').pack(anchor='w', pady=10)
    label = tk.StringVar(value='준비')
    ttk.Label(box, textvariable=label, wraplength=680).pack(anchor='w')
    messages, stop = queue.Queue(), threading.Event()
    worker = None
    def start():
        nonlocal worker, stop
        if worker and worker.is_alive(): return
        try:
            if targets.current() < 0: raise ValueError('대상 창을 선택하세요')
            goal, completion = fields['goal'].get().strip(), fields['completion'].get().strip()
            if not goal or not completion: raise ValueError('목표와 완료 기준을 입력하세요')
            fps, duration = float(fields['fps'].get()), float(fields['duration'].get())
            if not 0.2 <= fps <= 10 or not 0 < duration <= 86400: raise ValueError('화면 수 또는 시간을 확인하세요')
        except ValueError as exc:
            messagebox.showerror('입력 확인', str(exc)); return
        hwnd = options[targets.current()][0]
        output = 'recordings/' + datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        stop = threading.Event()
        def work():
            try:
                result = record(hwnd, output, goal, completion, fps, duration, stop, messages.put)
                messages.put(f"저장 완료: {output} · {result['frame_count']}개 화면")
            except Exception as exc: messages.put(f'기록 실패: {exc} · {output}')
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        label.set('대상 게임 창으로 전환하세요. F8로 중단할 수 있습니다.')
    buttons = ttk.Frame(box)
    buttons.pack(fill='x', pady=10)
    ttk.Button(buttons, text='기록 시작', command=start).pack(side='left')
    ttk.Button(buttons, text='중단 및 저장', command=stop.set).pack(side='left', padx=8)
    # Bind dynamically because each run has a fresh stop event.
    buttons.winfo_children()[1].configure(command=lambda: stop.set())
    def poll():
        while not messages.empty(): label.set(messages.get())
        root.after(100, poll)
    def close():
        stop.set()
        if worker and worker.is_alive():
            label.set('기록을 저장하고 있습니다…')
            root.after(100, close)
        else: root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    refresh(); poll(); root.mainloop()
