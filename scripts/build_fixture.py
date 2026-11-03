"""Rebuild the explicitly synthetic recording/scenario pair, deterministically."""
import json
from pathlib import Path

def build():
    out = Path(__file__).resolve().parents[1] / 'examples' / 'menu_demo'
    (out / 'frames').mkdir(parents=True, exist_ok=True)
    scenes = [('menu', 'MENU LAB', 'A small expedition awaits.', 'Open missions'),
              ('missions', 'DAILY MISSION', 'Visit the old observatory.', 'Complete mission'),
              ('reward', 'MISSION COMPLETE', 'Reward available: 100 coins', 'Claim reward'),
              ('done', 'REWARD RECEIVED', 'Balance: 100 coins', 'Finished')]
    events, nodes, edges = [], [], []
    for i, (name, title, subtitle, button) in enumerate(scenes):
        image = f'frames/{name}.svg'
        (out / image).write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">
<rect width="960" height="540" fill="#101b2d"/><rect x="60" y="52" width="840" height="436" rx="24" fill="#1d3048" stroke="#4a6483"/>
<text x="100" y="100" fill="#9db4d0" font-family="sans-serif" font-size="18">SYNTHETIC MENU FIXTURE · {i+1}/4</text>
<text x="100" y="205" fill="#f4f7fb" font-family="sans-serif" font-size="38" font-weight="bold">{title}</text>
<text x="100" y="260" fill="#bacadd" font-family="sans-serif" font-size="22">{subtitle}</text>
<rect x="300" y="360" width="360" height="70" rx="12" fill="{'#407d66' if i==3 else '#e3b66c'}"/>
<text x="480" y="404" text-anchor="middle" fill="#101b2d" font-family="sans-serif" font-size="23" font-weight="bold">{button}</text></svg>''', encoding='utf-8')
        frame_id = f'e{i*2:06d}'
        events.append(dict(id=frame_id, t_ns=i*2_000_000_000, kind='frame', path=image, capture_start_ns=i*2_000_000_000, rect=[0,0,960,540]))
        nodes.append(dict(id=name, image=image, evidence=[frame_id]))
        if i < 3:
            click_id = f'e{i*2+1:06d}'
            events.append(dict(id=click_id, t_ns=i*2_000_000_000+1_000_000_000, kind='mouse_button', button='left', action='up', client=[480,395], normalized=[0.5,395/540], rect=[0,0,960,540]))
            edges.append(dict(id=f't{i+1:03d}', **{'from':name, 'to':scenes[i+1][0]}, rect=[300,360,360,70], certainty='synthetic', evidence=[frame_id,click_id,f'e{(i+1)*2:06d}']))
    (out / 'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events), encoding='utf-8')
    manifest = dict(schema_version=1, source='synthetic', status='completed', goal='Claim the mission reward once',
                    completion_condition='Reward received screen and balance 100', fps=0.5,
                    frame_count=4, event_count=len(events), clock='Synthetic relative nanoseconds')
    (out / 'session.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    scenario = dict(schema_version=1, source='synthetic', goal=manifest['goal'], viewport=[960,540],
                    events='events.jsonl', initial='menu', nodes=nodes, transitions=edges,
                    success=dict(node='done', description=manifest['completion_condition'], evidence=['e000006']),
                    assumptions=['Author-specified synthetic hitboxes; not inferred from a recording.'])
    (out / 'scenario.json').write_text(json.dumps(scenario, indent=2), encoding='utf-8')

if __name__ == '__main__': build()
