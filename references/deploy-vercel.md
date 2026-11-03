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
  publication-manifest.json # 공개 산출물 목록·출처·해시
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

흐름: 매일 09:00 스케줄(주말·공휴일은 러너가 스킵, 2026-08-21 05:00→09:00 변경) → 브리핑·다이제스트 생성 → 공개본 site/ 갱신 → git push → Vercel 자동 재배포.
미설정 시 이 단계는 조용히 생략되어 기존 동작에 영향 없음.

## 안전 설계 메모

- `references/publication-contract.json`을 공개 구조 정본으로 사용한다. 입력 순서, 데이터가 있는 최대 3개 섹션의 상대 순서,
  반응형 열, 기사 유형 순서, 공개 안전 표기가 계약과 다르면 게시 빌드가 실패한다.
- 웹 배포본은 `--public` 렌더라 개인 메모·관심업무·관심 키워드 통계가 **포함되지 않는다**.
- 공식 CI를 쓰되 "비공식·개인 정리용" 표기와 하단 고지로 공식 발행물 오인을 방지한다.
- 전체 개인 버전이 필요하면 로컬에서 `digest --format html`(--public 없이)로 별도 생성.

## 2026-11-04 안정성 변경

- 신규 날짜 생성과 `--rebuild-index` 모두 기존 archive의 공개 모드·고지·개인용 요소를 검사한다.
  공개 모드 마커가 없거나 manifest의 기록과 다른 파일은 자동 승인하지 않는다.
- 모든 페이지를 먼저 준비·검증한 뒤 교체한다. 교체 중 오류는 기존 파일로 복구한다.
- 배포는 manifest에 기록된 생성 파일만 스테이징한다. 무관한 기존 staged 변경이 있으면 커밋하지 않는다.
- 새 HTML은 `edu-news-mode=public` 메타데이터로 공개본을 구분한다. 구형 archive는 아래의 명시 이관으로
  기존 HTML을 유지하면서 공개 안전 검사와 해시 기록을 적용할 수 있다. 마커를 수동으로 추가하지 않는다.
- 상류 러너의 CLI 순서는 유지된다. 구형 archive가 검토·이관되지 않았거나 검토 후 내용이 변하면
  게시 단계가 의도적으로 실패한다. 새 날짜는 현행 레이아웃 계약을 적용하고 검토된 과거 호의 레이아웃은 보존한다.
- 이번 코드 변경이나 Git push만으로 운영 DB 이관·기존 사이트 재생성·예약 실행이 검증되는 것은 아니다.

### 구형 공개본 이관

1. `publish_site.py --site-dir <site폴더> --adopt-legacy-public --dry-run`으로 후보를 만든다.
   이 단계는 파일을 쓰지 않는다. 모든 보관본·최신 화면·지난 호 목록의 공개 고지, 구 생성기 구조,
   개인용 요소·스크립트·자동 이동·외부 임베드와 링크를 검사한다.
2. 반환된 `files`의 경로·해시와 검토 대상을 대조한다. 프로그램으로 적용할 때는
   `adopt_legacy_public(site, dry_run=False, expected_files=candidate['files'])`를 사용하면
   검토 이후 파일 변경도 차단한다. CLI 적용 전에도 후보를 다시 확인한다.
3. 명시적으로 적용하면 `publication-manifest.json`만 생성한다. 기존 HTML은 바꾸지 않는다.
   이후 일반 빌드는 해당 legacy 파일의 기록된 해시와 안전 검사를 모두 통과해야 허용한다.
4. 안전 검사를 통과하지 못한 자료는 이관하지 않는다. 검증된 원본으로 새 사이트 폴더에서 날짜 전체를
   재생성·검토한 뒤 원래 사이트 교체 범위를 별도로 확정한다.
