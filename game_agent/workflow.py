"""워크플로우의 JSON 계약. 복잡한 프레임워크 대신 평범한 dict를 저장한다."""
import json
import math
from pathlib import Path


def asset(root, name):
    """그래프/녹화가 가리키는 파일은 해당 묶음 안에서만 읽는다."""
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Asset escapes bundle directory')
    return path


def save_json(path, data):
    # 중간에 종료되어도 기존 체크포인트가 반쪽 JSON으로 바뀌지 않게 교체한다.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def validate_graph(graph):
    if graph['schema_version'] != 1:
        raise ValueError('Unsupported workflow schema')
    nodes = graph['nodes']
    ids = [n['id'] for n in nodes]
    if not ids or any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)) or graph['initial'] not in ids:
        raise ValueError('Invalid node IDs or initial node')
    if not set(graph['ends']).issubset(ids):
        raise ValueError('Unknown end node')
    width, height = graph['viewport']
    if width <= 0 or height <= 0:
        raise ValueError('Invalid viewport')
    edge_ids = set()
    for node in nodes:
        if node.get('status', 'confirmed') not in ('candidate', 'confirmed'):
            raise ValueError('Invalid node status')
        if not node['references']:
            raise ValueError('Node has no references')
        for ref in node['references']:
            if ref['status'] not in ('candidate', 'confirmed', 'retired'):
                raise ValueError('Invalid reference status')
    for edge in graph['edges']:
        if not isinstance(edge['id'], str) or not edge['id'] or edge['id'] in edge_ids or edge['source'] not in ids or edge['target'] not in ids:
            raise ValueError('Invalid edge ID or endpoint')
        edge_ids.add(edge['id'])
        action = edge['action']
        if action['kind'] != 'click':
            raise ValueError('Only demonstrated clicks are supported')
        x, y = action['point']
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < width and 0 <= y < height):
            raise ValueError('Click outside viewport')
        if action.get('button') not in ('left', 'right', 'middle'):
            raise ValueError('Invalid mouse button')
        if not edge.get('template'):
            raise ValueError('Click requires a target template')
        offset = edge['template_offset']
        if len(offset) != 2 or any(not isinstance(v, int) or v < 0 for v in offset):
            raise ValueError('Invalid template offset')
        for rule in edge.get('when', {}).values():
            if isinstance(rule, (dict, list)):
                raise ValueError('Branch conditions must be scalar equality values')
    return graph


def load_graph(path):
    path = Path(path)
    graph = validate_graph(json.loads(path.read_text(encoding='utf-8')))
    for node in graph['nodes']:
        for ref in node['references']:
            if not asset(path.parent, ref['image']).is_file():
                raise ValueError('Missing reference image')
    for edge in graph['edges']:
        if not asset(path.parent, edge['template']).is_file():
            raise ValueError('Missing target template')
    return graph


def select_edge(graph, node_id, variables):
    """동일 우선순위 분기를 임의 선택하지 않는다. 조건은 eval 없는 값 비교다."""
    edges = [e for e in graph['edges'] if e['source'] == node_id
             and all(k in variables and variables[k] == v for k, v in e.get('when', {}).items())]
    conditional = [e for e in edges if e.get('when')]
    edges = conditional or edges
    return edges[0] if len(edges) == 1 else None
