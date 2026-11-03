"""픽셀 기반 판단: dHash → 정적 앵커 → 선택적 임베딩 → VLM → 사람.

점수는 확률이 아니다. 기본 임계값은 시작값이며 실제 데이터로 보정해야 한다.
외부 판단기는 scores(image, nodes, context) -> {scores, cost} 형태의 함수다.
"""
from dataclasses import asdict, dataclass, field
import math

from PIL import Image, ImageChops, ImageStat

from .workflow import asset


def read_image(path):
    with Image.open(path) as image:
        return image.convert('RGB')


def similarity(left, right):
    if left.size != right.size:
        return 0.0
    return 1 - sum(ImageStat.Stat(ImageChops.difference(left, right)).mean) / (255 * 3)


def dhash(image):
    small = image.convert('L').resize((9, 8)).tobytes()
    return sum((small[y * 9 + x] > small[y * 9 + x + 1]) << (y * 8 + x)
               for y in range(8) for x in range(8))


def static_anchors(images, others=(), tile=32, stability=.995, distinction=.95):
    """방문 프레임끼리 안정적이고, 다른 노드와는 다른 타일만 앵커로 채택한다."""
    if not images or any(im.size != images[0].size for im in images):
        raise ValueError('Anchor frames must share a viewport')
    width, height = images[0].size
    boxes = []
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            box = (x, y, min(x + tile, width), min(y + tile, height))
            patch = images[0].crop(box)
            # 단색 배경만 일치하는 것을 정체성 근거로 쓰지 않는다.
            if max(ImageStat.Stat(patch).stddev) < 5:
                continue
            if any(similarity(patch, im.crop(box)) < stability for im in images[1:]):
                continue
            if any(im.size == images[0].size and similarity(patch, im.crop(box)) >= distinction for im in others):
                continue
            boxes.append(list(box))
    return boxes


def anchor_score(image, reference, boxes):
    if image.size != reference.size or not boxes:
        return 0.0
    return sum(similarity(image.crop(b), reference.crop(b)) >= .98 for b in boxes) / len(boxes)


def locate_template(image, template, point, offset, radius=8, threshold=.99, margin=.002):
    """기록 위치 주변만 탐색한다. 여러 후보가 동점이면 누르지 않는다.

    반환 좌표는 템플릿 좌측 상단이 아니라 기록된 클릭 지점에 대응한다.
    """
    x0, y0 = point[0] - offset[0], point[1] - offset[1]
    width, height = template.size
    if not (0 <= offset[0] < width and 0 <= offset[1] < height):
        raise ValueError('Click offset outside target template')
    matches = []
    for y in range(max(0, y0 - radius), min(image.height - height, y0 + radius) + 1):
        for x in range(max(0, x0 - radius), min(image.width - width, x0 + radius) + 1):
            score = similarity(image.crop((x, y, x + width, y + height)), template)
            matches.append((score, x, y))
    matches.sort(reverse=True)
    if not matches or matches[0][0] < threshold:
        return None
    if len(matches) > 1 and matches[0][0] - matches[1][0] < margin:
        return None
    _, x, y = matches[0]
    return [x + offset[0], y + offset[1]]


def embedding_scorer(encode, root):
    """encode(PIL.Image)만 제공하면 기준 이미지와의 cosine 비교를 사용할 수 있다.

    특정 모델을 강제하지 않는다. encode는 같은 길이의 유한 실수 벡터를 반환한다.
    기준 임베딩은 파일 수정 시각별로 캐시하여 매 스텝 재계산하지 않는다.
    """
    cache = {}

    def normalized(image):
        vector = [float(v) for v in encode(image)]
        if not vector or any(not math.isfinite(v) for v in vector):
            raise ValueError('Invalid embedding')
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0 or not math.isfinite(norm):
            raise ValueError('Invalid embedding norm')
        return [v / norm for v in vector]

    def score(image, nodes, context):
        current = normalized(image)
        scores = {}
        for node in nodes:
            values = []
            for ref in node['references']:
                if ref['status'] != 'confirmed':
                    continue
                path = asset(root, ref['image'])
                key = (str(path), path.stat().st_mtime_ns)
                if key not in cache:
                    cache[key] = normalized(read_image(path))
                other = cache[key]
                if len(current) != len(other):
                    raise ValueError('Embedding dimensions differ')
                values.append(max(0.0, min(1.0, sum(a * b for a, b in zip(current, other)))))
            scores[node['id']] = max(values, default=0.0)
        return {'scores': scores, 'cost': 0.0}
    return score


