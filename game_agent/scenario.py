"""Portable checks for scenario structure and evidence references."""
import json
from pathlib import Path

def validate(path):
    errors = []
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        base = Path(path).resolve().parent
        def local(name):
            target = (base / name).resolve()
            if not target.is_relative_to(base): raise ValueError('Asset escapes scenario directory')
            return target
        if data['schema_version'] != 1: errors.append('Unsupported schema version')
        if data['source'] not in ('synthetic', 'recorded'): errors.append('Invalid source')
        if not data['goal'].strip(): errors.append('Missing goal')
        width, height = data['viewport']
        if width <= 0 or height <= 0: errors.append('Invalid viewport')
        event_rows = [json.loads(line) for line in local(data['events']).read_text(encoding='utf-8').splitlines() if line.strip()]
        events = {e['id']: e for e in event_rows}
        if len(events) != len(event_rows): errors.append('Duplicate event IDs')
        if any(e['t_ns'] < 0 for e in event_rows): errors.append('Negative timestamp')
        def evidence(refs, owner):
            if not refs: errors.append(f'{owner}: missing evidence')
            for ref in refs:
                if ref not in events: errors.append(f'{owner}: unknown event {ref}')
        ids = [n['id'] for n in data['nodes']]
        if len(set(ids)) != len(ids): errors.append('Duplicate node IDs')
        nodes = {n['id']: n for n in data['nodes']}
        if data['initial'] not in nodes: errors.append('Unknown initial node')
        for node in nodes.values():
            if not local(node['image']).is_file(): errors.append(f"Missing image: {node['image']}")
            evidence(node['evidence'], node['id'])
        transition_ids = set()
        for edge in data['transitions']:
            if edge['id'] in transition_ids: errors.append('Duplicate transition ID')
            transition_ids.add(edge['id'])
            if edge['from'] not in nodes or edge['to'] not in nodes: errors.append('Unknown transition endpoint')
            x, y, w, h = edge['rect']
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > width or y+h > height: errors.append('Invalid click rectangle')
            if edge['certainty'] not in ('observed', 'inferred', 'user_confirmed', 'synthetic'): errors.append('Invalid certainty')
            if edge['certainty'] == 'inferred' and not edge.get('uncertainty'): errors.append('Inference requires uncertainty')
            evidence(edge['evidence'], edge['id'])
        reachable = {data['initial']}
        while True:
            expanded = reachable | {e['to'] for e in data['transitions'] if e['from'] in reachable}
            if expanded == reachable: break
            reachable = expanded
        if set(nodes)-reachable: errors.append('Unreachable nodes: '+str(sorted(set(nodes)-reachable)))
        success = data['success']
        if not success['description'].strip(): errors.append('Missing success description')
        if success['node'] not in reachable: errors.append('Success node is unreachable')
        evidence(success['evidence'], 'success')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f'Malformed scenario: {exc}')
    return errors
