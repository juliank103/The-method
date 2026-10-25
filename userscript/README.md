# Tweetus Deletus — free version

Delete your own X (Twitter) posts in bulk, for free, from your own browser.
No account to set up, no fees. It adds a little **🪄 Tweetus Deletus** panel to
x.com that deletes your posts slowly, one at a time, the same way you would by
hand.

Works in Chrome, Edge, Firefox, Brave, or Safari — on Windows, Mac, anything.

## Install (about 2 minutes, once)

1. **Add the Tampermonkey extension** to your browser (it's free, and it's what
   runs the script):
   - Chrome / Edge / Brave: <https://tampermonkey.net> → "Download" → your browser
   - Firefox: same site, or search "Tampermonkey" in Firefox Add-ons
   - Safari: get "Tampermonkey" from the Mac App Store
2. **Install the script.** Click this link:
   **[Install Tweetus Deletus](https://raw.githubusercontent.com/juliank103/The-method/claude/pensive-goodall-9btu4p/userscript/tweetus-deletus.user.js)**
   Tampermonkey opens a page with an **Install** button — click it.
   (If the link just shows code instead, copy all of it, open the Tampermonkey
   icon → **Create a new script**, paste over what's there, and press
   Ctrl/⌘-S.)

That's it. It's installed and stays installed.

## Use it

1. Go to **x.com** and make sure you're **logged in**.
2. Click your profile picture to open **your own profile**.
   - Stay on the **Posts** tab to delete posts and reposts.
   - Switch to the **Replies** tab to delete your replies.
3. The **🪄 Tweetus Deletus** panel is in the top-right corner. Choose:
   - **Speed** — leave it on *Slow — safest* unless you're in a hurry.
   - **Keep my pinned post** — on by default.
   - **Only older than … days** — optional (e.g. `365` to keep the last year).
   - **Keep anything saying…** — optional words to protect (e.g. `wedding, job`).
4. Click **Start** and confirm. Leave the tab open and in front. Watch the
   counter climb. Press **Stop** whenever you like.

When it's done it says so. You can grab a list of what it deleted with
**Download list of deleted**.

## Please read

- **Deleting is permanent.** Start with a small test: set "Only older than" to
  something that matches just a few posts, run it, and check before doing more.
- **Go slow.** Automating X is against X's rules, so the *Slow* speed exists to
  stay gentle and not draw attention. Deleting thousands of posts can take
  hours or a couple of evenings — that's normal and on purpose. Leave the tab
  open; you can let it run while you do other things.
- **It only deletes what you could delete yourself** — your own posts, replies
  and reposts. It never touches other people's posts, and it skips your pinned
  post unless you tell it not to.
- **X only shows your most recent ~3,200 posts** on your profile. To clear a
  bigger backlog, delete what shows, and X gradually reveals older ones as the
  recent ones disappear — so running it again over a few sessions gets further
  back.
- **If X changes their website**, the script may stop finding the Delete
  button. It will say so in the panel rather than do anything wrong. If that
  happens, check for an updated version at the Install link above.

## For developers

`tweetus-deletus.user.js` is a single self-contained userscript. The pure
decision logic (what counts as yours, date/keyword filters, pacing) is in the
`Helpers` object so it can be tested without a browser.

```bash
node userscript/test_userscript.mjs   # helper unit tests + a headless-browser
                                      # run against a mock profile (needs playwright)
```
