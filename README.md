# MusicMatch

Your smart lights follow the album color of whatever's playing on Spotify — phone, TV, PlayStation, Echo, or web.

**Still in the works.** Windows is the smoother path. Mac works, but first launch is blocked by Gatekeeper until you Control-click → Open.

![MusicMatch](screenshot.png)

Finds Magic Home, WiZ, LIFX, Govee, Yeelight, and Hue on your Wi-Fi. Close the window and it keeps going from the tray / menu bar.

## Download

| | |
|---|---|
| **Windows** | [MusicMatch-Windows.zip](https://github.com/Ibrahim-Al-Omran/MusicMatch/releases/latest/download/MusicMatch-Windows.zip) — unzip, open `MusicMatch.exe` |
| **Mac (Apple Silicon)** | [MusicMatch-macOS.zip](https://github.com/Ibrahim-Al-Omran/MusicMatch/releases/latest/download/MusicMatch-macOS.zip) — unzip, drag to Applications |
| **Source** | [ZIP](https://github.com/Ibrahim-Al-Omran/MusicMatch/archive/refs/heads/main.zip) or clone below |

Apps aren't signed yet. Windows: SmartScreen → **More info → Run anyway**. Mac: Control-click the app → **Open**. Allow local network / private firewall when asked.

## Setup

The computer running MusicMatch just needs to be on the **same Wi-Fi as your lights**.

1. **Lights** — first launch searches the network. Switch off any you don't want. Hue: press the bridge button when asked. Govee / Yeelight: turn on **LAN Control** in their apps.
2. **Spotify** — **Keys → Create an app on Spotify** (the account needs Premium). Set the Redirect URI MusicMatch shows (`http://127.0.0.1:8888/callback`), tick **Web API**, paste the Client ID and secret, **Connect**. Add other household accounts under User Management (max 5).
3. **Play** something on Spotify. Lights follow within about a second.

## From source

Python 3.10+. Clone, then double-click `Start MusicMatch.cmd` (Windows) or `Start MusicMatch.command` (Mac).

```bash
git clone https://github.com/Ibrahim-Al-Omran/MusicMatch.git
cd MusicMatch
```

On Windows that also adds MusicMatch to the Start menu. `python -m musicmatch uninstall` removes it.
