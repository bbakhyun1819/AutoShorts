# AutoShorts — 치지직 하이라이트 분석 데모

치지직 다시보기 한 편을 받아 **지정 구간의 대본·채팅·음량을 분석하고, 후보를 검토한 뒤 세로 쇼츠 샘플을 만드는** Python 프로젝트입니다. 기본 분석 길이는 2시간이며 `--duration`으로 더 긴 구간을 지정할 수 있습니다. 한국어 방송, Windows + NVIDIA GPU를 대상으로 합니다.

현재는 **웹 작업실에서 원본 수집 → 임시 assistant 분석 자료 전달·결과 가져오기 → 후보 검토 → 제목·자막·컷·디자인 편집 → 9:16 영상·한국어 자막·비교 페이지**까지 지원합니다. 기존 CLI와 Gemini 경로도 유지합니다. assistant가 자료를 읽고 결과 JSON을 작성하는 과정은 사용자가 Codex에 따로 요청해야 하며, 프로그램이 Codex를 자동 호출하거나 영상을 게시하지 않습니다. 분석 결과와 자동 생성한 자막은 검수 초안입니다.

세로 영상은 1080×1920 검은 배경에 상단 제목, 가운데 4:3 화면(1080×810), 아래 한국어 자막과 사용자가 제공한 로고·채널명을 배치합니다. 여러 구간을 원래 시간 순서대로 이어 도입과 마지막 반응을 살릴 수 있습니다. 방송 원본·채팅·전사·완성 영상은 Git에 포함하지 않습니다.

## 다른 PC에서 시작

필요: Git, Python **3.11**, FFmpeg/ffprobe(PATH에 등록), NVIDIA 드라이버, 다운로드/원본용 디스크 공간. FFmpeg 배포 안내: https://ffmpeg.org/download.html

PowerShell:

```powershell
git clone https://github.com/bbakhyun1819/AutoShorts.git
cd AutoShorts
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

설치 스크립트는 가상 환경에 전사·Gemini·렌더링용 Python 패키지와 CUDA 12 런타임을 설치합니다. 시스템 PATH나 드라이버는 바꾸지 않습니다. FFmpeg는 별도로 설치해야 합니다. 처음 ASR 실행 때 모델 다운로드가 발생합니다. 실패하면 `doctor` 출력과 DLL 오류를 확인하세요. 실제 GPU 작동은 전사 실행으로 검증해야 하며 설치 여부만으로 확인되지 않습니다.

```powershell
.\.venv\Scripts\chzzk-demo.exe doctor
# 이전 버전의 가상 환경을 계속 쓰는 경우 렌더링 패키지 추가
.\.venv\Scripts\python.exe -m pip install -e '.[render]'
```

## 웹 작업실에서 시작

설치 후 저장소 루트에서 실행합니다. 파스텔 노랑 작업실 UI와 기존 검은 배경의 쇼츠 출력 디자인은 별개입니다.

```powershell
$demo = '.\.venv\Scripts\chzzk-demo.exe'
& $demo studio --workspace .
# 기존에 만든 쇼츠도 작업실에 함께 표시하려면
& $demo studio --workspace . --library runs/demo/shorts
# 브라우저를 자동으로 열지 않으려면 --no-open, 포트를 고정하려면 --port 8765
```

위 명령은 서로 다른 실행 예시입니다. 실행 중인 서버는 터미널에서 **Ctrl+C**로 종료합니다. `--workspace` 아래 `runs/studio/`에 프로젝트·설정·업로드 자료가 저장되고, 기본 편집 출력 위치는 `runs/studio/editor/`입니다. `--output`으로 편집 출력 폴더를 바꿀 수 있습니다. 브라우저를 직접 열 때는 터미널에 표시된 세션 토큰을 포함한 주소 전체를 사용하세요. 서버는 이 PC의 `127.0.0.1`에서 실행합니다.

1. **내 프로젝트**에서 치지직 다시보기 URL과 분석 시작·끝 시각을 입력합니다. 로컬 영상과 채팅 JSON으로 시작하거나 `job.json`이 있는 기존 작업 폴더를 등록할 수도 있습니다. 채팅을 쓰지 않을 때는 명시적으로 **채팅 없이 분석**을 선택합니다.
2. **원본과 분석 자료 준비**에서 **자료 준비**를 실행하고 작업 기록을 확인합니다. 이미 등록된 원본과 `transcript.json`이 있으면 재사용합니다. 채팅을 사용하는 작업은 제공된 비교 영상과 채팅으로 싱크를 직접 확인한 뒤 보정값을 저장합니다. 준비가 끝났다고 싱크를 자동 승인하지 않습니다.
3. 기본 분석 방식인 **Codex 직접 분석**에서 **분석 자료 만들기**를 누릅니다. 이것은 대본 자료와 응답 양식을 만드는 단계입니다. **요청문 복사**로 가져온 문구를 이 저장소를 연 Codex 채팅에 붙여 넣고, 작성된 `response.json`을 **대본 분석 결과 가져오기**로 입력합니다. 패킷 식별값과 자료 검증을 통과하면 전체 후보 목록과 보고서를 만듭니다.
4. 후보 목록에서 검토할 장면을 선택해 **선택 후보 검토 준비**를 실행합니다. 표본 화면·주변 대본·채팅을 Codex에 전달하고, 검토 결과 JSON을 가져옵니다. 자료 준비와 실제 검토 완료는 서로 다른 상태로 표시합니다. 후보가 없으면 억지로 편집안을 만들지 않습니다.
5. 후보의 **편집 초안**을 만들거나 직접 작성한 편집안 JSON을 가져옵니다. 초안은 후보 구간과 기존 전사를 묶은 출발점이며, 이야기의 완결성이나 자막 정확성을 자동 확정하지 않습니다. **수정**으로 별도 편집 탭을 열고 생성 목록의 카드를 작업 영역에 끌어놓거나 **불러오기**를 누릅니다.
6. 제목·자막·여러 컷과 디자인을 다듬고 **편집 저장**, **영상 만들기**를 실행합니다. 완성 영상은 목록에서 재생할 수 있습니다. 단계가 실패하거나 서버가 중단되면 기록을 확인하고 해당 단계를 다시 실행합니다.

**채널 디자인**에서는 채널명·로고·폰트를, **분석 설정**에서는 전사와 분석 방식을 지정합니다. 기본값은 assistant, 다운로드 1080p 이하, `large-v3`·CUDA·`float16`·묶음 크기 8, 검토 후보 10개·표본 프레임 6개입니다. 희망 길이 60~110초와 우선 제작 수 1개는 분석 요청에 전달하는 선호이며, 그 개수의 영상을 자동 선정·생성한다는 뜻은 아닙니다. 원본에 없는 해상도를 만들어내지는 않습니다. 기존 라이브러리에 연결된 디자인 파일이 있으면 초기값으로 활용하며, 로고·채널명은 실제 사용할 자료로 설정해야 합니다.

Gemini는 사용자가 분석 방식을 명시적으로 바꾸고 **외부 전송·API 비용 동의**를 선택한 경우에만 작업실에서 실행합니다. API 키·모델 ID는 실행할 PC의 환경 변수에 설정합니다. assistant 경로는 프로그램의 외부 AI API 호출을 하지 않지만, 사용자가 Codex에 전달한 자료는 해당 대화에서 처리됩니다. URL 원본 수집과 최초 전사 모델 다운로드에는 네트워크가 필요합니다.

## 분석 자료 수집을 한 번에 실행

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-demo.ps1 -Url 'https://chzzk.naver.com/video/영상번호'
```

