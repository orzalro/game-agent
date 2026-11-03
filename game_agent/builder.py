"""기존 녹화기의 타임라인을 근거가 연결된 그래프로 변환한다."""
import hashlib
import json
from pathlib import Path

from .vision import read_image, static_anchors
from .workflow import asset, save_json, validate_graph


def demonstration_steps(directory):
    """전후 프레임이 있는 완전한 클릭만 사용한다. 빠진 입력은 추측하지 않는다."""
    directory = Path(directory)
    session = json.loads((directory / 'session.json').read_text(encoding='utf-8'))
    if session['status'] != 'completed':
        raise ValueError('Recording is incomplete or failed')
    events = [json.loads(line) for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    events.sort(key=lambda e: (e['t_ns'], e['id']))
    if len({e['id'] for e in events}) != len(events):
        raise ValueError('Duplicate event IDs')
    frames = [e for e in events if e['kind'] == 'frame']
    inputs = [e for e in events if e['kind'] in ('mouse_button', 'key', 'wheel')]
    steps = []
    for index, down in enumerate(inputs):
        if down['kind'] != 'mouse_button' or down.get('action') != 'down':
            if down['kind'] in ('key', 'wheel'):
                raise ValueError('Keyboard and wheel demonstrations are not supported yet')
            continue
        if index + 1 >= len(inputs):
            raise ValueError('Unreleased click')
        up = inputs[index + 1]
        if up['kind'] != 'mouse_button' or up.get('action') != 'up' or up.get('button') != down['button']:
            raise ValueError('Overlapping inputs cannot be converted to a click')
        if not down.get('foreground', True) or not up.get('foreground', True):
            raise ValueError('Click crossed a focus boundary')
        if down['client'] != up['client']:
            raise ValueError('Dragging is not supported')
        # 캡처 도중 액션이 발생한 프레임은 이전/이후 증거 어느 쪽에도 쓰지 않는다.
        before = [f for f in frames if f['t_ns'] < down['t_ns']]
        next_input = inputs[index + 2]['t_ns'] if index + 2 < len(inputs) else float('inf')
        after = [f for f in frames if f.get('capture_start_ns', f['t_ns']) > up['t_ns'] and f['t_ns'] < next_input]
        if not before or not after:
            raise ValueError('Click has no unambiguous before/after frames; record more slowly')
        pre, post = before[-1], after[-1]
        if any(e['kind'] in ('error', 'input_error') or (e['kind'] == 'focus' and not e['active'])
               for e in events if pre['t_ns'] < e['t_ns'] < post['t_ns']):
            raise ValueError('Interrupted demonstration step')
        steps.append({'before': asset(directory, pre['path']), 'after': asset(directory, post['path']),
                      'action': {'kind': 'click', 'button': down['button'], 'point': down['client']},
                      'evidence': {'session': str(directory.resolve()),
                                   'events': [pre['id'], down['id'], up['id'], post['id']]}})
    if not steps:
        raise ValueError('Recording contains no supported steps')
    return session, steps


def build_graph(directories, output, success_last=False):
    """여러 시연을 합친다. 첫 버전은 동일 픽셀만 병합하여 잘못된 병합을 줄인다.

    마지막 화면이 성공이라는 업무 의미는 녹화 종료만으로 알 수 없다.
    따라서 success_last를 명시한 경우에만 마지막 노드를 종료 노드로 지정한다.
    """
    demonstrations = [demonstration_steps(d) for d in directories]
    if not demonstrations:
        raise ValueError('At least one demonstration is required')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'references').mkdir()
    (output / 'templates').mkdir()
    graph = {'schema_version': 1, 'goal': demonstrations[0][0].get('goal', ''),
             'viewport': None, 'initial': None, 'ends': [], 'nodes': [], 'edges': [], 'proposals': []}
    images = {}

    def node_for(path):
        image = read_image(path)
        if graph['viewport'] is None:
            graph['viewport'] = list(image.size)
        if list(image.size) != graph['viewport']:
            raise ValueError('Demonstrations must use the same viewport')
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        node_id = 'n_' + digest[:16]
        if node_id not in images:
            name = f'references/{node_id}.png'
            image.save(output / name)
            images[node_id] = image
            graph['nodes'].append({'id': node_id, 'status': 'confirmed', 'optional': False,
                                   'references': [{'image': name, 'status': 'confirmed', 'visits': 1, 'last_seen': 0}],
                                   'anchors': [], 'evidence': []})
        return node_id

    for session, steps in demonstrations:
        previous = None
        for step in steps:
            source, target = node_for(step['before']), node_for(step['after'])
            if previous is not None and source != previous:
                raise ValueError('Unrecorded screen change between actions; cannot infer a connecting edge')
            if previous is None:
                if graph['initial'] is not None and graph['initial'] != source:
                    raise ValueError('Demonstrations must start at the same screen')
                graph['initial'] = source
            previous = target
            for node in graph['nodes']:
                if node['id'] in (source, target):
                    node['evidence'].append(step['evidence'])
            duplicate = next((e for e in graph['edges'] if e['source'] == source and e['target'] == target
                              and e['action'] == step['action']), None)
            if duplicate:
                duplicate['evidence'].append(step['evidence'])
                continue
            edge_id = f'e{len(graph["edges"]):04d}'
            x, y = step['action']['point']
            width, height = images[source].size
            if not (0 <= x < width and 0 <= y < height):
                raise ValueError('Recorded click outside client area')
            box = [max(0, x - 16), max(0, y - 16), min(width, x + 17), min(height, y + 17)]
            template = f'templates/{edge_id}.png'
            images[source].crop(box).save(output / template)
            graph['edges'].append({'id': edge_id, 'source': source, 'target': target,
                                   'action': step['action'], 'template': template,
                                   'template_offset': [x - box[0], y - box[1]], 'when': {},
                                   'evidence': [step['evidence']]})
        if success_last and previous not in graph['ends']:
            graph['ends'].append(previous)
    for node in graph['nodes']:
        node['anchors'] = static_anchors([images[node['id']]],
                                        [im for key, im in images.items() if key != node['id']])
    save_json(output / 'graph.json', validate_graph(graph))
    return graph
