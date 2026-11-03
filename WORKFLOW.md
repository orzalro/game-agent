# 화면 기반 워크플로우 구현

설계 문서와 직접 작성한 메모의 핵심을 작은 Python 모듈로 구현했습니다. 상태 관리 프레임워크 없이 JSON과 명시적인 실행 루프를 사용합니다. 문서의 미확인 항목을 확정 요구사항으로 간주하지 않았습니다. 분기는 값의 일치 여부로 선택하고, 불확실한 판단·분기·입력 결과는 중단하는 초기 정책입니다.

## 코드 읽는 순서

| 파일 | 역할 |
| --- | --- |
| `game_agent/workflow.py` | 그래프 JSON 검증, 파일 저장, 분기 선택 |
| `game_agent/builder.py` | 녹화의 클릭 전후 화면을 노드·엣지로 변환, 여러 시연 병합 |
| `game_agent/vision.py` | 이미지 비교, 정적 앵커, 버튼 탐색, cascade와 임베딩 비교 |
| `game_agent/runner.py` | 화면 확인→분기 선택→버튼 확인→액션→사후 검증, 중단·재개 |
| `game_agent/healing.py` | 변경/신규 화면 후보, 검증된 반복 관찰의 승격, 은퇴 |
| `game_agent/evaluation.py` | 전체 단계 점수 캐시, 개발셋 임계값 선택, 오류·비용 지표 |
| `tests/test_workflow.py` | 합성 화면과 가짜 입력 함수를 사용하는 유닛테스트 |

주요 함수와 안전 조건에 한국어 주석을 달았습니다. 그래프·상태·기록은 dict이고, 화면 판정 결과만 작은 dataclass입니다.

## 설치와 유닛테스트

일반 환경에서는 다음과 같이 설치합니다.

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

이번 작업 환경에는 기존 Python 패키지에 영향을 주지 않도록 `.deps`에 Pillow를 설치했습니다. 이 설치를 그대로 사용하려면 같은 PowerShell 세션에서 다음 환경 변수를 설정합니다. 이 설정은 Python 코드가 변경하는 전역 설정이 아닙니다.

```powershell
$env:PYTHONPATH = "$PWD/.deps"
python -m unittest discover -s tests -v
```

제한된 Windows 실행 환경에서 임시 파일 교체가 거부되면 프로젝트 내부 임시 폴더로 테스트할 수 있습니다. 실제 환경에서는 정상적으로 동작하는 원자적 파일 교체를 테스트 편의 때문에 제거하지 않았습니다.

```powershell
New-Item -ItemType Directory -Force .test-tmp | Out-Null
$env:TEMP = "$PWD/.test-tmp"
$env:TMP = $env:TEMP
python -m unittest discover -s tests -v
```

## 녹화에서 그래프 생성

기존 녹화기의 완료된 세션을 사용합니다. 클릭 사이에 충분히 기다려 전후 프레임을 확보해야 합니다.

```powershell
python -m game_agent build-graph recordings/my-session --output workflows/my-task --success-last
python -m game_agent build-graph recordings/first recordings/second --output workflows/merged --success-last
```

`--success-last`는 마지막 화면이 업무 성공이라는 명시적인 선언입니다. 녹화 종료를 업무 성공으로 자동 간주하지 않습니다. 플래그를 생략하면 종료 노드 목록은 비어 있습니다. 출력 폴더가 이미 있으면 덮어쓰지 않습니다.

- `graph.json`: 초기·종료 노드, 기준 이미지 목록, 앵커, 액션, 분기 조건, 근거 이벤트.
- `references/`: 노드 기준 이미지. 실행 스냅샷과 분리합니다.
- `templates/`: 클릭 위치 주변 이미지와 클릭 지점의 상대 좌표.

현재 병합은 같은 크기의 동일 픽셀 화면만 대상으로 합니다. 시작 화면이 다른 시연, 클릭 사이의 설명되지 않는 화면 변화는 거부합니다. 같은 화면인데 업무 역할이 다른 경우는 픽셀만으로 분리할 수 없으므로 그래프 검토가 필요합니다. 그래프 생성 실패 시 원본은 보존되며, 출력 폴더에는 부분 결과가 남을 수 있습니다. 수정 후 다른 출력 폴더로 재시도하세요.

기존 기록기 이벤트를 읽으므로 기록기 자체는 변경하지 않았습니다. 전후 캡처를 확보하지 못한 클릭, 드래그, 키보드, 휠 입력은 지원 범위 밖이라는 오류를 냅니다. 클릭 주변 crop은 실제 버튼의 경계를 의미하지 않습니다. 템플릿이 유일하지 않으면 실행이 중단됩니다. SVG 합성 예제는 이 빌더의 입력이 아니며 PNG/BMP 등의 실제 래스터 녹화를 사용합니다.

