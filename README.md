# AutoShorts — 치지직 2시간 분석 데모

치지직 다시보기 한 편을 받아 **최대 2시간을 분석하고, 쇼츠 후보 목록·확인용 클립·검수 페이지**를 만드는 Python 프로젝트입니다. 한국어 방송, Windows + RTX 5080 16GB + RAM 64GB를 목표로 합니다.

**현재 범위는 파이프라인 1~3번입니다.** 완성 쇼츠의 자동 세로 편집·강조 자막·게시 기능은 아직 없습니다. 원본을 보존하며, 분석용 클립은 가로 480p 프록시입니다. 자동 결과는 검수 초안이며 사람이 승인한 결과가 아닙니다.

## 다른 PC에서 시작

필요: Git, Python **3.11**, FFmpeg/ffprobe(PATH에 등록), NVIDIA 드라이버, 다운로드/원본용 디스크 공간. FFmpeg 배포 안내: https://ffmpeg.org/download.html

PowerShell:

```powershell
git clone https://github.com/bbakhyun1819/AutoShorts.git
cd AutoShorts
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

설치 스크립트는 가상 환경에 Python 패키지와 CUDA 12 런타임을 설치합니다. 시스템 PATH나 드라이버는 바꾸지 않습니다. FFmpeg는 별도로 설치해야 합니다. 처음 ASR 실행 때 모델 다운로드가 발생합니다. 실패하면 `doctor` 출력과 DLL 오류를 확인하세요. 실제 GPU 작동은 전사 실행으로 검증해야 하며 설치 여부만으로 확인되지 않습니다.

```powershell
.\.venv\Scripts\chzzk-demo.exe doctor
```

## 한 번에 실행

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-demo.ps1 -Url 'https://chzzk.naver.com/video/영상번호'
```

- **영상 전체를 다운로드해 원본으로 보존**, 분석만 처음 7200초(2시간)로 제한합니다. 긴 방송은 다운로드·디스크 사용량이 2시간 분량보다 큽니다.
- 다른 구간은 `-Start 3600 -Duration 7200`처럼 **초 단위**로 지정합니다.
- 기본은 로컬 처리만 수행합니다. Gemini 키를 쓰지 않으면 채팅·음량 후보만 생성되며 토크 의미 분석과 영상 검증은 미실행 상태입니다.
- 채팅이 제공되지 않을 때만 `-AllowNoChat`으로 명시적으로 생략합니다.
- 같은 작업 폴더로 재실행하면 수집 페이지와 전사 조각을 재사용합니다. **다른 URL/분석 범위는 반드시 새 `-Job runs/다른이름`**을 지정하세요.
- 확인용 클립은 기본 10개만 생성합니다. 나머지 후보는 목록에서 삭제하지 않습니다. `-ClipLimit 30`으로 늘릴 수 있습니다.
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

싱크 확인은 자동으로 ‘완료’ 처리하지 않습니다. 고정 오프셋만 지원하며 중간 편집으로 시간축이 불연속인 영상은 수동 보완이 필요합니다. sync를 변경하면 signals → combine → clips → report를 다시 실행합니다.

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
7. 확인용 클립, 선택적 AI 검토, 브라우저 검수 페이지. 페이지에서 내보낸 선택은 아직 편집기로 자동 전달되지 않음.

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
  candidates.json           통합 후보
  clips/                    확인용 영상
  reviews/                  Gemini 검토 (선택)
  report.html               검수 페이지
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

시간축, 겹친 음성 조각의 소유 범위, 채팅 페이지 재개/중복/오류, 후보 병합, AI 시간 검증, 검수 HTML 이스케이프를 테스트합니다. 실제 2시간 영상·5080 추론·Gemini API는 해당 PC와 테스트 URL에서 별도로 실행해야 합니다. 의존성은 호환 범위로 지정했으며, 실제 GPU 검증 후 `pip freeze` 결과를 별도 lock 파일로 보관하는 것을 권장합니다.

참고 구현/문서: [yt-dlp CHZZK](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/chzzk.py), [VOD 채팅 구조](https://github.com/dudska12/chzzk-chat-report/blob/main/src/vod-chat.ts), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Google GenAI Python SDK](https://github.com/googleapis/python-genai).
