/* The character, its five states, and the seam that lets it be replaced.
 *
 * Style pack: workbench (docs/design/ui.md). Dials for this surface —
 * variance 4, motion 3, density 3. Motion 3 is the pack's own ceiling and it
 * is the right number twice over: this figure is on screen permanently, and
 * the motion doctrine decides by frequency before taste gets a vote.
 *
 * Every animation here answers "why does this move?" with STATE INDICATION,
 * the doctrine's own valid answer:
 *   - the breath says the bridge is alive rather than frozen (a still figure
 *     was reported as looking hung);
 *   - the eyes say which state it is in — shape, not only colour, so the
 *     state survives greyscale and a colour-blind reader;
 *   - pause and offline have NO motion at all, because «пауза выглядит как
 *     отсутствие» and a breathing corpse is worse than a still one.
 *
 * Only `transform` and `opacity` are animated: the doctrine bans animating
 * layout, and SVG geometry attributes (`r`, `cx`) are geometry — the previous
 * version animated those.
 *
 * One implementation, two surfaces: the panel embeds this file and the
 * floating pet loads the same one. Drawing the character twice is how a
 * mascot ends up with two moods for one bridge.
 */

/* Curves and durations come from the motion doctrine, not from taste.
 * `ease-in` is banned in UI; UI motion stays at or under 300 ms. */
const VB_EASE_OUT = "cubic-bezier(0.23, 1, 0.32, 1)";
const VB_EASE_IN_OUT = "cubic-bezier(0.77, 0, 0.175, 1)";
const VB_DUR = 220;                       // state change, inside the ceiling

const MASCOT_STATES = {
  idle:     {ink: "var(--ok)",     eyes: "calm",   label: "робот в сети",
             alive: true},
  thinking: {ink: "var(--accent)", eyes: "scan",   label: "робот думает",
             alive: true},
  asking:   {ink: "var(--warn)",   eyes: "wide",   label: "робот просит разрешение",
             alive: true},
  // No motion in either: on pause the device must look switched off, and an
  // offline robot is not doing anything to depict.
  paused:   {ink: "var(--muted)",  eyes: "closed", label: "мост на паузе",
             alive: false},
  offline:  {ink: "var(--muted)",  eyes: "closed", label: "робот не на связи",
             alive: false},
};

/* ── the skin seam ────────────────────────────────────────────────────────
 * A skin is `(state, size) -> svg string`. It receives the resolved state
 * descriptor so a skin never re-decides what a state MEANS — only how it
 * looks. That is the line between a skin and a fork.
 */
const MASCOT_SKINS = {};

function registerMascotSkin(name, draw){ MASCOT_SKINS[name] = draw; }

function mascotSvg(state, size, skin){
  const s = MASCOT_STATES[state] || MASCOT_STATES.idle;
  const draw = MASCOT_SKINS[skin] || MASCOT_SKINS.vasya;
  return draw(s, size, state);
}

/* ── default skin: Вася ───────────────────────────────────────────────────
 * A rounded head, two eyes and an antenna, drawn from the pack's tokens.
 */
registerMascotSkin("vasya", (s, size, state) => {
  const eyes = {
    calm: `
      <g class="vb-eyes">
        <circle cx="26" cy="42" r="4.5" fill="var(--panel)"/>
        <circle cx="46" cy="42" r="4.5" fill="var(--panel)"/>
      </g>`,
    // Scanning left and right: the robot is working through something.
    scan: `
      <g class="vb-eyes vb-scan">
        <circle cx="26" cy="42" r="4.5" fill="var(--panel)"/>
        <circle cx="46" cy="42" r="4.5" fill="var(--panel)"/>
      </g>`,
    // Wider AND rounder — a shape difference, not a colour one.
    wide: `
      <g class="vb-eyes">
        <circle cx="26" cy="42" r="6.2" fill="var(--panel)"/>
        <circle cx="46" cy="42" r="6.2" fill="var(--panel)"/>
      </g>`,
    closed: `
      <g class="vb-eyes">
        <rect x="21" y="41" width="10" height="2.6" rx="1.3" fill="var(--panel)"/>
        <rect x="41" y="41" width="10" height="2.6" rx="1.3" fill="var(--panel)"/>
      </g>`,
  }[s.eyes];

  return `<svg viewBox="0 0 72 72" width="${size}" height="${size}" role="img"
      aria-label="${s.label}" class="vb-skin vb-${state}"
      style="overflow:visible">
    <line x1="36" y1="18" x2="36" y2="26" stroke="${s.ink}"
          stroke-width="3" stroke-linecap="round"/>
    <circle class="vb-antenna" cx="36" cy="15" r="3.6" fill="${s.ink}"/>
    <rect x="10" y="26" width="52" height="38" rx="12" fill="${s.ink}"/>
    ${eyes}
    <rect x="29" y="53" width="14" height="3" rx="1.5"
          fill="var(--panel)" opacity=".55"/>
  </svg>`;
});

