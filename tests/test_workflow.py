"""외부 모델/실제 입력 없이 픽셀 판단과 실패 시 동작을 검증한다."""
import copy
import json
from pathlib import Path
import random
import tempfile
import unittest

from PIL import Image

from game_agent.builder import build_graph, demonstration_steps
from game_agent.evaluation import cache_scores, evaluate, tune, workflow_metrics
from game_agent.healing import observe_candidate, confirm_candidate, age_candidates, record_reference_visit
from game_agent.runner import Runner
from game_agent.vision import Recognizer, embedding_scorer, locate_template, static_anchors
from game_agent.workflow import asset, load_graph, save_json, select_edge, validate_graph


def screen(seed):
    rng = random.Random(seed)
    image = Image.new('RGB', (64, 64))
    image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(64 * 64)])
    return image


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.a, self.b, self.c = screen(1), screen(2), screen(3)
        self.a.save(self.root / 'a.png')
        self.b.save(self.root / 'b.png')
        self.a.crop((16, 16, 49, 49)).save(self.root / 'button.png')
        self.graph = {'schema_version': 1, 'viewport': [64, 64], 'initial': 'a', 'ends': ['b'],
                      'nodes': [{'id': name, 'status': 'confirmed', 'anchors': [],
                                 'references': [{'image': name + '.png', 'status': 'confirmed', 'visits': 1, 'last_seen': 0}]}
                                for name in ('a', 'b')],
                      'edges': [{'id': 'go', 'source': 'a', 'target': 'b',
                                 'action': {'kind': 'click', 'button': 'left', 'point': [32, 32]},
                                 'template': 'button.png', 'template_offset': [16, 16], 'when': {}}],
                      'proposals': []}

    def recognizer(self, **kwargs):
        return Recognizer(self.graph, self.root, **kwargs)

    def runner(self, frames, perform=None, name='run'):
        frames = iter(frames)
        return Runner(self.graph, self.root, self.recognizer(), lambda: next(frames),
                      perform or (lambda action: None), self.root / name)

    def recording(self, name='recording', end=True):
        path = self.root / name
        path.mkdir()
        self.a.save(path / 'a.png')
        self.b.save(path / 'b.png')
        save_json(path / 'session.json', {'status': 'completed', 'goal': 'reach b'})
        events = [{'id': 'f0', 't_ns': 1, 'kind': 'frame', 'path': 'a.png', 'capture_start_ns': 0},
                  {'id': 'd', 't_ns': 2, 'kind': 'mouse_button', 'button': 'left', 'action': 'down', 'client': [32, 32]},
                  {'id': 'u', 't_ns': 3, 'kind': 'mouse_button', 'button': 'left', 'action': 'up', 'client': [32, 32]}]
        if end:
            events.append({'id': 'f1', 't_ns': 5, 'kind': 'frame', 'path': 'b.png', 'capture_start_ns': 4})
        (path / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
        return path

    def test_graph_round_trip_and_asset_escape(self):
        save_json(self.root / 'graph.json', self.graph)
        self.assertEqual(load_graph(self.root / 'graph.json'), self.graph)
        with self.assertRaises(ValueError):
            asset(self.root, '../outside.png')

    def test_invalid_edge_and_click_rejected(self):
        for field, value in [('target', 'missing'), ('id', None)]:
            broken = copy.deepcopy(self.graph)
            broken['edges'][0][field] = value
            with self.assertRaises(ValueError):
                validate_graph(broken)
        self.graph['edges'][0]['action']['point'] = [-1, 0]
        with self.assertRaises(ValueError):
            validate_graph(self.graph)

    def test_rules_use_explicit_values_and_abstain_on_ties(self):
        other = copy.deepcopy(self.graph['edges'][0])
        other.update(id='other', when={'mode': 'special'})
        self.graph['edges'].append(other)
        self.assertEqual(select_edge(self.graph, 'a', {})['id'], 'go')
        self.assertEqual(select_edge(self.graph, 'a', {'mode': 'special'})['id'], 'other')
        other['when'] = {}
        self.assertIsNone(select_edge(self.graph, 'a', {}))

    def test_hash_recognizes_but_rejects_unknown(self):
        self.assertEqual(self.recognizer().identify(self.a).node_id, 'a')
        unknown = self.recognizer().identify(self.c)
        self.assertIsNone(unknown.node_id)
        self.assertEqual(unknown.stage, 'human')

    def test_equal_candidates_are_not_arbitrarily_selected(self):
        self.graph['nodes'][1]['references'][0]['image'] = 'a.png'
        self.assertIsNone(self.recognizer().identify(self.a).node_id)

    def test_hash_collision_on_flat_screens_is_rejected(self):
        Image.new('RGB', (64, 64), 'black').save(self.root / 'a.png')
        self.assertIsNone(self.recognizer().identify(Image.new('RGB', (64, 64), 'white')).node_id)

    def test_context_cannot_be_overridden_by_vlm(self):
        recognizer = self.recognizer(vlm=lambda *args: {'scores': {'a': 1}, 'cost': .2})
        result = recognizer.identify(self.a, previous='a', expected='b', action_id='go')
        self.assertIsNone(result.node_id)
        self.assertEqual(result.reason, 'context_mismatch')
        self.assertAlmostEqual(result.cost, .2)

    def test_vlm_fallback_and_failed_provider(self):
        recognizer = self.recognizer(embedding=lambda *args: {'scores': {'missing': 1}},
                                    vlm=lambda *args: {'scores': {'b': 1}, 'cost': .1})
        result = recognizer.identify(self.c)
        self.assertEqual((result.node_id, result.stage), ('b', 'vlm'))
        self.assertAlmostEqual(result.cost, .1)
        self.assertTrue(any('error' in s for s in result.stages))

    def test_candidate_and_retired_references_not_used(self):
        for status in ('candidate', 'retired'):
            self.graph['nodes'][0]['references'][0]['status'] = status
            self.assertIsNone(self.recognizer().identify(self.a).node_id)

    def test_provider_cannot_use_unconfirmed_reference(self):
        self.graph['nodes'][0]['references'][0]['status'] = 'candidate'
        seen = []
        def provider(image, nodes, context):
            seen.extend(n['id'] for n in nodes)
            return {'scores': {'a': 1}}
        self.assertIsNone(self.recognizer(vlm=provider).identify(self.c).node_id)
        self.assertEqual(seen, ['b'])

    def test_dynamic_and_shared_tiles_not_anchors(self):
        changed = self.a.copy()
        changed.paste(self.b.crop((0, 0, 32, 32)), (0, 0))
        anchors = static_anchors([self.a, changed], [self.b])
        self.assertNotIn([0, 0, 32, 32], anchors)
        self.assertEqual(len(anchors), 3)
        self.assertEqual(static_anchors([self.a], [self.a]), [])

    def test_expected_anchor_fast_path(self):
        self.graph['nodes'][1]['anchors'] = static_anchors([self.b], [self.a])
        result = self.recognizer().identify(self.b, expected='b', previous='a', action_id='go')
        self.assertEqual(result.stage, 'expected')
        self.assertEqual(len(result.stages), 1)

    def test_viewport_mismatch_stops(self):
        result = self.recognizer().identify(self.a.resize((32, 32)))
        self.assertEqual(result.reason, 'viewport_changed')

    def test_embedding_cosine_and_reference_cache(self):
        calls = []
        def encode(image):
            calls.append(image)
            return image.getpixel((0, 0))
        scorer = embedding_scorer(encode, self.root)
        self.assertAlmostEqual(scorer(self.a, self.graph['nodes'], {})['scores']['a'], 1)
        scorer(self.a, self.graph['nodes'], {})
        self.assertEqual(len(calls), 4)  # 현재 화면 두 번, 기준 화면은 각각 한 번.

    def test_offline_cache_collects_expensive_stages_after_hash_match(self):
        recognizer = self.recognizer(vlm=lambda *args: {'scores': {'a': 1}, 'cost': .3})
        rows = cache_scores([{'id': 'one', 'image': 'a.png', 'label': 'a', 'split': 'development'}],
                            self.root, recognizer, self.root / 'scores.json')
        self.assertEqual(rows[0]['stages'][-1]['stage'], 'vlm')
        self.assertEqual(evaluate(rows, {'hash': .99, 'vlm': .99})['total_cost'], 0)

    def test_invalid_paid_response_still_counts_cost(self):
        result = self.recognizer(vlm=lambda *args: {'scores': {'missing': 1}, 'cost': .4}).identify(self.c)
        self.assertIsNone(result.node_id)
        self.assertAlmostEqual(result.cost, .4)

    def test_template_relocates_and_ambiguous_background_abstains(self):
        template = self.a.crop((16, 16, 49, 49))
        moved = self.c.copy()
        moved.paste(template, (19, 18))
        self.assertEqual(locate_template(moved, template, [32, 32], [16, 16]), [35, 34])
        flat = Image.new('RGB', (64, 64))
        self.assertIsNone(locate_template(flat, flat.crop((0, 0, 10, 10)), [32, 32], [5, 5]))

    def test_build_merge_and_explicit_success(self):
        recording = self.recording()
        graph = build_graph([recording, recording], self.root / 'built', success_last=True)
        self.assertEqual((len(graph['nodes']), len(graph['edges']), len(graph['ends'])), (2, 1, 1))
        self.assertEqual(len(graph['edges'][0]['evidence']), 2)
        self.assertEqual(load_graph(self.root / 'built/graph.json'), graph)
        no_end = build_graph([recording], self.root / 'no-end')
        self.assertEqual(no_end['ends'], [])
        with self.assertRaises(FileExistsError):
            build_graph([recording], self.root / 'built')

    def test_missing_after_frame_is_not_invented(self):
        with self.assertRaisesRegex(ValueError, 'before/after'):
            demonstration_steps(self.recording(end=False))

    def test_overlapping_capture_is_not_after_evidence(self):
        path = self.recording()
        events = [json.loads(s) for s in (path / 'events.jsonl').read_text().splitlines()]
        events[-1]['capture_start_ns'] = 2
        (path / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        with self.assertRaises(ValueError):
            demonstration_steps(path)

    def test_run_success_records_before_after_and_checkpoint(self):
        actions = []
        runner = self.runner([self.a, self.b], actions.append)
        self.assertEqual(runner.run()['status'], 'completed')
        self.assertEqual(len(actions), 1)
        rows = [json.loads(s) for s in (runner.output / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
        result = next(r for r in rows if r['event'] == 'action_result')
        self.assertTrue(result['verified'])
        self.assertNotEqual(result['before'], result['after'])

    def test_failed_action_pauses_without_retry(self):
        actions = []
        runner = self.runner([self.a, self.a], actions.append)
        state = runner.run()
        self.assertEqual(state['reason'], 'postcondition_not_verified')
        self.assertIsNotNone(state['pending'])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(json.loads((runner.output / 'proposals.json').read_text())), 1)

    def test_resume_pending_observes_without_repeating_action(self):
        actions = []
        first = self.runner([self.a, self.a], actions.append)
        first.run()
        second = self.runner([self.b], actions.append, 'resume').restore(first.output)
        self.assertEqual(second.run()['status'], 'completed')
        self.assertEqual(len(actions), 1)

    def test_resume_preserves_previous_candidates(self):
        first = self.runner([self.a, self.c])
        first.run()
        second = self.runner([self.a], name='resume').restore(first.output)
        second.run()
        proposals = json.loads((first.output / 'proposals.json').read_text())
        self.assertEqual(len(proposals), 2)

    def test_input_exception_leaves_pending(self):
        def fail(action):
            raise RuntimeError('transport lost')
        runner = self.runner([self.a], fail)
        state = runner.run()
        self.assertEqual(state['status'], 'paused')
        self.assertIsNotNone(state['pending'])

    def test_template_failure_never_executes(self):
        self.c.crop((16, 16, 49, 49)).save(self.root / 'button.png')
        actions = []
        state = self.runner([self.a], actions.append).run()
        self.assertEqual(state['reason'], 'target_template_not_verified')
        self.assertEqual(actions, [])

    def test_branch_ambiguity_never_executes(self):
        other = copy.deepcopy(self.graph['edges'][0])
        other['id'] = 'other'
        self.graph['edges'].append(other)
        actions = []
        self.assertEqual(self.runner([self.a], actions.append).run()['reason'], 'missing_or_ambiguous_branch')
        self.assertEqual(actions, [])

    def test_candidate_requires_independent_verified_visits(self):
        for visit in ('one', 'one', 'two', 'three'):
            proposal = observe_candidate(self.graph, self.root, self.c, visit, node_id='a')
        self.assertEqual(len(proposal['visits']), 3)
        self.assertFalse(confirm_candidate(self.graph, proposal['id'], 'one'))
        self.assertFalse(confirm_candidate(self.graph, proposal['id'], 'one'))
        self.assertFalse(confirm_candidate(self.graph, proposal['id'], 'two'))
        self.assertTrue(confirm_candidate(self.graph, proposal['id'], 'three'))
        self.assertEqual(len(self.graph['nodes'][0]['references']), 2)

    def test_new_screen_cannot_silently_change_graph(self):
        for visit in ('one', 'two', 'three'):
            proposal = observe_candidate(self.graph, self.root, self.c, visit)
            self.assertFalse(confirm_candidate(self.graph, proposal['id'], visit))
        self.assertEqual(len(self.graph['nodes']), 2)

    def test_candidate_aging_is_local(self):
        proposal = observe_candidate(self.graph, self.root, self.c, 'one', previous='a', action_id='go')
        age_candidates(self.graph, 'elsewhere', 'go', limit=1)
        self.assertEqual(proposal['status'], 'candidate')
        age_candidates(self.graph, 'a', 'go', limit=1)
        self.assertEqual(proposal['status'], 'discarded')

    def test_retirement_keeps_observed_reference(self):
        node = self.graph['nodes'][0]
        node['references'].append({'image': 'b.png', 'status': 'confirmed', 'visits': 1, 'last_seen': 0})
        record_reference_visit(node, 'b.png', 21)
        self.assertEqual([r['status'] for r in node['references']], ['retired', 'confirmed'])


class EvaluationTests(unittest.TestCase):
    def test_unknown_acceptance_is_confident_error(self):
        samples = [{'label': 'a', 'stages': [{'stage': 'hash', 'scores': {'a': 1}}]},
                   {'label': None, 'stages': [{'stage': 'hash', 'scores': {'a': .999}}]},
                   {'label': 'b', 'stages': [{'stage': 'hash', 'scores': {'a': .2}}]}]
        report = evaluate(samples, {'hash': .99})
        self.assertEqual(report['precision'], .5)
        self.assertEqual(report['confident_errors'], 1)
        self.assertEqual(report['abstentions'], 1)

    def test_tuning_excludes_test_data_and_can_abstain(self):
        sample = {'split': 'test', 'label': 'a', 'stages': [{'stage': 'hash', 'scores': {'a': .9}}]}
        with self.assertRaises(ValueError):
            tune([sample], [{'hash': .8}])
        sample['split'] = 'development'
        result = tune([sample], [{'hash': .95}, {'hash': .85}])
        self.assertEqual(result['thresholds'], {'hash': .85})
        self.assertIsNone(tune([sample], [{'hash': .95}]))

    def test_cost_stops_after_accepted_stage(self):
        sample = {'label': 'a', 'stages': [{'stage': 'hash', 'scores': {'a': 1}, 'cost': 0},
                                          {'stage': 'vlm', 'scores': {'a': 1}, 'cost': 10}]}
        self.assertEqual(evaluate([sample], {'hash': .99, 'vlm': .99})['total_cost'], 0)

    def test_unaudited_runs_are_not_counted_as_correct(self):
        report = workflow_metrics([{'cost': .2, 'human_interventions': 1}], [None])
        self.assertIsNone(report['wrong_execution_rate'])
        self.assertEqual(report['human_interventions_per_run'], 1)


if __name__ == '__main__':
    unittest.main()
