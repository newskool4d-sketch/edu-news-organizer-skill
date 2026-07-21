# 정적 사이트 배포 가이드 (A안: 로컬 생성 → Vercel 자동 배포)

> 생성은 로컬(윈도 스케줄러)에서, 산출물만 웹에 올린다. 웹에는 **공개 안전본**만 올라간다
> — 개인 데이터(내 관심업무·메모) 제외 + "비공식·개인 정리용" 표기. 전체 개인 버전은 로컬에 그대로.

## 무엇이 올라가나

`publish_site.py`가 만드는 `site/` 폴더:

```
site/
  index.html              # 최신 날짜 공개 다이제스트
  archive/YYYY-MM-DD.html  # 날짜별 보관본
  archive/index.html       # 지난 호 목록
```

전부 자기완결 정적 HTML(로고 임베드, 외부 리소스 0). 서버·DB·빌드 불필요.

## 수동 빌드 (먼저 확인용)

```powershell
python ~/.codex/skills/edu-news-organizer/scripts/publish_site.py `
  --site-dir "$env:USERPROFILE\Documents\Codex\EduNewsSite" --date 2026-07-16
```

`site/index.html`을 브라우저로 열어 확인. 여러 날짜를 반복하면 아카이브가 쌓인다.

## 일회성 배포 설정 (사용자가 직접 — 계정 작업)

1. **site 폴더를 git 저장소로**:
   ```powershell
   cd "$env:USERPROFILE\Documents\Codex\EduNewsSite"
   git init -b main; git add -A; git commit -m "init"
   ```
2. **GitHub 저장소 생성**(private 권장) 후 remote 연결·push:
   ```powershell
   gh repo create edu-news-site --private --source=. --push
   ```
3. **Vercel 연결**: [vercel.com](https://vercel.com) 로그인 → Add New Project → 위 GitHub 저장소 Import →
   Framework Preset은 **Other**(정적) → Deploy. 빌드 명령·출력 디렉터리 설정 없이 그대로 배포됨.
   (Vercel Hobby는 URL을 아는 누구나 접근 가능. 접근 제어가 필요하면 Vercel Pro의 Password Protection
    또는 Cloudflare Access 무료 티어를 검토.)

## 매일 자동 갱신

러너(`run-daily-news-picker.ps1`)가 브리핑 생성 후 자동으로 공개 안전본을 굽고 push한다.
아래 환경변수를 **사용자 환경변수**로 등록하면 활성화:

- `EDU_NEWS_SITE_DIR` = `%USERPROFILE%\Documents\Codex\EduNewsSite` (site 폴더 = git 저장소 경로)
- `EDU_NEWS_SITE_PUSH` = `1` (git push까지 자동. 생략하면 빌드만 하고 push는 수동)

흐름: 평일 05:00 스케줄 → 브리핑·다이제스트 생성 → 공개본 site/ 갱신 → git push → Vercel 자동 재배포.
미설정 시 이 단계는 조용히 생략되어 기존 동작에 영향 없음.

## 안전 설계 메모

- 웹 배포본은 `--public` 렌더라 개인 메모·관심업무·관심 키워드 통계가 **포함되지 않는다**.
- 공식 CI를 쓰되 "비공식·개인 정리용" 표기와 하단 고지로 공식 발행물 오인을 방지한다.
- 전체 개인 버전이 필요하면 로컬에서 `digest --format html`(--public 없이)로 별도 생성.
