# Visual checks

`node --test` and pytest cannot see a page. Three real bugs shipped past
every static check this project has — `[hidden]` losing to a class's own
`display`, iOS time inputs overflowing, roster rows coming out different
heights — and each was found only by looking.

This renders the real `shared.js` and `shared.css` in a real browser and
asserts on measured geometry, which is what separates "the markup looks
right" from "the page is right".

    npm install --no-save playwright
    node tests/visual/check.js

Needs a Chromium; it uses the Edge already installed on Windows. Not in
CI — a headless browser download on every push costs more than it
catches at this size. Run it when you change layout.
