# NAS Discord Relay

이 파일은 Render 웹앱의 디스코드 알림을 NAS에서 중계하기 위한 설정 메모입니다.

## 역할

- Render 웹앱 -> NAS relay -> Discord
- `discord_bot.py`는 닉네임 패널용 봇이고, 알림 중계는 [discord_relay.py](/Users/taylorkim/Documents/디스코드/discord_relay.py) 가 담당합니다.

## NAS Container Manager

이미지 빌드 대상:

- [Dockerfile.relay](/Users/taylorkim/Documents/디스코드/Dockerfile.relay)

환경변수:

- `DISCORD_BOT_TOKEN` = 디스코드 봇 토큰
- `DISCORD_CHANNEL_ID` = 알림을 보낼 채널 ID
- `DISCORD_RELAY_SECRET` = Render와 NAS가 공유할 임의의 비밀 문자열
- `PORT` = `8011`

포트:

- 컨테이너 `8011` 노출

헬스체크:

- `GET /healthz`

알림 엔드포인트:

- `POST /notify`

## Render 설정

Render 웹앱 환경변수에 아래 값을 넣습니다.

- `DISCORD_RELAY_URL` = `https://<NAS에서-외부노출한-주소>/notify`
- `DISCORD_RELAY_SECRET` = NAS와 동일한 비밀 문자열

선택:

- `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_WEBHOOK_URL` 은 Render에서 제거하거나 비워두는 것을 권장

## 주의

- NAS가 외부에서 접근 가능해야 합니다.
- Cloudflare Tunnel, reverse proxy, 포트포워딩 중 하나가 필요합니다.
- 토큰과 secret은 절대 공개하지 않습니다.