- **영상 전체를 다운로드해 보존**, 기본 분석 범위는 처음 7200초(2시간)입니다. 긴 방송은 다운로드·디스크 사용량이 분석 구간보다 큽니다.
- 다른 구간은 `-Start 3600 -Duration 7200`처럼 **초 단위**로 지정합니다.
- 48분~4시간 50분은 `ingest --start 2880 --duration 14520`입니다. 전사는 긴 범위도 20분씩 나누어 저장·재개합니다. `ingest --format 'best[height<=480]'`으로 분석용 다운로드 화질을 제한할 수 있습니다. 고화질 편집본은 별도로 확보하세요.
- 실행 스크립트에서도 `-Format 'best[height<=1080]'`처럼 다운로드 화질을 지정할 수 있습니다. 기존 작업을 재개할 때는 이미 저장한 원본을 사용합니다.
- 기본 실행은 로컬 처리만 수행합니다. 분석 옵션을 지정하지 않으면 채팅·음량 후보만 생성되며 토크 의미 분석과 영상 검증은 미실행 상태입니다.
- `-WithAssistant`는 Codex가 읽을 대본 자료까지 준비하고 **assistant 분석 대기 상태로 종료**합니다. 아래 절차에 따라 Codex에 분석을 요청해야 클립·검수 페이지를 만들 수 있습니다.
- `-WithAI`는 기존 Gemini API 분석을 실행합니다. `-WithAssistant`와 함께 사용할 수 없습니다.
- 채팅이 제공되지 않을 때만 `-AllowNoChat`으로 명시적으로 생략합니다.
- 같은 작업 폴더로 재실행하면 수집 페이지와 전사 조각을 재사용합니다. **다른 URL/분석 범위는 반드시 새 `-Job runs/다른이름`**을 지정하세요.
- 같은 작업의 원본 파일을 다른 영상으로 교체하지 마세요. 기존 클립·전사 캐시를 재사용하므로, 원본을 바꿀 때도 새 작업 폴더에서 시작하세요.
- 확인용 클립은 기본 10개만 생성합니다. 나머지 후보는 목록에서 삭제하지 않습니다. `-ClipLimit 30`으로 늘릴 수 있습니다. assistant 모드는 출력된 후속 요청 문구에 이 개수를 반영합니다.
- 완료 후 `runs/demo/report.html`을 브라우저로 엽니다. 영상과 재생 채팅을 비교하고 ‘선택 결과 JSON 저장’을 누르세요. 선택은 새로고침하면 초기화되므로 저장이 필요합니다.

## 단계별 실행 (처음에는 이 경로 권장)

아래 명령은 저장소 루트에서 실행합니다.

