# 배포/공유 가이드

## 1. 디스코드 웹훅 연결

환경변수 `DISCORD_WEBHOOK_URL`을 설정하면 참석/제외 이벤트가 디스코드로 전송됩니다.

```bash
export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'
python3 app.py
```

`.env.example`를 참고해서 환경변수를 관리할 수 있습니다.

## 2. 임시 공개 링크

로컬 서버를 켠 뒤 터널 서비스를 사용해 외부 공개 링크를 만들 수 있습니다.

```bash
python3 app.py
```

## 3. Render 배포

이 폴더를 GitHub 저장소로 올린 뒤 Render에서 새 Web Service를 생성합니다.

- Build Command: `pip install -r requirements.txt`
- Start Command: `python3 app.py`
- Environment Variable: `DISCORD_WEBHOOK_URL`
- Python Version: `3.14.3`

`render.yaml`도 포함되어 있어 Blueprint 방식으로 배포할 수 있습니다.
