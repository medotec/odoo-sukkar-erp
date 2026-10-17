/**
 * Kitchen wall screen: keypad shortcuts, paging, and a hands-off refresh.
 *
 * Written as a plain script rather than an OWL patch on purpose. It only reads
 * the DOM and clicks buttons that already exist, so it cannot put the board into
 * a state the server did not sanction, and an Odoo upgrade that changes kanban
 * internals degrades this to "does nothing" instead of breaking the screen the
 * kitchen depends on.
 *
 * Input is a numeric keypad, not touch. A bakery kitchen is the wrong place for a
 * capacitive screen — gloves and cream defeat it — so every action is reachable
 * from keys a cheap washable keypad actually has:
 *
 *     1-9   mark that tile done
 *     +     next nine tickets
 *     -     previous nine
 *     0     jump back to the most urgent
 *
 * Touch still works for anyone who prefers it.
 */
(function () {
    "use strict";

    var PAGE_SIZE = 9;        // what a keypad addresses in one press, and what fits
    var REFRESH_MS = 30000;   // how often an untouched board pulls new tickets
    var QUIET_MS = 15000;     // never reload within this long of someone using it
    var HOME_MS = 60000;      // pages left open drift back to the most urgent
    // A press the board has not answered yet holds the reload off for longer than
    // the quiet window: on a slow morning the server can take longer to reply
    // than someone takes to stop touching the screen, and a reload in that gap
    // throws the press away.
    var PRESS_MS = 20000;

    var page = 0;
    var lastInteraction = 0;
    var pressBusyUntil = 0;

    function screenIsOpen() {
        return document.querySelector(".sukkar_kitchen_screen") !== null;
    }

    // An error or a confirmation on screen is something someone has to read; a
    // reload would wipe it before they had the chance.
    function messageIsOpen() {
        return document.querySelector(".modal, .o_dialog, .o_notification") !== null;
    }

    // Odoo pads an ungrouped kanban with six invisible tiles to keep the last
    // row aligned. They carry the same class as a real ticket, so counting them
    // told the kitchen six cakes were waiting that nobody had ordered.
    function records() {
        return Array.prototype.slice.call(
            document.querySelectorAll(".sukkar_kitchen_screen .o_kanban_record")
        ).filter(function (record) {
            return !record.classList.contains("o_kanban_ghost");
        });
    }

    function pageCount(total) {
        return Math.max(1, Math.ceil(total / PAGE_SIZE));
    }

    function touch() {
        lastInteraction = Date.now();
    }

    /**
     * Show one page of nine, number them for the keypad, and say what is hidden.
     * Nothing is ever silently dropped — a board that hides work without saying
     * so is worse than no board at all.
     */
    function render() {
        // The board has just changed, so anything that was pressed has landed.
        pressBusyUntil = 0;
        var all = records();
        var total = all.length;
        if (page >= pageCount(total)) {
            page = pageCount(total) - 1;
        }
        var first = page * PAGE_SIZE;
        var last = Math.min(first + PAGE_SIZE, total);

        all.forEach(function (record, index) {
            var onPage = index >= first && index < last;
            record.classList.toggle("ks-offpage", !onPage);
            var head = record.querySelector(".ks-head");
            if (!head) { return; }
            var badge = head.querySelector(".ks-key");
            if (!onPage) {
                if (badge) { badge.remove(); }
                return;
            }
            if (!badge) {
                badge = document.createElement("span");
                badge.className = "ks-key";
                head.insertBefore(badge, head.firstChild);
            }
            badge.textContent = String(index - first + 1);
        });
        renderPager(total, first, last);
        announce();
    }

    function renderPager(total, first, last) {
        var bar = document.querySelector(".ks-pager");
        if (!total) {
            if (bar) { bar.remove(); }
            return;
        }
        if (!bar) {
            bar = document.createElement("div");
            bar.className = "ks-pager";
            document.body.appendChild(bar);
        }
        var waiting = total - last;
        var range = total <= PAGE_SIZE
            ? total + (total === 1 ? " ticket" : " tickets")
            : (first + 1) + "-" + last + " of " + total;
        bar.innerHTML = "";

        var left = document.createElement("span");
        left.textContent = range;
        bar.appendChild(left);

        var keys = document.createElement("span");
        keys.className = "ks-pager-keys";
        keys.textContent = total > PAGE_SIZE
            ? "1-9 done  ·  + next  ·  − back  ·  0 most urgent"
            : "press 1-9 to mark done";
        bar.appendChild(keys);

        var more = document.createElement("span");
        more.className = "ks-pager-more";
        more.textContent = waiting > 0
            ? waiting + " more waiting"
            : "nothing else waiting";
        if (!soundReady) {
            more.textContent += "  ·  🔇 press any key for sound";
        }
        bar.appendChild(more);
    }

    function pressDone(indexOnPage) {
        var all = records();
        var record = all[page * PAGE_SIZE + indexOnPage];
        if (!record) { return; }
        var button = record.querySelector(".ks-done");
        if (!button) { return; }       // a blocked ticket has no button to press
        touch();
        pressBusyUntil = Date.now() + PRESS_MS;
        record.classList.add("ks-firing");
        // If the ticket stays on the board (a refused DONE, or simply the next
        // step of the same ticket) nothing replaces the tile, so undim it here —
        // a tile left grey looks like a press that was swallowed.
        setTimeout(function () {
            record.classList.remove("ks-firing");
        }, 1500);
        button.click();
    }

    function turnTo(newPage) {
        var total = records().length;
        var count = pageCount(total);
        page = Math.min(Math.max(newPage, 0), count - 1);
        touch();
        render();
    }

    document.addEventListener("keydown", function (event) {
        if (!screenIsOpen()) { return; }
        var target = event.target;
        // Never steal a keystroke from something the user is typing into.
        if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA"
                       || target.isContentEditable)) {
            return;
        }
        if (event.key >= "1" && event.key <= "9") {
            event.preventDefault();
            pressDone(parseInt(event.key, 10) - 1);
        } else if (event.key === "+" || event.key === "PageDown") {
            event.preventDefault();
            turnTo(page + 1);
        } else if (event.key === "-" || event.key === "PageUp") {
            event.preventDefault();
            turnTo(page - 1);
        } else if (event.key === "0" || event.key === "Home") {
            event.preventDefault();
            turnTo(0);
        }
    });

    // Every way a person can touch this screen counts as using it — a slow scroll
    // through a long ticket is as much "in the middle of something" as a tap.
    ["click", "pointerdown", "touchstart", "touchmove", "wheel", "keydown"]
        .forEach(function (name) {
            document.addEventListener(name, function () {
                touch();
                unlockSound();
            }, { passive: true });
        });

    // Try the moment the board opens: a kiosk browser allows it, an ordinary
    // one waits for the first key.
    unlockSound();

    setInterval(function () {
        if (!screenIsOpen()) { return; }
        var idle = Date.now() - lastInteraction;
        // Someone paged forward to look ahead and walked off. Come back to the
        // work that actually matters rather than sitting on page three all night.
        if (page > 0 && idle > HOME_MS) {
            turnTo(0);
            lastInteraction = 0;
            return;
        }
        // A reload mid-press would lose the press, so wait for a quiet moment,
        // for the last press to have come back, and for the screen to be saying
        // nothing that still has to be read.
        if (idle < QUIET_MS) { return; }
        if (Date.now() < pressBusyUntil) { return; }
        if (messageIsOpen()) { return; }
        window.location.reload();
    }, REFRESH_MS);

    // ── Sound ────────────────────────────────────────────────────────────
    // A wall screen is read from across a kitchen, with hands full: what
    // changes has to be heard, not spotted. Four sounds, each unmistakable
    // from the others, built from plain tones so there is no file to lose:
    //
    //     new ticket   two rising notes
    //     moved on     one short note
    //     finished     two falling notes
    //     LATE         a hard three-note alarm, twice as loud
    //
    // Browsers refuse to make a sound until someone has touched the page, so
    // the first key or tap unlocks it and the pager says so until then. A
    // kiosk browser started with --autoplay-policy=no-user-gesture-required
    // never needs the tap.
    var audio = null;
    var soundReady = false;

    function audioContext() {
        if (audio) { return audio; }
        var Ctor = window.AudioContext || window.webkitAudioContext;
        if (!Ctor) { return null; }
        try {
            audio = new Ctor();
        } catch (error) {
            return null;
        }
        return audio;
    }

    function note(startAt, frequency, seconds, volume) {
        var ctx = audioContext();
        if (!ctx) { return; }
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = frequency;
        // Fade both ends: a tone that starts and stops square clicks, and a
        // click is the one sound a kitchen learns to ignore.
        gain.gain.setValueAtTime(0.0001, ctx.currentTime + startAt);
        gain.gain.exponentialRampToValueAtTime(volume, ctx.currentTime + startAt + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + startAt + seconds);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(ctx.currentTime + startAt);
        osc.stop(ctx.currentTime + startAt + seconds + 0.02);
    }

    var SOUNDS = {
        arrive: [[0, 660, 0.14, 0.25], [0.16, 880, 0.18, 0.25]],
        moved: [[0, 880, 0.12, 0.2]],
        done: [[0, 660, 0.14, 0.22], [0.16, 440, 0.2, 0.22]],
        late: [[0, 1040, 0.18, 0.5], [0.24, 780, 0.18, 0.5], [0.48, 1040, 0.34, 0.5]],
    };

    function play(name) {
        var ctx = audioContext();
        if (!ctx || !SOUNDS[name]) { return; }
        if (ctx.state === "suspended") {
            soundReady = false;
            return;
        }
        soundReady = true;
        SOUNDS[name].forEach(function (part) {
            note(part[0], part[1], part[2], part[3]);
        });
    }

    function unlockSound() {
        var ctx = audioContext();
        if (ctx && ctx.state === "suspended") {
            ctx.resume().then(function () { soundReady = true; }, function () {});
        } else if (ctx) {
            soundReady = true;
        }
    }

    // ── What changed since the board was last looked at ──────────────────
    // The board reloads itself, so the comparison cannot live in a variable:
    // it is kept in the tab's own storage and read back after the reload.
    var SNAPSHOT_KEY = "sukkar_board_snapshot";

    function snapshot() {
        var state = {};
        records().forEach(function (record) {
            var number = record.querySelector(".ks-no");
            if (!number) { return; }
            state[number.textContent.trim()] = {
                stage: (record.querySelector(".ks-stage") || {}).textContent || "",
                // The colour lives on the tile inside the kanban record, not on
                // the record itself — reading the record said nothing was ever
                // late and the alarm never sounded.
                late: !!record.querySelector(".ks-late"),
            };
        });
        return state;
    }

    function readSnapshot() {
        try {
            return JSON.parse(sessionStorage.getItem(SNAPSHOT_KEY) || "{}");
        } catch (error) {
            return {};
        }
    }

    function writeSnapshot(state) {
        try {
            sessionStorage.setItem(SNAPSHOT_KEY, JSON.stringify(state));
        } catch (error) { /* a locked-down browser simply gets no sound */ }
    }

    /** One sound per board change, loudest case wins: a ticket that has just
        gone late is announced instead of the arrival that came with it. */
    function announce() {
        var before = readSnapshot();
        var now = snapshot();
        var wentLate = false;
        var arrived = false;
        var moved = false;
        Object.keys(now).forEach(function (key) {
            var was = before[key];
            if (!was) {
                arrived = true;
            } else {
                if (now[key].stage !== was.stage) { moved = true; }
                if (now[key].late && !was.late) { wentLate = true; }
            }
        });
        var finished = Object.keys(before).some(function (key) { return !now[key]; });
        writeSnapshot(now);
        // Nothing to say on the very first painting of the board.
        if (!Object.keys(before).length) { return; }
        if (wentLate) { play("late"); }
        else if (arrived) { play("arrive"); }
        else if (finished) { play("done"); }
        else if (moved) { play("moved"); }
    }

    // Tiles arrive and disappear asynchronously; re-page whenever the board changes.
    // Guarded start: this bundle can execute before <body> exists, and observing
    // null throws — which Odoo's module loader treats as a failed module and then
    // refuses to start the WHOLE web client. One missing guard here blanked every
    // screen on the test server (30 Aug 2026). Degrade to no-op, never throw.
    function startObserver() {
        if (!document.body) { return; }
        var scheduled = null;
        var observer = new MutationObserver(function () {
            if (!screenIsOpen()) { return; }
            clearTimeout(scheduled);
            scheduled = setTimeout(render, 50);
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }
    if (document.body) {
        startObserver();
    } else {
        document.addEventListener("DOMContentLoaded", startObserver);
    }
})();