```powershell
$demo = '.\.venv\Scripts\chzzk-demo.exe'
& $demo ingest --url 'https://chzzk.naver.com/video/영상번호' --job runs/demo
& $demo chats --job runs/demo
& $demo sync-report --job runs/demo
# runs/demo/sync-check/report.html에서 앞/중간/끝을 웹 다시보기와 비교
# 채팅을 2초 늦춰야 한다면 --offset 2, 2초 앞당기려면 --offset -2
& $demo sync --job runs/demo --offset 0 --confirmed
& $demo transcribe --job runs/demo --model large-v3 --batch-size 8
& $demo signals --job runs/demo
& $demo combine --job runs/demo
& $demo clips --job runs/demo --limit 10
& $demo report --job runs/demo
```

싱크 확인은 자동으로 ‘완료’ 처리하지 않습니다. 고정 오프셋만 지원하며 중간 편집으로 시간축이 불연속인 영상은 수동 보완이 필요합니다. sync를 변경하면 `signals`부터 후속 결과를 다시 만듭니다. **assistant 대본 분석이 있던 작업은** `signals` → 대본 `assistant-prepare` → 새 자료 분석·`assistant-import` → `combine` → 필요하면 `clips` → review 재준비·검토·가져오기 → `report` 순서로 진행합니다. 이전 패킷을 그대로 가져오거나 바로 `combine`하면 변경된 입력을 감지해 중단됩니다. assistant 분석이 없던 작업은 `signals` → `combine` → `clips` → `report`를 다시 실행합니다.

## 임시 assistant 분석 (Gemini 대신 Codex가 직접 분석)

추후 API들을 비교하기 전까지 쓰는 임시 경로입니다. 프로그램이 Gemini를 호출하는 대신 **로컬 자료를 내보내고, 이 저장소를 연 Codex가 자료를 읽어 JSON을 작성한 뒤 다시 가져옵니다.** Gemini 명령은 그대로 유지합니다. 이 경로에는 Gemini API 키나 `google-genai` 패키지가 필요하지 않으며, 프로그램이 외부 AI API를 호출하거나 영상을 업로드하지 않습니다. Codex에 읽힌 자료는 현재 대화의 처리 대상이므로, 전체 과정이 오프라인으로 실행되는 것은 아닙니다.

