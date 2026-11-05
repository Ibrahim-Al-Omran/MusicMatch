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

### Windows SmartScreen and Defender warnings

The Windows download is free and currently **unsigned**; no paid Windows code-signing certificate is used. Microsoft Defender may flag the ZIP or `MusicMatch.exe` **while the download finishes** (often `Behavior:Win32/Persistence.A!ml`). That is a machine-learning false positive common to unsigned PyInstaller apps, not evidence of malware in this repository.

If you downloaded `MusicMatch-Windows.zip` from this repository's GitHub Release and trust it:

1. In Windows Security, allow/restore the MusicMatch download or add an exclusion for that ZIP/folder.
2. On first launch, if SmartScreen shows **Windows protected your PC**, click **More info → Run anyway**.

Keep SmartScreen and antivirus enabled. These steps only allow this download on your computer. A future Authenticode-signed Windows build would be the lasting fix for reputation warnings.

## Setup

The computer running MusicMatch just needs to be on the **same Wi-Fi as your lights**.

1. **Lights** — first launch searches the network. Switch off any you don't want. Hue: press the bridge button when asked. Govee / Yeelight: turn on **LAN Control** in their apps.
2. **Spotify** — **Keys → Create an app on Spotify** (the account needs Premium). Set the Redirect URI MusicMatch shows (`http://127.0.0.1:8888/callback`), tick **Web API**, paste the Client ID and secret, **Connect**. Add other household accounts under User Management (max 5).
3. **Play** something on Spotify. Lights follow within about a second.

The window is resizable on Mac and Windows. The dashboard uses the available window width, including maximized and ultrawide screens, without a fixed aspect ratio or maximum content width. It stacks into one column in smaller windows and remains scrollable when the content is taller than the window. Resizing uses lightweight previews, with detailed artwork refreshed in the background after resizing stops. Use the scrollbars or mouse wheel / trackpad to reach all controls; Shift-scroll moves horizontally in very narrow windows.

**Connect opens your default browser on purpose:** sign in to Spotify and click **Agree**. Leave MusicMatch running; the browser will show a return-to-MusicMatch message and the app will say **Connected as ...**. You can then close the tab. If Spotify shows a redirect-URI error, register exactly the URI shown in MusicMatch in your Spotify app's settings (use 127.0.0.1, not localhost). Login waits up to three minutes; if it expires, click **Connect** again. If the callback port is already in use, quit any other copy of MusicMatch before retrying.

MusicMatch checks for updates in the background when opened and daily while the window stays running, with network checks limited to once every 24 hours. When a newer release has a ZIP ready for your platform, a banner offers **Download**. This opens the ZIP in your browser; it does not install or replace the app automatically. Offline or failed checks are silent.

Every push to main builds both apps in GitHub Actions, including signing and notarizing the Mac app. After both builds succeed, the workflow assigns a new version and publishes both ZIPs together as the latest GitHub Release. The download links above and in-app update checks then use that release. Builds are also available in the run's **Artifacts** section. CI embeds the chosen release version in both apps; the source VERSION file sets the minimum next version. Manual runs on other branches create artifacts only.

### Brightness and color boost

Song changes keep each powered-on light's current brightness, including adjustments made in its own app or with a voice assistant. The Brightness slider explicitly changes that level; the saved level is also used when MusicMatch turns an off light on. Off lights stay off unless auto turn-on is enabled.

Color boost changes saturation, not the brightness setting. Highly saturated colors can still look less bright than white or pastel colors at the same level; this is separate from brightness being reset on a song change.

If you switch from running the source checkout to a downloaded app, quit the old source tray/menu-bar service first. Source runs store settings and their instance lock in the checkout; downloaded apps use the per-user MusicMatch folder (%APPDATA%/MusicMatch on Windows, ~/Library/Application Support/MusicMatch on Mac). Both can otherwise run at once and send conflicting brightness and color settings to the same lights. If you enabled start at login for the source version, turn it off there, then enable it in the downloaded app so the login item points at the right copy.

### Lights not found?

- Keep the computer and lights on the same home network, not an isolated guest network.
- On Windows, allow MusicMatch through Windows Firewall on **Private** networks when prompted. Do not disable the firewall. Automatic discovery only supports the Wi-Fi/LAN-capable models of the brands listed above; Bluetooth-only bulbs cannot be discovered this way.
- A VPN can block local discovery. Try disconnecting it and clicking **Find** again, or enable its **Allow LAN / local network** option if available. MusicMatch does not change your VPN settings.
- On Mac, discovery also sends on active IPv4 LAN interfaces rather than relying only on the default route. This is VPN-provider-neutral, but cannot override a VPN or firewall that blocks LAN traffic. Actual discovery socket errors now appear in the app instead of being silently treated as no lights found.
- On Mac, enable **MusicMatch** in **System Settings > Privacy & Security > Local Network**, then quit and reopen MusicMatch if needed.
- Govee and Yeelight need **LAN Control** enabled in their own apps. For Hue, press the bridge button when prompted.

## From source

Python 3.10+. Clone, then double-click `Start MusicMatch.cmd` (Windows) or `Start MusicMatch.command` (Mac).

```bash
git clone https://github.com/Ibrahim-Al-Omran/MusicMatch.git
cd MusicMatch
```

On Windows that also adds MusicMatch to the Start menu. Start at login uses a Startup-folder shortcut (not a hidden registry Run key). `python -m musicmatch uninstall` removes the shortcuts.