@dataclass
class Decision:
    node_id: str | None = None
    stage: str = 'human'
    reason: str = 'uncertain'
    stages: list = field(default_factory=list)
    cost: float = 0.0

    def to_dict(self):
        return asdict(self)


class Recognizer:
    def __init__(self, graph, root, embedding=None, vlm=None, thresholds=None, margin=.05):
        self.graph, self.root = graph, root
        self.embedding, self.vlm = embedding, vlm
        self.thresholds = {'hash': .99, 'anchor': 1.0, 'embedding': .98, 'vlm': .98}
        self.thresholds.update(thresholds or {})
        self.margin = margin

    def references(self, node):
        # 후보/은퇴 이미지는 확정 판단에 사용하지 않는다.
        return [read_image(asset(self.root, r['image'])) for r in node['references'] if r['status'] == 'confirmed']

    def identify(self, image, expected=None, previous=None, action_id=None, collect_all=False):
        result = Decision()
        # 외부 모델에도 확정 이미지밖에 전달하지 않는다. 후보가 모델의 정답 근거로
        # 섞이면 로컬 비교를 우회해 그래프가 오염될 수 있다.
        nodes = [{**n, 'references': [r for r in n['references'] if r['status'] == 'confirmed']}
                 for n in self.graph['nodes'] if n.get('status', 'confirmed') == 'confirmed'
                 and any(r['status'] == 'confirmed' for r in n['references'])]
        context = {'expected': expected, 'previous': previous, 'action_id': action_id}
        allowed = {e['target'] for e in self.graph['edges']
                   if e['source'] == previous and (action_id is None or e['id'] == action_id)} if previous else None
        if image.size != tuple(self.graph['viewport']):
            result.reason = 'viewport_changed'
            return result
        refs = {n['id']: self.references(n) for n in nodes}
        # 정상 경로는 예상 노드의 식별력 있는 앵커만 검증한다.
        expected_node = next((n for n in nodes if n['id'] == expected), None)
        if not collect_all and expected_node and (allowed is None or expected in allowed):
            score = max((anchor_score(image, ref, expected_node.get('anchors', []))
                         for ref in refs[expected]), default=0)
            result.stages.append({'stage': 'expected', 'scores': {expected: score}, 'cost': 0})
            if score >= self.thresholds['anchor']:
                result.node_id, result.stage, result.reason = expected, 'expected', 'matched'
                return result
        for stage in ('hash', 'anchor', 'embedding', 'vlm'):
            provider = self.embedding if stage == 'embedding' else self.vlm
            cost = 0.0
            try:
                if stage == 'hash':
                    h = dhash(image)
                    # 해시 충돌(특히 단색 화면)은 픽셀 차이 검증으로 거른다.
                    scores = {n['id']: max((min(1 - (h ^ dhash(r)).bit_count()/64, similarity(image, r))
                                           for r in refs[n['id']]), default=0) for n in nodes}
                elif stage == 'anchor':
                    scores = {n['id']: max((anchor_score(image, r, n.get('anchors', []))
                                           for r in refs[n['id']]), default=0) for n in nodes}
                elif provider is None:
                    result.stages.append({'stage': stage, 'skipped': 'provider_not_configured', 'cost': 0})
                    continue
                else:
                    response = provider(image, nodes, context)
                    scores, cost = response['scores'], float(response.get('cost', 0))
                    if not math.isfinite(cost) or cost < 0:
                        cost = 0.0
                        raise ValueError('Invalid provider cost')
                if any(k not in refs or not math.isfinite(v) or not 0 <= v <= 1 for k, v in scores.items()):
                    raise ValueError('Invalid provider scores')
            except Exception as exc:
                # 응답 내용이 잘못돼도 이미 발생한 호출 비용은 집계한다.
                result.cost += cost
                result.stages.append({'stage': stage, 'error': str(exc), 'cost': cost})
                continue
            result.cost += cost
            result.stages.append({'stage': stage, 'scores': scores, 'cost': cost})
            if collect_all:
                continue
            ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            if not ranked:
                continue
            node_id, best = ranked[0]
            second = ranked[1][1] if len(ranked) > 1 else 0
            if best < self.thresholds[stage] or best - second < self.margin:
                continue
            if allowed is not None and node_id not in allowed:
                result.reason = 'context_mismatch'
                continue
            result.node_id, result.stage, result.reason = node_id, stage, 'matched'
            return result
        return result