처음 실행할 때:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-demo.ps1 -Url 'https://chzzk.naver.com/video/영상번호' -Job runs/demo -WithAssistant
```

수집·전사·신호 추출이 끝나면 `runs/demo/assistant/text/<packet_id>/`에 분석 자료를 만듭니다. 이 시점은 분석 완료가 아닙니다. 이 저장소를 연 Codex에 다음과 같이 요청하세요. 다른 작업 폴더를 썼다면 `runs/demo`를 바꾸면 됩니다.

> runs/demo 작업 폴더를 AGENTS.md의 임시 assistant 분석 절차로 분석하고 report까지 만들어줘. 모든 대본 창을 읽고 후보를 선정한 다음, 상위 10개 클립의 표본 프레임과 대본·채팅을 검토해줘. 실제로 확인한 근거와 확인하지 못한 부분을 구분해줘.

Codex는 생성된 `INSTRUCTIONS.md`, `packet.json`, `response-template.json`을 기준으로 분석합니다. 대본 자료는 전체 분석 범위를 창으로 나누어 제공하며, 후보가 없는 창도 빠짐없이 확인해야 합니다. 검토 자료는 클립별 표본 프레임과 주변 대본·채팅입니다. **표본 프레임 검토는 영상 전체 시청이나 음성 청취가 아니며**, 프레임 사이의 동작·타이밍·오디오가 필요한 판단은 확인하지 못한 부분으로 남깁니다. 이미지를 확인할 수 없거나 대본만 검토하려면 검토 자료 준비 시 `--frames 0`을 지정하세요. 이 경우 결과도 대본 검토로 기록하며 화면을 확인한 것으로 표시하지 않습니다.

기존 작업을 이어가거나 직접 단계별로 실행할 때:

```powershell
$demo = '.\.venv\Scripts\chzzk-demo.exe'
# transcript.json과 signals 결과가 준비된 작업에서 시작
& $demo assistant-prepare --job runs/demo --stage text
# Codex가 출력된 패킷 폴더의 모든 대본 자료를 읽고 response.json 작성
# <packet_id>는 assistant-prepare가 출력한 해당 패킷의 ID로 교체
& $demo assistant-import --job runs/demo --input 'runs/demo/assistant/text/<packet_id>/response.json'
& $demo combine --job runs/demo
# candidates.json이 빈 배열이면 clips/review 단계를 건너뛰고 report만 실행
& $demo clips --job runs/demo --limit 10
& $demo assistant-prepare --job runs/demo --stage review --limit 10 --frames 6
# Codex가 출력된 review 패킷의 표본 프레임·대본·채팅을 확인하고 response.json 작성
& $demo assistant-import --job runs/demo --input 'runs/demo/assistant/review/<packet_id>/response.json'
& $demo report --job runs/demo
```

후보별 MP4를 만들지 않고 원본의 표본 화면만 검토하려면 `--from-source`를 사용하고 `clips` 단계를 생략할 수 있습니다. 예를 들어 전체 대본을 분석한 뒤 샘플 한 개만 만들려면, `combine`이 만든 `candidates.json`에서 선택한 후보의 `id`를 확인하고 다음처럼 그 후보만 검토합니다.

```powershell
# <candidate_id>는 candidates.json에 있는 실제 id로 교체
& $demo assistant-prepare --job runs/demo --stage review --from-source --candidate-id '<candidate_id>' --limit 1 --frames 6
# 출력된 패킷의 프레임을 확인한 뒤 response.json 작성 → assistant-import → report
```

`--candidate-id`는 여러 번 지정할 수 있으며, 지정한 후보로 좁힌 뒤 `--limit`를 적용합니다. 후보 4개를 비교하려면 실제 ID 네 개와 `--limit 4`를 지정합니다. `--from-source`는 `job.json`에 기록된 원본 파일을 사용하며 후보 MP4를 만들지 않습니다. 검토 후 아래 `render` 명령으로 세로 편집본을 만듭니다.

명령만 실행하면 분석 응답이 생기지 않습니다. `response-template.json`은 작성 양식이며, 내용을 채우지 않은 양식을 분석 결과로 가져오면 안 됩니다. `combine` 후 후보가 0개라면 클립 생성과 review 준비·가져오기를 생략하고 `report --job runs/demo`를 실행합니다. 후보를 억지로 만들지 않고 분석은 완료했지만 선정된 장면은 없다고 보고합니다.

패킷은 자료와 분석 범위를 식별합니다. 가져오기에서는 패킷·원본 자료·응답 범위와 구간을 검사하므로, 대본·싱크·후보·원본 파일이나 `clips/`의 MP4 등 입력이 바뀌었다면 해당 패킷을 다시 준비하고 분석하세요. 중간에 멈췄다면 이미 작성한 응답을 가져온 다음 위의 후속 명령부터 이어가면 됩니다. 실행 스크립트를 다시 호출하는 것만으로 assistant 분석이 자동 재개되지는 않습니다.

대본 분석을 가져오면 `text-candidates.json`과 `text-analysis.json`, 클립 검토를 가져오면 `reviews/<id>-assistant.json`에 저장합니다. `text-analysis.json`과 각 review 결과에는 `provider: assistant`가 기록되며, 대본 후보의 `sources`에는 `assistant`가 포함됩니다. **Gemini와 assistant 중 마지막으로 성공한 대본 분석 결과**가 `combine`에 사용되므로, 두 방식을 같은 작업 폴더에서 실행하면 대본 후보가 교체됩니다. 결과를 나란히 비교하려면 별도 작업 폴더를 사용하세요. assistant 분석은 사람의 승인이나 채팅 싱크 확인을 대신하지 않습니다.

## 검토한 후보를 세로 쇼츠로 만들기

한 줄 농담이나 채팅 급증만 떼어내면 맥락과 끝맺음이 부족할 수 있습니다. 후보를 고를 때는 **상황이 이해되는 도입, 이야기가 바뀌는 지점, 마지막 답변이나 반응**이 함께 있는지 확인합니다. 길이는 내용에 맞춰 정하고, 예를 들어 60~110초가 필요하다면 짧게 만들기 위해 결말을 자르지 않습니다. 긴 영상이라고 더 재미있어지는 것은 아니므로 반복·대기·다른 주제는 편집안에서 제외합니다.

Codex에 다음처럼 요청할 수 있습니다.

> 검토한 후보 중 도입과 결말이 있는 장면 4개를 골라줘. 원래 시간 순서를 지키고 마지막 반응까지 남겨 편집안을 작성해줘. 기존 검은 배경·상단 제목·4:3 화면·한국어 자막·하단 로고와 채널명 디자인으로 렌더링하고 비교 페이지를 만들어줘. 자막에서 확실하지 않은 전사는 구분해줘.

샘플 한 편이면 요청 개수를 1개로 바꾸면 됩니다. 사람이 직접 편집안을 작성해도 같은 렌더러를 사용할 수 있습니다. 검수 페이지에서 저장한 선택 JSON은 아직 자동으로 편집안으로 바뀌지 않습니다.

### 화질과 준비 파일

최종 화면을 크게 보여주려면 편집용 원본을 가능한 한 **1080p**로 확보하세요. 480p 분석용 파일을 1080p로 확대해도 사라진 글자나 디테일이 복원되지는 않습니다. 원본의 4:3 영역을 골라 세로 캔버스에 배치하므로, 게임 UI나 방송 아바타가 잘리지 않도록 `crop`을 확인해야 합니다.

처음부터 고화질 원본을 쓸 경우:

```powershell
$demo = '.\.venv\Scripts\chzzk-demo.exe'
& $demo ingest --url 'https://chzzk.naver.com/video/영상번호' --start 2880 --duration 14520 --format 'best[height<=1080]' --job runs/high-quality
```

이는 1080p 이하에서 가능한 화질을 선택하며, 제공되지 않는 해상도를 만들어내지는 않습니다. 이미 분석한 작업의 원본을 교체하지 마세요. 따로 확보한 고화질 편집용 파일을 편집안의 `source`로 지정할 수 있습니다.

렌더링에는 FFmpeg/ffprobe, `.[render]` 추가 패키지, 한국어를 지원하는 TTF 글꼴, 사용할 로고 이미지가 필요합니다. Windows에서는 `C:/Windows/Fonts/malgunbd.ttf`와 글꼴 이름 `맑은 고딕`을 사용할 수 있습니다. 로고 파일은 사용자가 준비하며 저장소에 방송 채널의 로고를 기본 포함하지 않습니다.

### JSON 편집안과 실행

[`examples/render-plan.json`](examples/render-plan.json)을 `runs/` 아래에 복사하고 실제 파일 경로·구간·제목·자막으로 수정합니다. 예시는 양식이며 방송 자료나 분석 결과를 포함하지 않습니다.

```powershell
New-Item -ItemType Directory -Force runs/demo/edit-plans | Out-Null
Copy-Item examples/render-plan.json runs/demo/edit-plans/sample.json
# sample.json의 경로와 편집 내용을 실제 자료에 맞게 수정한 다음 실행
& $demo render --plan runs/demo/edit-plans/sample.json --output runs/demo/shorts --gallery
```

편집안의 주요 값은 다음과 같습니다. 파일 경로는 **편집안 JSON이 있는 폴더 기준**이며 절대 경로도 사용할 수 있습니다. Windows 경로는 `C:/...`처럼 `/`를 쓰면 JSON 이스케이프를 피할 수 있습니다.

| 값 | 의미 |
| --- | --- |
| `source` | 편집에 쓸 영상 파일 |
| `source_start` | 이 파일의 첫 화면에 해당하는 원본 방송 시각(초). 전체 원본이면 `0`, 원본 3600초부터 잘라 둔 파일이면 `3600` |
| `logo`, `font`, `font_name` | 로고 이미지, TTF/OTF 파일, 자막에 사용할 글꼴 이름 |
| `channel`, `title_lines` | 하단 채널명과 상단 제목 1~2줄 |
| `keep` | 남길 구간의 `[시작초, 끝초]` 목록. **원본 방송 시각**을 사용하고 시간 순서대로 배치 |
| `captions` | `start`, `end`, `text`를 가진 한국어 자막 목록. 시각은 **원본 방송 기준**. 선택 항목 `style`은 `Caption`(기본), `Emphasis`, `Punchline` 중 하나 |
| `slug` | 선택 항목. 출력 파일을 구분하는 영문 소문자·숫자·`_`·`-` 이름(첫 글자는 영문 소문자 또는 숫자, 최대 64자). 기본값 `short` |
| `crop` | 선택 항목. 원본 픽셀 기준 `x`, `y`, `width`, `height`. 모든 값은 짝수 정수이고 너비:높이는 4:3. 생략하면 원본 안에서 가장 큰 중앙 4:3 영역 |
| `context` | 선택 항목. `start`, `end`, `text` 목록으로 한 줄 편집 설명을 표시. 이 항목만 **완성 영상 시작부터의 상대 시각** 사용 |
| `design` | 선택 항목. `title`, `caption`, `channel`, `logo`, `context`별 위치·크기. 생략하면 기존 승인 디자인을 사용하며 아래 편집기에서 조절 가능 |

`source_start`는 잘라 둔 파일의 시간축을 원본 시각에 맞추기 위한 값입니다. 채팅 싱크 오프셋과는 다릅니다. `keep`과 자막에서 이를 미리 빼지 마세요. 각 자막은 `keep`의 한 구간 안에 완전히 들어가야 하며, 컷을 가로지르는 자막은 편집안에서 나눠야 합니다. 렌더러가 제외 구간을 빼고 출력 영상의 자막 시각을 계산하며, 원본 오디오를 사용합니다. ASS 자막을 영상에 입히고 별도 SRT도 저장합니다. 자막 문구를 고치거나 구간을 잇는 판단은 작성한 편집안에 따릅니다.

여러 편을 같은 폴더에 렌더링하려면 편마다 다른 `slug`를 지정하고 아래처럼 순서대로 실행합니다. `slug`를 생략한 채 반복하면 기본 이름 `short`의 결과를 갱신하므로 주의하세요.

```powershell
& $demo render --plan runs/demo/edit-plans/candidate-01.json --output runs/demo/shorts --gallery
& $demo render --plan runs/demo/edit-plans/candidate-02.json --output runs/demo/shorts --gallery
& $demo render --plan runs/demo/edit-plans/candidate-03.json --output runs/demo/shorts --gallery
& $demo render --plan runs/demo/edit-plans/candidate-04.json --output runs/demo/shorts --gallery
```

`runs/demo/shorts/index.html`에서 각 영상을 재생하고 MP4·자막을 비교할 수 있습니다. `manifest.json`은 같은 출력 폴더의 렌더 결과를 모으며, `--gallery`는 그 목록 전체의 비교 페이지를 갱신합니다. 같은 `slug`로 다시 렌더링하면 해당 결과를 갱신하고, 렌더 실패 시 기존 결과를 보존합니다. 결과 프레임의 글자 잘림·크롭, 오디오와 자막, 특히 마지막 발화가 중간에 잘리지 않는지를 확인하세요. 이 명령은 영상 업로드나 게시를 수행하지 않습니다.

### 별도 탭에서 제목·자막·컷·디자인 편집

완성된 후보 영상을 라이브러리에서 비교한 뒤 필요한 영상만 편집할 수 있습니다. `render`가 만든 결과 폴더를 지정하면 하위 폴더의 `manifest.json`도 찾습니다. 목록의 영상을 바로 재생하고, **수정**을 누르면 별도 브라우저 탭에 편집기가 열립니다. 편집 탭 안의 **생성한 쇼츠** 목록에서 영상 카드를 작업 영역으로 드래그하거나 **불러오기**를 눌러 편집할 영상을 선택합니다. 라이브러리를 열기만 했을 때는 어떤 영상도 편집 대상으로 자동 선택하지 않습니다.

```powershell
# 여러 후보 영상에서 고르기
& $demo editor --library runs/demo/shorts --output runs/demo/design-edits
# 편집안 한 개로 시작하는 기존 명령도 지원
& $demo editor --plan runs/demo/edit-plans/sample.json --output runs/demo/sample-editor
# 브라우저 자동 열기를 끄고 주소만 표시하려면 --no-open
# 포트를 지정하려면 --port 8765 (기본 --port 0은 사용 가능한 포트 자동 선택)
```

폴더 대신 라이브러리 JSON으로 영상과 편집안을 직접 연결할 수 있습니다. 경로는 이 JSON 파일이 있는 폴더 기준입니다. `--plan`과 `--library` 중 하나만 지정하세요.

```json
{
  "schema_version": 1,
  "items": [
    {"title": "첫 번째 후보", "video": "shorts/first.mp4", "plan": "edit-plans/first.json"},
    {"title": "두 번째 후보", "video": "shorts/second.mp4", "plan": "edit-plans/second.json"}
  ]
}
```

예를 들어 이 파일을 `runs/demo/library.json`에 저장했다면 `editor --library runs/demo/library.json --output runs/demo/design-edits`로 실행합니다. MP4만 있고 편집안이나 필요한 원본·글꼴·로고가 없는 항목은 재생만 허용합니다. `--plan` 또는 작업실에서 만든 유효한 편집안은 아직 완성 MP4가 없어도 불러와 편집할 수 있습니다. 임의의 MP4 끌어놓기로 컷·자막 자료를 추측하지 않으며, 목록에 연결된 편집 자료를 재사용합니다. 기존 편집안에 `design`이 없으면 검은 배경, 상단 제목, 가운데 4:3 화면, 하단 자막·로고·채널명 배치로 시작합니다.

실행하면 라이브러리가 브라우저에 열립니다. `--no-open`을 사용했다면 터미널에 표시된 `http://127.0.0.1:포트/#token=...` 주소 전체를 여세요. 편집기는 이 PC의 `127.0.0.1`에서만 실행하며, 세션 토큰이 맞는 요청만 편집 자료를 읽거나 저장할 수 있습니다. 종료는 실행한 터미널에서 **Ctrl+C**입니다. 브라우저 탭만 닫으면 서버는 계속 실행됩니다.

