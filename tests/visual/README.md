# Visual checks

`node --test` and pytest cannot see a page. Real bugs have shipped past
every static check this project has — `[hidden]` losing to a class's own
`display`, iOS time inputs overflowing, roster rows coming out different
heights, a tab handler swallowing every control in the sheet, and a
screen that flipped a change back for half a second before flipping it
forward again — and each was found only by looking.

These render the real `shared.js` and `shared.css` in a real browser and
assert on what can be measured, which is what separates "the markup looks
right" from "the page is right".

    npm install --no-save playwright

| Script | What it asks | When to run it |
|---|---|---|
| `check.js` | geometry: row heights, overflow, tap target sizes, input font sizes, the busy state | after changing layout |
| `smoke.js` | does every button do *anything* | after changing a click handler |
| `feedback.js` | does every button do it *immediately* — within 200ms, by drawing the change, going busy, or showing 儲存中 | after changing anything that writes |
| `chaos.js` | two people hammering one game, and a change that must not flip back | after changing who may be on a roster |
| `adverse.js` | the same on a 2.5-second network, plus a write the server refuses | after changing anything optimistic |

All five need a Chromium; they use the Edge already installed on Windows.
Every one but `check.js` also needs both local servers up
and seed data, and they press destructive controls — point them only at a
local server and re-run `scripts/seed_dev.py` afterwards.

None are in CI: a headless browser download on every push costs more than
it catches at this size.

## Why `feedback.js` is separate from `smoke.js`

`smoke.js` waits 900ms and asks whether anything happened. That was
enough to catch a control wired to nothing, and useless against the
complaint that actually kept coming back — 「反應很慢」, 「按了沒反應」.
A control may wait in silence for about 200ms, which is roughly how long
it takes a person to notice; past that it owes them either the change
itself, a spinner on the control, or the page's 儲存中 marker. That is a
different question with a different answer, so it is a different file.
