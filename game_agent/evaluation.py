"""저장한 판단 점수로 평가한다. 모델 재호출 없이 임계값을 비교한다."""
from collections import Counter
import math

from .vision import read_image
from .workflow import asset, save_json


def cache_scores(samples, image_root, recognizer, output):
    """라벨은 판단기에 주지 않고 평가용 캐시에만 보관한다.

    samples: [{id, image, label, split, ...}]. 각 단계를 모두 실행하므로
    VLM을 연결했다면 이 사전 계산에서도 비용이 발생한다.
    """
    rows = []
    for sample in samples:
        if sample.get('split') not in ('development', 'test'):
            raise ValueError('Evaluation sample requires development/test split')
        decision = recognizer.identify(read_image(asset(image_root, sample['image'])), collect_all=True)
        rows.append({**sample, 'stages': decision.stages})
    save_json(output, rows)
    return rows


def predict(sample, thresholds, margin=.05):
    """sample.stages는 미리 계산한 전체 단계 점수다. 누락된 단계는 판단을 유보한다."""
    cost = 0.0
    allowed = sample.get('allowed_nodes')
    for stage in sample['stages']:
        if stage['stage'] not in thresholds:
            continue
        stage_cost = float(stage.get('cost', 0))
        if not math.isfinite(stage_cost) or stage_cost < 0:
            raise ValueError('Invalid cached cost')
        cost += stage_cost
        scores = stage.get('scores', {})
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in scores.values()):
            raise ValueError('Invalid cached scores')
        ranked = sorted(scores.items(), key=lambda p: p[1], reverse=True)
        if not ranked:
            continue
        node, score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        if score >= thresholds[stage['stage']] and score - runner_up >= margin:
            if allowed is None or node in allowed:
                return node, cost
    return None, cost


def evaluate(samples, thresholds, margin=.05):
    accepted = correct = wrong = 0
    cost = 0.0
    matrix = Counter()
    for sample in samples:
        predicted, used_cost = predict(sample, thresholds, margin)
        cost += used_cost
        truth = sample['label']  # None은 미지의 화면이다. 기존 노드로 확정하면 위험한 오답이다.
        matrix[(truth, predicted)] += 1
        if predicted is not None:
            accepted += 1
            correct += predicted == truth
            wrong += predicted != truth
    count = len(samples)
    return {'samples': count, 'precision': correct / accepted if accepted else None,
            'coverage': accepted / count if count else 0, 'confident_errors': wrong,
            'confident_error_rate': wrong / count if count else 0,
            'abstentions': count - accepted, 'total_cost': cost,
            'confusion': [{'truth': t, 'predicted': p, 'count': n} for (t, p), n in matrix.items()]}


def tune(development, candidates, target_precision=.99, margin=.05):
    # 테스트 데이터로 다시 고르면 성능이 부풀려지므로 개발 데이터만 허용한다.
    if not development or any(s.get('split') != 'development' for s in development):
        raise ValueError('Threshold tuning accepts development samples only')
    eligible = []
    for thresholds in candidates:
        report = evaluate(development, thresholds, margin)
        if report['precision'] is not None and report['precision'] >= target_precision:
            eligible.append((report['coverage'], -report['total_cost'], thresholds, report))
    if not eligible:
        return None
    best = max(eligible, key=lambda row: row[:2])
    return {'thresholds': best[2], 'report': best[3]}


def workflow_metrics(states, audited_results):
    """잘못된 실행은 사람이 확인한 사후 라벨만 사용한다. 미검증을 성공으로 세지 않는다."""
    count = len(states)
    audited = [value for value in audited_results if value is not None]
    if len(states) != len(audited_results):
        raise ValueError('Every workflow needs an audit label or None')
    return {'runs': count, 'human_interventions_per_run': sum(s['human_interventions'] for s in states)/count if count else 0,
            'cost_per_run': sum(s['cost'] for s in states)/count if count else 0,
            'audited_runs': len(audited), 'wrong_execution_rate': sum(not v for v in audited)/len(audited) if audited else None}