1. **디자인** 탭에서 설치된 글꼴을 고르거나 **내 폰트 추가**로 TTF/OTF 파일을 추가합니다. 제목·자막·채널명·상황 설명에 같은 글꼴을 적용합니다. 화면의 요소를 **드래그**하거나 크기·X/Y 좌표를 입력할 수 있습니다. 제목 두 줄은 함께 움직이며, 자막 위치는 모든 자막에 공통 적용됩니다. 기본·강조·마지막 반응 자막의 크기는 따로 설정합니다. 선택한 요소는 **방향키로 1px**, **Shift + 방향키로 10px**씩 옮길 수 있습니다.
2. **제목 · 자막** 탭에서 제목 1~2줄과 채널명을 수정합니다. 자막 목록에서 문구·시작·끝 시각·스타일을 바꾸고 자막을 추가하거나 삭제할 수 있습니다. 자막 시각은 **원본 방송 기준 초**입니다. 각 자막은 한 컷 안에 들어가야 하며 다른 자막과 겹칠 수 없습니다.
3. **컷 편집** 탭에서 여러 구간의 시작·끝을 숫자나 슬라이더로 조절하고 구간을 추가·삭제합니다. **원본 탐색**과 **원본 ±5초**로 현재 컷 앞뒤를 재생하고, **현재 원본 시점을 시작/끝으로** 표시할 수 있습니다. **완성 영상** 모드는 남긴 구간만 시간순으로 이어 재생합니다. 탐색·확장은 연결된 원본 파일의 실제 범위 안에서만 가능하며, `source_start`가 있는 잘라 둔 파일도 원본 방송 시각으로 표시합니다.
4. 컷을 줄이면 밖에 있는 자막은 삭제하고, 경계에 걸친 자막은 자르거나 나눕니다. 화면에 삭제·조정·분할 개수를 표시하며, 상황 설명도 같은 원본 장면에 맞춰 옮깁니다. 확장한 부분에는 자막이 자동으로 생기지 않습니다. **실행 취소**로 컷과 이에 따른 자막 변경을 함께 되돌릴 수 있습니다. 겹치는 구간 등 입력 오류가 있으면 초안을 유지하고 오류를 표시하며, 컷 재생에는 마지막 유효한 구간을 사용합니다.
5. 빠른 미리보기는 글자 모양·간격이 최종 출력과 약간 다를 수 있습니다. 포함된 구간에서 **최종 렌더로 한 장 확인**을 누르면 실제 MP4와 같은 FFmpeg·ASS 방식으로 현재 시점 PNG를 만듭니다. 전체 영상의 오디오·자막 싱크와 마지막 발화 경계는 생성한 MP4로 따로 확인하세요.
6. **편집 저장**은 `--library`/작업실 모드에서는 `출력폴더/items/영상ID/edit-plan.json`, `--plan` 모드에서는 `출력폴더/edit-plan.json`에 디자인과 제목·자막·컷을 저장합니다. 처음 입력한 편집안과 원본 영상은 유지합니다. 같은 라이브러리·출력 폴더로 재시작하면 저장한 편집을 이어갑니다. 다른 영상으로 전환할 때 저장하지 않은 변경이 있으면 저장·버리기·취소 중에서 선택합니다.
7. **영상 만들기**는 현재 편집안을 저장한 뒤 해당 편집 폴더의 `renders/`에 MP4·자막·비교 페이지를 만듭니다. 완료되면 영상 링크가 표시됩니다. 영상별 저장 내용과 렌더 결과는 분리되며, 기존 `render` 명령으로 저장된 `edit-plan.json`을 렌더링해도 같습니다.