/* ── alternative skin: точка ──────────────────────────────────────────────
 * Deliberately minimal, and it exists to prove the seam is a seam: a skin
 * system with one skin is an assertion, not a contract.
 */
registerMascotSkin("dot", (s, size, state) => `
  <svg viewBox="0 0 72 72" width="${size}" height="${size}" role="img"
       aria-label="${s.label}" class="vb-skin vb-${state}"
       style="overflow:visible">
    <circle cx="36" cy="36" r="22" fill="${s.ink}"/>
    ${s.eyes === "closed"
      ? `<rect x="26" y="34.5" width="20" height="3" rx="1.5" fill="var(--panel)"/>`
      : `<circle class="vb-antenna" cx="36" cy="36" r="7" fill="var(--panel)"/>`}
  </svg>`);

/* ── the stylesheet both surfaces share ───────────────────────────────────
 * Injected once so the panel and the floating window cannot drift apart.
 */
function mascotStyles(){
  if (document.getElementById("vb-mascot-style")) return;
  const el = document.createElement("style");
  el.id = "vb-mascot-style";
  el.textContent = `
    .vb-skin{transition:opacity ${VB_DUR}ms ${VB_EASE_OUT}}
    /* The breath: the ONLY continuous motion, and it is why the figure does
       not read as hung. Transform only — the doctrine bans animating layout. */
    @keyframes vb-breathe{0%,100%{transform:translateY(0)}
                          50%{transform:translateY(-2.5px)}}
    @keyframes vb-blink{0%,96%,100%{transform:scaleY(1)}98%{transform:scaleY(.1)}}
    @keyframes vb-scan{0%,100%{transform:translateX(-2px)}
                       50%{transform:translateX(2px)}}
    @keyframes vb-pulse{0%,100%{opacity:1}50%{opacity:.35}}
    .vb-idle, .vb-thinking, .vb-asking{
      animation:vb-breathe 4.5s ${VB_EASE_IN_OUT} infinite}
    .vb-idle .vb-eyes{animation:vb-blink 7s ${VB_EASE_OUT} infinite;
                      transform-origin:center 42px}
    .vb-scan{animation:vb-scan 1.6s ${VB_EASE_IN_OUT} infinite}
    .vb-asking .vb-antenna{animation:vb-pulse .9s ${VB_EASE_IN_OUT} infinite}
    /* Pause and offline carry no motion at all: a breathing figure over a
       stopped bridge says the opposite of what pause means. */
    .vb-paused, .vb-offline{animation:none}
    .vb-paused *, .vb-offline *{animation:none}
    .vb-offline{opacity:.75}
    @media (prefers-reduced-motion: reduce){
      .vb-skin, .vb-skin *{animation:none !important;transition:none !important}
    }`;
  document.head.appendChild(el);
}

/* Render into `el`. `snap` is exactly what /api/mascot returns — no local
 * state, so the character can never disagree with the bridge. */
function renderMascot(el, snap, opts){
  opts = opts || {};
  mascotStyles();
  const size = opts.size || 72;
  const s = MASCOT_STATES[snap.state] || MASCOT_STATES.idle;
  const esc = (x) => {
    const d = document.createElement("div");
    d.textContent = String(x == null ? "" : x);
    return d.innerHTML;
  };

  // The bubble renders whatever the robot said, so it is escaped here and
  // inserted as text — a reply containing markup must stay a reply.
  const bubble = snap.says
    ? `<div class="mascot-bubble">${esc(snap.says)}</div>` : "";
  const buttons = (snap.actionable && opts.answerable)
    ? `<div class="mascot-actions">
         <button class="btn primary" data-decide="allow">Разрешить</button>
         <button class="btn ghost" data-decide="allow_grant">Такие — 15 мин</button>
         <button class="btn dangerous" data-decide="deny">Отклонить</button>
       </div>` : "";

  el.innerHTML =
    `${bubble}<div class="mascot-body" title="${esc(s.label)}">` +
    `${mascotSvg(snap.state, size, opts.skin)}</div>${buttons}`;

  if (buttons && opts.onDecide) {
    el.querySelectorAll("[data-decide]").forEach((b) => {
      b.onclick = () => opts.onDecide(snap.request_id, b.dataset.decide);
    });
  }
}
