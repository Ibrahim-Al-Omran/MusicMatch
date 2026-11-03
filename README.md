# MusicMatch

Your smart lights follow the album color of whatever's playing on Spotify — phone, TV, PlayStation, Echo, or web.

**Still in the works.** Downloads are available for Windows and Apple Silicon Macs.

![MusicMatch](screenshot.png)

Finds Magic Home, WiZ, LIFX, Govee, Yeelight, and Hue on your Wi-Fi. Close the window and it keeps going from the tray / menu bar.

## Download

| | |
|---|---|
| **Windows** | [MusicMatch-Windows.zip](https://github.com/Ibrahim-Al-Omran/MusicMatch/releases/latest/download/MusicMatch-Windows.zip) — unzip, open `MusicMatch.exe` |
| **Mac (Apple Silicon)** | [MusicMatch-macOS.zip](https://github.com/Ibrahim-Al-Omran/MusicMatch/releases/latest/download/MusicMatch-macOS.zip) — unzip, drag to Applications |
| **Source** | [ZIP](https://github.com/Ibrahim-Al-Omran/MusicMatch/archive/refs/heads/main.zip) or clone below |

The Mac download is Developer ID signed and notarized by Apple. Windows is unsigned: SmartScreen → **More info → Run anyway**. Allow local network / private firewall when asked.

These download links automatically serve the newest successful main build. Both apps must finish building, including Mac notarization, before a new release replaces the previous downloads.

## Setup

The computer running MusicMatch just needs to be on the **same Wi-Fi as your lights**.

1. **Lights** — first launch searches the network. Switch off any you don't want. Hue: press the bridge button when asked. Govee / Yeelight: turn on **LAN Control** in their apps.
2. **Spotify** — **Keys → Create an app on Spotify** (the account needs Premium). Set the Redirect URI MusicMatch shows (`http://127.0.0.1:8888/callback`), tick **Web API**, paste the Client ID and secret, **Connect**. Add other household accounts under User Management (max 5).
3. **Play** something on Spotify. Lights follow within about a second.

The window is resizable on Mac and Windows. In a smaller window, use the scrollbars or mouse wheel / trackpad to reach all controls; Shift-scroll moves horizontally.

MusicMatch checks for updates in the background when opened and daily while the window stays running, with network checks limited to once every 24 hours. When a newer release has a ZIP ready for your platform, a banner offers **Download**. This opens the ZIP in your browser; it does not install or replace the app automatically. Offline or failed checks are silent.

Every push to main builds both apps in GitHub Actions, including signing and notarizing the Mac app. After both builds succeed, the workflow assigns a new version and publishes both ZIPs together as the latest GitHub Release. The download links above and in-app update checks then use that release. Builds are also available in the run's **Artifacts** section. CI embeds the chosen release version in both apps; the source VERSION file sets the minimum next version. Manual runs on other branches create artifacts only.

### Lights not found?

- Keep the computer and lights on the same home network, not an isolated guest network.
- A VPN can block local discovery. Try disconnecting it and clicking **Find** again, or enable its **Allow LAN / local network** option if available. MusicMatch does not change your VPN settings.
- On Mac, enable **MusicMatch** in **System Settings > Privacy & Security > Local Network**, then quit and reopen MusicMatch if needed.
- Govee and Yeelight need **LAN Control** enabled in their own apps. For Hue, press the bridge button when prompted.

## From source

Python 3.10+. Clone, then double-click `Start MusicMatch.cmd` (Windows) or `Start MusicMatch.command` (Mac).

```bash
git clone https://github.com/Ibrahim-Al-Omran/MusicMatch.git
cd MusicMatch
```

On Windows that also adds MusicMatch to the Start menu. `python -m musicmatch uninstall` removes it.