**기본 디자인으로 되돌리기**는 위치·크기와 처음 입력한 편집안의 폰트를 복원하며 제목·자막 문구와 컷 내용은 유지합니다. 글자가 길면 지정한 크기보다 작게 맞출 수 있으므로 한글 글리프와 여러 자막 시점의 실제 렌더 프레임을 확인하세요. 편집기 자체는 영상·폰트를 로컬에서 처리하며 외부 AI API 호출이나 영상 게시를 하지 않습니다.

## Gemini 의미 분석과 영상 검토 (선택, 유료 API)

API 키는 **사용할 PC의 환경 변수로만** 입력합니다. 채팅/영상/대본/키를 Git에 올리지 않습니다. 모델 ID는 사용 가능한 영상 입력 지원 모델로 직접 지정하세요. 모델 가용성·비용은 계정별로 확인합니다.

```powershell
$env:GEMINI_API_KEY = '해당-PC에서만-입력'
$env:GEMINI_MODEL = '계정에서-사용-가능한-모델-ID'
& $demo text-candidates --job runs/demo
& $demo combine --job runs/demo
& $demo clips --job runs/demo --limit 10
& $demo review-video --job runs/demo --limit 10
& $demo report --job runs/demo
```

`text-candidates`는 **전체 분석 구간의 대본**을 10분+문맥 겹침 단위로 Google API에 전송합니다. 채팅이 없는 토크도 후보에 들어옵니다. `review-video`는 생성된 후보 클립을 Google Files API에 업로드하여 영상 내용을 확인하고 삭제를 시도합니다. 외부 AI 호출은 이 두 명령에 구현되어 있으며, 작업실에서 Gemini를 선택하고 동의하면 같은 명령을 실행합니다. 실행 스크립트에서는 `-WithAI`로 두 단계를 켤 수 있습니다. API 키와 모델이 없는 상태에서 API 단계는 중단됩니다.

