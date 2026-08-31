/* The character, drawn from the workbench tokens (docs/design/ui.md).
 *
 * One implementation, two surfaces: the panel embeds it on the dashboard and
 * the floating window loads the same page. Drawing it twice is how a mascot
 * ends up with two moods for one bridge.
 *
 * Colour never carries a state alone — the style pack's own rule, and here it
 * matters twice over: the eyes and the antenna change shape as well as hue,
 * so the state survives a colour-blind reader and a greyscale screenshot.
 * Motion respects prefers-reduced-motion: the calm state is the resting one,
 * not a special case.
 */
const MASCOT_STATES = {
  idle:     {ink: "var(--ok)",     eye: "calm",   label: "робот в сети"},
  thinking: {ink: "var(--accent)", eye: "busy",   label: "робот думает"},
  asking:   {ink: "var(--warn)",   eye: "wide",   label: "робот просит разрешение"},
  paused:   {ink: "var(--muted)",  eye: "closed", label: "мост на паузе"},
  offline:  {ink: "var(--muted)",  eye: "closed", label: "робот не на связи"},
};

function mascotSvg(state, size) {
  const s = MASCOT_STATES[state] || MASCOT_STATES.idle;
  const eyes = {
    // Shape, not only colour: the state must survive greyscale.
    calm:   `<circle cx="26" cy="42" r="4.5" fill="var(--panel)"/><circle cx="46" cy="42" r="4.5" fill="var(--panel)"/>`,
    busy:   `<circle cx="26" cy="42" r="4.5" fill="var(--panel)"/><circle cx="46" cy="42" r="4.5" fill="var(--panel)"><animate attributeName="r" values="4.5;2;4.5" dur="1.1s" repeatCount="indefinite"/></circle>`,
    wide:   `<circle cx="26" cy="42" r="6" fill="var(--panel)"/><circle cx="46" cy="42" r="6" fill="var(--panel)"/>`,
    closed: `<rect x="21" y="41" width="10" height="2.6" rx="1.3" fill="var(--panel)"/><rect x="41" y="41" width="10" height="2.6" rx="1.3" fill="var(--panel)"/>`,
  }[s.eye];

  const antennaPulse = state === "asking"
    ? `<animate attributeName="r" values="3.4;5;3.4" dur="0.9s" repeatCount="indefinite"/>` : "";

  return `<svg viewBox="0 0 72 72" width="${size}" height="${size}" role="img"
      aria-label="${s.label}" style="overflow:visible">
    <line x1="36" y1="18" x2="36" y2="26" stroke="${s.ink}" stroke-width="3" stroke-linecap="round"/>
    <circle cx="36" cy="15" r="3.4" fill="${s.ink}">${antennaPulse}</circle>
    <rect x="10" y="26" width="52" height="38" rx="12" fill="${s.ink}"/>
    ${eyes}
    <rect x="29" y="53" width="14" height="3" rx="1.5" fill="var(--panel)" opacity=".55"/>
  </svg>`;
}

/* Render into `el`. `snap` is exactly what /api/mascot returns — no local
 * state, so the character can never disagree with the bridge. */
function renderMascot(el, snap, opts) {
  opts = opts || {};
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
         <button class="btn ghost" data-decide="allow_grant">На 15 мин</button>
         <button class="btn dangerous" data-decide="deny">Отклонить</button>
       </div>` : "";

  el.innerHTML =
    `${bubble}<div class="mascot-body" title="${esc(s.label)}">${mascotSvg(snap.state, size)}</div>${buttons}`;

  if (buttons && opts.onDecide) {
    el.querySelectorAll("[data-decide]").forEach((b) => {
      b.onclick = () => opts.onDecide(snap.request_id, b.dataset.decide);
    });
  }
}
