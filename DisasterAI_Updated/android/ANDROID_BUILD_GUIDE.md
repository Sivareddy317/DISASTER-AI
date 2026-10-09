# DisasterAI — Android APK Build Guide

## Prerequisites
- Android Studio (Giraffe 2022.3.1 or newer)
- JDK 17 (bundled with Android Studio)
- Android SDK API 34

---

## STEP 1: Deploy Backend First (Required)

Your APK is a WebView — it needs the Python backend running online.

### Option A: Railway (Recommended — Free tier)
1. Go to https://railway.app → Sign up with GitHub
2. Click "New Project" → "Deploy from GitHub repo"
3. Push your DisasterAI folder to a GitHub repo first
4. Railway auto-detects Python → configure:
   - Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
   - Add environment variables in Railway dashboard:
     - `DATABASE_URL` = `sqlite:///./disaster.db`
     - `CORS_ORIGINS` = `*`
     - `AI_PROVIDER_API_KEY` = your Anthropic key (optional)
     - `APP_API_KEY` = pick a random secret string
5. Copy your Railway URL: `https://disasterai-xxxx.railway.app`

### Option B: Render (Free tier)
1. Go to https://render.com → New → Web Service
2. Connect GitHub repo
3. Runtime: Python 3 | Build: `pip install -r requirements.txt`
4. Start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`

### Option C: Local testing (same WiFi only)
- Run: `uvicorn backend.main:app --host 0.0.0.0 --port 8000`
- Find your PC's IP: `ipconfig` (Windows) or `ifconfig` (Mac/Linux)
- Set BACKEND_URL to `http://192.168.X.X:8000` in MainActivity.java

---

## STEP 2: Update BACKEND_URL in MainActivity.java

Open: `android/app/src/main/java/com/disasterai/app/MainActivity.java`

Change line 28:
```java
// BEFORE
private static final String BACKEND_URL = "https://YOUR-APP.railway.app";

// AFTER (example)
private static final String BACKEND_URL = "https://disasterai-abc123.railway.app";
```

---

## STEP 3: Open Project in Android Studio

1. Launch Android Studio
2. File → Open → select the `android/` folder inside DisasterAI_Updated
3. Wait for Gradle sync to complete (first time takes 2–5 minutes, downloads SDK)
4. If prompted: "Install missing SDK components" → click OK

---

## STEP 4: Build Debug APK

### Via menu:
Build → Build Bundle(s) / APK(s) → Build APK(s)

### Via terminal (inside android/ folder):
```bash
# Windows
gradlew.bat assembleDebug

# Mac / Linux
./gradlew assembleDebug
```

APK output location:
`android/app/build/outputs/apk/debug/app-debug.apk`

A notification will appear: "APK(s) generated → locate"

---

## STEP 5: Install on Android Phone

### Via ADB (USB cable):
```bash
# Enable Developer Options on phone: Settings → About Phone → tap Build Number 7 times
# Enable USB Debugging in Developer Options
adb install android/app/build/outputs/apk/debug/app-debug.apk
```

### Via file transfer (easier):
1. Copy `app-debug.apk` to your phone (USB, WhatsApp, Google Drive, etc.)
2. On phone: Settings → Apps → Install unknown apps → allow your file manager
3. Tap the APK file → Install

---

## STEP 6: Build Release APK (For sharing / Play Store)

### Create signing keystore (one-time):
Build → Generate Signed Bundle / APK → APK → Create new keystore
- Fill in alias, password, name, country
- Save the `.jks` file somewhere safe — you need it for every update

### Then build:
Build → Generate Signed Bundle / APK → APK → Release → Finish

Release APK: `android/app/build/outputs/apk/release/app-release.apk`

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Gradle sync fails | File → Invalidate Caches → Restart |
| White/blank screen | Check BACKEND_URL in MainActivity.java |
| "Connection refused" | Backend is not running — deploy to Railway |
| HTTP on HTTPS page | Already handled: usesCleartextTraffic=true in Manifest |
| App crashes on open | Check logcat: View → Tool Windows → Logcat |
| SDK not found | SDK Manager → install API 34 |

---

## App Behavior

- Opens DisasterAI dashboard as a full-screen app
- Back button navigates browser history (like a browser)
- Progress bar shows loading state
- Error screen with Retry button if backend is unreachable
- No title bar — fullscreen immersive mode
- Portrait orientation locked (change in Manifest if you want landscape)