대본 분석은 10분 창당 최대 5개 제안, 영상 검토는 `--limit`개까지입니다. 숨겨진 전수 검증으로 표시하지 않습니다. 검토 결과의 구간·분류를 검증하고 잘못된 응답은 저장 또는 오류로 남깁니다. 생성 실패 시 재실행하면 저장한 응답은 재사용됩니다.

## 노래·춤 또는 누락된 장면을 직접 추가

```powershell
& $demo add --job runs/demo --start 3100 --end 3160 --category '노래·춤' --reason '후렴과 포인트 안무'
& $demo combine --job runs/demo
& $demo clips --job runs/demo --limit 20
& $demo report --job runs/demo
```

**한계:** 이 버전에는 음악 구조/춤 동작의 전수 탐지 모델이나 게임별 이벤트 인식기가 없습니다. 대본에 드러나지 않고 조용한 춤·게임 명장면은 놓칠 수 있습니다. 음악의 음량을 비명으로 단정하지 않으며, 음량 후보는 ‘효과음일 수 있음’으로 표시합니다. 수동 추가로 세 유형을 데모에서 비교할 수 있습니다.

## 내부 처리와 산출물

1. 원본 전체 보존 + 지정 구간만 분석. 원본 재생 시각이 모든 결과의 기준.
2. 채팅은 `playerMessageTime`을 사용해 페이지 단위 저장/재개. 비공식 API이므로 변경·권한·보관 누락 가능. 커서 반복/누락된 시간값은 성공으로 숨기지 않고 중단.
3. 음성은 20분 중심 구간 + 경계 5초 문맥으로 처리. 단어 중간 시각이 속한 중심 구간이 그 단어를 소유. 경계 전사 오차는 실제 자료 검수가 필요.
4. 모델은 한 번 로드하고 조각별 저장. `benchmark.json`에 실제 추론 시간 기록. 이번 데모는 경계 병합과 자막 재사용을 위해 **전체 단어 타임스탬프를 켭니다**. 구간 대본만 만드는 방식보다 느릴 수 있습니다.
5. 채팅은 10초 집계(동일 작성자 반복 메시지 제한), 주변 중앙값 대비 급증 후보. 음성은 1초 RMS와 주변 30초 비교. 둘 다 하이라이트 확정이 아님.
6. 겹치는 후보를 제한적으로 병합하고 대본 의미 후보와 합침. 정밀한 같은 사건 판별·자동 맥락 확장은 후속 버전 범위.
7. 확인용 클립, 선택적 Gemini/assistant 검토, 브라우저 검수 페이지. 페이지에서 내보낸 선택은 아직 편집기로 자동 전달되지 않음.
8. 수동/assistant 편집안 또는 작업실의 후보·전사 기반 초안을 편집기에서 수정한 뒤 세로 영상·ASS/SRT·비교 페이지 생성. 제목·컷·자막을 자동 확정하거나 게시하지 않음.

