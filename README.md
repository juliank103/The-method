# Tweetus Deletus

Delete your own X (Twitter) posts in bulk. There are two ways to use it — pick
one:

### 🆓 Free — the browser version (recommended)

A little panel that runs in your own browser on x.com and deletes your posts
slowly, for free, with nothing to sign up for. Best for most people.

→ **[Set it up in the `userscript/` folder](userscript/README.md)** (about 2
minutes, works on any computer).

It deletes your posts, replies and reposts the same way you would by hand —
one at a time, slowly — so it's free and gentle. The trade-off is that it's
slower, and automating X is against X's rules, so go slow and start small.

### 💳 The desktop app (official, costs a little)

A polished app with filters, auto-delete and history that uses X's official
API. Rock-solid, but X charges a small per-delete fee to the developer account
behind it, so it isn't free. Details below.

![What should disappear?](docs/home.png)

## The desktop app

1. **Get the app**: the file someone sent you, or download it:
   - Mac: [Tweetus-Deletus-Mac.zip](https://github.com/juliank103/The-method/releases/latest/download/Tweetus-Deletus-Mac.zip)
   - Windows: [Tweetus-Deletus-Windows.exe](https://github.com/juliank103/The-method/releases/latest/download/Tweetus-Deletus-Windows.exe)
2. **Open it.** It opens in your web browser.
   - *Mac, first time only:* if macOS says it can't be opened, go to
     **System Settings → Privacy & Security**, scroll down, click
     **Open Anyway**, and confirm.
   - *Windows, first time only:* if you see “Windows protected your PC”, click
     **More info → Run anyway**.
3. **Click “Connect with X”**, sign in on X, and approve.
4. **Pick what should disappear**, for example “Old posts older than 1 year”.
5. **Look over the list.** Uncheck anything you want to keep, then click
   **Delete**.

That's it. Your pinned post is always kept, and a copy of everything you
delete is saved in **History**. Click **Quit** (top right) when you're done.

![Reviewing before deleting](docs/results.png)

### Other things it can do

- **Auto-delete:** keep only recent posts, e.g. delete anything older than 30
  days, checked every few hours while the app is open.
- **Custom search:** the link under the choices lets you pick exactly what to
  delete by date, words, hashtags, likes, photos or links, and what to keep.
- **Your X archive:** X only lets apps see your latest ~3,200 posts. For older
  ones, request your archive on X (Settings → Your account → Download an
  archive of your data), then add the .zip in a custom search.
- **History:** search everything you've deleted, or download it as a
  spreadsheet.

## One-time setup (for whoever sets it up)

X only allows deleting through its official API, which needs an X developer
app. You create it once, it gets built into the downloads, and from then on
people only ever see **Connect with X**.

1. Go to <https://developer.x.com>, sign in, and create a Project and an App.
   Name the app **Tweetus Deletus**: that's the name shown on X's “approve”
   screen.
2. In the app's **User authentication settings**, click **Set up**:
   - App permissions: **Read and write and Direct message**
   - Type of App: **Native App**
   - Callback URI: `http://127.0.0.1:8721/callback`
   - Website URL: anything, e.g. `https://example.com`
3. Copy the **OAuth 2.0 Client ID**.
4. Make a copy of the app with your Client ID built in: on a Mac,
   double-click `packaging/Build the Mac app.command`; on Windows,
   double-click `packaging/Build the Windows app.bat`. It needs Python 3.11+
   and takes a couple of minutes. Paste the Client ID when it asks. The
   finished app appears in the `dist` folder; send her that file (AirDrop,
   email, USB stick).
5. Add credits in the X Developer Portal. X bills API use to your developer
   account (pay-per-use), roughly one request per deleted item.

Keep your Client ID out of the public downloads on this page: anyone using a
copy with your Client ID built in runs up *your* X bill. (The build workflow
can bake one in from an `X_CLIENT_ID` repository variable, so only set that
if you're fine with that.) The public downloads have no Client ID and show
a setup screen instead.

*Mac tip:* an app sent by AirDrop or downloaded gets the “can't be opened”
warning the first time. You can clear it for her in Terminal with
`xattr -dr com.apple.quarantine "/Applications/Tweetus Deletus.app"`.

## Good to know

- Nothing is deleted until you've seen the list and confirmed.
- X only lets you delete messages *you* sent, and only from your side.
- If X slows things down (rate limits), the app waits and carries on by
  itself.
- If X says “403”, the developer app is missing a permission (step 2 above);
  fix it and click **Reconnect** in Settings. “402” means it's out of credits.
- Everything stays on your computer in `~/.config/tweetus_deletus/`.
- Want to try it without an account? Run it with `--demo` (or open
  `Try the demo` when running from source) for a pretend account.

## Running from source

For developers, or for Macs with an Intel chip (the Mac download is built for
Apple chips): install Python 3.11+, then double-click
`Start Tweetus Deletus.command` (Mac/Linux) or `Start Tweetus Deletus.bat`
(Windows). The first run sets itself up.

Command line, for scripting:

```bash
pip install -e .
tweetus-deletus                          # open the app
tweetus-deletus preview --what posts --older-than 365
tweetus-deletus delete  --what posts,likes --archive ~/twitter.zip --log ~/deleted.jsonl
tweetus-deletus watch   --what posts --older-than 30 --interval 6h
```

Run `tweetus-deletus <command> -h` for every option.

## Development

```bash
python3 tests/test_smoke.py              # filters, archive parsing, deletes
python3 -m unittest tests.test_webui     # the app's server, end to end, offline
pyinstaller --noconfirm packaging/tweetus.spec   # build the double-click app
```

`.github/workflows/build-app.yml` runs the tests, builds the Mac and Windows
apps, checks each one starts, and publishes them as the “Tweetus Deletus”
release. Code map: `webui.py` and `ui/` (the app), `jobs.py` (background
search/delete), `actions.py` (X API calls), `filters.py`, `archive.py`,
`auth.py` (OAuth 2.0 with PKCE), `client.py`, `store.py`, `demo.py`, `cli.py`.
