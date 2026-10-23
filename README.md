# AutoShorts — 치지직 하이라이트 분석 데모

치지직 다시보기 한 편을 받아 **지정 구간의 대본·채팅·음량을 분석하고, 후보를 검토한 뒤 세로 쇼츠 샘플을 만드는** Python 프로젝트입니다. 기본 분석 길이는 2시간이며 `--duration`으로 더 긴 구간을 지정할 수 있습니다. 한국어 방송, Windows + NVIDIA GPU를 대상으로 합니다.

현재는 **임시 assistant 분석 → 후보 검토 → JSON 편집안 → 9:16 영상·한국어 자막·비교 페이지**까지 지원합니다. Gemini 경로도 유지합니다. assistant가 자료를 읽고 후보와 편집안을 작성하는 과정이 필요하며, 명령 한 번으로 재미있는 장면을 골라 게시하는 서비스는 아닙니다. 분석 결과와 자막은 검수 초안입니다.

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
| `logo`, `font`, `font_name` | 로고 이미지, TTF 파일, 자막에 사용할 글꼴 이름 |
| `channel`, `title_lines` | 하단 채널명과 상단 제목 1~2줄 |
| `keep` | 남길 구간의 `[시작초, 끝초]` 목록. **원본 방송 시각**을 사용하고 시간 순서대로 배치 |
| `captions` | `start`, `end`, `text`를 가진 한국어 자막 목록. 시각은 **원본 방송 기준**. 선택 항목 `style`은 `Caption`(기본), `Emphasis`, `Punchline` 중 하나 |
| `slug` | 선택 항목. 출력 파일을 구분하는 영문 소문자·숫자·`_`·`-` 이름(첫 글자는 영문 소문자 또는 숫자, 최대 64자). 기본값 `short` |
| `crop` | 선택 항목. 원본 픽셀 기준 `x`, `y`, `width`, `height`. 모든 값은 짝수 정수이고 너비:높이는 4:3. 생략하면 원본 안에서 가장 큰 중앙 4:3 영역 |
| `context` | 선택 항목. `start`, `end`, `text` 목록으로 한 줄 편집 설명을 표시. 이 항목만 **완성 영상 시작부터의 상대 시각** 사용 |

`source_start`는 잘라 둔 파일의 시간축을 원본 시각에 맞추기 위한 값입니다. 채팅 싱크 오프셋과는 다릅니다. `keep`과 자막에서 이를 미리 빼지 마세요. 각 자막은 `keep`의 한 구간 안에 완전히 들어가야 하며, 컷을 가로지르는 자막은 편집안에서 나눠야 합니다. 렌더러가 제외 구간을 빼고 출력 영상의 자막 시각을 계산하며, 원본 오디오를 사용합니다. ASS 자막을 영상에 입히고 별도 SRT도 저장합니다. 자막 문구를 고치거나 구간을 잇는 판단은 작성한 편집안에 따릅니다.

여러 편을 같은 폴더에 렌더링하려면 편마다 다른 `slug`를 지정하고 아래처럼 순서대로 실행합니다. `slug`를 생략한 채 반복하면 기본 이름 `short`의 결과를 갱신하므로 주의하세요.

```powershell
& $demo render --plan runs/demo/edit-plans/candidate-01.json --output runs/demo/shorts --gallery
& $demo render --plan runs/demo/edit-plans/candidate-02.json --output runs/demo/shorts --gallery
& $demo render --plan runs/demo/edit-plans/candidate-03.json --output runs/demo/shorts --gallery
& $demo render --plan runs/demo/edit-plans/candidate-04.json --output runs/demo/shorts --gallery
```

`runs/demo/shorts/index.html`에서 각 영상을 재생하고 MP4·자막을 비교할 수 있습니다. `manifest.json`은 같은 출력 폴더의 렌더 결과를 모으며, `--gallery`는 그 목록 전체의 비교 페이지를 갱신합니다. 같은 `slug`로 다시 렌더링하면 해당 결과를 갱신하고, 렌더 실패 시 기존 결과를 보존합니다. 결과 프레임의 글자 잘림·크롭, 오디오와 자막, 특히 마지막 발화가 중간에 잘리지 않는지를 확인하세요. 이 명령은 영상 업로드나 게시를 수행하지 않습니다.

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

`text-candidates`는 **전체 분석 구간의 대본**을 10분+문맥 겹침 단위로 Google API에 전송합니다. 채팅이 없는 토크도 후보에 들어옵니다. `review-video`는 생성된 후보 클립을 Google Files API에 업로드하여 영상 내용을 확인하고 삭제를 시도합니다. 두 명령만 외부 AI를 호출합니다. 자동 실행 스크립트에서는 `-WithAI`로 두 단계를 켤 수 있습니다. API 키와 모델이 없는 상태에서 API 단계는 중단됩니다.

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
8. 수동 또는 assistant가 작성한 JSON 편집안으로 세로 영상·ASS/SRT·비교 페이지 생성. 제목·컷·자막을 자동 확정하거나 게시하지 않음.

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

참고 구현/문서: [yt-dlp CHZZK](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/chzzk.py), [VOD 채팅 구조](https://github.com/dudska12/chzzk-chat-report/blob/main/src/vod-chat.ts), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Google GenAI Python SDK](https://github.com/googleapis/python-genai).