```text
runs/demo/
  source.*                  원본 (전체)
  job.json                  분석 범위·싱크 상태
  chat-pages/               원본 API 응답 (재시작용)
  chats.json                정규화 채팅
  chat-status.json           수집 완료/실패 기록
  asr/                      조각별 음성·전사 캐시
  transcript.json           원본 시간 기준 단어 대본
  benchmark.json            처리 시간
  chat-bins.json            채팅 반응
  audio-levels.json         음량 변화
  text-candidates.json      Gemini 또는 assistant의 대본 후보 (선택)
  text-analysis.json        현재 대본 분석의 출처·범위 (선택)
  candidates.json           통합 후보
  clips/                    확인용 영상
  assistant/                임시 assistant 분석 자료·응답 (선택)
    text/<packet_id>/       대본 창·지시문·응답 양식
    review/<packet_id>/     표본 프레임·대본·채팅·응답 양식
  reviews/                  Gemini 또는 assistant 검토 (선택)
  report.html               검수 페이지
  edit-plans/               세로 편집안 JSON (사용자가 지정한 위치)
  shorts/                   render 출력 (사용자가 지정한 위치)
    <slug>.mp4              제목·자막·로고를 넣은 세로 영상
    <slug>.ass              영상에 입힌 자막
    <slug>.ko.srt           별도 한국어 자막
    <slug>.json             해당 영상의 편집안·렌더 정보
    manifest.json          렌더 결과 목록
    index.html             --gallery로 생성한 비교 페이지
```

로그인 필요한 영상은 해당 권한을 가진 계정의 Netscape 쿠키 파일을 `ingest/chats --cookies 경로`로 지정할 수 있습니다. 쿠키 파일을 저장소 안에 보관하지 마세요. 접근 불가능한 영상의 권한을 우회하지 않습니다.

로컬 파일로 시작할 수도 있습니다:

```powershell
& $demo ingest --local-video 'D:\recordings\vod.mp4' --duration 7200 --job runs/local
& $demo transcribe --job runs/local
& $demo signals --job runs/local --allow-no-chat
```

## 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

시간축, 겹친 음성 조각의 소유 범위, 채팅 페이지 재개/중복/오류, 긴 분석 범위와 다운로드 화질 선택, 후보 병합, AI 시간 검증, assistant 패킷 가져오기·원본 프레임 검토·후보 선택, 검수 HTML 이스케이프를 테스트합니다. 실제 영상 다운로드·GPU 추론·Gemini API는 해당 PC와 테스트 URL에서 별도로 실행해야 합니다. 의존성은 호환 범위로 지정했으며, 실제 GPU 검증 후 `pip freeze` 결과를 별도 lock 파일로 보관하는 것을 권장합니다.

렌더 테스트는 구간·자막 검증에 더해 합성 영상을 실제로 편집해 장면 순서, 출력 픽셀, 자막 시각, 실패 시 기존 파일 보존을 확인합니다. FFmpeg/ffprobe·Pillow·사용 가능한 글꼴이 없으면 실제 렌더 테스트는 건너뛰므로 테스트 출력의 `skipped` 여부도 확인하세요.

편집기 테스트는 실제 로컬 HTTP 서버로 세션 토큰·Host/Origin 검사, 영상 바이트 범위 전송, 경로 제한, 폰트 추가·검증, 저장 후 재시작, 입력 파일 보존과 비동기 렌더 상태를 확인합니다. 라이브러리에서는 영상 선택 검증과 영상별 저장·미디어 분리도 확인합니다. 이 서버 테스트의 원본 검사·인코딩은 대체하며, 실제 영상 합성은 별도 렌더 테스트에서 확인합니다. Pillow와 테스트용 글꼴이 없으면 해당 편집기 통합 테스트도 건너뜁니다.

작업실 테스트는 기존 작업 등록·자료 재사용, 기본 설정 저장, 실패·재시도·재시작 상태, 수동 싱크 확인, Gemini 동의, assistant 준비 대기와 실제 결과 검증, 후보 0개 처리, 편집 초안 등록을 확인합니다. 편집기에서는 제목·자막·컷 저장 및 복원, 원본 시각과 검증 실패 시 입력 보존, 저장하지 않은 미리보기의 시간축을 검사합니다. 작은 합성 자료와 대체 실행을 이용한 테스트이며 장시간 실제 방송 전체의 분석·시청·청취 검증을 대신하지 않습니다.

참고 구현/문서: [yt-dlp CHZZK](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/chzzk.py), [VOD 채팅 구조](https://github.com/dudska12/chzzk-chat-report/blob/main/src/vod-chat.ts), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Google GenAI Python SDK](https://github.com/googleapis/python-genai).
