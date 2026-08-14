# Launchpad demo video

Records a short Chromium walkthrough of the local app and writes an MP4.

## Prerequisites

- `make web` and `make api` running (`http://localhost:3000`)
- A user that can log in with email/password (register once if needed)
- `ffmpeg` on PATH

## Record

```bash
cd scripts/demo-video
npm install
npx playwright install chromium
DEMO_EMAIL=you@example.com DEMO_PASSWORD='your-password' node record.mjs
```

Output: `out/launchpad-end-to-end-demo.mp4`
