# Fantasy Agent user guide

Fantasy Agent answers questions about your Sleeper fantasy football league. It only reads your data. In this version it never makes a move in Sleeper. You make every lineup change, add, drop and trade yourself in Sleeper.

You need a Windows 10 or 11 computer, an internet connection and a Sleeper account.

## 1. Install on Windows

1. Download the installer: `FantasyAgent-Setup-<version>.exe` from [OWNER: download link or location].
2. Double-click the file. It does not need administrator rights.
3. Follow the installer. Tick "Create a desktop shortcut" if you want one.
4. Leave "Start Fantasy Agent" ticked on the last page and click Finish.

The app installs in `%LOCALAPPDATA%\Programs\FantasyAgent`. Your settings, data and log file are in `%LOCALAPPDATA%\FantasyAgent`. To open these folders, paste the path into the Windows Explorer address bar.

The app needs the Microsoft WebView2 runtime. Windows 10 and 11 normally have it already. If the app opens a blank window or does not open, install "Evergreen Standalone Installer" from Microsoft's WebView2 page, then start the app again. [OWNER: confirm this on a clean machine in Phase 3.3.]

[OWNER: if Windows shows a "Windows protected your PC" warning on first run, say here what to click, for example "More info", then "Run anyway".]

To start the app later, use the Start menu entry "Fantasy Agent" or the desktop shortcut.

## 2. Get an OpenRouter key

The app uses an AI model through a service called OpenRouter. You need your own free account and key.

1. Open https://openrouter.ai in your browser.
2. Create an account and sign in.
3. Open https://openrouter.ai/keys.
4. Create a new key. Give it any name, for example "Fantasy Agent".
5. Copy the key right away. OpenRouter may show it only once.

The app stores the key on this computer only. The app never shows it again after you save it.

### Free and paid models

- A free model costs nothing. It has a daily limit of about 50 requests on a free account.
- One question uses about 2 requests. So you can ask about 25 questions a day.
- A paid model has no such daily limit. It uses credits that you buy on your OpenRouter account.
- The limit is on OpenRouter's side, not in the app.

## 3. First setup

The first time you open the app, the "Set up Fantasy Agent" screen appears. It has four steps.

1. **Sleeper username.** Type your Sleeper login name, not a league ID. Click "Find leagues".
2. **League.** Pick your league. If you are in only one league that the app supports, it is already picked. Leagues the app cannot use are greyed out with a reason, for example "Dynasty league. Season-long redraft only." This version supports season-long redraft leagues only.
3. **OpenRouter key.** Paste the key into the "Key" box.
4. **Model.** Pick a model. Choose the one marked "Recommended" unless you have a reason not to.

Click "Save and start". The app checks the key. If it works, the chat opens.

You can change any of this later: click "Settings" at the top right. In Settings, "Replace key" swaps the key and "Remove key" deletes it. Click "Save" to keep your changes.

## 4. Ask questions

The chat is on the left. Type a question and press Enter. Press Shift+Enter for a new line. Click "Send" to send it with the mouse. You can also click an example question on the empty screen to fill the box.

A question can take a minute. A progress card shows the steps and a timer. You can keep reading the panel on the right while you wait.

Each answer names its sources and when they were fetched.

The panel on the right shows your fantasy team: record, rank, this week's matchup, and your starters, bench and IR. Click "Refresh" to get the latest data from Sleeper. Injury chips: Q questionable, D doubtful, OUT out, IR injured reserve.

Example questions:

- Is my lineup set for this week?
- Who are the best waiver pickups at RB?
- Which injured players should I stash?
- Would you trade [player A] for [player B]?
- Who is trending up on waivers in the last day?
- How did my matchup go in week [number]?
- Who leads the league in points at WR?
- What does the Seattle depth chart look like at WR?
- Which of my players are injured?

Use player names and NFL team names in your questions. Ask about one position or one player at a time for best results.

Other buttons:

- "Start fresh" (top bar) deletes the chat history for the current league. Your league and settings stay.
- If you are in several leagues, the league name at the top is a list. Pick another league to switch.

The app never changes anything in Sleeper. If you ask it to make a move, it tells you it cannot.

## 5. Troubleshooting

**"Daily limit reached for this model."**
You used the free requests for today. Wait, or click "Choose another model", or add credits to your OpenRouter account and pick a paid model. Your question is kept. Click "Ask again" later.

**"OpenRouter did not accept this key. Check that you copied all of it, or create a new one. Nothing was saved."**
The key was not copied fully, or it was deleted on OpenRouter. Paste it again, or create a new key at https://openrouter.ai/keys.

**"OpenRouter refused the saved key."**
Same cause, in the chat. Click "Open settings", replace the key, then click "Ask again".

**"Could not reach OpenRouter to check the key. Try again."**
Check your internet connection, then click "Save and start" again.

**"Could not refresh from Sleeper."**
The app could not reach Sleeper. The answer uses data stored earlier, and the message gives the time. A move you made in Sleeper after that time is not included. Check your internet connection, then click "Refresh" in the panel or ask again.

**"No Sleeper user named that. Check the spelling."**
Use your Sleeper login name, not your display name or a league ID.

**"Something went wrong, so there is no answer."**
Nothing was changed in Sleeper. Click "Ask again".

**Anything else**
The log file is `%LOCALAPPDATA%\FantasyAgent\fantasy-agent.log`. Send it to [OWNER: contact for support] with a short description.

## 6. For developers

Run the app in Docker instead of the desktop app:

1. Install Docker Desktop (or Docker Engine).
2. Run `scripts\start.bat` on Windows, or `scripts/start.sh` on Mac or Linux.
3. Open http://localhost:8080.
4. Run `scripts\stop.bat` or `scripts/stop.sh` to stop.

The port is open on this computer only. Data is kept in the `fantasy-data` Docker volume. See `README.md` for more.