## 실행 코어 연결

`Runner`는 실제 장치에 직접 입력하지 않습니다. 캡처와 입력의 두 함수를 연결하는 구조입니다. 아래 두 함수는 호출자가 구현해야 하는 경계이며, 유닛테스트에서는 합성 이미지와 리스트로 대체합니다.

```python
from pathlib import Path
from game_agent.workflow import load_graph
from game_agent.vision import Recognizer
from game_agent.runner import Runner

root = Path("workflows/my-task")
graph = load_graph(root / "graph.json")
recognizer = Recognizer(graph, root)

# capture()는 대상 창 클라이언트 영역의 PIL.Image를 반환한다.
# perform(action)은 {kind: 'click', button, point: [x, y]}를 받아 입력한다.
# 실제 연결 시 창 포커스·크기와 좌표를 확인하고, 화면 전환 대기도 여기서 처리한다.
runner = Runner(graph, root, recognizer, capture, perform,
                output="runs/first", variables={"mode": "normal"})
state = runner.run(max_steps=50)
```

`when: {"mode": "normal"}` 같은 조건이 있는 엣지를 우선 선택하고, 맞는 조건이 없을 때 조건 없는 엣지를 사용합니다. 후보가 둘 이상이면 임의로 선택하지 않습니다. 업무 데이터는 `variables`로 전달하며 폼 입력은 아직 구현하지 않았습니다.

`runs/first/`에는 `state.json`, `trace.jsonl`, `snapshots/`와 필요 시 `proposals.json`, `candidates/`가 생깁니다. 판정 단계별 점수·비용, 액션 전후 스냅샷, 예상 전이와 사후 검증을 기록합니다. 사람 개입 횟수는 실제 응답 횟수가 아니라 **개입이 필요한 중단 횟수**입니다.

액션 전송 전에 `pending`을 저장합니다. 입력 직후 프로그램이 종료되어도 재개 시 액션을 다시 보내지 않고 도착 화면을 먼저 검증합니다. 명시적 재개는 다음과 같습니다.

```python
resumed = Runner(graph, root, recognizer, capture, perform, output="runs/resume-setup")
resumed.restore("runs/first")
state = resumed.run()
```

`restore`는 원래 실행 폴더에 기록을 이어 쓰며 `resume-setup`에는 초기 상태 파일만 남습니다. 그래프 내용이 바뀌었으면 기존 체크포인트 재개를 거부합니다. 도착 화면을 검증할 수 없으면 `pending`을 유지하고 다시 중단합니다. 자동 재시도, 실패 후 좌표 클릭, 임의 경로 복구는 하지 않습니다. 동일 실행 폴더를 여러 프로세스에서 동시에 사용하지 마세요.

## 화면 판단과 외부 모델 연결

정상 경로는 예상 노드 앵커를 1:1 검증합니다. 실패하면 dHash와 전체 픽셀 차이 → 전체 노드 앵커 → 임베딩 → VLM 순서로 진행합니다. 점수 임계값과 1·2위 차이를 모두 검사합니다. 그래프상 불가능한 전이는 VLM이 높은 점수를 반환해도 확정하지 않습니다. 기준 이미지 중 후보·은퇴 상태는 비교에서 제외합니다.

현재 빌더의 앵커는 하나의 기준 화면에서 다른 노드와 구별되는 타일을 추출합니다. `static_anchors([여러_방문_이미지], 다른_노드_이미지)`는 동적 타일을 제외하는 기능도 제공합니다. 다중 프레임 자동 수집·동일 노드 묶기는 추가 연결이 필요합니다. 예상 노드 앵커가 일치해도 다른 영역의 팝업을 놓칠 수 있으므로 실제 데이터로 검증해야 합니다.

모델과 계정은 강제하지 않았습니다. 임베딩은 `embedding_scorer(encode, root)`에 이미지→벡터 함수를 넣으면 기준 벡터 캐시와 cosine 비교를 제공합니다. VLM은 다음 계약의 함수를 전달합니다.

```python
# provider(image, nodes, context) -> {"scores": {"node_id": 0.99}, "cost": 0.002}
# context는 expected / previous / action_id를 포함한다.
# nodes의 references[].image는 root 기준 경로이며, provider가 기준 이미지를 읽는다.
recognizer = Recognizer(graph, root, embedding=embedding_provider, vlm=vlm_provider)
```

