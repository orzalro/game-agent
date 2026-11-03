"""관찰은 후보로 격리한다. 그래프 경로 변경은 별도 검토 전에는 적용하지 않는다."""
import hashlib
from pathlib import Path


def observe_candidate(graph, root, image, visit_id, node_id=None, previous=None, action_id=None):
    """node_id=None은 새 화면, 지정되면 기존 화면의 변경 후보다.

    node_id는 VLM/사람 등이 제안한 값이지 이 함수가 확정한 라벨이 아니다.
    동일 실행의 반복 폴링은 visit_id로 중복 제거한다.
    """
    if node_id is not None and node_id not in {n['id'] for n in graph['nodes']}:
        raise ValueError('Unknown proposed node')
    digest = hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()
    proposals = graph.setdefault('proposals', [])
    candidate = next((p for p in proposals if p['digest'] == digest and p['node_id'] == node_id
                      and p['previous'] == previous and p['action_id'] == action_id
                      and p['status'] == 'candidate'), None)
    if candidate is None:
        name = f'candidates/{digest}.png'
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        candidate = {'id': f'p{len(proposals):04d}', 'digest': digest, 'image': name,
                     'node_id': node_id, 'previous': previous, 'action_id': action_id,
                     'optional': node_id is None, 'suggested_edges': [],
                     'status': 'candidate', 'visits': [], 'verified_visits': [], 'misses': 0}
        proposals.append(candidate)
    if visit_id not in candidate['visits']:
        candidate['visits'].append(visit_id)
    candidate['misses'] = 0
    return candidate


def confirm_candidate(graph, proposal_id, visit_id, minimum_visits=3):
    """사후 검증으로 같은 기능임을 확인한 독립 방문만 승격 근거로 사용한다.

    화면이 반복됐다는 이유만으로 신규 노드나 새 전이를 자동으로 만들지 않는다.
    신규 경로는 proposal에 남겨 잘못된 그래프 오염을 피한다.
    """
    proposal = next(p for p in graph['proposals'] if p['id'] == proposal_id)
    if proposal['status'] != 'candidate' or visit_id not in proposal['visits']:
        raise ValueError('Verification requires an observed candidate visit')
    if minimum_visits < 2:
        raise ValueError('Promotion requires repeated evidence')
    if visit_id not in proposal['verified_visits']:
        proposal['verified_visits'].append(visit_id)
    if proposal['node_id'] is None or len(proposal['verified_visits']) < minimum_visits:
        return False
    node = next(n for n in graph['nodes'] if n['id'] == proposal['node_id'])
    node['references'].append({'image': proposal['image'], 'status': 'confirmed',
                               'visits': len(proposal['verified_visits']), 'last_seen': 0})
    # 새 레이아웃으로 이전 앵커가 무효화될 수 있으므로 재추출 전 fast path를 끈다.
    node['anchors'] = []
    proposal['status'] = 'confirmed'
    return True


def age_candidates(graph, previous, action_id, seen_ids=(), limit=5):
    """동일 지점을 다시 방문했을 때만 미출현 횟수를 증가시킨다."""
    for proposal in graph.get('proposals', []):
        if proposal['status'] != 'candidate' or (proposal['previous'], proposal['action_id']) != (previous, action_id):
            continue
        proposal['misses'] = 0 if proposal['id'] in seen_ids else proposal['misses'] + 1
        if proposal['misses'] >= limit:
            proposal['status'] = 'discarded'


def record_reference_visit(node, image_name, visit, retire_after=20):
    """확인된 참조의 실제 사용 기록에 따라 오래된 이미지를 은퇴시킨다."""
    active = [r for r in node['references'] if r['status'] == 'confirmed']
    matched = next((r for r in active if r['image'] == image_name), None)
    if matched is None:
        raise ValueError('Visit must match a confirmed reference')
    matched['last_seen'] = visit
    matched['visits'] += 1
    for ref in active:
        if ref is not matched and visit - ref['last_seen'] >= retire_after:
            ref['status'] = 'retired'
