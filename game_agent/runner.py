"""명시적인 실행 상태 머신. 화면 캡처와 입력 함수만 외부에서 주입한다.

capture() -> PIL.Image, perform(action) -> None. 테스트에서는 둘 다 가짜 함수를
사용하므로 실제 창이나 계정에 접근하지 않는다. 자동 액션 재시도는 하지 않는다.
"""
import copy
import hashlib
import json
from pathlib import Path

from .healing import observe_candidate
from .vision import locate_template, read_image
from .workflow import asset, save_json, select_edge, validate_graph


class Runner:
    def __init__(self, graph, root, recognizer, capture, perform, output, variables=None):
        self.graph = copy.deepcopy(validate_graph(graph))
        self.root, self.recognizer = Path(root), recognizer
        self.capture, self.perform = capture, perform
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        (self.output / 'snapshots').mkdir()
        fingerprint = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
        self.state = {'status': 'ready', 'node': None, 'pending': None, 'steps': 0,
                      'variables': variables or {}, 'graph_digest': fingerprint,
                      'cost': 0.0, 'human_interventions': 0, 'reason': None, 'sequence': 0}
        self.checkpoint()

    def checkpoint(self):
        save_json(self.output / 'state.json', self.state)

    def trace(self, **fields):
        # 실행 기록은 기준 이미지와 분리한다. 모든 판정 점수와 사후 결과를 남긴다.
        with (self.output / 'trace.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'step': self.state['steps'], **fields}, ensure_ascii=False, allow_nan=False) + '\n')

    def snapshot(self):
        image = self.capture().convert('RGB')
        self.state['sequence'] += 1
        name = f'snapshots/{self.state["sequence"]:06d}.png'
        image.save(self.output / name)
        return image, name

    def pause(self, reason):
        self.state.update(status='paused', reason=reason)
        self.state['human_interventions'] += 1
        self.checkpoint()
        self.trace(event='paused', reason=reason)
        return self.state

    def restore(self, output):
        """새 Runner 대신 재개할 체크포인트를 선택한다. 입력 재실행은 금지한다.

        사용 예: 빈 새 output으로 Runner를 만든 뒤 restore(previous_output).
        pending이 있으면 다음 run은 캡처/사후검증만 먼저 수행한다.
        """
        output = Path(output)
        saved = json.loads((output / 'state.json').read_text(encoding='utf-8'))
        if saved['graph_digest'] != self.state['graph_digest']:
            raise ValueError('Checkpoint belongs to a different graph')
        self.output, self.state = output, saved
        proposals = output / 'proposals.json'
        if proposals.is_file():
            self.graph['proposals'] = json.loads(proposals.read_text(encoding='utf-8'))
        if self.state['status'] != 'completed':
            self.state.update(status='ready', reason=None)
        # 체크포인트 저장 전 실패했더라도 이미 남은 스냅샷을 덮어쓰지 않는다.
        existing = [int(p.stem) for p in (output / 'snapshots').glob('*.png') if p.stem.isdigit()]
        self.state['sequence'] = max([self.state['sequence']] + existing)
        return self

    def identify(self, image, name, **context):
        decision = self.recognizer.identify(image, **context)
        self.state['cost'] += decision.cost
        self.trace(event='recognition', snapshot=name, context=context, decision=decision.to_dict())
        if decision.node_id is None:
            proposal = observe_candidate(self.graph, self.output, image,
                                         f'{self.output.name}:{self.state["steps"]}',
                                         previous=context.get('previous'), action_id=context.get('action_id'))
            save_json(self.output / 'proposals.json', self.graph['proposals'])
            self.trace(event='candidate', proposal_id=proposal['id'])
        return decision

    def run(self, max_steps=100):
        """한 번의 제한된 실행. 멈추면 호출자가 원인을 해결하고 명시적으로 재개한다."""
        if max_steps <= 0:
            raise ValueError('max_steps must be positive')
        if self.state['status'] in ('completed', 'paused'):
            return self.state
        try:
            for _ in range(max_steps + 1):
                image, name = self.snapshot()
                pending = self.state['pending']
                if pending:
                    decision = self.identify(image, name, expected=pending['target'],
                                             previous=pending['source'], action_id=pending['id'])
                    self.trace(event='action_result', edge_id=pending['id'],
                               before=pending['before'], after=name,
                               verified=decision.node_id == pending['target'])
                    if decision.node_id != pending['target']:
                        return self.pause('postcondition_not_verified')
                    self.state.update(node=decision.node_id, pending=None)
                    self.checkpoint()
                else:
                    # 초기 위치와 재개 위치도 화면으로 다시 검증한다.
                    decision = self.identify(image, name, expected=self.state['node'] or self.graph['initial'])
                    if decision.node_id is None:
                        return self.pause(decision.reason)
                    if self.state['node'] is not None and decision.node_id != self.state['node']:
                        return self.pause('unexpected_resume_location')
                    self.state['node'] = decision.node_id
                if self.state['node'] in self.graph['ends']:
                    self.state.update(status='completed', reason=None)
                    self.checkpoint()
                    self.trace(event='completed')
                    return self.state
                if _ == max_steps:
                    return self.pause('step_limit')
                edge = select_edge(self.graph, self.state['node'], self.state['variables'])
                if edge is None:
                    return self.pause('missing_or_ambiguous_branch')
                point = locate_template(image, read_image(asset(self.root, edge['template'])),
                                        edge['action']['point'], edge['template_offset'])
                if point is None:
                    return self.pause('target_template_not_verified')
                action = {**edge['action'], 'point': point}
                # OS 입력과 파일 저장은 하나의 원자적 작업이 될 수 없다. pending을 먼저
                # 저장하고, 비정상 종료 후에는 입력 전송 여부가 불명확하므로 관찰만 재개한다.
                self.state['pending'] = {**edge, 'before': name}
                self.state['steps'] += 1
                self.state['status'] = 'running'
                self.checkpoint()
                self.trace(event='action_intent', edge_id=edge['id'], before=name, action=action)
                self.perform(action)
        except Exception as exc:
            self.trace(event='error', error=str(exc))
            return self.pause('runtime_error: ' + str(exc))