`scores`는 기존 노드 ID별 0~1 점수입니다. 알려지지 않은 화면은 낮은 점수 또는 빈 scores로 유보합니다. 점수는 보정된 확률이 아니며 기본 임계값에 정밀도 보장은 없습니다. 공급자가 없는 단계는 생략하고 최종적으로 사람 개입이 필요한 중단 상태를 반환합니다. API 비밀키, 실제 모델 호출, 사람 검토 UI는 포함하지 않았습니다. 비용 단위는 호출자가 모든 provider에 동일하게 적용해야 합니다.

## 후보 관리와 셀프힐링

`observe_candidate`는 신규/변경 화면을 별도 파일과 proposal로 보관합니다. `node_id`를 지정하면 기존 노드의 변경 후보이며 생략하면 신규/선택적 노드 후보입니다. 동일 관찰 ID는 중복 집계하지 않습니다. `suggested_edges`에는 향후 VLM이 찾은 경로 제안을 기록할 수 있지만 실행 경로로 사용하지 않습니다.

`confirm_candidate`는 같은 기능임이 사후 확인된 독립 방문을 집계하고 기본 3회 후 기존 노드의 새 기준 이미지를 추가합니다. 단순 반복 출현만으로 승격하지 않습니다. 기준 이미지는 교체하지 않고 누적합니다. 신규 노드·전이는 자동 확정하지 않습니다. 새 이미지 승격 시 기존 앵커는 비워 재추출 전 잘못된 fast path를 막습니다.

`age_candidates`는 같은 지점의 반복 방문에서 나오지 않은 후보를 폐기 표시합니다. `record_reference_visit`는 확인된 기준 이미지 사용 기록으로 오래된 다른 이미지를 은퇴시킵니다. 실제 이미지 파일은 지우지 않습니다. 이 함수들은 dict를 수정하므로 호출자가 `save_json`으로 저장해야 합니다. 실행기의 불확실한 화면 수집은 연결했지만 후보 검증·승격·은퇴의 자동 스케줄과 검토 UI는 아직 연결하지 않았습니다.

## 평가

라벨링된 샘플을 준비합니다. `label: null`은 기존 노드에 없는 화면입니다. 같은 원본에서 파생한 이미지나 같은 세션은 개발/테스트 양쪽에 나누지 마세요. split 필드 검사만으로 데이터 누출을 자동 방지할 수는 없습니다.

```python
from game_agent.evaluation import cache_scores, evaluate, tune

samples = [
    {"id": "dev-1", "image": "dev/001.png", "label": "node_id", "split": "development"},
    {"id": "test-1", "image": "test/001.png", "label": None, "split": "test"},
]
rows = cache_scores(samples, "evaluation-images", recognizer, "evaluation/scores.json")
dev = [r for r in rows if r["split"] == "development"]
test = [r for r in rows if r["split"] == "test"]
chosen = tune(dev, [
    {"hash": .99, "anchor": 1.0, "embedding": .98, "vlm": .98},
    {"hash": 1.0, "anchor": 1.0, "embedding": .995, "vlm": .995},
])
if chosen is not None:
    report = evaluate(test, chosen["thresholds"])
```

전체 단계 사전 계산은 VLM을 연결하면 실제 호출 비용이 발생합니다. 이후 임계값 비교는 캐시만 사용합니다. 평가 결과에는 확정 정밀도, 자동 확정 비율, 확신 오답 수/비율, 유보 수, 혼동행렬, 사용된 단계까지의 비용이 포함됩니다. 기본 평가는 전체 식별 cascade이며 예상 노드 fast path의 평가는 실행 기록을 별도로 사용해야 합니다. 맥락 제약 평가는 샘플의 `allowed_nodes`로 제한할 수 있습니다.

`workflow_metrics(states, audited_results)`는 실행당 개입 요구·비용과 사후 감사된 잘못된 실행 비율을 계산합니다. 감사하지 않은 결과는 `None`으로 넣고 성공으로 세지 않습니다. 소수 샘플에서 관측 정밀도 99%를 만족해도 실제 정밀도 보장으로 해석할 수 없습니다.

## 현재 범위

핵심 로직을 먼저 읽고 수정할 수 있는 구현입니다. 유닛테스트만 실행했으며 실제 게임 자동 실행, 실제 인식률·지연·비용, 외부 모델의 응답 품질은 검증하지 않았습니다. 실제 Windows 입력 연결, 화면 안정화 대기, 키보드/폼 입력, 비슷한 화면의 시연 병합, 자동 경로 복구는 후속 작업입니다.
