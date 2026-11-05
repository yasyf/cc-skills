const NS = "http://www.w3.org/2000/svg";
const FONT_HREF = "https://fonts.googleapis.com/css2?family=Shantell+Sans:wght@400;500;600;700&display=swap";

const LIGHT = String.raw`
  --smap-d-paper: #fbfaf5; --smap-d-ink: #1f2633; --smap-d-line: #e3e0d4;
  --wb-marker: #2453d4; --wb-marker-soft: rgba(36, 83, 212, .12);
  --wb-red: #c9302a; --wb-red-soft: rgba(201, 48, 42, .1);
  --wb-green: #1d8a4b; --wb-green-soft: rgba(29, 138, 75, .12);
  --wb-amber: #a86f12; --wb-amber-soft: rgba(214, 150, 20, .14); --wb-hl: #ffd84d;
  --wb-tint-blue: rgba(36, 83, 212, .18); --wb-tint-green: rgba(29, 138, 75, .2); --wb-tint-amber: rgba(214, 150, 20, .24);
  --wb-tint-red: rgba(201, 48, 42, .16); --wb-tint-grey: rgba(31, 38, 51, .14);
  --wb-sticky: #fff3a8; --wb-sticky-ink: #3b3417;
  --wb-shadow: 0 10px 30px rgba(31, 38, 51, .12), 0 2px 6px rgba(31, 38, 51, .08);`;

const DARK = String.raw`
  --smap-d-paper: #1a1d21; --smap-d-ink: #ece8dc; --smap-d-line: #353a42;
  --wb-marker: #86a8ff; --wb-marker-soft: rgba(134, 168, 255, .14);
  --wb-red: #ff7c6e; --wb-red-soft: rgba(255, 124, 110, .12);
  --wb-green: #63d293; --wb-green-soft: rgba(99, 210, 147, .13);
  --wb-amber: #f0b44c; --wb-amber-soft: rgba(240, 180, 76, .14); --wb-hl: #8a7215;
  --wb-tint-blue: rgba(134, 168, 255, .24); --wb-tint-green: rgba(99, 210, 147, .22); --wb-tint-amber: rgba(240, 180, 76, .24);
  --wb-tint-red: rgba(255, 124, 110, .2); --wb-tint-grey: rgba(236, 232, 220, .13);
  --wb-sticky: #4a4220; --wb-sticky-ink: #f6edc4;
  --wb-shadow: 0 10px 30px rgba(0, 0, 0, .45), 0 2px 6px rgba(0, 0, 0, .3);`;

const HAND = '"Shantell Sans", ui-rounded, system-ui, sans-serif';
const SANS = 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

const CSS = String.raw`
.smap {${LIGHT}
  --wb-paper: var(--smap-paper, var(--smap-d-paper)); --wb-ink: var(--smap-ink, var(--smap-d-ink)); --wb-line: var(--smap-line, var(--smap-d-line));
  --wb-paper-2: color-mix(in srgb, var(--wb-ink) 6%, var(--wb-paper)); --wb-dot: color-mix(in srgb, var(--wb-ink) 16%, var(--wb-paper));
  --wb-ink-2: color-mix(in srgb, var(--wb-ink) 74%, var(--wb-paper)); --wb-ink-3: color-mix(in srgb, var(--wb-ink) 55%, var(--wb-paper));
  --wb-card: color-mix(in srgb, var(--wb-ink) 3%, var(--wb-paper));
  --hand: ${HAND}; --smap-sans: ${SANS};
  display: grid; grid-template-rows: auto auto auto auto; background: var(--wb-paper); color: var(--wb-ink); font: 14px/1.45 var(--smap-sans); min-width: 0; border-radius: inherit; overflow: hidden;
}
@media screen and (prefers-color-scheme: dark) { :root:not([data-theme="light"]):not([data-print]) .smap {${DARK} } }
@media screen { :root[data-theme="dark"]:not([data-print]) .smap {${DARK} } }
.smap button { font: inherit; color: inherit; }
.smap-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; padding: 8px 12px; border-bottom: 1px solid var(--wb-line); min-width: 0; }
.smap-crumbs { display: none; align-items: center; gap: 6px; font-size: 13px; color: var(--wb-ink-3); white-space: nowrap; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.smap.focused > .smap-bar .smap-crumbs, .smap.leveled > .smap-bar .smap-crumbs { display: flex; }
.smap-crumbs button { background: none; border: 0; padding: 0; cursor: pointer; color: var(--wb-marker); font-weight: 600; }
.smap-crumbs b { color: var(--wb-ink); font-weight: 650; }
.smap-chips { display: flex; gap: 6px; overflow-x: auto; scrollbar-width: none; flex: 1 1 220px; min-width: 0; padding: 2px 0; }
.smap-chips::-webkit-scrollbar { display: none; }
.smap-chip { border: 1.5px solid var(--wb-line); background: transparent; border-radius: 999px; padding: 4px 12px; white-space: nowrap; cursor: pointer; font-size: 13px; font-weight: 500; color: var(--wb-ink-2); }
.smap-chip:hover { border-color: var(--wb-ink-3); color: var(--wb-ink); }
.smap-chip[aria-pressed="true"] { background: var(--wb-ink); color: var(--wb-paper); border-color: var(--wb-ink); }
.smap-chip.ok::before, .smap-chip.fail::before, .smap-chip.wait::before { content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--wb-red); margin-right: 7px; vertical-align: 1px; }
.smap-chip.ok::before { background: var(--wb-green); }
.smap-chip.wait::before { background: none; box-shadow: inset 0 0 0 1.5px var(--wb-ink-3); }
.smap-tools { flex: none; display: flex; gap: 6px; margin-left: auto; }
.smap-tools button { height: 30px; min-width: 32px; border-radius: 9px; border: 1px solid var(--wb-line); background: var(--wb-card); cursor: pointer; font-weight: 600; padding: 0 10px; box-shadow: 0 1px 2px rgba(0, 0, 0, .06); }
.smap-tools button:hover, .smap-open:hover { border-color: var(--wb-ink-3); }
.smap-canvas { position: relative; min-width: 0; height: 360px; max-height: var(--smap-max-h, 80vh); overflow: hidden; touch-action: pan-y; }
.smap.dialog > .smap-canvas { height: auto; max-height: none; touch-action: none; }
.smap.compact > .smap-canvas { touch-action: auto; }
.smap-canvas svg { width: 100%; height: 100%; display: block; cursor: grab; user-select: none; -webkit-user-select: none; }
.smap-canvas svg.dragging { cursor: grabbing; }
.smap.compact > .smap-canvas svg { cursor: pointer; }
.smap:not(.compact).cut > .smap-canvas svg { -webkit-mask-image: linear-gradient(to right, transparent, #000 var(--fl, 0px), #000 calc(100% - var(--fr, 0px)), transparent), linear-gradient(to bottom, transparent, #000 var(--ft, 0px), #000 calc(100% - var(--fb, 0px)), transparent); -webkit-mask-composite: source-in; mask-image: linear-gradient(to right, transparent, #000 var(--fl, 0px), #000 calc(100% - var(--fr, 0px)), transparent), linear-gradient(to bottom, transparent, #000 var(--ft, 0px), #000 calc(100% - var(--fb, 0px)), transparent); mask-composite: intersect; }
.smap.compact.cut > .smap-canvas svg { -webkit-mask-image: linear-gradient(to right, #000 86%, transparent), linear-gradient(to bottom, #000 82%, transparent); -webkit-mask-composite: source-in; mask-image: linear-gradient(to right, #000 86%, transparent), linear-gradient(to bottom, #000 82%, transparent); mask-composite: intersect; }
.smap.compact > .smap-bar, .smap.compact > .smap-readout { display: none; }
.smap.compact > .smap-canvas svg .lanes, .smap.compact > .smap-canvas svg .lab, .smap.compact > .smap-canvas svg .spine { display: none; }
.smap-glimpse { display: none; flex-wrap: wrap; align-items: center; gap: 6px; padding: 8px 12px 10px; border-top: 1px solid var(--wb-line); }
.smap.compact > .smap-glimpse { display: flex; }
.smap-open { border: 1.5px solid var(--wb-ink); background: var(--wb-ink); color: var(--wb-paper) !important; border-radius: 999px; padding: 4px 14px; cursor: pointer; font-size: 13px; font-weight: 650; white-space: nowrap; }
.smap-readout { padding: 7px 12px 9px; border-top: 1px solid var(--wb-line); font-size: 13px; line-height: 1.5; color: var(--wb-ink-2); min-height: 1.5em; overflow-wrap: anywhere; }
.smap-readout b { color: var(--wb-ink); font-weight: 650; }
.smap-deeper { margin-left: 6px; border: 1px solid var(--wb-marker); background: var(--wb-marker-soft); color: var(--wb-marker) !important; border-radius: 999px; padding: 1px 10px; cursor: pointer; font-size: 12.5px; font-weight: 650; white-space: nowrap; }
.smap-deeper:hover { background: var(--wb-marker); color: var(--wb-paper) !important; }
.smap-readout ol { display: inline; margin: 0; padding: 0; list-style: none; }
.smap-readout li { display: inline; margin-right: 12px; }
.smap-readout li .n { display: inline-block; min-width: 18px; height: 18px; border-radius: 9px; background: var(--wb-marker); color: var(--wb-paper); font: 700 11px/18px var(--smap-sans); text-align: center; margin-right: 5px; vertical-align: 1px; }
.smap-readout li.risk .n { background: var(--wb-red); }
.smap-readout li.stop .n { background: var(--wb-ink-2); }
.smap-hint .pan, .smap.cut .smap-hint .click { display: none; }
.smap.cut .smap-hint .pan { display: inline; color: var(--wb-ink-2); font-weight: 600; }
.smap-pathline { display: none; }
.smap-problems { padding: 12px 14px; color: var(--wb-red); font-size: 13px; }
.smap-problems ul { margin: 6px 0 0; padding-left: 18px; }
.smap-dialog { padding: 0; margin: 0; border: 0; width: 100vw; height: 100vh; height: 100dvh; max-width: 100vw; max-height: 100dvh; background: var(--wb-paper, #fbfaf5); overflow: hidden; }
.smap-dialog::backdrop { background: rgba(20, 22, 26, .5); }
.smap.printing { break-inside: avoid; }
.smap.printing > .smap-canvas { max-height: 92vh; }
.smap-dialog > .smap { height: 100%; grid-template-rows: auto minmax(0, 1fr) auto auto; border-radius: 0; }
.smap svg text { font-family: var(--hand); fill: var(--wb-ink); }
.smap svg .t-ink2 { fill: var(--wb-ink-2); }
.smap svg .t-ink3 { fill: var(--wb-ink-3); }
.smap svg .t-red { fill: var(--wb-red); }
.smap svg .t-green { fill: var(--wb-green); }
.smap svg .t-marker { fill: var(--wb-marker); }
.smap svg .t-amber { fill: var(--wb-amber); }
.smap svg .t-sticky { fill: var(--wb-sticky-ink); }
.smap svg .t-paper { fill: var(--wb-paper); }
.smap svg .t-sans { font-family: var(--smap-sans); font-variant-numeric: tabular-nums; }
.smap svg .t-caps { font-family: var(--smap-sans); text-transform: uppercase; letter-spacing: .08em; }
.smap svg .halo { paint-order: stroke; stroke: var(--wb-paper); stroke-width: 7px; stroke-linejoin: round; }
.smap svg .k-ink path { stroke: var(--wb-ink); }
.smap svg .k-ink2 path { stroke: var(--wb-ink-2); }
.smap svg .k-ink3 path { stroke: var(--wb-ink-3); }
.smap svg .k-red path { stroke: var(--wb-red); }
.smap svg .k-green path { stroke: var(--wb-green); }
.smap svg .k-marker path { stroke: var(--wb-marker); }
.smap svg .k-amber path { stroke: var(--wb-amber); }
.smap svg .k-hl path { stroke: var(--wb-hl); }
.smap svg .f-blue path { stroke: var(--wb-tint-blue); }
.smap svg .f-green path { stroke: var(--wb-tint-green); }
.smap svg .f-amber path { stroke: var(--wb-tint-amber); }
.smap svg .f-red path { stroke: var(--wb-tint-red); }
.smap svg .f-grey path { stroke: var(--wb-tint-grey); }
.smap svg .f-sticky path { fill: var(--wb-sticky); stroke: none; }
.smap svg .fh-ink { fill: var(--wb-ink); }
.smap svg .fh-ink2 { fill: var(--wb-ink-2); }
.smap svg .fh-ink3 { fill: var(--wb-ink-3); }
.smap svg .fh-marker { fill: var(--wb-marker); }
.smap svg .fh-red { fill: var(--wb-red); }
.smap svg .dotgrid circle { fill: var(--wb-dot); }
.smap svg .paper { fill: var(--wb-paper); }
.smap svg .paper2 { fill: var(--wb-paper-2); }
.smap svg .paper.box { fill: transparent; }
.smap svg .disc { fill: var(--wb-marker); }
.smap svg .disc.risk { fill: var(--wb-red); }
.smap svg .disc.stop { fill: var(--wb-ink-2); }
.smap svg .bd .base { fill: var(--wb-paper); }
.smap svg .bd .face { fill: var(--wb-paper); stroke: var(--wb-ink-3); stroke-width: 1.2; }
.smap svg .bd.fail .face { fill: var(--wb-red-soft); stroke: var(--wb-red); }
.smap svg .bd.warn .face { fill: var(--wb-amber-soft); stroke: var(--wb-amber); }
.smap svg .bd.wait .face { fill: var(--wb-paper-2); }
.smap svg .bub path { fill: var(--wb-paper); stroke: var(--wb-ink-2); stroke-width: 1.3; }
.smap svg .pill rect { fill: none; stroke: var(--wb-ink-3); stroke-width: 1.1; }
.smap svg .pill.warn rect { stroke: var(--wb-amber); }
.smap svg .overview, .smap svg .inside-layer { transition: opacity .35s; }
.smap svg .inside-layer { opacity: 0; pointer-events: none; }
.smap.inside > .smap-canvas svg .overview { opacity: 0; pointer-events: none; }
.smap.inside > .smap-canvas svg .inside-layer { opacity: 1; pointer-events: auto; }
.smap svg .block { cursor: pointer; transition: opacity .35s; outline: none; }
.smap svg .block .hit { fill: transparent; }
.smap svg .block:hover .ring, .smap svg .block:focus-visible .ring, .smap svg .block.sel .ring { opacity: 1; }
.smap svg .ring { opacity: 0; transition: opacity .2s; }
.smap svg .link, .smap svg .lab, .smap svg .spine, .smap svg .frame, .smap svg .lanes, .smap svg .thesis { transition: opacity .35s; }
.smap svg .dim { opacity: .25; }
.smap svg .gone { opacity: 0; pointer-events: none; }
.smap svg .step { cursor: pointer; }
.smap svg .step .hl { opacity: 0; transition: opacity .2s; }
.smap svg .step.hot .hl { opacity: .7; }
.smap svg .part { cursor: pointer; outline: none; }
.smap svg .part .sel { opacity: 0; }
.smap svg .part.on .sel, .smap svg .part:focus-visible .sel { opacity: 1; }
.smap svg .ghostcard { cursor: pointer; opacity: .78; }
.smap svg .ghostcard:hover { opacity: 1; }
.smap svg .sibcard { cursor: pointer; }
.smap svg .sibcard:hover .paper { fill: var(--wb-paper-2); }
.smap svg .overview .rec:not(.lit) { display: none; }
.smap svg .edge .hit { stroke: transparent; fill: none; stroke-width: 16; }
.smap svg .edge, .smap svg .elab { transition: opacity .15s; }
.smap svg .edge.faint { opacity: 0; pointer-events: none; }
.smap svg .elab.faint { opacity: 0; pointer-events: none; }
.smap svg .edge.faint.hov, .smap svg .edge.faint.on, .smap svg .elab.faint.hov, .smap svg .elab.faint.on { opacity: 1; }
.smap svg .inside-art.hovering .edge:not(.hov), .smap svg .inside-art.hovering .elab:not(.hov) { opacity: .28; }
.smap svg .inside-art.picked .edge:not(.on), .smap svg .inside-art.picked .elab:not(.on) { opacity: .4; }
.smap.still svg, .smap.still svg * { transition: none !important; }
@media print {
  .smap-chrome, .smap-hint { display: none !important; }
  .smap-pathline { display: block; }
}
:root[data-print] .smap .smap-chrome, :root[data-print] .smap .smap-hint { display: none !important; }
:root[data-print] .smap .smap-pathline { display: block; }
.smap.clean { --smap-d-paper: #fbfbfc; --smap-d-ink: #172033; --smap-d-line: #e2e5eb; --wb-marker: #2f5bd8; --wb-marker-soft: rgba(47, 91, 216, .13); --wb-sticky: #fff4c2; --wb-sticky-ink: #3d3412; --wb-amber: #9a6a14; }
@media screen and (prefers-color-scheme: dark) { :root:not([data-theme="light"]):not([data-print]) .smap.clean { --smap-d-paper: #16191e; --smap-d-ink: #e8eaee; --smap-d-line: #30353d; --wb-marker: #8aa9ff; --wb-marker-soft: rgba(138, 169, 255, .16); --wb-sticky: #463f22; --wb-sticky-ink: #f4ebc3; } }
@media screen { :root[data-theme="dark"]:not([data-print]) .smap.clean { --smap-d-paper: #16191e; --smap-d-ink: #e8eaee; --smap-d-line: #30353d; --wb-marker: #8aa9ff; --wb-marker-soft: rgba(138, 169, 255, .16); --wb-sticky: #463f22; --wb-sticky-ink: #f4ebc3; } }
.smap.clean svg text { font-family: var(--smap-sans); }
.smap.clean svg .dotgrid circle { fill: color-mix(in srgb, var(--wb-ink) 8%, var(--wb-paper)); }
.smap.clean svg .block { filter: drop-shadow(0 1px 1.5px rgba(16, 24, 40, .09)); }
.smap.clean svg .f-blue path { fill: var(--wb-tint-blue); stroke: none; opacity: .5; }
.smap.clean svg .f-green path { fill: var(--wb-tint-green); stroke: none; opacity: .7; }
.smap.clean svg .f-amber path { fill: var(--wb-tint-amber); stroke: none; }
.smap.clean svg .f-red path { fill: var(--wb-tint-red); stroke: none; }
.smap.clean svg .f-grey path { fill: var(--wb-tint-grey); stroke: none; }
.smap.clean svg .k-hl path { stroke: var(--wb-marker-soft); }
.smap.clean svg .note { filter: drop-shadow(0 1px 2px rgba(16, 24, 40, .16)); }
.smap.clean svg .note .k-ink3 path { stroke: color-mix(in srgb, var(--wb-sticky-ink) 18%, var(--wb-sticky)); }
.smap.clean .smap-chip { border-width: 1px; font-weight: 550; }
.smap.clean .smap-tools button { border-radius: 8px; box-shadow: none; }
`;

const TY = {
  thesis: { size: 23, weight: 600, lh: 29 },
  name: { size: 21, weight: 700, lh: 25 },
  role: { size: 17, weight: 400, lh: 21 },
  step: { size: 18, weight: 600, lh: 22 },
  link: { size: 16, weight: 500, lh: 19 },
  frame: { size: 15, weight: 600, lh: 18 },
  note: { size: 19.5, weight: 500, lh: 24 },
  heap: { size: 15, weight: 500, lh: 18 },
  member: { size: 18, weight: 700, lh: 22 },
  mrole: { size: 15.5, weight: 400, lh: 19 },
  hold: { size: 15, weight: 500, lh: 18 },
  small: { size: 15, weight: 500, lh: 18, sans: true },
  badge: { size: 13, weight: 700, lh: 16, sans: true },
  tag: { size: 12, weight: 700, lh: 14, sans: true },
  lane: { size: 13, weight: 650, lh: 16, sans: true },
  disc: { size: 13, weight: 700, lh: 16, sans: true },
  title: { size: 28, weight: 700, lh: 32 },
};
const GLIMPSE = { ...TY, thesis: { size: 34, weight: 600, lh: 40 }, name: { size: 30, weight: 700, lh: 34 }, frame: { size: 17, weight: 600, lh: 21 }, badge: { size: 20, weight: 700, lh: 24, sans: true } };
const CLEAN_TY = {
  thesis: { size: 20, weight: 650, lh: 26, sans: true },
  name: { size: 16.5, weight: 650, lh: 21, sans: true },
  role: { size: 14, weight: 400, lh: 18.5, sans: true },
  step: { size: 15, weight: 600, lh: 19.5, sans: true },
  link: { size: 13.5, weight: 500, lh: 17, sans: true },
  frame: { size: 12.5, weight: 650, lh: 15, sans: true },
  note: { size: 14.5, weight: 500, lh: 19.5, sans: true },
  heap: { size: 13, weight: 500, lh: 16.5, sans: true },
  member: { size: 14.5, weight: 650, lh: 18.5, sans: true },
  mrole: { size: 13, weight: 400, lh: 16.5, sans: true },
  hold: { size: 13, weight: 500, lh: 16.5, sans: true },
  small: { size: 13, weight: 500, lh: 17, sans: true },
  badge: { size: 12, weight: 700, lh: 15, sans: true },
  tag: { size: 11, weight: 700, lh: 13, sans: true },
  lane: { size: 11.5, weight: 650, lh: 14, sans: true },
  disc: { size: 12, weight: 700, lh: 15, sans: true },
  title: { size: 24, weight: 700, lh: 28, sans: true },
};
const CLEAN_GLIMPSE = { ...CLEAN_TY, thesis: { size: 28, weight: 650, lh: 34, sans: true }, name: { size: 25, weight: 650, lh: 30, sans: true }, frame: { size: 14.5, weight: 650, lh: 18, sans: true }, badge: { size: 18, weight: 700, lh: 22, sans: true } };
const LY = { pad: 14, minW: 176, maxW: 204, phone: 165, stack: 22, rowGap: 40, phoneRowGap: 70, gutter: [60, 190], phoneGutter: [40, 120], frame: 16, inset: 16, margin: 22, lane: 28, labelW: 116 };
const SKINS = {
  sketch: { ty: TY, glimpse: GLIMPSE, ly: LY, least: { note: 16, step: 14.5, block: 14, part: 12.5, edge: 13, glimpse: 17.5 }, clean: false },
  clean: { ty: CLEAN_TY, glimpse: CLEAN_GLIMPSE, ly: { ...LY, minW: 150, maxW: 184, phone: 150, rowGap: 64, gutter: [48, 160], phoneGutter: [34, 104], labelW: 104 }, least: { note: 12, step: 12.4, block: 11.5, part: 10.5, edge: 10.8, glimpse: 14 }, clean: true },
};
const READ = 0.84;
const GLIMPSE_NAME = 18;
const TALL_AT = 900;
const LANE_AT = { main: 0, background: 1, control: 2 };
const LANES = ["Main path", "Background", "Control"];
const LINK = {
  sync: { cls: "k-ink", fh: "fh-ink", sw: 2.2, dash: null, head: "fill" },
  async: { cls: "k-ink2", fh: "fh-ink2", sw: 1.8, dash: [9, 7], head: "open" },
  copy: { cls: "k-ink", fh: "fh-ink", sw: 2, dash: [14, 6], head: "double" },
  control: { cls: "k-ink3", fh: "fh-ink3", sw: 1.6, dash: [2, 6], head: "open" },
};
const FRAME = {
  zone: { cls: "k-amber", t: "t-amber", dash: [12, 9], sw: 1.6 },
  machine: { cls: "k-ink3", t: "t-ink3", dash: [6, 7], sw: 1.5 },
  process: { cls: "k-ink3", t: "t-ink3", dash: [2, 5], sw: 1.2 },
};
const SIL = { memory: "chip", disk: "cylinder", bucket: "bucket", keys: "key", pages: "pages" };
const INSET = {
  actor: { t: 14, r: 14, b: 14, l: 14, g: 46 },
  service: { t: 14, r: 14, b: 26, l: 20, g: 30 },
  worker: { t: 14, r: 14, b: 14, l: 14, g: 30 },
  app: { t: 32, r: 14, b: 14, l: 14, g: 0 },
  chip: { t: 12, r: 12, b: 12, l: 14, g: 0 },
  cylinder: { t: 28, r: 12, b: 18, l: 14, g: 0 },
  bucket: { t: 30, r: 18, b: 16, l: 18, g: 0 },
  key: { t: 14, r: 14, b: 14, l: 14, g: 36 },
  pages: { t: 18, r: 22, b: 14, l: 14, g: 0 },
  external: { t: 14, r: 14, b: 14, l: 14, g: 30 },
  package: { t: 24, r: 14, b: 14, l: 14, g: 0 },
  page: { t: 14, r: 26, b: 14, l: 14, g: 0 },
};
const DOERS = new Set(["service", "worker", "app"]);
const STORES = new Set(["store", "log"]);
const TONE = { ok: "pass", fail: "fail", warn: "warn", info: "wait" };
const MARK = { ok: "✓", fail: "✗", warn: "!", info: "" };
const CHIP = { ok: "ok", fail: "fail", pending: "wait" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hash = (s) => { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return (h >>> 0) % 2147483646 + 1; };
const byId = (xs) => Object.fromEntries((xs || []).map((x) => [x.id, x]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const grow = (r, d) => ({ x: r.x - d, y: r.y - d, w: r.w + d * 2, h: r.h + d * 2 });
const area = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
const frames = (n) => new Promise((r) => { const tick = (k) => (k ? requestAnimationFrame(() => tick(k - 1)) : r()); tick(n); });
const oneLine = (s) => String(s).split(/\s+/).filter(Boolean).join(" ");

let meter = null;
const widths = new Map();
const measure = (s, ty) => {
  const k = `${ty.weight}|${ty.size}|${ty.sans ? 1 : 0}|${s}`;
  if (!widths.has(k)) {
    meter = meter || document.createElement("canvas").getContext("2d");
    meter.font = `${ty.weight} ${ty.size}px ${ty.sans ? SANS : HAND}`;
    widths.set(k, meter.measureText(s).width);
  }
  return widths.get(k);
};

const chop = (word, maxW, ty) => {
  if (measure(word, ty) <= maxW) return [word];
  const out = [];
  let cur = "";
  for (const bit of word.split(/(?<=[/_.:-])/)) {
    for (const ch of measure(bit, ty) > maxW ? [...bit] : [bit]) {
      if (cur && measure(cur + ch, ty) > maxW) { out.push(cur); cur = ch; } else cur += ch;
    }
  }
  if (cur) out.push(cur);
  return out;
};
const wrap = (text, maxW, ty) => {
  const out = [];
  let cur = "";
  for (const w of String(text).split(/\s+/).filter(Boolean).flatMap((x) => chop(x, maxW, ty))) {
    const next = cur ? `${cur} ${w}` : w;
    if (!cur || measure(next, ty) <= maxW) cur = next;
    else { out.push(cur); cur = w; }
  }
  if (cur) out.push(cur);
  return out;
};
const balance = (text, maxW, ty) => {
  let lines = wrap(text, maxW, ty);
  let w = maxW;
  if (lines.length > 2) {
    const wider = wrap(text, maxW * 1.3, ty);
    if (wider.length < lines.length) { lines = wider; w = maxW * 1.3; }
  }
  if (lines.length < 2) return lines;
  let lo = Math.min(w, Math.max(30, ...String(text).split(/\s+/).filter(Boolean).map((x) => measure(x, ty))));
  let hi = w;
  while (hi - lo > 2) { const mid = (lo + hi) / 2; if (wrap(text, mid, ty).length <= lines.length) hi = mid; else lo = mid; }
  return wrap(text, hi, ty);
};

let fontGen = 0;
const live = new Set();
let printFlag = false;
const printing = () => printFlag || document.documentElement.hasAttribute("data-print") || matchMedia("print").matches;
let watching = false;
const watchPrint = () => {
  if (watching) return;
  watching = true;
  const again = () => { for (const b of live) b.refresh(); };
  new MutationObserver(again).observe(document.documentElement, { attributeFilter: ["data-print"] });
  matchMedia("print").addEventListener("change", again);
  addEventListener("beforeprint", () => { printFlag = true; again(); });
  addEventListener("afterprint", () => { printFlag = false; again(); });
};
let fontWait = null;
const sheet = () => {
  const link = document.querySelector('link[href*="family=Shantell+Sans"]');
  if (!link || link.sheet) return Promise.resolve();
  return new Promise((r) => { link.addEventListener("load", r, { once: true }); link.addEventListener("error", r, { once: true }); });
};
const loadFonts = () => {
  if (!fontWait) {
    const faces = sheet().then(() => Promise.all([400, 500, 600, 700].map((w) => document.fonts.load(`${w} 20px "Shantell Sans"`))));
    fontWait = Promise.race([faces, new Promise((r) => setTimeout(r, 3000))]).catch(() => {}).then(() => { widths.clear(); fontGen += 1; });
  }
  return fontWait;
};
const injectCss = () => {
  if (document.getElementById("smap-css")) return;
  const st = document.createElement("style");
  st.id = "smap-css";
  st.textContent = CSS;
  document.head.append(st);
};
const injectFont = () => {
  if (document.querySelector('link[href*="family=Shantell+Sans"]')) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = FONT_HREF;
  document.head.append(link);
};

const crisp = () => {
  const shape = (d, o) => {
    const g = document.createElementNS(NS, "g");
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", d);
    p.setAttribute("fill", o.fill || "none");
    p.setAttribute("stroke", o.stroke || "#000");
    p.setAttribute("stroke-width", o.strokeWidth ?? 1);
    p.setAttribute("stroke-linecap", "round");
    p.setAttribute("stroke-linejoin", "round");
    if (o.strokeLineDash) p.setAttribute("stroke-dasharray", o.strokeLineDash.join(" "));
    g.append(p);
    return g;
  };
  const poly = (pts, close) => `M ${pts.map(([x, y]) => `${x} ${y}`).join(" L ")}${close ? " Z" : ""}`;
  const oval = (cx, cy, w, h) => `M ${cx - w / 2} ${cy} A ${w / 2} ${h / 2} 0 1 0 ${cx + w / 2} ${cy} A ${w / 2} ${h / 2} 0 1 0 ${cx - w / 2} ${cy} Z`;
  return {
    line: (x1, y1, x2, y2, o) => shape(`M ${x1} ${y1} L ${x2} ${y2}`, o),
    linearPath: (pts, o) => shape(poly(pts, false), o),
    polygon: (pts, o) => shape(poly(pts, true), o),
    path: (d, o) => shape(d, o),
    circle: (cx, cy, d, o) => shape(oval(cx, cy, d, d), o),
    ellipse: (cx, cy, w, h, o) => shape(oval(cx, cy, w, h), o),
    rectangle: (x, y, w, h, o) => {
      const r = Math.max(0, Math.min(8, w / 4, h / 4));
      return shape(`M ${x + r} ${y} H ${x + w - r} Q ${x + w} ${y} ${x + w} ${y + r} V ${y + h - r} Q ${x + w} ${y + h} ${x + w - r} ${y + h} H ${x + r} Q ${x} ${y + h} ${x} ${y + h - r} V ${y + r} Q ${x} ${y} ${x + r} ${y} Z`, o);
    },
  };
};

function router(boxes, vg, hg, avoid) {
  const list = Object.entries(boxes);
  const shy = avoid || [];
  const segHits = (x1, y1, x2, y2, r, pad) => {
    const lx = Math.min(x1, x2);
    const hx = Math.max(x1, x2);
    const ly = Math.min(y1, y2);
    const hy = Math.max(y1, y2);
    return hx > r.x - pad && lx < r.x + r.w + pad && hy > r.y - pad && ly < r.y + r.h + pad;
  };
  const blocked = (pts, a, b) => {
    for (let i = 0; i < pts.length - 1; i++) {
      const [x1, y1] = pts[i];
      const [x2, y2] = pts[i + 1];
      for (const [id, r] of list) {
        if ((id === a && i === 0) || (id === b && i === pts.length - 2)) continue;
        if (segHits(x1, y1, x2, y2, r, 7)) return true;
      }
    }
    return false;
  };
  const len = (pts) => pts.slice(1).reduce((s, p, i) => s + Math.abs(p[0] - pts[i][0]) + Math.abs(p[1] - pts[i][1]), 0);
  const crossings = (pts, taken) => {
    let n = 0;
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, ay] = pts[i];
      const [bx, by] = pts[i + 1];
      for (const q of taken) {
        for (let j = 0; j < q.length - 1; j++) {
          const [cx, cy] = q[j];
          const [dx, dy] = q[j + 1];
          const h1 = ay === by;
          const h2 = cy === dy;
          if (h1 === h2) {
            if (h1 && Math.abs(ay - cy) < 6 && Math.min(ax, bx) < Math.max(cx, dx) && Math.min(cx, dx) < Math.max(ax, bx)) n += 2;
            if (!h1 && Math.abs(ax - cx) < 6 && Math.min(ay, by) < Math.max(cy, dy) && Math.min(cy, dy) < Math.max(ay, by)) n += 2;
            continue;
          }
          const [hx1, hx2, hy] = h1 ? [ax, bx, ay] : [cx, dx, cy];
          const [vx, vy1, vy2] = h1 ? [cx, cy, dy] : [ax, ay, by];
          if (vx > Math.min(hx1, hx2) && vx < Math.max(hx1, hx2) && hy > Math.min(vy1, vy2) && hy < Math.max(vy1, vy2)) n += 1;
        }
      }
    }
    return n;
  };
  const candidates = (a, b) => {
    const A = boxes[a];
    const Z = boxes[b];
    const out = [];
    const acx = A.x + A.w / 2;
    const acy = A.y + A.h / 2;
    const zcx = Z.x + Z.w / 2;
    const zcy = Z.y + Z.h / 2;
    const oy0 = Math.max(A.y, Z.y);
    const oy1 = Math.min(A.y + A.h, Z.y + Z.h);
    if (oy1 - oy0 >= 22) {
      const y = (oy0 + oy1) / 2;
      if (A.x + A.w <= Z.x) out.push({ pts: [[A.x + A.w, y], [Z.x, y]], straight: true });
      else if (Z.x + Z.w <= A.x) out.push({ pts: [[A.x, y], [Z.x + Z.w, y]], straight: true });
    }
    const ox0 = Math.max(A.x, Z.x);
    const ox1 = Math.min(A.x + A.w, Z.x + Z.w);
    if (ox1 - ox0 >= 22) {
      const x = (ox0 + ox1) / 2;
      if (A.y + A.h <= Z.y) out.push({ pts: [[x, A.y + A.h], [x, Z.y]], straight: true });
      else if (Z.y + Z.h <= A.y) out.push({ pts: [[x, A.y], [x, Z.y + Z.h]], straight: true });
    }
    const ay = clamp(zcy, A.y + 14, A.y + A.h - 14);
    const zy = clamp(acy, Z.y + 14, Z.y + Z.h - 14);
    const ax = clamp(zcx, A.x + 14, A.x + A.w - 14);
    const zx = clamp(acx, Z.x + 14, Z.x + Z.w - 14);
    const sideX = (r, g) => (g > r.x + r.w ? r.x + r.w : r.x);
    const sideY = (r, g) => (g > r.y + r.h ? r.y + r.h : r.y);
    const outX = (r, g) => g >= r.x + r.w || g <= r.x;
    const outY = (r, g) => g >= r.y + r.h || g <= r.y;
    if (outY(Z, ay) && outX(A, zx)) out.push({ pts: [[sideX(A, zx), ay], [zx, ay], [zx, sideY(Z, ay)]] });
    if (outX(Z, ax) && outY(A, zy)) out.push({ pts: [[ax, sideY(A, zy)], [ax, zy], [sideX(Z, ax), zy]] });
    for (const g of vg) {
      if (!outX(A, g) || !outX(Z, g)) continue;
      if (Math.abs(ay - zy) < 1) out.push({ pts: [[sideX(A, g), ay], [sideX(Z, g), zy]], straight: true, via: true });
      else out.push({ pts: [[sideX(A, g), ay], [g, ay], [g, zy], [sideX(Z, g), zy]], gut: ["v", g] });
    }
    for (const g of hg) {
      if (!outY(A, g) || !outY(Z, g)) continue;
      if (Math.abs(ax - zx) < 1) continue;
      out.push({ pts: [[ax, sideY(A, g)], [ax, g], [zx, g], [zx, sideY(Z, g)]], gut: ["h", g] });
    }
    return out;
  };
  const hits = (pts, a, b) => {
    let n = 0;
    for (let i = 0; i < pts.length - 1; i++) {
      const [x1, y1] = pts[i];
      const [x2, y2] = pts[i + 1];
      for (const [id, r] of list) {
        if ((id === a && i === 0) || (id === b && i === pts.length - 2)) continue;
        if (segHits(x1, y1, x2, y2, r, 7)) n += 1;
      }
    }
    return n;
  };
  const detours = (a, b) => {
    const A = boxes[a];
    const Z = boxes[b];
    const ay = A.y + A.h / 2;
    const zy = Z.y + Z.h / 2;
    const ax = A.x + A.w / 2;
    const zx = Z.x + Z.w / 2;
    const outX = (r, g) => g >= r.x + r.w || g <= r.x;
    const outY = (r, g) => g >= r.y + r.h || g <= r.y;
    const sideX = (r, g) => (g > r.x + r.w ? r.x + r.w : r.x);
    const sideY = (r, g) => (g > r.y + r.h ? r.y + r.h : r.y);
    const out = [];
    for (const h of hg) {
      for (const g1 of vg) {
        if (!outX(A, g1)) continue;
        for (const g2 of vg) {
          if (!outX(Z, g2)) continue;
          out.push({ pts: [[sideX(A, g1), ay], [g1, ay], [g1, h], [g2, h], [g2, zy], [sideX(Z, g2), zy]] });
        }
        if (outY(Z, h)) out.push({ pts: [[sideX(A, g1), ay], [g1, ay], [g1, h], [zx, h], [zx, sideY(Z, h)]] });
      }
      if (outY(A, h)) for (const g2 of vg) if (outX(Z, g2)) out.push({ pts: [[ax, sideY(A, h)], [ax, h], [g2, h], [g2, zy], [sideX(Z, g2), zy]] });
    }
    return out.map((c) => ({ ...c, pts: c.pts.filter((p, i, xs) => i === 0 || p[0] !== xs[i - 1][0] || p[1] !== xs[i - 1][1]) }));
  };
  return (a, b, taken) => {
    let best = null;
    const score = (c) => len(c.pts) + (c.pts.length - 2) * 48 + crossings(c.pts, taken) * 36 + shy.reduce((n, r) => n + c.pts.slice(1).filter((p, i) => segHits(c.pts[i][0], c.pts[i][1], p[0], p[1], r, 2)).length, 0) * 400;
    for (const c of [...candidates(a, b), ...detours(a, b)]) {
      if (c.via) continue;
      if (blocked(c.pts, a, b)) continue;
      const sc = score(c);
      if (!best || sc < best.score) best = { ...c, score: sc };
    }
    if (best) return best;
    for (const c of [...candidates(a, b), ...detours(a, b)]) {
      if (c.via) continue;
      const sc = hits(c.pts, a, b) * 2000 + score(c);
      if (!best || sc < best.score) best = { ...c, score: sc, loose: true };
    }
    return best;
  };
}

function tracks(routes) {
  const lanes = new Map();
  routes.forEach((r) => {
    if (!r || !r.gut) return;
    const i = 1;
    const [kind, at] = r.gut;
    const [p, q] = [r.pts[i], r.pts[i + 1]];
    const lo = kind === "v" ? Math.min(p[1], q[1]) : Math.min(p[0], q[0]);
    const hi = kind === "v" ? Math.max(p[1], q[1]) : Math.max(p[0], q[0]);
    const key = `${kind}${Math.round(at)}`;
    if (!lanes.has(key)) lanes.set(key, []);
    lanes.get(key).push({ r, kind, lo, hi });
  });
  for (const segs of lanes.values()) {
    const used = [];
    for (const s of segs) {
      let off = 0;
      for (const k of [0, 10, -10, 20, -20, 30, -30, 40, -40]) {
        if (!used.some((u) => u.off === k && u.lo < s.hi + 14 && s.lo < u.hi + 14)) { off = k; break; }
      }
      used.push({ off, lo: s.lo, hi: s.hi });
      const ax = s.kind === "v" ? 0 : 1;
      s.r.pts[1][ax] += off;
      s.r.pts[2][ax] += off;
    }
  }
}

function placer(hard) {
  const taken = hard.map((r) => ({ ...r }));
  const leads = [];
  const through = (r, [[x1, y1], [x2, y2]]) => {
    const n = Math.ceil(Math.hypot(x2 - x1, y2 - y1) / 3);
    for (let i = 0; i <= n; i++) {
      const x = x1 + ((x2 - x1) * i) / n;
      const y = y1 + ((y2 - y1) * i) / n;
      if (x > r.x && x < r.x + r.w && y > r.y && y < r.y + r.h) return true;
    }
    return false;
  };
  const free = (r) => taken.every((o) => !(r.x < o.x + o.w && o.x < r.x + r.w && r.y < o.y + o.h && o.y < r.y + r.h)) && !leads.some((l) => through(r, l));
  const crosses = (pts, r) => {
    for (let i = 0; i < pts.length - 1; i++) {
      const [x1, y1] = pts[i];
      const [x2, y2] = pts[i + 1];
      if (Math.max(x1, x2) > r.x && Math.min(x1, x2) < r.x + r.w && Math.max(y1, y2) > r.y && Math.min(y1, y2) < r.y + r.h) return true;
    }
    return false;
  };
  const tie = (r, mx, my) => [[clamp(mx, r.x, r.x + r.w), clamp(my, r.y, r.y + r.h)], [mx, my]];
  const toward = (r, [x1, y1], [x2, y2]) => {
    const L = (x2 - x1) ** 2 + (y2 - y1) ** 2;
    const t = L ? clamp(((r.x + r.w / 2 - x1) * (x2 - x1) + (r.y + r.h / 2 - y1) * (y2 - y1)) / L, 0.15, 0.85) : 0.5;
    return tie(r, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t);
  };
  const cuts = ([[x1, y1], [x2, y2]]) => {
    const n = Math.ceil(Math.hypot(x2 - x1, y2 - y1) / 4);
    for (let i = 1; i < n; i++) {
      const x = x1 + ((x2 - x1) * i) / n;
      const y = y1 + ((y2 - y1) * i) / n;
      if (taken.some((o) => x > o.x && x < o.x + o.w && y > o.y && y < o.y + o.h)) return true;
    }
    return false;
  };
  const spiral = (size, mx, my, soft, strict, reach, lead) => {
    for (let rad = 30; rad <= (reach || 520); rad += 14) {
      let best = null;
      for (let k = 0; k < 24; k++) {
        const a = (k / 24) * Math.PI * 2;
        const r = { x: mx + Math.cos(a) * rad - size.w / 2, y: my + Math.sin(a) * rad - size.h / 2, w: size.w, h: size.h };
        if (!free(r)) continue;
        const cost = soft.filter((p) => crosses(p, r)).length;
        if (strict && ((strict === "clear" && cost) || !lead(r))) continue;
        if (!best || cost < best.cost) best = { r, cost };
      }
      if (best) return best.r;
    }
    return null;
  };
  const near = (size, pts, soft, strict) => {
    const segs = [];
    for (let i = 0; i < pts.length - 1; i++) segs.push([pts[i], pts[i + 1], Math.abs(pts[i + 1][0] - pts[i][0]) + Math.abs(pts[i + 1][1] - pts[i][1])]);
    segs.sort((p, q) => q[2] - p[2]);
    let best = null;
    segs.forEach(([p, q, len], rank) => {
      if (len < 10) return;
      const horiz = Math.abs(p[1] - q[1]) < Math.abs(p[0] - q[0]);
      for (const t of [0.5, 0.38, 0.62, 0.26, 0.74, 0.14, 0.86]) {
        const cx = p[0] + (q[0] - p[0]) * t;
        const cy = p[1] + (q[1] - p[1]) * t;
        for (const d of [5, 13, 24, 38]) {
          const opts = horiz ? [[cx - size.w / 2, cy - size.h - d], [cx - size.w / 2, cy + d]] : [[cx + d, cy - size.h / 2], [cx - size.w - d, cy - size.h / 2]];
          for (const [x, y] of opts) {
            const r = { x, y, w: size.w, h: size.h };
            if (!free(r)) continue;
            const n = soft.filter((s) => crosses(s, r)).length;
            const cost = Math.abs(t - 0.5) * 30 + d * 1.1 + rank * 8 + n * 40;
            if (!best || cost < best.cost) best = { r, cost, n };
          }
        }
      }
    });
    if (best && !best.n) { taken.push(best.r); return { r: best.r, leader: null }; }
    const [p, q] = segs[0] || [pts[0], pts[pts.length - 1]];
    const mx = (p[0] + q[0]) / 2;
    const my = (p[1] + q[1]) / 2;
    const first = strict ? (r) => toward(r, p, q) : (r) => tie(r, mx, my);
    const along = (r) => segs.flatMap(([a, b]) => [0.5, 0.3, 0.7, 0.15, 0.85].map((t) => tie(r, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))).find((l) => !cuts(l)) || null;
    const lead = (r) => { const l = first(r); return cuts(l) ? along(r) : l; };
    const clear = spiral(size, mx, my, soft, "clear", 120, lead);
    if (best && !clear) { taken.push(best.r); return { r: best.r, leader: null }; }
    const r = clear || spiral(size, mx, my, soft, "tied", 520, lead) || spiral(size, mx, my, soft);
    if (!r) return null;
    const leader = lead(r);
    taken.push(r);
    if (leader) leads.push(leader);
    return { r, leader };
  };
  near.around = (size, bx, soft, within) => {
    const opts = [];
    for (const d of [12, 26, 44]) {
      opts.push([bx.x + bx.w + d, bx.y + bx.h - size.h + 8], [bx.x + bx.w + d, bx.y - 4], [bx.x + bx.w - size.w + 20, bx.y + bx.h + d], [bx.x, bx.y + bx.h + d], [bx.x + bx.w - size.w, bx.y - size.h - d], [bx.x - size.w - d, bx.y + bx.h - size.h], [bx.x - size.w - d, bx.y]);
    }
    const outside = (r) => (within ? Math.max(0, within.x - r.x) + Math.max(0, r.x + r.w - within.x - within.w) + Math.max(0, within.y - r.y) + Math.max(0, r.y + r.h - within.y - within.h) : 0);
    const cut = (r) => soft.filter((s) => crosses(s, r)).length;
    let best = null;
    opts.forEach(([x, y], i) => {
      const r = { x, y, w: size.w, h: size.h };
      if (!free(r)) return;
      const n = cut(r);
      const cost = i + n * 400 + outside(r) * 0.6;
      if (!best || cost < best.cost) best = { r, cost, n };
    });
    if (best && best.n) {
      const cx = bx.x + bx.w / 2;
      const cy = bx.y + bx.h / 2;
      for (const loose of [false, true]) {
        for (let rad = 30; rad <= 260 && best.n; rad += 14) {
          for (let k = 0; k < 24; k++) {
            const a = (k / 24) * Math.PI * 2;
            const r = { x: cx + Math.cos(a) * rad - size.w / 2, y: cy + Math.sin(a) * rad - size.h / 2, w: size.w, h: size.h };
            if (free(r) && !cut(r) && (loose || !outside(r))) { best = { r, n: 0 }; break; }
          }
        }
      }
    }
    const r = best ? best.r : spiral(size, bx.x + bx.w / 2, bx.y + bx.h / 2, soft);
    if (r) taken.push(r);
    return r;
  };
  near.add = (r) => taken.push(r);
  near.free = free;
  return near;
}

const offsetLine = (pts, d) => {
  const segs = pts.slice(1).map((p, i) => {
    const q = pts[i];
    const h = Math.abs(p[1] - q[1]) < Math.abs(p[0] - q[0]);
    return { h, off: d * (h ? Math.sign(p[0] - q[0]) : Math.sign(p[1] - q[1])) };
  });
  return pts.map(([x, y], i) => {
    let nx = x;
    let ny = y;
    for (const s of i === 0 ? [segs[0]] : i === pts.length - 1 ? [segs[segs.length - 1]] : [segs[i - 1], segs[i]]) {
      if (s.h) ny = y + s.off;
      else nx = x - s.off;
    }
    return [nx, ny];
  });
};

export function validate(map, o = {}) {
  if (!map || typeof map !== "object" || !Array.isArray(map.blocks)) return ["the map needs a title and a list of blocks"];
  const out = [];
  const groups = map.groups || [];
  const links = map.links || [];
  const stories = map.stories || [];
  const pathSteps = (map.path && map.path.steps) || [];
  const kindOf = {};
  const owner = {};
  const claim = (id, kind) => {
    if (Object.hasOwn(kindOf, id)) out.push(`id "${id}" is used twice; ids are unique across groups, blocks, parts and stories`);
    else kindOf[id] = kind;
  };
  for (const g of groups) claim(g.id, "group");
  for (const b of map.blocks) {
    claim(b.id, "block");
    owner[b.id] = b.id;
    for (const p of b.parts || []) { claim(p.id, "part"); owner[p.id] = b.id; }
  }
  for (const s of stories) claim(s.id, "story");
  const G = byId(groups);
  for (const g of groups) {
    if ((g.kind || "zone") === "process") {
      if (!g.in) out.push(`group "${g.id}": a process group needs "in" naming the machine group it runs on`);
      else if (!G[g.in] || G[g.in].kind !== "machine") out.push(`group "${g.id}": "in" names "${g.in}", which is not a machine group`);
    } else if (g.in) out.push(`group "${g.id}": only a process group takes "in"`);
  }
  const ref = (where, field, id) => { if (kindOf[id] !== "block" && kindOf[id] !== "part") out.push(`${where}: "${field}" names "${id}", which is not a block or part`); };
  for (const b of map.blocks) {
    if (b.group && kindOf[b.group] !== "group") out.push(`block "${b.id}": "group" names "${b.group}", which is not a group`);
    const store = STORES.has(b.kind);
    if (store && !b.medium) out.push(`block "${b.id}": a ${b.kind} block needs a "medium"`);
    if (!store) for (const f of ["medium", "tag", "contents"]) if (b[f] != null) out.push(`block "${b.id}": only store and log blocks take "${f}"`);
    for (const p of b.parts || []) {
      if (p.shape === "store" && !p.medium) out.push(`part "${p.id}": a store part needs a "medium"`);
      if (p.shape !== "store") for (const f of ["medium", "tag"]) if (p[f] != null) out.push(`part "${p.id}": only a store part takes "${f}"`);
    }
  }
  links.forEach((l, i) => {
    ref(`link ${i + 1}`, "from", l.from);
    ref(`link ${i + 1}`, "to", l.to);
    if (l.from === l.to) out.push(`link ${i + 1}: joins "${l.from}" to itself`);
    else if (owner[l.from] && owner[l.from] === owner[l.to] && (l.from === owner[l.from] || l.to === owner[l.to])) out.push(`link ${i + 1}: joins block "${owner[l.from]}" to one of its own parts`);
  });
  pathSteps.forEach((s, i) => {
    ref(`path step ${i + 1}`, "from", s.from);
    ref(`path step ${i + 1}`, "to", s.to);
    if (s.waitsFor != null && !(Number.isInteger(s.waitsFor) && s.waitsFor >= 1 && s.waitsFor <= i)) out.push(`path step ${i + 1}: "waitsFor" is ${s.waitsFor}, which is not an earlier step`);
    else if (s.waitsFor != null && owner[s.from] && owner[s.from] === owner[s.to]) out.push(`path step ${i + 1}: "waitsFor" needs an arrow to sit on, but this step stays inside block "${owner[s.from]}"`);
    if (s.mark != null) out.push(`path step ${i + 1}: "mark" belongs only on story steps`);
  });
  const labels = new Set();
  for (const st of stories) {
    if (labels.has(st.label)) out.push(`story label "${st.label}" is used twice`);
    labels.add(st.label);
    (st.steps || []).forEach((s, i) => {
      ref(`story "${st.id}" step ${i + 1}`, "from", s.from);
      ref(`story "${st.id}" step ${i + 1}`, "to", s.to);
      if (s.waitsFor != null) out.push(`story "${st.id}" step ${i + 1}: "waitsFor" belongs only on the main path`);
    });
  }
  (map.notes || []).forEach((n, i) => {
    if (n.link == null) { ref(`note ${i + 1}`, "on", n.on); return; }
    if (n.on != null) out.push(`note ${i + 1}: give "on" or "link", not both`);
    ref(`note ${i + 1}`, "link.from", n.link.from);
    ref(`note ${i + 1}`, "link.to", n.link.to);
    if (!links.some((l) => (l.from === n.link.from && l.to === n.link.to) || (l.from === n.link.to && l.to === n.link.from))) out.push(`note ${i + 1}: no link joins "${n.link.from}" and "${n.link.to}"`);
  });
  const start = map.start || {};
  if (start.story != null && kindOf[start.story] !== "story") out.push(`start: "story" names "${start.story}", which is not a story`);
  if (start.block != null) {
    const b = map.blocks.find((x) => x.id === start.block);
    if (!b) out.push(`start: "block" names "${start.block}", which is not a block`);
    else if (!(b.parts || []).length && !(b.contents || []).length) out.push(`start: block "${b.id}" has no parts to open`);
  }
  if (o.derived) return out;
  const used = new Set([...links, ...pathSteps, ...stories.flatMap((s) => s.steps || [])].flatMap((x) => [x.from, x.to]));
  for (const b of map.blocks) for (const p of b.parts || []) if (!used.has(p.id)) out.push(`part "${p.id}" in block "${b.id}" is in no link or step`);
  const arrows = new Set(links.filter((l) => owner[l.from] && owner[l.to] && owner[l.from] !== owner[l.to]).map((l) => `${[owner[l.from], owner[l.to]].sort().join("|")}|${l.style || "sync"}`));
  if (arrows.size > 12) out.push(`the links roll up to ${arrows.size} arrows between blocks; keep it to 12`);
  return out;
}

function normalize(map) {
  const owner = {};
  for (const b of map.blocks) { owner[b.id] = b.id; for (const p of b.parts || []) owner[p.id] = b.id; }
  const items = {};
  const parts = [];
  const blocks = map.blocks.map((b, index) => {
    const store = STORES.has(b.kind);
    const own = b.parts || [];
    const members = [];
    if (store || !own.length) {
      items[b.id] = { id: b.id, label: b.name, type: store ? "store" : b.kind === "actor" ? "actor" : "component", status: b.status || "live", what: b.about || "", medium: b.medium, tag: b.tag, holds: b.contents || [], badges: [] };
      members.push(b.id);
    }
    for (const p of own) {
      items[p.id] = { id: p.id, label: p.name, type: p.shape === "store" ? "store" : "component", status: p.status || "live", what: p.about || "", medium: p.medium, tag: p.tag, holds: [], badges: p.badges || [] };
      parts.push({ part: p.id, shape: p.shape, role: p.role });
      members.push(p.id);
    }
    return { id: b.id, kind: b.kind, name: b.name, role: b.role, what: b.about || "", frame: b.group || null, members, medium: b.medium, tag: b.tag, status: b.status || "live", critical: !!b.critical, badges: b.badges || [], lane: b.lane || null, open: own.length > 0 || (b.contents || []).length > 0, index };
  });
  const K = byId(map.blocks);
  const port = (id) => {
    if (items[id]) return id;
    const own = K[id].parts;
    return (own.find((p) => p.shape !== "store") || own[0]).id;
  };
  const flows = {};
  const roll = [];
  (map.links || []).forEach((l, i) => {
    const kind = l.style || "sync";
    const f = { id: `l${i}`, src: l.from, dst: l.to, from: port(l.from), to: port(l.to), label: l.label || "", kind, mode: kind === "sync" || kind === "control" ? "sync" : "async", critical: false, hidden: !!l.hidden };
    flows[f.id] = f;
    const a = owner[l.from];
    const z = owner[l.to];
    if (a === z) return;
    const key = `${[a, z].sort().join("|")}|${kind}`;
    let r = roll.find((x) => x.key === key);
    if (!r) { r = { key, from: a, to: z, kind, label: "", flows: [], hidden: true }; roll.push(r); }
    if (!r.label) r.label = f.label;
    r.flows.push(f.id);
    r.hidden = r.hidden && f.hidden;
  });
  const all = Object.values(flows);
  const stepOf = (s) => {
    const a = owner[s.from];
    const z = owner[s.to];
    let use = all.filter((f) => (f.src === s.from && f.dst === s.to) || (f.src === s.to && f.dst === s.from));
    if (!use.length && a !== z) {
      use = all.filter((f) => owner[f.src] === a && owner[f.dst] === z);
      if (!use.length) use = all.filter((f) => owner[f.src] === z && owner[f.dst] === a);
    }
    return { from: a, to: z, at: [s.from, s.to], say: s.say, flows: use.map((f) => f.id) };
  };
  const path = map.path || { title: "", steps: [] };
  const spine = { title: path.title, steps: path.steps.map((s) => ({ ...stepOf(s), waitsFor: s.waitsFor || null })) };
  for (const s of spine.steps) for (const f of s.flows) flows[f].critical = true;
  const paths = (map.stories || []).map((st) => ({ id: st.id, label: st.label, title: st.title || st.label, tone: st.tone || null, steps: st.steps.map((s) => ({ ...stepOf(s), stop: s.mark === "stop", risk: s.mark === "risk" })) }));
  const walk = (steps) => { const m = new Map(); steps.forEach((s, i) => { for (const f of s.flows) if (!m.has(f)) m.set(f, i + 1); }); return m; };
  const walks = { "": walk(spine.steps) };
  for (const p of paths) walks[p.id] = walk(p.steps);
  const lanes = map.lanes || {};
  return {
    thesis: map.title,
    frames: (map.groups || []).map((g) => ({ id: g.id, scope: g.kind || "zone", label: g.label, parent: g.in || null })),
    blocks,
    parts,
    items,
    owner,
    flows,
    links: roll.map(({ key, ...r }) => r),
    spine,
    paths,
    walks,
    callouts: (map.notes || []).map((n) => (n.link ? { block: null, link: [n.link.from, n.link.to], text: n.text, source: null } : { block: owner[n.on], text: n.text, source: n.on })),
    lanes: [lanes.main || LANES[0], lanes.background || LANES[1], lanes.control || LANES[2]],
    start: map.start || null,
  };
}

let boards = 0;

class Board {
  constructor(host, model, o) {
    this.o = { ...o, compact: false };
    this.skin = SKINS[o.skin || "sketch"];
    this.T = this.skin.ty;
    this.L = this.skin.ly;
    this.ty = this.T;
    this.id = ++boards;
    this.st = { layout: null, stack: false, story: null, shape: null, part: null, sel: null, step: null, vb: null, hot: null };
    this.root = document.createElement("div");
    this.root.className = `smap${o.dialog ? " dialog" : ""}${this.skin.clean ? " clean" : ""}`;
    const end = o.dialog
      ? `<button type="button" data-z="close" aria-label="Close the map">&times;</button>`
      : `<button type="button" data-z="open" aria-label="Open the map full screen"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5 9 7M2.5 13.5 7 9" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>`;
    this.root.innerHTML = `<div class="smap-bar smap-chrome"><nav class="smap-crumbs" aria-label="Where you are"></nav><nav class="smap-chips" aria-label="Stories"></nav><div class="smap-tools"><button type="button" data-z="fit" aria-label="Zoom out to show all of it">Fit</button><button type="button" data-z="in" aria-label="Zoom in">+</button><button type="button" data-z="out" aria-label="Zoom out">&minus;</button>${end}</div></div>
<div class="smap-canvas"><svg class="smap-svg" role="img"></svg></div>
<div class="smap-glimpse smap-chrome"><button type="button" class="smap-open">Open the map</button></div>
<div class="smap-readout" aria-live="polite"></div>`;
    host.append(this.root);
    this.svg = this.root.querySelector(".smap-svg");
    this.canvas = this.root.querySelector(".smap-canvas");
    this.readout = this.root.querySelector(".smap-readout");
    this.rc = this.skin.clean ? crisp() : o.rough.svg(this.svg);
    this.dragMoved = false;
    this.rev = 0;
    this.full = {};
    this.ready = new Promise((r) => { this.markReady = r; });
    this.interactions();
    live.add(this);
    watchPrint();
    this.ro = new ResizeObserver(() => {
      const r = this.canvas.getBoundingClientRect();
      const k = `${Math.round(r.width)}x${o.dialog ? Math.round(r.height) : 0}`;
      if (k === this.sized) return;
      this.sized = k;
      clearTimeout(this.rz);
      this.rz = setTimeout(() => this.refresh(), 60);
    });
    this.ro.observe(this.canvas);
    this.setModel(model, o.state || (model.start ? { story: model.start.story, block: model.start.block } : null));
    loadFonts().then(() => {
      if (this.gone) return;
      this.settled = true;
      this.refresh();
    });
  }

  setModel(model, view) {
    const was = this.view();
    this.M = model;
    this.rev += 1;
    this.full = {};
    this.derive();
    this.st.story = null;
    this.st.shape = null;
    this.st.part = null;
    this.st.sel = null;
    this.st.step = null;
    this.applyView(view || was);
    this.key = null;
    this.refresh();
  }

  view() {
    return { story: this.st.story || undefined, block: this.st.shape || this.st.sel || undefined, part: this.st.part || undefined };
  }

  applyView(v) {
    if (!v || !this.K) return;
    if (v.story && this.P[v.story]) this.st.story = v.story;
    const pid = v.part && this.items[v.part] && this.K[this.blockOf[v.part]] && this.K[this.blockOf[v.part]].open ? v.part : null;
    const bid = pid ? this.blockOf[pid] : v.block && this.K[v.block] ? v.block : null;
    if (bid && this.K[bid].open) { this.st.shape = bid; this.st.part = pid; }
    else if (bid) this.st.sel = bid;
  }

  derive() {
    const M = this.M;
    this.B = M;
    this.K = byId(M.blocks);
    this.F = byId(M.frames);
    this.P = byId(M.paths);
    this.items = M.items;
    this.blockOf = M.owner;
    this.flows = M.flows;
    this.rec = new Set(M.links.map((l, i) => (l.hidden ? i : -1)).filter((i) => i >= 0));
  }

  path(id) { return this.P[id] || null; }
  labelOf(id) { return this.items[id] ? this.items[id].label : this.nameOf(id); }
  nameOf(id) { return this.K[id] ? this.K[id].name : id; }
  pathTone(p) { return CHIP[p.tone] || ""; }
  openable(id) { return !!(this.K[id] && this.K[id].open); }

  sil(b) {
    if (STORES.has(b.kind)) return SIL[b.medium] || "pages";
    if (b.kind === "doc") return "page";
    if (b.kind === "artifact") return "package";
    return b.kind;
  }

  unbuilt(b) { return b.status !== "live"; }

  tagOf(b) { return STORES.has(b.kind) && b.tag ? b.tag : null; }

  inset(b) {
    const k = INSET[this.sil(b)];
    return this.o.compact ? { ...k, g: 0 } : k;
  }

  wantW(b) {
    const k = this.inset(b);
    return Math.max(measure(b.name, this.ty.name) + k.g, this.o.compact ? 0 : measure(b.role, this.T.role)) + k.l + k.r + 4;
  }

  wordW(b) {
    const k = this.inset(b);
    return Math.max(...b.name.split(/\s+/).map((x) => measure(x, this.ty.name))) + k.g + k.l + k.r + 4;
  }

  blockText(b, w) {
    const k = this.inset(b);
    const tw = w - k.l - k.r;
    const name = wrap(b.name, tw - k.g, this.ty.name);
    const role = this.o.compact ? [] : wrap(b.role, this.sil(b) === "actor" ? tw - k.g : tw, this.T.role);
    const foot = this.unbuilt(b) || (!this.o.compact && (this.tagOf(b) || b.kind === "log")) ? 26 : 0;
    const strip = this.o.compact ? [] : this.stripOf(b);
    const heaps = this.o.compact ? [] : this.heapOf(b).map((m) => { const p = this.shapeOf(m); return { m, lines: wrap(p && p.role ? p.role : this.labelOf(m), w - 76, this.T.heap) }; });
    const heapH = heaps.reduce((s, x) => s + Math.max(46, x.lines.length * this.T.heap.lh + 18) + 8, 0);
    return { k, name, role, foot, strip, heaps, h: k.t + name.length * this.ty.name.lh + 4 + role.length * this.T.role.lh + (strip.length ? 44 : 0) + (heapH ? heapH + 6 : 0) + foot + k.b };
  }

  heapOf(b) {
    return DOERS.has(b.kind) ? b.members.filter((m) => this.items[m].type === "store" && this.items[m].medium === "memory") : [];
  }

  stripOf(b) {
    if (!DOERS.has(b.kind)) return [];
    const A = b.kind === "service" ? this.anatomy(b.id) : null;
    const ids = A ? A.path.filter((m, i) => i === A.path.findIndex((x) => A.depth[x] === A.depth[m])) : b.members.filter((m) => this.items[m].type === "component" && this.shapeOf(m));
    return ids.length >= 2 ? ids.slice(0, 4) : [];
  }

  spineMap() {
    const L = this.B.links;
    const on = {};
    const free = [];
    const rails = [];
    this.B.spine.steps.forEach((s, i) => {
      if (s.from === s.to) { free.push(i); return; }
      const own = this.linkFor(s.from, s.to, true);
      if (own >= 0) { (on[own] = on[own] || []).push(i); return; }
      const back = this.linkFor(s.to, s.from, true);
      if (back >= 0) { rails.push({ i, link: back }); return; }
      free.push(i);
    });
    return { on, free, rails };
  }

  linkFor(a, b, strict) {
    const L = this.B.links;
    const pick = (x, y) => { const all = L.map((l, i) => (l.from === x && l.to === y ? i : -1)).filter((i) => i >= 0); return all.find((i) => L[i].kind === "sync") ?? all[0] ?? -1; };
    const same = pick(a, b);
    return same >= 0 || strict ? same : pick(b, a);
  }

  rowsOf(rows, ty, width) {
    const out = rows.map((r) => {
      const lines = r.text ? balance(r.text, width || this.L.labelW, ty) : [];
      const w = (lines.length ? Math.max(...lines.map((ln) => measure(ln, ty))) : 0) + (r.n != null ? 28 : 0);
      return { ...r, lines, w, h: Math.max(r.n != null ? 22 : 0, lines.length * ty.lh) };
    });
    return { rows: out, w: Math.max(...out.map((r) => r.w)) + 8, h: out.reduce((s, r) => s + r.h, 0) + (out.length - 1) * 5 + 6, ty };
  }

  linkLabel(i) {
    const steps = this.SM.on[i] || [];
    if (steps.length) return this.rowsOf(steps.map((n) => ({ n, text: this.B.spine.steps[n].say, tone: "marker" })), this.T.step);
    return this.B.links[i].label ? this.rowsOf([{ text: this.B.links[i].label }], this.T.link) : null;
  }

  arrange() {
    const B = this.B;
    const tall = this.st.layout === "tall";
    const compact = this.o.compact;
    const spineAt = {};
    B.spine.steps.forEach((s, i) => { for (const id of [s.from, s.to]) if (!Object.hasOwn(spineAt, id)) spineAt[id] = i; });
    const linkAt = {};
    B.links.forEach((l, i) => { for (const id of [l.from, l.to]) if (!Object.hasOwn(linkAt, id)) linkAt[id] = i; });
    const onSpine = new Set(Object.keys(spineAt));
    const hasPath = B.spine.steps.length > 0;
    const ends = (id) => B.links.filter((l) => l.from === id || l.to === id).map((l) => (l.from === id ? l.to : l.from));
    const lane = {};
    for (const b of B.blocks) {
      const mine = B.links.filter((l) => l.from === b.id || l.to === b.id);
      if (b.lane) lane[b.id] = LANE_AT[b.lane];
      else if (onSpine.has(b.id) || mine.some((l) => l.kind === "sync" && onSpine.has(l.from === b.id ? l.to : l.from))) lane[b.id] = 0;
      else if ((mine.length && mine.every((l) => l.kind === "control")) || b.medium === "keys") lane[b.id] = 2;
      else lane[b.id] = hasPath ? 1 : 0;
    }
    const top = (b) => { const f = b.frame && this.F[b.frame]; return !f ? null : f.scope === "process" ? f.parent : f.id; };
    const units = [];
    let solo = null;
    for (const b of B.blocks) {
      const t = top(b);
      if (!t) {
        if (!solo) { solo = { id: null, list: [] }; units.push(solo); }
        solo.list.push(b);
        continue;
      }
      solo = null;
      let u = units.find((x) => x.id === t);
      if (!u) { u = { id: t, list: [] }; units.push(u); }
      u.list.push(b);
    }
    const touch = (list) => Math.min(99, ...list.map((b) => spineAt[b.id] ?? 99));
    const col = {};
    let next = 0;
    for (const u of units) {
      const f = u.id && this.F[u.id];
      const subs = f && f.scope === "machine"
        ? [...B.frames.filter((p) => p.scope === "process" && p.parent === f.id).map((p, i) => ({ list: u.list.filter((b) => b.frame === p.id), i })).filter((p) => p.list.length).sort((a, z) => touch(a.list) - touch(z.list) || a.i - z.i).map((p) => p.list), u.list.filter((b) => b.frame === f.id)].filter((l) => l.length)
        : [u.list];
      for (const list of subs) {
        const rel = {};
        const taken = new Set();
        for (const L of [0, 1, 2]) {
          let seq = 0;
          for (const b of list.filter((x) => lane[x.id] === L)) {
            const near = ends(b.id).filter((o) => Object.hasOwn(rel, o)).map((o) => rel[o]);
            const want = L === 0 || !near.length ? seq : Math.min(...near);
            let c = want;
            for (let d = 0; d < 99; d++) {
              const hit = [want + d, want - d].find((x) => x >= 0 && !taken.has(`${L},${x}`));
              if (hit != null) { c = hit; break; }
            }
            taken.add(`${L},${c}`);
            rel[b.id] = c;
            seq = c + 1;
          }
        }
        const width = Math.max(...Object.values(rel)) + 1;
        for (const b of list) col[b.id] = next + rel[b.id];
        next += width;
      }
    }
    const raw = {};
    for (const b of B.blocks) raw[b.id] = tall ? [lane[b.id] && !this.st.stack ? 1 : 0, col[b.id]] : [col[b.id], lane[b.id]];
    const squash = (k) => { const vals = [...new Set(B.blocks.map((b) => raw[b.id][k]))].sort((a, b) => a - b); return Object.fromEntries(vals.map((v, i) => [v, i])); };
    const sx = squash(0);
    const sy = squash(1);
    const gx = {};
    const gy = {};
    for (const b of B.blocks) { gx[b.id] = sx[raw[b.id][0]]; gy[b.id] = sy[raw[b.id][1]]; }
    const nx = Object.keys(sx).length;
    const ny = Object.keys(sy).length;
    const rowLane = Object.fromEntries(Object.entries(sy).map(([v, i]) => [i, Number(v)]));
    const colLane = Object.fromEntries(Object.entries(sx).map(([v, i]) => [i, Number(v)]));
    const rank = (id) => [lane[id], spineAt[id] ?? 99, linkAt[id] ?? 99, this.K[id].index];
    const cmp = (a, b) => { const x = rank(a); const y = rank(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i]; return 0; };
    const cells = {};
    for (const b of B.blocks) (cells[`${gx[b.id]},${gy[b.id]}`] = cells[`${gx[b.id]},${gy[b.id]}`] || []).push(b.id);
    for (const k of Object.keys(cells)) cells[k].sort(cmp);
    const colW = Array(nx).fill(tall ? this.L.phone : this.L.minW);
    for (const b of B.blocks) colW[gx[b.id]] = Math.max(colW[gx[b.id]], tall ? 0 : Math.min(this.L.maxW, Math.ceil(this.wantW(b))), Math.ceil(this.wordW(b)));
    const text = {};
    const h = {};
    for (const b of B.blocks) { text[b.id] = this.blockText(b, colW[gx[b.id]]); h[b.id] = text[b.id].h; }
    this.SM = this.spineMap();
    const pairLabel = (a, b) => {
      let best = null;
      B.links.forEach((l, i) => { if ((l.from === a && l.to === b) || (l.from === b && l.to === a)) { const s = this.linkLabel(i); if (s) best = !best || s.w > best.w ? s : best; } });
      return best;
    };
    const railLabel = (a, b) => {
      let best = null;
      for (const r of this.SM.rails) {
        const l = B.links[r.link];
        if ((l.from === a && l.to === b) || (l.from === b && l.to === a)) { const s = this.rowsOf([{ n: r.i, text: B.spine.steps[r.i].say, tone: "marker" }], this.T.step); best = !best || s.w > best.w ? s : best; }
      }
      return best;
    };
    const [gmin, gmax] = tall ? this.L.phoneGutter : this.L.gutter;
    const gut = Array(Math.max(0, nx - 1)).fill(gmin);
    const rgap = Array(Math.max(0, ny - 1)).fill(tall ? this.L.phoneRowGap : this.L.rowGap);
    const sgap = {};
    const chain = (b) => { const f = b.frame && this.F[b.frame]; return !f ? [] : f.parent ? [f.id, f.parent] : [f.id]; };
    const walls = (a, b) => { const x = chain(a); const y = chain(b); return (x.filter((f) => !y.includes(f)).length + y.filter((f) => !x.includes(f)).length) * this.L.frame; };
    const waits = (a, b) => B.links.some((l, li) => ((l.from === a && l.to === b) || (l.from === b && l.to === a)) && [...(this.SM.on[li] || []), ...this.SM.rails.filter((r) => r.link === li).map((r) => r.i)].some((n) => B.spine.steps[n].waitsFor));
    if (!compact) for (const a of B.blocks) for (const b of B.blocks) {
      if (a.id >= b.id) continue;
      const s = pairLabel(a.id, b.id);
      const rs = railLabel(a.id, b.id);
      if (!s && !rs) continue;
      const sw = Math.max(s ? s.w : 0, rs ? rs.w : 0);
      const sh = Math.max(s ? s.h : 0, rs ? rs.h : 0);
      const extra = walls(a, b) + (waits(a.id, b.id) ? 36 : 0);
      if (gy[a.id] === gy[b.id] && Math.abs(gx[a.id] - gx[b.id]) === 1) {
        const i = Math.min(gx[a.id], gx[b.id]);
        gut[i] = clamp(Math.max(gut[i], sw + 18 + extra), gmin, gmax + extra);
      }
      if (gx[a.id] === gx[b.id] && Math.abs(gy[a.id] - gy[b.id]) === 1) {
        const j = Math.min(gy[a.id], gy[b.id]);
        rgap[j] = clamp(Math.max(rgap[j], sh + 20 + extra), rgap[j], 130 + extra);
      }
      if (gx[a.id] === gx[b.id] && gy[a.id] === gy[b.id]) {
        const list = cells[`${gx[a.id]},${gy[a.id]}`];
        const ia = list.indexOf(a.id);
        const ib = list.indexOf(b.id);
        if (Math.abs(ia - ib) === 1) sgap[list[Math.max(ia, ib)]] = Math.max(sgap[list[Math.max(ia, ib)]] || 0, sh + 16);
      }
    }
    if (!compact) for (const n of this.SM.free) {
      const s = B.spine.steps[n];
      if (!s.waitsFor) continue;
      if (gy[s.from] === gy[s.to] && Math.abs(gx[s.from] - gx[s.to]) === 1) {
        const i = Math.min(gx[s.from], gx[s.to]);
        gut[i] = Math.max(gut[i], (this.waitSize(s.waitsFor, 80).w + 14) / 0.7);
      }
      if (gx[s.from] === gx[s.to] && Math.abs(gy[s.from] - gy[s.to]) === 1) {
        const j = Math.min(gy[s.from], gy[s.to]);
        rgap[j] = Math.max(rgap[j], (tall ? this.L.phoneRowGap : this.L.rowGap) + 60);
      }
    }
    const stackGap = (id, i) => (i === 0 ? 0 : Math.max(this.L.stack, sgap[id] || 0));
    const stackH = (list) => list.reduce((s, id, i) => s + h[id] + stackGap(id, i), 0);
    const off = {};
    for (const list of Object.values(cells)) { let yy = 0; list.forEach((id, n) => { yy += stackGap(id, n); off[id] = yy; yy += h[id]; }); }
    for (const list of Object.values(cells)) {
      const id = list[0];
      if (this.K[id].kind !== "service") continue;
      const reach = ends(id).filter((o) => gy[o] === gy[id] && gx[o] !== gx[id] && this.K[cells[`${gx[o]},${gy[o]}`][0]].kind !== "service").map((o) => off[o] + Math.min(h[o], 80));
      h[id] = Math.max(h[id], ...reach);
    }
    const rowH = Array(ny).fill(0);
    for (const [k, list] of Object.entries(cells)) { const j = Number(k.split(",")[1]); rowH[j] = Math.max(rowH[j], stackH(list)); }
    const showLanes = hasPath && !compact && new Set(Object.values(tall ? colLane : rowLane)).size > 1;
    const left = this.L.margin + (!tall && showLanes ? this.L.lane : 0);
    const colX = [];
    let x = left;
    for (let i = 0; i < nx; i++) { colX[i] = x; x += colW[i] + (i < nx - 1 ? gut[i] : 0); }
    const right = x;
    const width = right - left;
    const thesis = compact ? [] : wrap(B.thesis, Math.max(width, 300), this.ty.thesis);
    const thesisTop = this.L.margin;
    const thesisBottom = thesisTop + thesis.length * this.ty.thesis.lh;
    const topY = thesisBottom + (compact ? 14 : tall && showLanes ? 64 : 50);
    const rowY = [];
    let y = topY;
    for (let j = 0; j < ny; j++) { rowY[j] = y; y += rowH[j] + (j < ny - 1 ? rgap[j] : 0); }
    const bottom = y;
    const box = {};
    for (const [k, list] of Object.entries(cells)) {
      const [i, j] = k.split(",").map(Number);
      let yy = rowY[j];
      list.forEach((id, n) => { yy += stackGap(id, n); box[id] = { x: colX[i], y: yy, w: colW[i], h: h[id] }; yy += h[id]; });
    }
    const bb = (ids, pad) => {
      const rs = ids.map((id) => box[id]);
      const x0 = Math.min(...rs.map((r) => r.x));
      const y0 = Math.min(...rs.map((r) => r.y));
      const x1 = Math.max(...rs.map((r) => r.x + r.w));
      const y1 = Math.max(...ids.map((id) => box[id].y + box[id].h + (this.sil(this.K[id]) === "chip" ? 8 : 0)));
      return { x: x0 - pad, y: y0 - pad, w: x1 - x0 + pad * 2, h: y1 - y0 + pad * 2 };
    };
    const union = (rs) => {
      const x0 = Math.min(...rs.map((r) => r.x));
      const y0 = Math.min(...rs.map((r) => r.y));
      return { x: x0, y: y0, w: Math.max(...rs.map((r) => r.x + r.w)) - x0, h: Math.max(...rs.map((r) => r.y + r.h)) - y0 };
    };
    const frames = [];
    const procRect = {};
    for (const f of B.frames) {
      if (f.scope === "machine") continue;
      const ids = B.blocks.filter((b) => b.frame === f.id).map((b) => b.id);
      if (!ids.length) continue;
      const rect = bb(ids, this.L.frame);
      if (f.scope === "process") procRect[f.id] = rect;
      frames.push({ f, rect });
    }
    for (const f of B.frames.filter((m) => m.scope === "machine")) {
      const own = B.blocks.filter((b) => b.frame === f.id).map((b) => b.id);
      const parts = [...(own.length ? [bb(own, this.L.frame)] : []), ...B.frames.filter((p) => p.parent === f.id && procRect[p.id]).map((p) => grow(procRect[p.id], this.L.inset))];
      if (parts.length) frames.push({ f, rect: union(parts) });
    }
    frames.sort((a, b) => (a.f.scope === "machine" ? 0 : 1) - (b.f.scope === "machine" ? 0 : 1));
    const fixed = [...B.blocks.map((b) => grow(box[b.id], b.critical && !compact ? 14 : 2)),...B.blocks.map((b) => this.marksZone(b.id, box[b.id])).filter(Boolean), { x: left, y: thesisTop, w: width, h: thesisBottom - thesisTop + 8 }];
    const busy = [...fixed];
    for (const fr of frames) {
      const w = measure(fr.f.label, this.ty.frame) + 10;
      const lh = this.ty.frame.lh + 2;
      const r = fr.rect;
      const head = { x: r.x + 14, y: r.y - lh / 2, w, h: lh };
      const foot = { x: r.x + 14, y: r.y + r.h - lh / 2, w, h: lh };
      const spots = fr.f.scope === "process" ? [foot, { ...foot, x: r.x + r.w - 14 - w }, head] : [head, { ...head, x: r.x + r.w - 14 - w }];
      const near = (q) => busy.filter((o) => area(grow(q, 2), o));
      const free = (q) => !near(q).length;
      let pick = spots.find(free);
      if (!pick && fr.f.scope !== "process") {
        const up = { ...head, y: Math.min(...near(head).map((o) => o.y)) - lh - 3 };
        const edge = up.y + lh / 2;
        if (r.y - edge <= 40 && free(up)) { pick = up; fr.rect = { ...r, y: edge, h: r.h + r.y - edge }; }
      }
      if (!pick && compact) { fr.label = null; continue; }
      if (!pick) pick = [...spots, foot, { ...foot, x: r.x + r.w - 14 - w }].map((q) => [q, busy.reduce((n, o) => n + area(q, o), 0)]).sort((a, z) => a[1] - z[1])[0][0];
      fr.label = pick;
      busy.push(grow(pick, 3));
    }
    const lanes = [];
    if (showLanes && tall) {
      for (let i = 0; i < nx; i++) {
        const has = B.blocks.filter((b) => gx[b.id] === i).map((b) => lane[b.id]);
        const t = colLane[i] === 0 ? B.lanes[0] : has.every((l) => l === 2) ? B.lanes[2] : B.lanes[1];
        lanes.push({ text: t, x: colX[i], y: topY - 40, w: colW[i], vertical: false });
      }
    } else if (showLanes) {
      for (let j = 0; j < ny; j++) lanes.push({ text: B.lanes[rowLane[j]], x: this.L.margin - 6, y: rowY[j], h: rowH[j], vertical: true });
    }
    const all = [...Object.values(box), ...frames.flatMap((fr) => (fr.label ? [fr.rect, fr.label] : [fr.rect])), ...(thesis.length ? [{ x: left, y: thesisTop, w: Math.max(width, ...thesis.map((ln) => measure(ln, this.ty.thesis) + 6)), h: thesisBottom - thesisTop }] : [])];
    const ext = union(all);
    const vg = [colX[0] - 40, ...colX.slice(0, -1).map((cx, i) => cx + colW[i] + gut[i] / 2), right + 40];
    const hg = [topY - 26, ...rowY.slice(0, -1).map((ry, j) => ry + rowH[j] + rgap[j] / 2), bottom + 40];
    return { box, text, frames, lanes, thesis: { x: left, y: thesisTop, lines: thesis, w: width }, vg, hg, ext, colX, colW, rowY, rowH, gx, gy, col, fixed };
  }

  route() {
    const G = this.G;
    const B = this.B;
    const go = router(G.box, G.vg, G.hg, G.frames.map((fr) => fr.label).filter(Boolean));
    const order = B.links.map((l, i) => i).sort((a, b) => (this.SM.on[b] ? 1 : 0) - (this.SM.on[a] ? 1 : 0) || (this.rec.has(a) ? 1 : 0) - (this.rec.has(b) ? 1 : 0) || (B.links[a].kind === "sync" ? 0 : 1) - (B.links[b].kind === "sync" ? 0 : 1) || a - b);
    const taken = [];
    const routes = [];
    const pairs = {};
    for (const i of order) {
      const l = B.links[i];
      const r = go(l.from, l.to, taken);
      const key = [l.from, l.to].sort().join("|");
      const k = (pairs[key] = (pairs[key] || 0) + 1);
      if (k > 1 && r.straight) {
        const d = (k % 2 === 0 ? 1 : -1) * Math.ceil((k - 1) / 2) * 10;
        const ax = r.pts[0][1] === r.pts[1][1] ? 1 : 0;
        for (const p of r.pts) p[ax] += d;
      }
      routes[i] = r;
      taken.push(r.pts);
    }
    tracks(routes);
    this.spread(routes);
    const lines = [...routes.filter(Boolean).map((r) => r.pts), ...this.SM.rails.map((rl) => offsetLine([...routes[rl.link].pts].reverse(), -13))];
    const cut = (q) => lines.some((pts) => this.crossesRect(pts, grow(q, 3)));
    for (const fr of G.frames) {
      const L = fr.label;
      if (!L || !cut(L)) continue;
      const others = [...G.fixed, ...G.frames.filter((o) => o !== fr && o.label).map((o) => grow(o.label, 3))];
      const clear = (q) => !cut(q) && others.every((o) => !area(q, o));
      const rows = [...new Set([L.y, fr.rect.y - L.h / 2, fr.rect.y + fr.rect.h - L.h / 2])];
      const spot = rows.flatMap((y) => Array.from({ length: Math.max(0, Math.floor((fr.rect.w - L.w - 28) / 12) + 1) }, (_, k) => ({ ...L, x: fr.rect.x + 14 + k * 12, y }))).find(clear);
      if (spot) Object.assign(L, spot);
    }
    this.go = go;
    this.taken = taken;
    this.routes = routes;
  }

  spread(routes) {
    const sides = new Map();
    const B = this.B;
    routes.forEach((r, i) => {
      if (!r || r.loose) return;
      const l = B.links[i];
      [[0, l.from], [r.pts.length - 1, l.to]].forEach(([pi, id]) => {
        const p = r.pts[pi];
        const bx = this.G.box[id];
        const side = Math.abs(p[0] - bx.x) < 1 ? "l" : Math.abs(p[0] - bx.x - bx.w) < 1 ? "r" : Math.abs(p[1] - bx.y) < 1 ? "t" : "b";
        const key = `${id}:${side}`;
        if (!sides.has(key)) sides.set(key, []);
        sides.get(key).push({ r, pi, ax: side === "l" || side === "r" ? 1 : 0, bx });
      });
    });
    const shift = (cur, d) => {
      if (cur.r.straight) for (const p of cur.r.pts) p[cur.ax] += d;
      else { cur.r.pts[cur.pi][cur.ax] += d; const nb = cur.pi === 0 ? 1 : cur.pi - 1; cur.r.pts[nb][cur.ax] += d; }
    };
    for (const list of sides.values()) {
      if (list.length < 2) continue;
      list.sort((a, b) => a.r.pts[a.pi][a.ax] - b.r.pts[b.pi][b.ax]);
      for (let n = 1; n < list.length; n++) {
        const prev = list[n - 1].r.pts[list[n - 1].pi][list[n - 1].ax];
        const cur = list[n];
        const v = cur.r.pts[cur.pi][cur.ax];
        if (v - prev >= 16) continue;
        const lim = cur.ax === 1 ? cur.bx.y + cur.bx.h - 8 : cur.bx.x + cur.bx.w - 8;
        shift(cur, Math.min(prev + 16, lim) - v);
      }
    }
    for (const [key, list] of sides) {
      if (!key.endsWith(":t")) continue;
      const bx = list[0].bx;
      const z = this.marksZone(key.slice(0, -2), bx);
      if (!z) continue;
      let edge = z.x - 10;
      for (const cur of [...list].sort((a, b) => b.r.pts[b.pi][0] - a.r.pts[a.pi][0])) {
        const v = cur.r.pts[cur.pi][0];
        if (v <= edge) { edge = v - 16; continue; }
        if (edge < bx.x + 8) break;
        shift(cur, edge - v);
        edge -= 16;
      }
    }
  }

  obstacles() {
    const G = this.G;
    const out = Object.values(G.box).map((r) => grow(r, 3));
    for (const fr of G.frames) if (fr.label) out.push(fr.label);
    if (G.thesis.lines.length) out.push({ x: -1e5, y: -1e5, w: 2e5, h: 1e5 + G.thesis.y + G.thesis.lines.length * this.ty.thesis.lh + 8 });
    for (const ln of G.lanes) out.push(ln.vertical ? { x: ln.x - 10, y: ln.y, w: 18, h: ln.h } : { x: ln.x, y: ln.y - 12, w: ln.w, h: 16 });
    for (const b of Object.values(this.marks || {})) out.push(b);
    for (const b of Object.values(this.checks || {})) out.push(b);
    return out;
  }

  crossesRect(pts, r) {
    for (let i = 0; i < pts.length - 1; i++) {
      const [x1, y1] = pts[i];
      const [x2, y2] = pts[i + 1];
      if (Math.max(x1, x2) > r.x && Math.min(x1, x2) < r.x + r.w && Math.max(y1, y2) > r.y && Math.min(y1, y2) < r.y + r.h) return true;
    }
    return false;
  }

  el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) e.setAttribute(k, v);
    if (parent) parent.append(e);
    return e;
  }

  R(cls, node, parent) { node.setAttribute("class", cls); parent.append(node); return node; }

  O(extra) { return { roughness: 1.1, bowing: 0.9, strokeWidth: 2, seed: this.seed++, ...extra }; }

  text(parent, x, y, lines, ty, cls, anchor) {
    const t = this.el("text", { x, y, "font-size": ty.size, "font-weight": ty.weight, class: `${ty.sans ? "t-sans " : ""}${cls || ""}`, "text-anchor": anchor || "start" }, parent);
    lines.forEach((ln, i) => { const s = this.el("tspan", { x, dy: i === 0 ? 0 : ty.lh }, t); s.textContent = ln; });
    return t;
  }

  render() {
    this.G = this.arrange();
    this.route();
    const G = this.G;
    this.svg.replaceChildren();
    this.svg.setAttribute("aria-label", this.B.thesis);
    const defs = this.el("defs", {}, this.svg);
    const pid = `smap-dots-${this.id}`;
    const pat = this.el("pattern", { id: pid, width: 26, height: 26, patternUnits: "userSpaceOnUse", class: "dotgrid" }, defs);
    this.dots = pat;
    this.el("circle", { cx: 2, cy: 2, r: 1.5 }, pat);
    this.el("rect", { x: -6000, y: -6000, width: 14000, height: 14000, fill: `url(#${pid})` }, this.svg);
    const ov = this.el("g", { class: "overview" }, this.svg);
    this.layers = {
      ov,
      frames: this.el("g", {}, ov),
      lanes: this.el("g", { class: "lanes" }, ov),
      links: this.el("g", {}, ov),
      captions: this.el("g", {}, ov),
      spineRing: this.el("g", { class: "spine" }, ov),
      pathRing: this.el("g", {}, ov),
      blocks: this.el("g", {}, ov),
      spine: this.el("g", { class: "spine" }, ov),
      labels: this.el("g", {}, ov),
      notes: this.el("g", {}, ov),
      path: this.el("g", { class: "pathlayer" }, ov),
      inside: this.el("g", { class: "inside-layer" }, this.svg),
    };
    this.drawThesis();
    this.drawFrames();
    this.drawLanes();
    this.blockEls = {};
    this.marks = {};
    this.checks = {};
    this.heapRects = {};
    for (const b of this.B.blocks) this.drawBlock(b);
    this.drawLinks();
    this.drawAck();
    G.ext = this.extent();
    this.drawPath();
    this.drawInside();
    this.applyFocus();
  }

  extent() {
    const rs = [this.G.ext, ...(this.o.compact ? [] : this.placed)];
    this.routes.forEach((r, i) => { if (r && !this.rec.has(i)) for (const [x, y] of r.pts) rs.push({ x: x - 6, y: y - 6, w: 12, h: 12 }); });
    const x0 = Math.min(...rs.map((r) => r.x));
    const y0 = Math.min(...rs.map((r) => r.y));
    return { x: x0, y: y0, w: Math.max(...rs.map((r) => r.x + r.w)) - x0, h: Math.max(...rs.map((r) => r.y + r.h)) - y0 };
  }

  drawThesis() {
    const t = this.G.thesis;
    if (!t.lines.length) return;
    const g = this.el("g", { class: "thesis" }, this.layers.ov);
    this.text(g, t.x, t.y + this.ty.thesis.size, t.lines, this.ty.thesis, "");
    this.seed = hash(`thesis-${this.B.thesis}`);
    if (this.skin.clean) return;
    const lw = Math.max(...t.lines.map((ln) => measure(ln, this.ty.thesis)));
    const uy = t.y + this.ty.thesis.size + (t.lines.length - 1) * this.ty.thesis.lh + 9;
    this.R("k-marker", this.rc.line(t.x - 2, uy, t.x + lw + 4, uy + 1.5, this.O({ strokeWidth: 2.2, roughness: 1.4 })), g);
  }

  drawFrames() {
    for (const fr of this.G.frames) {
      const s = FRAME[fr.f.scope];
      const g = this.el("g", { class: "frame", "data-frame": fr.f.id }, this.layers.frames);
      this.seed = hash(`frame-${fr.f.id}-${fr.rect.x}`);
      const r = fr.rect;
      this.R(s.cls, this.rc.rectangle(r.x, r.y, r.w, r.h, this.O({ strokeWidth: s.sw, roughness: 1.4, strokeLineDash: s.dash })), g);
      const L = fr.label;
      if (L) this.text(this.el("g", { class: "frame", "data-frame": fr.f.id }, this.layers.captions), L.x + 5, L.y + 4 + this.ty.frame.size * 0.8, [fr.f.label], this.ty.frame, `${s.t} halo`);
    }
  }

  drawLanes() {
    for (const ln of this.G.lanes) {
      const label = ln.text.toUpperCase();
      if (ln.vertical) {
        const cy = ln.y + ln.h / 2;
        const words = label.split(" ");
        const lines = measure(label, this.T.lane) * 1.1 > ln.h && words.length > 1 ? [words.slice(0, Math.ceil(words.length / 2)).join(" "), words.slice(Math.ceil(words.length / 2)).join(" ")] : [label];
        const t = this.text(this.layers.lanes, ln.x, cy, lines, this.T.lane, "t-caps t-ink3", "middle");
        t.setAttribute("transform", `rotate(-90 ${ln.x} ${cy})`);
        t.setAttribute("letter-spacing", "1.2");
      } else {
        const t = this.text(this.layers.lanes, ln.x, ln.y, [label], this.T.lane, "t-caps t-ink3");
        t.setAttribute("letter-spacing", "1.2");
      }
    }
  }

  doodle() {
    const rc = this.rc;
    const R = (c, n, p) => this.R(c, n, p);
    const O = (e) => this.O(e);
    const gear = (g, cx, cy, r) => {
      R("k-ink2", rc.circle(cx, cy, r * 2, O({ strokeWidth: 1.6 })), g);
      R("k-ink2", rc.circle(cx, cy, r * 0.8, O({ strokeWidth: 1.3 })), g);
      for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4; R("k-ink2", rc.line(cx + Math.cos(a) * r, cy + Math.sin(a) * r, cx + Math.cos(a) * (r + 4), cy + Math.sin(a) * (r + 4), O({ strokeWidth: 2, roughness: 0.6 })), g); }
    };
    const person = (g, x, y, s, cls) => {
      const c = cls || "k-ink2";
      R(c, rc.circle(x, y, 16 * s, O({ strokeWidth: 1.6 })), g);
      R(c, rc.line(x, y + 8 * s, x, y + 30 * s, O({ strokeWidth: 1.6 })), g);
      R(c, rc.line(x - 11 * s, y + 16 * s, x + 11 * s, y + 16 * s, O({ strokeWidth: 1.6 })), g);
      R(c, rc.line(x, y + 30 * s, x - 9 * s, y + 44 * s, O({ strokeWidth: 1.6 })), g);
      R(c, rc.line(x, y + 30 * s, x + 9 * s, y + 44 * s, O({ strokeWidth: 1.6 })), g);
    };
    const key = (g, x, y, s, cls) => {
      const c = cls || "k-ink2";
      R(c, rc.circle(x, y, 34 * s, O({ strokeWidth: 1.8 })), g);
      R(c, rc.circle(x, y, 10 * s, O({ strokeWidth: 1.4 })), g);
      R(c, rc.line(x + 17 * s, y, x + 78 * s, y, O({ strokeWidth: 2 })), g);
      R(c, rc.line(x + 60 * s, y, x + 60 * s, y + 13 * s, O({ strokeWidth: 2 })), g);
      R(c, rc.line(x + 72 * s, y, x + 72 * s, y + 10 * s, O({ strokeWidth: 2 })), g);
    };
    const cycle = (g, cx, cy, r, cls, sw) => {
      const c = cls || "k-ink2";
      const w = sw || 1.8;
      const a0 = -0.3;
      const a1 = Math.PI * 1.65;
      const p = (a) => [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
      const [x0, y0] = p(a0);
      const [x1, y1] = p(a1);
      R(c, rc.path(`M ${x0} ${y0} A ${r} ${r} 0 1 1 ${x1} ${y1}`, O({ strokeWidth: w })), g);
      const ang = Math.atan2(Math.cos(a1), -Math.sin(a1));
      R(c, rc.line(x1, y1, x1 - Math.cos(ang - 0.5) * 7, y1 - Math.sin(ang - 0.5) * 7, O({ strokeWidth: w })), g);
      R(c, rc.line(x1, y1, x1 - Math.cos(ang + 0.5) * 7, y1 - Math.sin(ang + 0.5) * 7, O({ strokeWidth: w })), g);
    };
    const plug = (g, x, y) => {
      R("k-ink2", rc.rectangle(x - 8, y - 4, 16, 12, O({ strokeWidth: 1.6 })), g);
      R("k-ink2", rc.line(x - 4, y - 4, x - 4, y - 11, O({ strokeWidth: 1.8 })), g);
      R("k-ink2", rc.line(x + 4, y - 4, x + 4, y - 11, O({ strokeWidth: 1.8 })), g);
      R("k-ink2", rc.line(x, y + 8, x, y + 14, O({ strokeWidth: 1.6 })), g);
    };
    const shield = (g, x, y) => R("k-ink2", rc.path(`M ${x} ${y - 9} L ${x + 8} ${y - 6} L ${x + 7} ${y + 3} Q ${x + 4} ${y + 8} ${x} ${y + 10} Q ${x - 4} ${y + 8} ${x - 7} ${y + 3} L ${x - 8} ${y - 6} Z`, O({ strokeWidth: 1.5 })), g);
    const prompt = (g, x, y) => { R("k-ink2", rc.linearPath([[x - 7, y - 5], [x - 1, y], [x - 7, y + 5]], O({ strokeWidth: 1.6 })), g); R("k-ink2", rc.line(x + 1, y + 6, x + 8, y + 6, O({ strokeWidth: 1.6 })), g); };
    const sheet = (g, x, y) => { R("k-ink2", rc.polygon([[x - 7, y - 9], [x + 3, y - 9], [x + 7, y - 5], [x + 7, y + 9], [x - 7, y + 9]], O({ strokeWidth: 1.4 })), g); };
    return { gear, person, key, cycle, plug, shield, prompt, sheet };
  }

  glyph(shape, g, cx, cy, s, cls) {
    const rc = this.rc;
    const c = cls || "k-ink2";
    const u = s / 44;
    const sw = Math.max(1.1, 1.7 * Math.min(1, u * 1.15));
    const O = (e) => this.O({ strokeWidth: sw, roughness: 0.7, bowing: 0.6, ...e });
    const X = (x) => cx + x * u;
    const Y = (y) => cy + y * u;
    const R = (n) => this.R(c, n, g);
    const L = (x1, y1, x2, y2, e) => R(rc.line(X(x1), Y(y1), X(x2), Y(y2), O(e)));
    const P = (pts, e) => R(rc.linearPath(pts.map(([x, y]) => [X(x), Y(y)]), O(e)));
    const G = (pts, e) => R(rc.polygon(pts.map(([x, y]) => [X(x), Y(y)]), O(e)));
    const C = (x, y, d, e) => R(rc.circle(X(x), Y(y), d * u, O(e)));
    const B = (x, y, w, h, e) => R(rc.rectangle(X(x), Y(y), w * u, h * u, O(e)));
    const E = (x, y, w, h, e) => R(rc.ellipse(X(x), Y(y), w * u, h * u, O(e)));
    const Q = (d, e) => R(rc.path(d.replace(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g, (_, a, b) => `${X(+a)} ${Y(+b)}`), O(e)));
    const head = (x, y, ang) => { const l = 6; P([[x - Math.cos(ang - 0.55) * l, y - Math.sin(ang - 0.55) * l], [x, y], [x - Math.cos(ang + 0.55) * l, y - Math.sin(ang + 0.55) * l]]); };
    const arrow = (x1, y1, x2, y2) => { L(x1, y1, x2, y2); head(x2, y2, Math.atan2(y2 - y1, x2 - x1)); };
    const k = {
      relay: () => { L(-20, -7, 20, -7); L(-20, 7, 20, 7); arrow(-14, -1, 10, -1); arrow(14, 13, -10, 13); P([[-20, -15], [-20, 15]]); P([[20, -15], [20, 15]]); },
      gate: () => { L(-20, 20, 20, 20); L(0, 2, 0, 20, { strokeWidth: sw * 1.4 }); C(0, -2, 9); L(4, -2, 22, -2); L(-2, 2, -11, 17); L(-2, -6, -11, -21); B(-6, 16, 12, 4); },
      router: () => { L(-21, 0, -6, 0); arrow(-6, 0, 18, -15); arrow(-6, 0, 20, 0); arrow(-6, 0, 18, 15); C(-6, 0, 5); },
      guard: () => { G([[0, -20], [16, -14], [14, 4], [0, 20], [-14, 4], [-16, -14]]); P([[-7, 0], [-2, 6], [8, -7]], { strokeWidth: sw * 1.3 }); },
      queue: () => { for (const y of [-13, 0, 13]) { L(-21, y + 6, 8, y + 6, { strokeWidth: sw * 0.7 }); B(-19, y - 4, 7, 8); B(-9, y - 4, 7, 8); } L(12, -19, 12, 19, { strokeWidth: sw * 1.6 }); arrow(13, 0, 22, 0); },
      pool: () => { B(-12, -19, 30, 22); B(-16, -12, 30, 22); B(-20, -5, 30, 22); C(-5, 6, 8); L(-5, 1, -5, 6); L(-5, 6, -1, 8); },
      log: () => { for (let i = 0; i < 4; i++) B(-21 + i * 10, -3, 9, 12); L(19, -3, 19, 9, { strokeLineDash: [2, 3] }); arrow(14, -20, 14, -7); },
      recovery: () => { E(0, -2, 16, 6); L(-8, -2, -8, 8); L(8, -2, 8, 8); Q("M -8,8 Q 0,12 8,8"); Q("M 13,-14 A 18 18 0 1 1 -14,-13"); head(-14, -13, -2.3); },
      handoff: () => { B(-22, -9, 14, 18, { strokeLineDash: [4, 3] }); B(8, -9, 14, 18); arrow(-6, 0, 6, 0); G([[-3, -5], [3, -5], [3, -2], [-3, -2]]); },
      watcher: () => { Q("M -21,0 Q 0,-20 21,0"); Q("M -21,0 Q 0,20 21,0"); C(0, 0, 13); C(0, 0, 4, { fill: "currentColor", fillStyle: "solid" }); },
      keys: () => { C(-10, 0, 18); C(-10, 0, 6); L(-1, 0, 21, 0); L(15, 0, 15, 7); L(20, 0, 20, 5); },
      budget: () => { Q("M -19,8 A 19 19 0 0 1 19,8"); for (const a of [-160, -125, -90, -55, -20]) { const r = (a * Math.PI) / 180; L(Math.cos(r) * 15, 8 + Math.sin(r) * 15, Math.cos(r) * 19, 8 + Math.sin(r) * 19); } arrow(0, 8, 11, -5); L(-21, 13, 21, 13); },
      timer: () => { C(0, 0, 38); L(0, 0, 0, -12); L(0, 0, 9, 5); for (const [x, y] of [[0, -16], [16, 0], [0, 16], [-16, 0]]) L(x * 0.85, y * 0.85, x, y); },
      uploader: () => { Q("M -16,-4 Q -20,-16 -8,-16 Q -4,-24 6,-19 Q 18,-20 17,-9 Q 22,-3 14,-2 Z"); arrow(0, 20, 0, -6); L(-12, 20, 12, 20); },
      codec: () => { Q("M -6,-19 Q -14,-19 -12,-9 Q -11,-2 -18,0 Q -11,2 -12,9 Q -14,19 -6,19"); Q("M 6,-19 Q 14,-19 12,-9 Q 11,-2 18,0 Q 11,2 12,9 Q 14,19 6,19"); L(-3, -3, 3, -3); L(-3, 4, 3, 4); },
      compactor: () => { for (const y of [-9, 0, 9]) L(-6, y, 6, y, { strokeWidth: sw * 1.3 }); arrow(-21, 0, -10, 0); arrow(21, 0, 10, 0); L(-21, -14, -21, 14); L(21, -14, 21, 14); },
      sweeper: () => { L(16, -20, -2, 4, { strokeWidth: sw * 1.3 }); G([[-2, 2], [5, 8], [-6, 20], [-18, 12]]); for (const t of [0.3, 0.6]) L(-2 + (5 + 2) * t, 2 + 6 * t, -18 + (12 + 6) * t, 12 + 8 * t); C(12, 16, 2.5); C(18, 12, 2); },
      cache: () => { G([[3, -21], [-12, 3], [-1, 3], [-5, 21], [12, -4], [1, -4]]); },
      api: () => { C(0, 0, 36); L(-6, -9, -6, 4, { strokeWidth: sw * 1.5 }); L(6, -9, 6, 4, { strokeWidth: sw * 1.5 }); L(0, 10, 0, 16); },
      ui: () => { B(-21, -15, 42, 30); L(-21, -7, 21, -7); for (let i = 0; i < 3; i++) C(-16 + i * 6, -11, 3); B(-15, -2, 14, 11); L(4, 0, 15, 0); L(4, 6, 12, 6); },
      tool: () => { B(-21, -15, 42, 30); P([[-13, -6], [-5, 0], [-13, 6]], { strokeWidth: sw * 1.3 }); L(-1, 7, 11, 7, { strokeWidth: sw * 1.3 }); },
      docs: () => { G([[-14, -20], [6, -20], [15, -11], [15, 20], [-14, 20]]); P([[6, -20], [6, -11], [15, -11]]); for (const y of [-4, 3, 10]) L(-8, y, 9, y, { strokeWidth: sw * 0.8 }); },
      sdk: () => { G([[-15, -6], [5, -6], [5, 16], [-15, 16]]); G([[-15, -6], [-6, -16], [14, -16], [5, -6]]); G([[5, -6], [14, -16], [14, 6], [5, 16]]); },
      module: () => { C(0, 0, 22); C(0, 0, 8); for (let i = 0; i < 8; i++) { const a = (i * Math.PI) / 4; L(Math.cos(a) * 11, Math.sin(a) * 11, Math.cos(a) * 16, Math.sin(a) * 16, { strokeWidth: sw * 1.4 }); } },
    };
    (k[shape] || k.module)();
  }

  storeGlyph(medium, g, cx, cy, s, cls) {
    const u = s / 44;
    const r = { x: cx - 20 * u, y: cy - 16 * u, w: 40 * u, h: 30 * u };
    const kind = SIL[medium] || "pages";
    if (kind === "key") { this.glyph("keys", g, cx, cy, s, cls); return; }
    this.outline(kind, g, r, { cls: cls || "k-ink2", strokeWidth: Math.max(1.1, 1.6 * u), roughness: 0.7, mini: u });
  }

  outline(kind, g, r, o) {
    const rc = this.rc;
    const R = (c, n) => this.R(c, n, g);
    const { mini, cls: tone, ...rough } = o;
    const O = (e) => this.O({ ...rough, ...e });
    const { x, y, w, h } = r;
    const cls = tone || "k-ink";
    const z = mini || 1;
    if (kind === "cylinder") {
      const ry = 10 * z;
      R(cls, rc.ellipse(x + w / 2, y + ry, w, ry * 2, O()));
      R(cls, rc.line(x, y + ry, x, y + h - ry, O()));
      R(cls, rc.line(x + w, y + ry, x + w, y + h - ry, O()));
      R(cls, rc.path(`M ${x} ${y + h - ry} A ${w / 2} ${ry} 0 0 0 ${x + w} ${y + h - ry}`, O()));
      return;
    }
    if (kind === "bucket") {
      const rim = 13 * z;
      const tap = Math.min(16 * z, w * 0.08);
      R(cls, rc.ellipse(x + w / 2, y + rim, w, rim * 2, O()));
      R(cls, rc.line(x, y + rim, x + tap, y + h, O()));
      R(cls, rc.line(x + w, y + rim, x + w - tap, y + h, O()));
      R(cls, rc.path(`M ${x + tap} ${y + h} Q ${x + w / 2} ${y + h + 9 * z} ${x + w - tap} ${y + h}`, O()));
      return;
    }
    if (kind === "key" || kind === "external") {
      const k = 12 * z;
      R(cls, rc.path(`M ${x + k} ${y} L ${x + w - k} ${y} Q ${x + w} ${y} ${x + w} ${y + k} L ${x + w} ${y + h - k} Q ${x + w} ${y + h} ${x + w - k} ${y + h} L ${x + k} ${y + h} Q ${x} ${y + h} ${x} ${y + h - k} L ${x} ${y + k} Q ${x} ${y} ${x + k} ${y} Z`, O()));
      return;
    }
    if (kind === "page") {
      const f = 18;
      R(cls, rc.polygon([[x, y], [x + w - f, y], [x + w, y + f], [x + w, y + h], [x, y + h]], O()));
      R(cls, rc.polygon([[x + w - f, y], [x + w - f, y + f], [x + w, y + f]], O({ strokeWidth: 1.4 })));
      return;
    }
    R(cls, rc.rectangle(x, y, w, h, O()));
    if (kind === "service") R(cls, rc.line(x + 6, y + 4, x + 6, y + h - 4, O({ strokeWidth: 1.4 })));
    if (kind === "app") { R(cls, rc.line(x, y + 20, x + w, y + 20, O({ strokeWidth: 1.6 }))); for (let i = 0; i < 3; i++) R("k-ink2", rc.circle(x + 13 + i * 12, y + 10, 6, O({ strokeWidth: 1.2 }))); }
    if (kind === "package") { R(cls, rc.line(x, y + 14, x + w, y + 14, O({ strokeWidth: 1.4 }))); R("k-ink2", rc.line(x + w / 2, y, x + w / 2, y + 14, O({ strokeWidth: 1.4 }))); }
    if (kind === "pages") R("k-ink3", rc.linearPath([[x + 6, y], [x + 6, y - 6], [x + w + 6, y - 6], [x + w + 6, y + h - 6], [x + w, y + h - 6]], O({ strokeWidth: 1.3 })));
    if (kind === "chip") for (let i = 0; i < 7; i++) { const px = x + ((i + 0.5) * w) / 7; R(cls, rc.line(px, y + h, px, y + h + 8 * z, O({ strokeWidth: 1.8 * Math.max(z, 0.7), roughness: 0.5 }))); }
  }

  drawBlock(b) {
    const G = this.G;
    const r = G.box[b.id];
    const tx = G.text[b.id];
    const k = tx.k;
    const kind = this.sil(b);
    const { x, y, w, h } = r;
    this.seed = hash(`block-${b.id}`);
    const compact = !!this.o.compact;
    const g = this.el("g", { class: "block", "data-block": b.id, tabindex: compact ? null : 0, role: compact ? null : "button", "aria-label": compact ? null : this.blockAria(b) }, this.layers.blocks);
    this.el("rect", { class: "hit", x: x - 8, y: y - 8, width: w + 16, height: h + 16 }, g);
    const ring = this.el("g", { class: "ring" }, g);
    this.R("k-hl", this.rc.rectangle(x - 7, y - 7, w + 14, h + 14, this.O({ strokeWidth: 5, roughness: 1.8 })), ring);
    this.el("rect", { class: "paper", x: x + 2, y: y + 2, width: w - 4, height: h - 4, rx: 6 }, g);
    const D = this.doodle();
    const dashed = this.unbuilt(b);
    const base = { strokeWidth: 2.3, ...(dashed ? { strokeLineDash: [9, 6], roughness: 1.5 } : {}) };
    if (kind === "service" && !tx.strip.length && !tx.heaps.length) this.R("f-blue", this.rc.rectangle(x + 9, y + h - 15, w - 13, 11, this.O({ stroke: "none", fill: "#000", fillStyle: "hachure", hachureGap: 7, hachureAngle: -50, fillWeight: 1 })), g);
    this.outline(kind, g, r, base);
    g.addEventListener("click", (ev) => { if (this.dragMoved) return; ev.stopPropagation(); this.pickShape(b.id); });
    g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.pickShape(b.id); } });
    this.blockEls[b.id] = { g, box: r };
    if (compact) {
      this.text(g, x + k.l, y + k.t + this.ty.name.size - 3, tx.name, this.ty.name, "");
      if (dashed) this.pill(g, x + k.l, y + h - k.b - 19, b.status, "warn");
      this.drawBadges(b, g);
      return;
    }
    if (kind === "actor") {
      D.person(g, x + w - 22, y + 16, 0.62);
      this.R("k-ink2", this.rc.rectangle(x + w - 44, y + 22, 11, 19, this.O({ strokeWidth: 1.3 })), g);
    }
    if (kind === "service") D.gear(g, x + w - 21, y + 25, 7);
    if (kind === "worker") D.cycle(g, x + w - 21, y + 25, 8);
    if (kind === "external") D.plug(g, x + w - 21, y + 25);
    if (kind === "key") D.key(g, x + w - 42, y + 28, 0.38);
    if (b.kind === "log") for (let i = 0; i < 4; i++) this.R("k-ink2", this.rc.rectangle(x + w - k.r - 4 * 17 + i * 17, y + h - k.b - 15, 13, 9, this.O({ strokeWidth: 1.1, roughness: 0.7 })), g);
    let ty = y + k.t + this.ty.name.size - 3;
    this.text(g, x + k.l, ty, tx.name, this.ty.name, "");
    ty += (tx.name.length - 1) * this.ty.name.lh + 6 + this.T.role.size;
    this.text(g, x + k.l, ty, tx.role, this.T.role, "t-ink2");
    let cy = y + k.t + tx.name.length * this.ty.name.lh + 4 + tx.role.length * this.T.role.lh + 8;
    if (tx.strip.length) { this.drawStrip(g, x + k.l, cy, tx.strip); cy += 44; }
    for (const hp of tx.heaps) {
      const hh = Math.max(46, hp.lines.length * this.T.heap.lh + 18);
      this.drawHeapIn(g, hp, { x: x + 10, y: cy, w: w - 20, h: hh });
      cy += hh + 8;
    }
    const tag = this.tagOf(b);
    const fy = y + h - k.b - 19;
    let fx = x + k.l;
    if (tag) fx = this.pill(g, fx, fy, tag, "") + 6;
    if (dashed) this.pill(g, fx, fy, b.status, "warn");
    this.drawBadges(b, g);
  }

  blockAria(b) {
    return `${b.name}: ${b.role}${b.badges.length ? `. ${b.badges.map((x) => x.text).join(", ")}` : ""}${b.open ? ". Press Enter to look inside" : ""}`;
  }

  drawStrip(g, x, y, ids) {
    const sg = this.el("g", { class: "strip" }, g);
    ids.forEach((m, i) => {
      const cx = x + 16 + i * 46;
      this.glyph((this.shapeOf(m) || {}).shape || "module", sg, cx, y + 18, 30, "k-ink2");
      if (i < ids.length - 1) this.R("k-ink3", this.rc.line(cx + 19, y + 18, cx + 27, y + 18, this.O({ strokeWidth: 1.4, roughness: 0.4 })), sg);
    });
    this.el("title", {}, sg).textContent = ids.map((m) => this.labelOf(m)).join(" → ");
  }

  drawHeapIn(g, hp, r) {
    this.heapRects[hp.m] = r;
    const hg = this.el("g", { class: "heapin", "data-part": hp.m }, g);
    this.el("title", {}, hg).textContent = `${this.labelOf(hp.m)}: inside the process`;
    this.el("rect", { class: "paper2", x: r.x, y: r.y, width: r.w, height: r.h, rx: 8 }, hg);
    this.R("f-green", this.rc.rectangle(r.x + 2, r.y + 2, r.w - 4, r.h - 4, this.O({ stroke: "none", fill: "#000", fillStyle: "hachure", hachureGap: 9, hachureAngle: 60, fillWeight: 0.7 })), hg);
    this.R("k-ink2", this.rc.rectangle(r.x, r.y, r.w, r.h, this.O({ strokeWidth: 1.4, roughness: 1, strokeLineDash: [7, 4] })), hg);
    this.storeGlyph("memory", hg, r.x + 24, r.y + r.h / 2 - 3, 30, "k-ink2");
    this.text(hg, r.x + 50, r.y + 9 + this.T.heap.size, hp.lines, this.T.heap, "t-ink2 halo");
  }

  pillW(text) {
    return measure(text.toUpperCase(), this.T.tag) + 0.8 * text.length + 14;
  }

  pill(g, x, y, text, cls) {
    const pw = this.pillW(text);
    const pg = this.el("g", { class: `pill ${cls}` }, g);
    this.el("rect", { x, y, width: pw, height: 17, rx: 8.5 }, pg);
    const t = this.text(pg, x + 7, y + 12.5, [text.toUpperCase()], this.T.tag, `t-caps ${cls === "warn" ? "t-amber" : "t-ink3"}`);
    t.setAttribute("letter-spacing", "0.8");
    return x + pw;
  }

  markList(badges, right, top, ty) {
    const bh = ty.size + 9;
    return badges.map((d) => {
      const ask = d.tone === "ask";
      const label = ask ? `? ${d.text}` : MARK[d.tone] ? `${MARK[d.tone]} ${d.text}` : d.text;
      const w = measure(label, ty) + 16;
      const x = right - w;
      right = x - 6;
      return ask ? { d, label, ask, x, y: top - 3, w, h: 27, ty } : { d, label, ask, x, y: top - bh / 2 + 7, w, h: bh, ty };
    });
  }

  badgeRects(b, r) {
    const list = this.o.compact ? b.badges.filter((x) => x.tone === "fail" || x.tone === "warn") : b.badges;
    return this.markList(list, r.x + r.w - 12, r.y - 9, this.ty.badge);
  }

  marksZone(bid, r) {
    const rs = this.badgeRects(this.K[bid], r);
    if (!rs.length) return null;
    const x0 = Math.min(...rs.map((q) => q.x));
    const y0 = Math.min(...rs.map((q) => q.y));
    return { x: x0 - 4, y: y0 - 3, w: Math.max(...rs.map((q) => q.x + q.w)) - x0 + 8, h: Math.max(...rs.map((q) => q.y + q.h)) - y0 + 22 };
  }

  drawBadges(b, g) {
    for (const q of this.badgeRects(b, this.G.box[b.id])) {
      this.drawMark(g, q, b.name);
      this.marks[`${b.id}:${q.x}`] = grow(q, 2);
    }
  }

  drawMark(g, q, what) {
    const ty = q.ty;
    if (q.ask) {
      const bg = this.el("g", { class: "bub" }, g);
      this.el("title", {}, bg).textContent = `${q.d.text}: a question about ${what}`;
      this.bubblePath(bg, q.x, q.y, q.w);
      this.text(bg, q.x + q.w / 2, q.y + 15, [q.label], ty, "t-ink2", "middle");
      return;
    }
    const tone = { ok: "t-green", fail: "t-red", warn: "t-amber", info: "t-ink3" }[q.d.tone];
    const bg = this.el("g", { class: `bd ${TONE[q.d.tone]}` }, g);
    this.el("title", {}, bg).textContent = `${what}: ${q.d.text}`;
    this.el("rect", { x: q.x, y: q.y, width: q.w, height: q.h, rx: q.h / 2, class: "base" }, bg);
    this.el("rect", { x: q.x, y: q.y, width: q.w, height: q.h, rx: q.h / 2, class: "face" }, bg);
    const t = this.el("text", { x: q.x + q.w / 2, y: q.y + q.h / 2 + ty.size * 0.36, "text-anchor": "middle", "font-size": ty.size, "font-weight": ty.weight, class: "t-sans" }, bg);
    if (MARK[q.d.tone]) {
      const a = this.el("tspan", { class: tone }, t);
      a.textContent = MARK[q.d.tone];
      const z = this.el("tspan", { class: q.d.tone === "ok" ? "t-ink3" : tone }, t);
      z.textContent = ` ${q.d.text}`;
    } else {
      t.setAttribute("class", `t-sans ${tone}`);
      t.textContent = q.label;
    }
  }

  arrowPath(pts, rad) {
    const r = rad == null ? 14 : rad;
    let d = `M ${pts[0][0]} ${pts[0][1]}`;
    for (let i = 1; i < pts.length - 1; i++) {
      const [px, py] = pts[i - 1];
      const [cx, cy] = pts[i];
      const [nx, ny] = pts[i + 1];
      const l1 = Math.hypot(cx - px, cy - py) || 1;
      const l2 = Math.hypot(nx - cx, ny - cy) || 1;
      const k = Math.min(r, l1 / 2, l2 / 2);
      d += ` L ${cx - ((cx - px) / l1) * k} ${cy - ((cy - py) / l1) * k} Q ${cx} ${cy} ${cx + ((nx - cx) / l2) * k} ${cy + ((ny - cy) / l2) * k}`;
    }
    const e = pts[pts.length - 1];
    return `${d} L ${e[0]} ${e[1]}`;
  }

  drawArrow(parent, pts, sw, dash, head, cls, fh) {
    this.R(cls, this.rc.path(this.arrowPath(pts), this.O({ strokeWidth: sw, roughness: 0.8, bowing: 0.6, ...(dash ? { strokeLineDash: dash } : {}) })), parent);
    const [px, py] = pts[pts.length - 2];
    const [x, y] = pts[pts.length - 1];
    const a = Math.atan2(y - py, x - px);
    const hl = 9 + sw * 1.6;
    const tip = (ox) => [x - Math.cos(a) * ox, y - Math.sin(a) * ox];
    const wing = (ox, s) => [x - Math.cos(a) * (ox + hl) + Math.cos(a + Math.PI / 2) * s * hl * 0.5, y - Math.sin(a) * (ox + hl) + Math.sin(a + Math.PI / 2) * s * hl * 0.5];
    if (head === "fill") {
      const [t0, l, r] = [tip(0), wing(0, 1), wing(0, -1)];
      this.el("path", { d: `M ${t0[0]} ${t0[1]} L ${l[0]} ${l[1]} L ${r[0]} ${r[1]} Z`, class: fh }, parent);
      return;
    }
    const open = (ox) => {
      const t0 = tip(ox);
      const l = wing(ox, 1);
      const r = wing(ox, -1);
      this.R(cls, this.rc.linearPath([l, t0, r], this.O({ strokeWidth: sw, roughness: 0.6 })), parent);
    };
    open(0);
    if (head === "double") open(7);
  }

  drawAck() {
    if (this.o.compact) return;
    this.seed = hash("ack");
    for (const b of this.B.blocks) {
      if (!b.critical) continue;
      const r = this.G.box[b.id];
      const ring = this.el("g", { class: "ackring" }, this.layers.spineRing);
      this.R("k-hl", this.rc.rectangle(r.x - 9, r.y - 9, r.w + 18, r.h + 18, this.O({ strokeWidth: 8, roughness: 1.3 })), ring);
    }
    this.B.spine.steps.forEach((s, i) => { if (this.bars[i]) this.drawWait(i, s.waitsFor); });
  }

  waitBar(i, n) {
    const li = Object.keys(this.SM.on).find((k) => this.SM.on[k].includes(i));
    const [p, q] = this.railPts[i] || this.loosePts[i] || this.routes[li].pts;
    const horiz = Math.abs(p[1] - q[1]) < Math.abs(p[0] - q[0]);
    const barAt = (t) => {
      const cx = p[0] + (q[0] - p[0]) * t;
      const cy = p[1] + (q[1] - p[1]) * t;
      return { cx, cy, bar: horiz ? { x: cx - 4, y: cy - 19, w: 8, h: 38 } : { x: cx - 19, y: cy - 4, w: 38, h: 8 } };
    };
    const hard = this.obstacles();
    const cost = (x) => hard.reduce((k, o) => k + area(grow(x.bar, 2), o), 0);
    const opts = [0.3, 0.2, 0.42, 0.12, 0.55, 0.65].map(barAt).filter((x) => this.place.free(grow(x.bar, 6)));
    const soft = [...this.routes.filter(Boolean).map((r) => r.pts), ...Object.values(this.railPts), ...Object.values(this.loosePts)];
    const clear = (r) => !soft.some((pts) => this.crossesRect(pts, r));
    const sizes = [this.waitSize(n, 140), this.waitSize(n, 80)];
    const beside = ({ cx, cy, bar }, z) => (horiz
      ? [[bar.x + bar.w + 3, cy - z.h - 3], [bar.x + bar.w + 3, cy + 3], [bar.x - z.w - 3, cy - z.h - 3], [bar.x - z.w - 3, cy + 3], [cx - z.w / 2, bar.y - z.h - 2], [cx - z.w / 2, bar.y + bar.h + 2]]
      : [[bar.x + bar.w + 4, cy - z.h / 2], [bar.x - z.w - 4, cy - z.h / 2], [cx + 3, bar.y - z.h - 2], [cx + 3, bar.y + bar.h + 2]]
    ).map(([x, y]) => ({ x, y, w: z.w, h: z.h }));
    const snug = [true, false].flatMap((strict) => opts.flatMap((x) => sizes.flatMap((z) => beside(x, z).filter((r) => this.place.free(r) && (!strict || clear(r))).map((r) => ({ x, r, z })))))[0];
    const pick = snug ? snug.x : opts[0] || [0.3, 0.2, 0.42, 0.12, 0.55, 0.65].map(barAt).reduce((a, b) => (cost(b) < cost(a) ? b : a));
    this.place.add(grow(pick.bar, 3));
    this.checks[`bar${i}`] = grow(pick.bar, 3);
    const cap = snug ? { r: snug.r, z: snug.z } : { r: this.place.around(sizes[0], grow(pick.bar, 4), soft), z: sizes[0] };
    if (snug) this.place.add(cap.r);
    if (cap.r) this.checks[`lab${i}`] = cap.r;
    return { ...pick, horiz, cap };
  }

  waitSize(n, w) {
    return this.rowsOf([{ text: `waits for step ${n}` }], { size: 14, weight: 600, lh: 17 }, w);
  }

  drawWait(i, n) {
    const { cx, cy, bar, horiz, cap } = this.bars[i];
    const g = this.el("g", { class: "ack", "data-step": i }, this.layers.spine);
    this.el("title", {}, g).textContent = `Step ${i + 1} waits here until step ${n} is done`;
    this.el("rect", { class: "paper", x: bar.x, y: bar.y, width: bar.w, height: bar.h }, g);
    this.R("k-red", this.rc.rectangle(bar.x, bar.y, bar.w, bar.h, this.O({ strokeWidth: 1.8, roughness: 0.6 })), g);
    for (let j = 1; j < 4; j++) {
      const k = j / 4;
      if (horiz) this.R("k-red", this.rc.line(bar.x, bar.y + bar.h * k - 3, bar.x + bar.w, bar.y + bar.h * k + 3, this.O({ strokeWidth: 2.2, roughness: 0.4 })), g);
      else this.R("k-red", this.rc.line(bar.x + bar.w * k - 3, bar.y, bar.x + bar.w * k + 3, bar.y + bar.h, this.O({ strokeWidth: 2.2, roughness: 0.4 })), g);
    }
    this.el("circle", { cx: horiz ? cx : bar.x, cy: horiz ? bar.y : cy, r: 4, class: "disc stop" }, g);
    if (!cap.r) return;
    const lab = this.el("g", { class: "ack-lab" }, g);
    this.drawRows(lab, cap.r, cap.z, "plain");
    lab.querySelectorAll("text").forEach((x) => x.setAttribute("class", "t-red halo"));
    this.placed.push(cap.r);
  }

  drawLinks() {
    const B = this.B;
    const place = placer(this.obstacles());
    this.place = place;
    this.placed = [];
    const edges = this.G.frames.map(({ rect: f }) => [[f.x, f.y], [f.x + f.w, f.y], [f.x + f.w, f.y + f.h], [f.x, f.y + f.h], [f.x, f.y]]);
    const soft = [...this.routes.filter(Boolean).map((r) => r.pts), ...edges];
    this.labEls = [];
    this.linkEls = [];
    const put = (size, pts, others) => {
      const spot = place(size, pts, others);
      if (spot) this.placed.push(spot.r);
      return spot;
    };
    this.leads = [];
    const leader = (g, spot, cls) => {
      if (!spot || !spot.leader) return;
      this.leads.push(spot.leader);
      this.R(`${cls || "k-ink3"} lead`, this.rc.line(spot.leader[0][0], spot.leader[0][1], spot.leader[1][0], spot.leader[1][1], this.O({ strokeWidth: 1.1, roughness: 0.4, strokeLineDash: [3, 4] })), g);
    };
    this.leader = leader;
    this.railPts = {};
    for (const rl of this.SM.rails) this.railPts[rl.i] = offsetLine([...this.routes[rl.link].pts].reverse(), -13);
    this.loosePts = {};
    for (const n of this.SM.free) { const s = B.spine.steps[n]; if (s.from !== s.to) this.loosePts[n] = this.loose(s.from, s.to, B.spine.steps).pts; }
    soft.push(...Object.values(this.railPts), ...Object.values(this.loosePts));
    this.bars = {};
    if (!this.o.compact) B.spine.steps.forEach((s, i) => { if (s.waitsFor) this.bars[i] = this.waitBar(i, s.waitsFor); });
    this.drawCallouts();
    const order = B.links.map((l, i) => i).sort((a, b) => (this.SM.on[b] ? 1 : 0) - (this.SM.on[a] ? 1 : 0) || (this.rec.has(a) ? 1 : 0) - (this.rec.has(b) ? 1 : 0) || a - b);
    for (const i of order) {
      const l = B.links[i];
      const r = this.routes[i];
      const spine = !!this.SM.on[i];
      const st = LINK[l.kind];
      this.seed = hash(`link-${i}-${l.from}-${l.to}`);
      const rec = this.rec.has(i) && !spine ? " rec" : "";
      const g = this.el("g", { class: `link${rec}`, "data-link": i }, this.layers.links);
      this.drawArrow(g, r.pts, spine ? 2.8 : st.sw, spine ? null : st.dash, spine ? "fill" : st.head, spine ? "k-marker" : st.cls, spine ? "fh-marker" : st.fh);
      this.linkEls[i] = g;
      const size = this.linkLabel(i);
      if (!size) continue;
      const spot = put(size, r.pts, soft.filter((p) => p !== r.pts));
      if (!spot) continue;
      const lab = this.el("g", { class: `lab${rec}`, "data-link": i }, this.layers.labels);
      leader(lab, spot);
      this.drawRows(lab, spot.r, size, spine ? "spine" : "plain", spine ? (rg, row) => this.wireStep(rg, row.n, null) : null);
      if (spine && l.label) {
        const plain = this.rowsOf([{ text: l.label }], this.T.link);
        const pl = this.el("g", { class: "lab plain gone", "data-link": i }, this.layers.labels);
        this.drawRows(pl, { x: spot.r.x + (spot.r.w - plain.w) / 2, y: spot.r.y + (spot.r.h - plain.h) / 2, w: plain.w, h: plain.h }, plain, "plain");
        this.labEls.push({ g: pl, i, plain: true });
      }
      this.labEls.push({ g: lab, i, spine });
    }
    const sp = this.layers.spine;
    for (const rl of this.SM.rails) {
      const pts = this.railPts[rl.i];
      this.seed = hash(`rail-${rl.i}`);
      const g = this.el("g", { class: "step", "data-step": rl.i }, sp);
      this.drawArrow(g, pts, 1.6, null, "open", "k-marker", "fh-marker");
      const size = this.rowsOf([{ n: rl.i, text: B.spine.steps[rl.i].say, tone: "marker" }], this.T.step);
      const spot = put(size, pts, soft);
      if (spot) { leader(g, spot, "k-marker"); this.drawRows(g, spot.r, size, "spine"); }
      this.wireStep(g, rl.i, null);
    }
    for (const n of this.SM.free) {
      const s = B.spine.steps[n];
      const g = this.el("g", { class: "step", "data-step": n }, sp);
      this.seed = hash(`spine-free-${n}`);
      this.drawLoose(g, s, B.spine.steps, "marker", false, this.T.step, n, this.el("g", { class: "step", "data-step": n }, this.layers.spineRing), place, soft);
      this.wireStep(g, n, null);
    }
  }

  wireStep(g, n, p) {
    g.classList.add("step");
    g.dataset.step = String(n);
    g.addEventListener("mouseenter", () => this.hot(n));
    g.addEventListener("mouseleave", () => this.hot(this.st.step));
    g.addEventListener("click", (ev) => { if (this.dragMoved) return; ev.stopPropagation(); this.pickStep(n, p ? p.id : null); });
  }

  loose(a, b, steps) {
    if (!steps.some((x) => x.from === b && x.to === a)) return this.go(a, b, this.taken);
    const [p, q] = [a, b].sort();
    const pts = this.go(p, q, this.taken).pts;
    return { pts: offsetLine(p === a ? pts : [...pts].reverse(), 9) };
  }

  drawLoose(g, s, steps, tone, dashed, ty, n, under, place, soft = []) {
    const size = this.rowsOf([{ n, text: s.say, tone }], ty);
    const lab = this.el("g", { class: "step", "data-step": n });
    const cls = tone === "red" ? "k-red" : tone === "ink2" ? "k-ink2" : "k-marker";
    const mode = tone === "red" ? "risk" : tone === "ink2" ? "stop" : "spine";
    if (s.from === s.to) {
      const r = this.G.box[s.from];
      const heap = this.heapBox(s);
      const ug = under || g;
      if (heap) {
        const hl = this.el("g", { class: "hl" }, ug);
        this.R("k-hl", this.rc.rectangle(heap.x - 4, heap.y - 4, heap.w + 8, heap.h + 8, this.O({ strokeWidth: 8, roughness: 1.2 })), hl);
        this.R(cls, this.rc.rectangle(heap.x - 3, heap.y - 3, heap.w + 6, heap.h + 6, this.O({ strokeWidth: 2.4, roughness: 1.1 })), ug);
        const spot = place.around(size, { x: r.x, y: heap.y, w: r.w, h: r.y + r.h - heap.y }, soft);
        if (spot) {
          const gap = (a0, a1, b0, b1) => (a1 < b0 ? [a1, b0 - 3] : a0 > b1 ? [a0, b1 + 3] : Array(2).fill((Math.max(a0, b0) + Math.min(a1, b1)) / 2));
          const [x0, x1] = gap(spot.x, spot.x + spot.w, heap.x, heap.x + heap.w);
          const [y0, y1] = gap(spot.y, spot.y + spot.h, heap.y, heap.y + heap.h);
          const into = [[x0, y0], [x1, y1]];
          this.drawArrow(g, into, 2, dashed ? [8, 6] : null, "fill", cls, cls.replace("k-", "fh-"));
          this.drawRows(lab, spot, size, mode);
          this.placed.push(spot);
        }
        g.append(lab);
        return lab;
      }
      const hl = this.el("g", { class: "hl" }, ug);
      this.R("k-hl", this.rc.rectangle(r.x - 9, r.y - 9, r.w + 18, r.h + 18, this.O({ strokeWidth: 9, roughness: 1.2 })), hl);
      this.R(cls, this.rc.rectangle(r.x - 6, r.y - 6, r.w + 12, r.h + 12, this.O({ strokeWidth: 2.4, roughness: 1.3, ...(dashed ? { strokeLineDash: [10, 7] } : {}) })), ug);
      const spot = place.around(size, grow(r, 8), soft);
      if (spot) { this.drawRows(lab, spot, size, mode); this.placed.push(spot); }
      g.append(lab);
      return lab;
    }
    const route = this.loose(s.from, s.to, steps);
    const hl = this.el("g", { class: "hl" }, g);
    this.R("k-hl", this.rc.path(this.arrowPath(route.pts), this.O({ strokeWidth: 12, roughness: 0.5 })), hl);
    this.drawArrow(this.el("g", { class: "loose" }, g), route.pts, 2.6, dashed ? [11, 8] : null, "fill", cls, cls.replace("k-", "fh-"));
    const spot = place(size, route.pts, [...soft, route.pts], soft.length > 0);
    if (spot) { this.leader(lab, spot, cls); this.drawRows(lab, spot.r, size, mode); this.placed.push(spot.r); }
    g.append(lab);
    return lab;
  }

  heapBox(s) {
    const b = this.K[s.from];
    const heaps = this.heapOf(b);
    const hit = heaps.find((m) => s.at.includes(m) || s.flows.some((f) => this.flows[f] && (this.flows[f].from === m || this.flows[f].to === m)));
    return hit && this.heapRects && this.heapRects[hit] ? this.heapRects[hit] : null;
  }

  drawRows(g, at, size, mode, wire) {
    let y = at.y + 3;
    for (const row of size.rows) {
      const rg = this.el("g", {}, g);
      this.el("rect", { x: at.x, y: y - 2, width: at.w, height: row.h + 4, fill: "transparent" }, rg);
      let x = at.x + 4;
      if (row.n != null) {
        const tone = row.tone === "red" ? " risk" : row.tone === "ink2" ? " stop" : "";
        this.el("circle", { cx: x + 11, cy: y + 11, r: 11, class: `disc${tone}` }, rg);
        this.text(rg, x + 11, y + 15.5, [String(row.n + 1)], this.T.disc, "t-paper", "middle");
        x += 28;
      }
      const cls = mode === "plain" ? "t-ink2" : row.tone === "red" ? "t-red" : row.tone === "ink2" ? "t-ink2" : "t-marker";
      const lh = size.ty.lh;
      const top = row.n != null && row.lines.length === 1 ? y + 11 - lh / 2 : y;
      this.text(rg, x, top + size.ty.size - 2, row.lines, size.ty, `${cls} halo`);
      if (wire && row.n != null) wire(rg, row);
      y += row.h + 5;
    }
  }

  drawCallouts() {
    if (this.o.compact) return;
    const loose = [...this.B.paths.map((p) => p.steps), this.B.spine.steps].flatMap((steps) => steps.filter((s) => s.from !== s.to && this.linkFor(s.from, s.to) < 0).map((s) => this.loose(s.from, s.to, steps).pts));
    const soft = [...this.routes.filter(Boolean).map((x) => x.pts), ...Object.values(this.railPts), ...loose];
    (this.B.callouts || []).forEach((c) => {
      const lines = wrap(c.text, 176, this.T.note);
      const w = Math.max(...lines.map((ln) => measure(ln, this.T.note))) + 24;
      const h = lines.length * this.T.note.lh + 18;
      if (c.link) {
        const [a, z] = c.link.map((id) => this.B.owner[id]);
        const li = this.B.links.findIndex((l) => (l.from === a && l.to === z) || (l.from === z && l.to === a));
        const route = li >= 0 && this.routes[li];
        if (!route) return;
        const spot = this.place({ w: w + 8, h: h + 8 }, route.pts, soft);
        if (!spot) return;
        this.placed.push(spot.r);
        if (spot.leader) this.R("k-ink3 lead", this.rc.line(spot.leader[0][0], spot.leader[0][1], spot.leader[1][0], spot.leader[1][1], this.O({ strokeWidth: 1.1, roughness: 0.4, strokeLineDash: [3, 4] })), this.layers.notes);
        this.drawSticky(this.layers.notes, grow(spot.r, -4), lines, c.text);
        return;
      }
      const r = this.G.box[c.block];
      if (!r) return;
      const at = this.place.around({ w: w + 8, h: h + 8 }, grow(r, 2), soft, this.extent());
      if (!at) return;
      this.placed.push(at);
      this.drawSticky(this.layers.notes, grow(at, -4), lines, c.text);
    });
  }

  drawPath() {
    const layer = this.layers.path;
    layer.replaceChildren();
    const p = this.st.story && this.path(this.st.story);
    this.layers.spine.classList.toggle("gone", !!p);
    this.layers.spineRing.classList.toggle("gone", !!p);
    this.layers.pathRing.replaceChildren();
    const flows = p ? new Set(p.steps.flatMap((s) => s.flows)) : null;
    this.litLinks = p ? new Set(this.B.links.map((l, i) => (l.flows.some((f) => flows.has(f)) ? i : -1)).filter((i) => i >= 0)) : null;
    for (const e of this.labEls || []) {
      const carries = p && p.steps.some((s) => s.from !== s.to && this.linkFor(s.from, s.to) === e.i);
      const off = !!p && !this.litLinks.has(e.i);
      e.g.classList.toggle("gone", e.plain ? !p || carries || off : (e.spine && !!p) || !!carries || off);
    }
    if (!p) return;
    const hard = [...this.obstacles(), ...this.svg.querySelectorAll(".overview .note")].map((x) => (x instanceof Element ? this.boxOf(x) : x));
    for (const e of this.labEls || []) if (!e.g.classList.contains("gone")) hard.push(this.boxOf(e.g));
    const place = placer(hard);
    const onLink = {};
    p.steps.forEach((s, i) => { if (s.from !== s.to) { const li = this.linkFor(s.from, s.to); if (li >= 0) (onLink[li] = onLink[li] || []).push(i); } });
    const lines = this.routes.filter(Boolean).map((r) => r.pts);
    const loose = p.steps.map((s) => (s.from !== s.to && this.linkFor(s.from, s.to) < 0 ? this.loose(s.from, s.to, p.steps).pts : null));
    const tags = [];
    const done = new Set();
    p.steps.forEach((s, i) => {
      const tone = s.risk ? "red" : s.stop ? "ink2" : "marker";
      const li = s.from === s.to ? -1 : this.linkFor(s.from, s.to);
      this.seed = hash(`path-${p.id}-${i}`);
      const g = this.el("g", { class: "step", "data-step": i }, layer);
      if (li < 0) {
        const lab = this.drawLoose(g, s, p.steps, tone, !!s.risk, this.T.step, i, this.el("g", { class: "step", "data-step": i }, this.layers.pathRing), place, [...lines, ...loose.filter((x, k) => x && k !== i)]);
        if (s.stop) { const at = this.boxOf(g); this.octagon(g, at.x - 12, at.y + 11); }
        this.wireStep(lab, i, p);
        tags.push(lab);
      } else {
        const base = this.routes[li].pts;
        const pts = this.B.links[li].from === s.from ? base : [...base].reverse();
        const hl = this.el("g", { class: "hl" }, g);
        this.R("k-hl", this.rc.path(this.arrowPath(pts), this.O({ strokeWidth: 12, roughness: 0.5 })), hl);
        const cls = tone === "red" ? "k-red" : tone === "ink2" ? "k-ink2" : "k-marker";
        this.drawArrow(g, pts, 2.8, s.risk ? [11, 8] : null, "fill", cls, cls.replace("k-", "fh-"));
        if (s.stop) { const m = pts.length > 2 ? pts[Math.floor(pts.length / 2)] : [(pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2]; this.octagon(g, m[0], m[1]); }
        if (!done.has(li)) {
          done.add(li);
          const rows = onLink[li].map((n) => { const x = p.steps[n]; return { n, text: x.say, tone: x.risk ? "red" : x.stop ? "ink2" : "marker" }; });
          const size = this.rowsOf(rows, this.T.step);
          const spot = place(size, base, lines, true);
          if (spot) {
            const lab = this.el("g", { class: "step", "data-step": i }, layer);
            this.leader(lab, spot, "k-marker");
            this.drawRows(lab, spot.r, size, "spine", (rg, row) => this.wireStep(rg, row.n, p));
            tags.push(lab);
          }
        }
      }
      this.wireStep(g, i, p);
    });
    layer.append(...tags);
  }

  boxOf(g) {
    const b = g.getBBox();
    return { x: b.x, y: b.y, w: b.width, h: b.height };
  }

  octagon(g, cx, cy) {
    const pts = Array.from({ length: 8 }, (_, i) => [cx + Math.cos(Math.PI / 8 + (i * Math.PI) / 4) * 10, cy + Math.sin(Math.PI / 8 + (i * Math.PI) / 4) * 10]);
    this.el("path", { d: `M ${pts.map((p) => p.join(" ")).join(" L ")} Z`, class: "paper" }, g);
    this.R("k-ink2", this.rc.polygon(pts, this.O({ strokeWidth: 1.8, roughness: 0.6 })), g);
    this.R("k-ink2", this.rc.line(cx - 5, cy, cx + 5, cy, this.O({ strokeWidth: 2.4, roughness: 0.3 })), g);
  }

  hot(i) {
    this.st.hot = i;
    const scope = this.st.story ? [this.layers.path, this.layers.pathRing] : [this.layers.ov];
    for (const sc of scope) sc.querySelectorAll(".step").forEach((g) => g.classList.toggle("hot", Number(g.dataset.step) === i));
  }

  applyFocus() {
    if (!this.blockEls) return;
    const p = this.st.story && this.path(this.st.story);
    const inPath = p ? new Set(p.steps.flatMap((s) => [s.from, s.to])) : null;
    for (const [id, e] of Object.entries(this.blockEls)) { e.g.classList.toggle("dim", !!inPath && !inPath.has(id)); e.g.classList.toggle("sel", id === this.st.sel); }
    (this.linkEls || []).forEach((g, i) => g && g.classList.toggle("dim", !!p && !(this.litLinks && this.litLinks.has(i))));
    for (const e of this.labEls || []) e.g.classList.toggle("dim", !!p && !(this.litLinks && this.litLinks.has(e.i)));
    for (const e of [...(this.linkEls || []), ...(this.labEls || []).map((x) => x.g)]) if (e) e.classList.toggle("lit", !!p && !!this.litLinks && this.litLinks.has(Number(e.dataset.link)));
    this.svg.querySelectorAll(".frame, .lanes, .thesis").forEach((x) => x.classList.toggle("dim", !!p));
    this.svg.querySelectorAll(".part").forEach((x) => x.classList.toggle("on", x.dataset.part === this.st.part));
    this.svg.querySelectorAll(".edge, .elab").forEach((x) => x.classList.toggle("on", !!this.st.part && (x.dataset.a === this.st.part || x.dataset.b === this.st.part)));
    this.svg.querySelector(".inside-art")?.classList.toggle("picked", !!this.st.part);
    this.root.classList.toggle("focused", !!this.st.shape);
    this.root.classList.toggle("inside", !!this.st.shape);
  }

  anatomy(bid) {
    const b = this.K[bid];
    const ids = b.members.filter((m) => this.items[m]);
    const mine = new Set(ids);
    const comp = (m) => this.items[m] && this.items[m].type === "component";
    const frame = b.frame && this.F[b.frame] ? this.F[b.frame] : null;
    const sibs = frame && frame.scope === "process" ? this.B.blocks.filter((x) => x.id !== bid && x.frame === frame.id).map((x) => x.id) : [];
    const node = (e) => {
      if (mine.has(e)) return e;
      const o = this.blockOf[e];
      if (!o || o === bid) return null;
      return sibs.includes(o) ? `sib:${o}` : `ghost:${o}`;
    };
    const touching = Object.values(this.flows).filter((f) => mine.has(f.from) || mine.has(f.to));
    const walk = this.walkFlows();
    const loose = b.kind === "worker";
    const carries = (f) => f.kind !== "control" && (loose || f.critical || f.mode === "sync");
    const into = touching.filter((f) => mine.has(f.to) && comp(f.to) && !mine.has(f.from) && node(f.from) && f.kind !== "control");
    const walked = into.filter((f) => walk.has(f.id)).sort((x, y) => walk.get(x.id) - walk.get(y.id));
    const asked = into.filter((f) => f.kind === "sync");
    const entries = [...new Set((walked.length ? walked : asked.length ? asked : into).map((f) => f.to))];
    const depth = {};
    const reach = (start) => {
      depth[start] = 0;
      const q = [start];
      while (q.length) {
        const m = q.shift();
        for (const f of touching) {
          if (f.from !== m || !mine.has(f.to) || !comp(f.to) || Object.hasOwn(depth, f.to) || !carries(f)) continue;
          depth[f.to] = depth[m] + 1;
          q.push(f.to);
        }
      }
    };
    for (const e of entries) if (!Object.hasOwn(depth, e)) reach(e);
    if (!Object.keys(depth).length && ids.some(comp)) reach(ids.find(comp));
    const onWalk = (m) => touching.some((f) => walk.has(f.id) && (f.from === m || f.to === m));
    const path = Object.keys(depth).sort((x, y) => depth[x] - depth[y] || (onWalk(y) ? 1 : 0) - (onWalk(x) ? 1 : 0) || ids.indexOf(x) - ids.indexOf(y));
    const inPath = new Set(path);
    const guards = {};
    for (const m of ids) {
      if (!comp(m) || inPath.has(m)) continue;
      const calls = touching.filter((f) => f.kind === "control" && ((f.from === m && inPath.has(f.to)) || (f.to === m && inPath.has(f.from))));
      if (!calls.length) continue;
      const by = calls.map((f) => (f.from === m ? f.to : f.from)).sort((x, y) => depth[x] - depth[y])[0];
      guards[m] = by;
    }
    const heap = ids.filter((m) => this.items[m].type === "store" && this.items[m].medium === "memory");
    const rest = ids.filter((m) => comp(m) && !inPath.has(m) && !guards[m]);
    const others = ids.filter((m) => !comp(m) && !heap.includes(m));
    return { b, ids, mine, node, touching, path, depth, guards, heap, rest, others, sibs };
  }

  walkFlows() {
    return this.B.walks[this.st.story || ""];
  }

  walkSteps() {
    return this.st.story ? this.path(this.st.story).steps : this.B.spine.steps;
  }

  memberSize(m, w) {
    const it = this.items[m];
    const tw = w - 70;
    const name = wrap(it.label, tw, this.T.member);
    const part = this.shapeOf(m);
    const role = part && part.role ? wrap(part.role, tw, this.T.mrole) : [];
    const unbuilt = it.status !== "live";
    const tail = part && part.shape === "guard" ? 20 : 0;
    return { name, role, h: Math.max(78, 14 + name.length * this.T.member.lh + (role.length ? 4 + role.length * this.T.mrole.lh : 0) + (unbuilt ? 20 : 0) + 14 + tail) };
  }

  memberPath(shape, r) {
    const { x, y, w, h } = r;
    if (shape === "gate") return `M ${x} ${y} L ${x + w} ${y + h * 0.12} L ${x + w} ${y + h * 0.88} L ${x} ${y + h} Z`;
    if (shape === "relay") return `M ${x} ${y} H ${x + w - 16} L ${x + w} ${y + h / 2} L ${x + w - 16} ${y + h} H ${x} L ${x + 8} ${y + h / 2} Z`;
    if (shape === "guard") return `M ${x} ${y} H ${x + w} V ${y + h - 24} Q ${x + w} ${y + h - 8} ${x + w / 2} ${y + h} Q ${x} ${y + h - 8} ${x} ${y + h - 24} Z`;
    const k = shape === "watcher" ? Math.min(h / 2, 30) : shape === "recovery" ? 18 : 0;
    if (!k) return `M ${x} ${y} H ${x + w} V ${y + h} H ${x} Z`;
    return `M ${x + k} ${y} H ${x + w - k} Q ${x + w} ${y} ${x + w} ${y + k} V ${y + h - k} Q ${x + w} ${y + h} ${x + w - k} ${y + h} H ${x + k} Q ${x} ${y + h} ${x} ${y + h - k} V ${y + k} Q ${x} ${y} ${x + k} ${y} Z`;
  }

  memberW(m, least) {
    return Math.max(least, Math.ceil(Math.max(...this.items[m].label.split(/\s+/).map((x) => measure(x, this.T.member))) + 78));
  }

  shapeOf(m) {
    return (this.B.parts || []).find((p) => p.part === m) || null;
  }

  ghostSize(o) {
    const blk = this.K[o];
    const sil = blk.kind === "actor" ? "actor" : this.sil(blk);
    const w = sil === "actor" ? 132 : 158;
    const [top, bottom] = { actor: [58, 12], chip: [13, 22], cylinder: [27, 18], bucket: [32, 16], key: [14, 14], pages: [14, 14] }[sil] || [14, 14];
    const lead = sil === "key" ? 44 : 14;
    const name = wrap(blk.name, w - lead - 12, this.T.link);
    return { w, h: top + name.length * this.T.link.lh + bottom, name, sil, top, lead };
  }

  drawInside() {
    const layer = this.layers.inside;
    layer.replaceChildren();
    this.inBox = null;
    const sid = this.st.shape;
    if (!sid || !this.K[sid]) return;
    const A = this.anatomy(sid);
    const b = A.b;
    const own = this.G.box[sid];
    const storeView = STORES.has(b.kind);
    const walk = this.walkFlows();
    const boxes = {};
    const meta = {};
    const GY = 60;
    const pad = 30;
    const title = wrap(b.name, 520, this.T.title);
    const roleLines = wrap(b.role, 560, this.T.role);
    const TH = title.length * this.T.title.lh + roleLines.length * this.T.role.lh + 50;
    const pairs = new Map();
    for (const f of A.touching) {
      const a = A.node(f.from);
      const z = A.node(f.to);
      if (!a || !z || a === z) continue;
      const key = [a, z].sort().join("|");
      if (!pairs.has(key)) pairs.set(key, { a, z, flows: [], back: false, nums: [] });
      const e = pairs.get(key);
      e.flows.push(f);
      if (a !== e.a) e.back = true;
    }
    for (const [f, n] of walk) {
      const fl = this.flows[f];
      if (!fl) continue;
      const a = A.node(fl.from);
      const z = A.node(fl.to);
      if (a && z && a !== z) { const e = pairs.get([a, z].sort().join("|")); if (e) e.nums.push([n, fl]); continue; }
      if (a && a === z && a.startsWith("ghost:")) {
        const near = [...pairs.values()].filter((e) => (e.a === a || e.z === a) && e.nums.length).sort((x, y) => y.flows.filter((q) => q.critical).length - x.flows.filter((q) => q.critical).length)[0];
        if (near) near.nums.push([n, fl]);
      }
    }
    const edges = [...pairs.values()];
    for (const e of edges) {
      e.nums.sort((x, y) => x[0] - y[0]);
      e.strength = Math.max(...e.flows.map((f) => (f.kind === "control" ? 0 : f.mode === "async" ? 1 : 2)));
      const lead = e.flows.find((f) => A.node(f.from) === e.a && walk.has(f.id)) || e.flows.find((f) => A.node(f.from) === e.a) || e.flows[0];
      const label = e.flows.map((f) => f.label).find(Boolean);
      e.size = e.nums.length ? this.rowsOf(e.nums.map(([n, f]) => ({ n: n - 1, text: f.label || this.walkSteps()[n - 1].say, tone: "marker" })), this.T.link, 104) : label ? this.rowsOf([{ text: lead.label || label }], this.T.link, 104) : null;
      if (lead && A.node(lead.from) !== e.a) [e.a, e.z] = [e.z, e.a];
    }
    const inPath = new Set(A.path);
    const core = (e) => e.nums.length > 0 || [e.a, e.z].some((n) => A.heap.includes(n) || n.startsWith("sib:")) || (inPath.has(e.a) && inPath.has(e.z)) || A.guards[e.a] === e.z || A.guards[e.z] === e.a || ((inPath.has(e.a) || inPath.has(e.z)) && A.mine.has(e.a) && A.mine.has(e.z));
    for (const e of edges) e.tier = core(e) ? 0 : 2;
    const rank = (e) => e.strength * 10 + e.flows.filter((f) => f.critical).length;
    for (const n of new Set(edges.flatMap((e) => [e.a, e.z]))) {
      if (inPath.has(n) || edges.some((e) => e.tier < 2 && (e.a === n || e.z === n))) continue;
      const best = edges.filter((e) => e.a === n || e.z === n).sort((x, y) => rank(y) - rank(x))[0];
      if (best) best.tier = 1;
    }
    const labelW = (x, y) => Math.max(0, ...edges.filter((e) => e.size && e.tier < 2 && ((e.a === x && e.z === y) || (e.a === y && e.z === x))).map((e) => e.size.w));
    if (storeView) {
      const stores = A.ids.filter((m) => this.items[m].type === "store");
      const comps = A.ids.filter((m) => this.items[m].type !== "store");
      let y = TH;
      for (const s of stores) {
        const card = this.contentsCard(this.items[s]);
        boxes[s] = { x: 0, y, w: card.w, h: card.h };
        meta[s] = { kind: "card", card };
        y += card.h + GY;
      }
      const cw = Math.max(...stores.map((s) => boxes[s].w));
      const gx = Math.max(90, ...comps.map((m) => Math.max(...stores.map((s) => labelW(m, s) + 30))));
      let yy = TH;
      for (const m of comps) {
        const mw = this.memberW(m, 200);
        const sz = this.memberSize(m, mw);
        boxes[m] = { x: cw + gx, y: yy, w: mw, h: sz.h };
        meta[m] = { kind: "member", size: sz };
        yy += sz.h + GY;
      }
    } else {
      const cols = [];
      for (const m of A.path) (cols[A.depth[m]] = cols[A.depth[m]] || []).push(m);
      const colOf = {};
      const rowOf = {};
      cols.forEach((list, d) => list.forEach((m, r) => { colOf[m] = d; rowOf[m] = r; }));
      const byCaller = {};
      for (const [g, by] of Object.entries(A.guards)) (byCaller[by] = byCaller[by] || []).push(g);
      for (const [by, gs] of Object.entries(byCaller)) {
        const d = colOf[by];
        const top = Math.min(...cols[d].map((m) => rowOf[m]));
        let below = Math.max(...cols[d].map((m) => rowOf[m]));
        let above = top;
        gs.forEach((g, i) => {
          colOf[g] = d;
          if (i % 2 === 0) { above -= 1; rowOf[g] = above; }
          else { below += 1; rowOf[g] = below; }
          cols[d].push(g);
        });
      }
      const placed = Object.keys(colOf);
      const nCols = cols.length;
      const colW = cols.map((list) => Math.max(...list.map((m) => this.memberW(m, 196))));
      const size = {};
      for (const m of placed) size[m] = this.memberSize(m, colW[colOf[m]]);
      const rows = [...new Set(placed.map((m) => rowOf[m]))].sort((x, y) => x - y);
      const rowH = Object.fromEntries(rows.map((r) => [r, Math.max(...placed.filter((m) => rowOf[m] === r).map((m) => size[m].h))]));
      const gut = [];
      for (let d = 0; d < nCols - 1; d++) {
        let w = 64;
        for (const x of cols[d]) for (const y of cols[d + 1]) if (rowOf[x] === rowOf[y]) w = Math.max(w, labelW(x, y) + 24);
        gut[d] = Math.min(w, 150);
      }
      const colX = [];
      let x = 0;
      for (let d = 0; d < nCols; d++) { colX[d] = x; x += colW[d] + (d < nCols - 1 ? gut[d] : 0); }
      const pathRight = x;
      const vgap = {};
      rows.slice(0, -1).forEach((r, i) => {
        const next = rows[i + 1];
        let h = 52;
        for (const m of placed) for (const z of placed) if (rowOf[m] === r && rowOf[z] === next && colOf[m] === colOf[z]) h = Math.max(h, labelW(m, z) ? edges.find((e) => (e.a === m && e.z === z) || (e.a === z && e.z === m)).size.h + 22 : 0);
        vgap[r] = Math.min(h, 110);
      });
      const rowY = {};
      let y = TH;
      rows.forEach((r) => { rowY[r] = y; y += rowH[r] + (vgap[r] || 0); });
      for (const m of placed) {
        const cw = colW[colOf[m]];
        boxes[m] = { x: colX[colOf[m]], y: rowY[m in rowOf ? rowOf[m] : 0] + (rowH[rowOf[m]] - size[m].h) / 2, w: cw, h: size[m].h };
        meta[m] = { kind: "member", size: size[m] };
      }
      let bottom = y;
      const heapIds = A.heap;
      if (heapIds.length) {
        const touch = placed.filter((m) => edges.some((e) => (e.a === m && heapIds.includes(e.z)) || (e.z === m && heapIds.includes(e.a)))).map((m) => colOf[m]);
        let lo = touch.length ? Math.min(...touch) : 0;
        let hi = touch.length ? Math.max(...touch) : nCols - 1;
        if (hi === lo && nCols > 1) { if (hi < nCols - 1) hi += 1; else lo -= 1; }
        const hx = nCols ? colX[lo] : 0;
        const hw = Math.max(nCols ? colX[hi] + colW[hi] - hx : 0, 420);
        const tw = hw - 120 - this.pillW("in memory");
        const lines = heapIds.map((m) => { const p = this.shapeOf(m); return { m, name: wrap(this.labelOf(m), tw, this.T.member), role: wrap(p && p.role ? p.role : (this.items[m].what || ""), tw, this.T.mrole) }; });
        let hy = bottom + GY - 12;
        for (const ln of lines) {
          const h = Math.max(70, 16 + ln.name.length * this.T.member.lh + ln.role.length * this.T.mrole.lh + 16);
          boxes[ln.m] = { x: hx, y: hy, w: hw, h };
          meta[ln.m] = { kind: "heap", lines: ln };
          hy += h + 18;
        }
        bottom = hy - 18;
      }
      const back = [...A.rest, ...A.others];
      const items = [...back.map((m) => ({ id: m, w: this.memberW(m, 196), h: this.memberSize(m, this.memberW(m, 196)).h })), ...A.sibs.map((s) => ({ id: `sib:${s}`, w: 226, h: this.sibSize(s).h }))];
      if (items.length) {
        const known = { ...boxes };
        const bary = (id) => {
          const xs = edges.filter((e) => e.a === id || e.z === id).map((e) => (e.a === id ? e.z : e.a)).filter((o) => known[o]).map((o) => known[o].x + known[o].w / 2);
          return xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : null;
        };
        items.forEach((it, i) => { const v = bary(it.id); it.want = v == null ? (it.id.startsWith("sib:") ? Infinity : i * 1000) : v; });
        items.sort((p, q) => (p.id.startsWith("sib:") ? 1 : 0) - (q.id.startsWith("sib:") ? 1 : 0) || p.want - q.want);
        const by = bottom + GY + 10;
        const rowHb = Math.max(...items.map((it) => it.h));
        let bx = 0;
        for (const it of items) {
          const want = Number.isFinite(it.want) ? it.want - it.w / 2 : bx;
          const at = Math.max(bx, Math.min(want, bx + 40));
          boxes[it.id] = { x: at, y: by + (rowHb - it.h) / 2, w: it.w, h: it.h };
          meta[it.id] = it.id.startsWith("sib:") ? { kind: "sib", size: this.sibSize(it.id.slice(4)) } : { kind: "member", size: this.memberSize(it.id, it.w) };
          bx = at + it.w + 52;
        }
        bottom = by + rowHb;
      }
    }
    const inner = Object.values(boxes);
    const W = Math.max(...inner.map((r) => r.x + r.w), measure(title[0], this.T.title) + 40);
    const H = Math.max(...inner.map((r) => r.y + r.h));
    const fr = { x: -pad, y: 0, w: W + pad * 2, h: H + pad };
    const ghosts = [...new Set(edges.flatMap((e) => [e.a, e.z]).filter((n) => n.startsWith("ghost:")))].map((n) => n.slice(6));
    const leftSide = (o) => {
      const blk = this.K[o];
      if (blk.kind === "actor") return true;
      const fl = A.touching.filter((f) => (this.blockOf[f.from] === o || this.blockOf[f.to] === o) && f.kind !== "control");
      const first = fl.filter((f) => walk.has(f.id)).sort((x, y) => walk.get(x.id) - walk.get(y.id))[0] || fl.find((f) => f.kind === "sync");
      if (first) return this.blockOf[first.from] === o && A.path.includes(first.to);
      return this.G.col[o] < this.G.col[sid];
    };
    const centerY = (o) => {
      const near = edges.filter((e) => e.a === `ghost:${o}` || e.z === `ghost:${o}`).map((e) => (e.a === `ghost:${o}` ? e.z : e.a)).filter((n) => boxes[n]);
      const path = near.filter((n) => A.path.includes(n)).sort((x, y) => A.depth[x] - A.depth[y]);
      const ys = (leftSide(o) && path.length ? path.slice(0, 1) : near).map((n) => boxes[n].y + boxes[n].h / 2);
      return ys.length ? ys.reduce((s, v) => s + v, 0) / ys.length : H / 2;
    };
    const sideGap = (list) => clamp(Math.max(0, ...edges.filter((e) => e.size && e.tier < 2 && list.some((o) => e.a === `ghost:${o}` || e.z === `ghost:${o}`)).map((e) => e.size.w)) + 24, 64, 160);
    const L = ghosts.filter(leftSide);
    const Rg = ghosts.filter((o) => !leftSide(o));
    const stack = (list, xAt) => {
      const sized = list.map((o) => ({ o, s: this.ghostSize(o), c: centerY(o) })).sort((p, q) => p.c - q.c);
      let last = -Infinity;
      for (const g of sized) {
        const y0 = Math.max(last + 26, g.c - g.s.h / 2);
        boxes[`ghost:${g.o}`] = { x: xAt(g.s.w), y: y0, w: g.s.w, h: g.s.h };
        meta[`ghost:${g.o}`] = { kind: "ghost", size: g.s, o: g.o };
        last = y0 + g.s.h;
      }
    };
    const gl = sideGap(L);
    const gr = sideGap(Rg);
    stack(L, (w) => fr.x - gl - w);
    stack(Rg, () => fr.x + fr.w + gr);
    const all = Object.values(boxes);
    const x0 = Math.min(fr.x, ...all.map((r) => r.x)) - 30;
    const y0 = Math.min(0, ...all.map((r) => r.y)) - 26;
    const x1 = Math.max(fr.x + fr.w, ...all.map((r) => r.x + r.w)) + 30;
    const y1 = Math.max(fr.y + fr.h, ...all.map((r) => r.y + r.h)) + 30;
    const CW = x1 - x0;
    const CH = y1 - y0;
    const k = Math.min(own.w / CW, own.h / CH) * 1.25;
    const oc = [own.x + own.w / 2, own.y + own.h / 2];
    const tx = oc[0] - (CW * k) / 2 - x0 * k;
    const ty = oc[1] - (CH * k) / 2 - y0 * k;
    const g = this.el("g", { class: "inside-art", transform: `translate(${tx} ${ty}) scale(${k})` }, layer);
    this.inK = k;
    this.inBox = { x: tx + x0 * k, y: ty + y0 * k, w: CW * k, h: CH * k };
    const entry = A.path[0] && boxes[A.path[0]];
    const lead = L.length ? boxes[`ghost:${L[0]}`] : entry;
    this.inAnchor = { x: tx + (lead ? Math.min(lead.x, fr.x) - 30 : x0) * k, y: ty + (entry ? entry.y + entry.h / 2 : CH / 2) * k };
    this.seed = hash(`inside-${sid}`);
    const procLabel = b.frame && this.F[b.frame] && this.F[b.frame].scope === "process" ? this.F[b.frame].label : null;
    this.R("k-ink3", this.rc.rectangle(fr.x, fr.y, fr.w, fr.h, this.O({ strokeWidth: 1.8, roughness: 1.2, strokeLineDash: [7, 6] })), g);
    const titleG = this.el("g", { class: "in-title" }, g);
    this.text(titleG, fr.x + 22, 40, title, this.T.title, "");
    this.text(titleG, fr.x + 22, 40 + (title.length - 1) * this.T.title.lh + 28, roleLines, this.T.role, "t-ink2");
    const hard = [{ x: fr.x + 14, y: 8, w: Math.max(measure(title[0], this.T.title), ...roleLines.map((l) => measure(l, this.T.role))) + 20, h: TH - 30 }];
    if (procLabel) {
      const tag = procLabel.toUpperCase();
      const tw = measure(tag, this.T.lane) + 16;
      const tg = this.text(g, fr.x + fr.w - tw - 8, 24, [tag], this.T.lane, "t-caps t-ink3");
      tg.setAttribute("letter-spacing", "1.2");
      tg.classList.add("in-tag");
      hard.push({ x: fr.x + fr.w - tw - 14, y: 8, w: tw + 12, h: 24 });
    }
    const marks = {};
    for (const [id, r] of Object.entries(boxes)) {
      hard.push(grow(r, 5));
      if (meta[id].kind === "member" || meta[id].kind === "card" || meta[id].kind === "heap") { marks[id] = this.marksOf(id, r); hard.push(...marks[id].map((q) => grow(q, 2))); }
    }
    const vg = [];
    const hg = [];
    const xs = [...new Set(Object.values(boxes).flatMap((r) => [r.x, r.x + r.w]))].sort((p, q) => p - q);
    for (let i = 0; i < xs.length - 1; i++) if (xs[i + 1] - xs[i] > 30 && !Object.values(boxes).some((r) => r.x < xs[i] + 1 && r.x + r.w > xs[i + 1] - 1)) vg.push((xs[i] + xs[i + 1]) / 2);
    vg.push(x0 + 14, x1 - 14);
    const ys = [...new Set(Object.values(boxes).flatMap((r) => [r.y, r.y + r.h]))].sort((p, q) => p - q);
    for (let i = 0; i < ys.length - 1; i++) if (ys[i + 1] - ys[i] > 26 && !Object.values(boxes).some((r) => r.y < ys[i] + 1 && r.y + r.h > ys[i + 1] - 1)) hg.push((ys[i] + ys[i + 1]) / 2);
    hg.push(y1 - 14, TH - 11, y0 + 14);
    const go = router(boxes, vg, hg, hard.slice(0, procLabel ? 2 : 1));
    const taken = [];
    const order = [...edges].sort((p, q) => p.tier - q.tier || q.nums.length - p.nums.length || q.strength - p.strength || (p.a.startsWith("ghost:") ? 1 : 0) - (q.a.startsWith("ghost:") ? 1 : 0));
    const routes = new Map();
    for (const e of order) {
      const r = go(e.a, e.z, taken);
      routes.set(e, r);
      taken.push(r.pts);
    }
    tracks([...routes.values()]);
    const art = this.el("g", {}, g);
    const edgeLayer = this.el("g", {}, g);
    const nodeLayer = this.el("g", {}, g);
    const labLayer = this.el("g", {}, g);
    for (const [id, r] of Object.entries(boxes)) {
      const m = meta[id];
      if (m.kind === "member") this.drawMember(nodeLayer, id, r, m.size, marks[id]);
      else if (m.kind === "card") this.drawCard(nodeLayer, { id, ...r, ...m.card }, marks[id]);
      else if (m.kind === "heap") this.drawHeap(nodeLayer, id, r, m.lines, marks[id]);
      else if (m.kind === "sib") this.drawSib(nodeLayer, id.slice(4), r, m.size);
      else if (m.kind === "ghost") this.drawGhost(art, m.o, r, m.size);
    }
    const place = placer(hard);
    const soft = [...routes.values()].map((r) => r.pts);
    for (const e of order) {
      const r = routes.get(e);
      const lit = e.nums.length > 0;
      const outside = e.a.startsWith("ghost:") || e.z.startsWith("ghost:");
      const faint = e.tier === 2;
      const eg = this.el("g", { class: `edge${lit ? " lit" : ""}${faint ? " faint" : ""}`, "data-a": e.a, "data-b": e.z }, edgeLayer);
      this.el("path", { d: this.arrowPath(r.pts), class: "hit" }, eg);
      const cls = lit ? "k-marker" : faint || e.strength === 0 ? "k-ink3" : e.strength === 1 || outside ? "k-ink2" : "k-ink";
      this.seed = hash(`edge-${e.a}-${e.z}`);
      this.drawArrow(eg, r.pts, lit ? 2.6 : e.strength === 0 ? 1.5 : 1.9, lit ? null : e.strength === 0 ? [2, 6] : e.strength === 1 ? [9, 7] : null, e.strength === 2 || lit ? "fill" : "open", cls, cls.replace("k-", "fh-"));
      if (e.back) this.drawArrow(eg, [...r.pts].reverse().slice(0, 2), 1.4, null, "open", cls, cls.replace("k-", "fh-"));
      if (!e.size) continue;
      const spot = place(e.size, r.pts, soft.filter((p) => p !== r.pts));
      if (!spot) continue;
      const lab = this.el("g", { class: `elab${lit ? " lit" : ""}${faint ? " faint" : ""}`, "data-a": e.a, "data-b": e.z }, labLayer);
      if (spot.leader) this.R("k-ink3", this.rc.line(spot.leader[0][0], spot.leader[0][1], spot.leader[1][0], spot.leader[1][1], this.O({ strokeWidth: 1, roughness: 0.4, strokeLineDash: [3, 4] })), lab);
      this.drawRows(lab, spot.r, e.size, lit ? "spine" : "plain");
      this.el("title", {}, lab).textContent = e.flows.map((f) => f.label).filter(Boolean).join("; ");
    }
    for (const c of this.B.callouts || []) {
      const lines = wrap(c.text, 170, this.T.note);
      const w = Math.max(...lines.map((ln) => measure(ln, this.T.note))) + 24;
      const h = lines.length * this.T.note.lh + 18;
      let spot = null;
      if (c.link) {
        const ed = order.find((e) => (e.a === c.link[0] && e.z === c.link[1]) || (e.a === c.link[1] && e.z === c.link[0]));
        const near = ed && place({ w, h }, routes.get(ed).pts, soft);
        if (near && near.leader) this.R("k-ink3", this.rc.line(near.leader[0][0], near.leader[0][1], near.leader[1][0], near.leader[1][1], this.O({ strokeWidth: 1, roughness: 0.4, strokeLineDash: [3, 4] })), labLayer);
        spot = near && near.r;
      } else {
        const at = boxes[c.source] || (c.block === sid ? boxes[A.path[0]] : null);
        if (!at) continue;
        spot = place.around({ w, h }, at, soft, fr);
      }
      if (!spot) continue;
      this.drawSticky(labLayer, spot, lines, c.text);
      const sx = tx + (spot.x - 30) * k;
      const sy = ty + (spot.y - 30) * k;
      const ex = Math.max(this.inBox.x + this.inBox.w, tx + (spot.x + spot.w + 30) * k);
      const ey = Math.max(this.inBox.y + this.inBox.h, ty + (spot.y + spot.h + 30) * k);
      this.inBox = { x: Math.min(this.inBox.x, sx), y: Math.min(this.inBox.y, sy), w: 0, h: 0 };
      this.inBox.w = ex - this.inBox.x;
      this.inBox.h = ey - this.inBox.y;
      this.inAnchor.x = Math.min(this.inAnchor.x, sx);
    }
  }

  sibSize(o) {
    const blk = this.K[o];
    const name = wrap(blk.name, 200, this.T.member);
    const role = wrap(blk.role, 200, this.T.mrole);
    return { name, role, h: 16 + name.length * this.T.member.lh + 4 + role.length * this.T.mrole.lh + 44 };
  }

  marksOf(m, r) {
    return this.markList(this.items[m].badges, r.x + r.w - 6, r.y - 8, this.T.badge);
  }

  drawMarks(pg, m, list) {
    for (const q of list) this.drawMark(pg, q, this.labelOf(m));
  }

  bubblePath(g, x, y, w) {
    this.el("path", { d: `M ${x + 6} ${y} H ${x + w - 6} Q ${x + w} ${y} ${x + w} ${y + 6} V ${y + 15} Q ${x + w} ${y + 21} ${x + w - 6} ${y + 21} H ${x + 15} L ${x + 8} ${y + 27} L ${x + 9} ${y + 21} H ${x + 6} Q ${x} ${y + 21} ${x} ${y + 15} V ${y + 6} Q ${x} ${y} ${x + 6} ${y} Z` }, g);
  }

  drawSticky(parent, at, lines, text) {
    this.seed = hash(`note-${text}`);
    const g = this.el("g", { class: "note", transform: `rotate(${this.skin.clean ? 0 : (hash(text) % 3) - 1} ${at.x + at.w / 2} ${at.y + at.h / 2})` }, parent);
    this.R("f-sticky", this.rc.rectangle(at.x, at.y, at.w, at.h, this.O({ stroke: "none", fill: "#000", fillStyle: "solid" })), g);
    this.R("k-ink3", this.rc.rectangle(at.x, at.y, at.w, at.h, this.O({ strokeWidth: 1, roughness: 0.8 })), g);
    this.text(g, at.x + 12, at.y + 9 + this.T.note.size - 1, lines, this.T.note, "t-sticky");
    return g;
  }

  contentsCard(st) {
    const rows = st.holds.map((h) => {
      const what = wrap(h.text, 340, this.T.hold);
      const sub = h.note ? wrap(h.note, 340, this.T.small) : [];
      return { h, what, sub, height: what.length * this.T.hold.lh + sub.length * this.T.small.lh + 14 };
    });
    const head = wrap(st.label, st.tag ? 300 : 380, this.T.member);
    const top = 22 + head.length * this.T.member.lh;
    const h = top + rows.reduce((s, r) => s + r.height, 0) + 12;
    return { w: 420, h, rows, st, head, top };
  }

  drawCard(g, c, marks) {
    const pg = this.el("g", { class: "part contents", "data-part": c.id, role: "button", tabindex: 0, "aria-label": c.st.label }, g);
    this.el("rect", { class: "paper", x: c.x, y: c.y, width: c.w, height: c.h, rx: 8 }, pg);
    const sel = this.el("g", { class: "sel" }, pg);
    this.R("k-marker", this.rc.rectangle(c.x - 5, c.y - 5, c.w + 10, c.h + 10, this.O({ strokeWidth: 3, roughness: 1.6 })), sel);
    this.R("k-ink", this.rc.rectangle(c.x, c.y, c.w, c.h, this.O({ strokeWidth: 1.8 })), pg);
    this.text(pg, c.x + 14, c.y + 28, c.head, this.T.member, "");
    if (c.st.tag) this.pill(pg, c.x + c.w - 14 - this.pillW(c.st.tag), c.y + 14, c.st.tag, "");
    let y = c.y + c.top;
    for (const r of c.rows) {
      if (r.h.sealed != null) this.lock(pg, c.x + 22, y + 9, r.h.sealed);
      this.text(pg, c.x + 40, y + 13, r.what, this.T.hold, "");
      let yy = y + 13 + (r.what.length - 1) * this.T.hold.lh;
      for (const s of r.sub) { yy += this.T.small.lh; this.text(pg, c.x + 40, yy, [s], this.T.small, "t-ink3"); }
      y += r.height;
    }
    this.drawMarks(pg, c.id, marks || []);
    this.hoverEdges(pg, c.id);
    pg.addEventListener("click", (ev) => { if (this.dragMoved) return; ev.stopPropagation(); this.pickPart(c.id); });
    pg.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); ev.stopPropagation(); this.pickPart(c.id); } });
  }

  hoverEdges(pg, m) {
    const set = (on) => {
      this.svg.querySelector(".inside-art")?.classList.toggle("hovering", on);
      this.svg.querySelectorAll(".edge, .elab").forEach((x) => { if (x.dataset.a === m || x.dataset.b === m) x.classList.toggle("hov", on); });
    };
    pg.addEventListener("mouseenter", () => set(true));
    pg.addEventListener("mouseleave", () => set(false));
  }

  lock(g, x, y, closed) {
    this.R(closed ? "k-green" : "k-red", this.rc.rectangle(x - 6, y - 2, 12, 10, this.O({ strokeWidth: 1.4, roughness: 0.5 })), g);
    this.R(closed ? "k-green" : "k-red", this.rc.path(closed ? `M ${x - 4} ${y - 2} L ${x - 4} ${y - 6} Q ${x} ${y - 11} ${x + 4} ${y - 6} L ${x + 4} ${y - 2}` : `M ${x - 4} ${y - 2} L ${x - 4} ${y - 6} Q ${x} ${y - 11} ${x + 4} ${y - 7}`, this.O({ strokeWidth: 1.4, roughness: 0.4 })), g);
  }

  drawMember(g, m, r, size, marks) {
    const it = this.items[m];
    const pg = this.el("g", { class: "part", "data-part": m, role: "button", tabindex: 0, "aria-label": `${it.label}${this.shapeOf(m) ? `: ${this.shapeOf(m).role}` : ""}` }, g);
    this.el("rect", { class: "paper box", x: r.x, y: r.y, width: r.w, height: r.h, rx: 6 }, pg);
    const sel = this.el("g", { class: "sel" }, pg);
    this.R("k-marker", this.rc.rectangle(r.x - 5, r.y - 5, r.w + 10, r.h + 10, this.O({ strokeWidth: 3, roughness: 1.6 })), sel);
    const unbuilt = it.status !== "live";
    const actor = it.type === "actor";
    const shape = it.type === "store" ? null : (this.shapeOf(m) || {}).shape || "module";
    this.seed = hash(`member-${m}`);
    if (shape === "pool") for (const d of [10, 5]) this.R("k-ink3", this.rc.rectangle(r.x + d, r.y - d, r.w, r.h, this.O({ strokeWidth: 1.2 })), pg);
    const d = this.memberPath(shape, r);
    const stroke = this.O({ strokeWidth: 2, ...(unbuilt || actor ? { strokeLineDash: [7, 6] } : {}) });
    if (shape === "handoff") {
      this.el("path", { class: "paper", d: this.memberPath(null, { ...r, x: r.x - 9, y: r.y + 9 }) }, pg);
      this.R("k-ink3", this.rc.path(this.memberPath(null, { ...r, x: r.x - 9, y: r.y + 9 }), this.O({ strokeWidth: 1.4, strokeLineDash: [6, 5] })), pg);
    }
    this.el("path", { class: "paper", d }, pg);
    this.R(actor ? "k-ink2" : "k-ink", this.rc.path(d, stroke), pg);
    if (shape === "queue") for (const f of [0.52, 0.66, 0.8]) this.R("k-ink2", this.rc.line(r.x + r.w * f, r.y, r.x + r.w * f, r.y + 9, this.O({ strokeWidth: 1.6, roughness: 0.4 })), pg);
    if (shape === "recovery") this.R("k-ink2", this.rc.path(`M ${r.x + r.w - 30} ${r.y - 6} A 16 16 0 1 1 ${r.x + r.w + 6} ${r.y + 26}`, this.O({ strokeWidth: 1.6, roughness: 0.6 })), pg);
    if (shape === "handoff") this.drawArrow(pg, [[r.x - 4, r.y + r.h + 4], [r.x + 22, r.y + r.h + 4]], 1.6, null, "fill", "k-ink2", "fh-ink2");
    if (it.type === "store") this.storeGlyph(it.medium, pg, r.x + 32, r.y + 38, 40, "k-ink2");
    else this.glyph(shape, pg, r.x + 32, r.y + 38, 40, "k-ink2");
    let ty = r.y + 14 + this.T.member.size - 2;
    this.text(pg, r.x + 62, ty, size.name, this.T.member, "");
    ty += (size.name.length - 1) * this.T.member.lh + 4 + this.T.mrole.lh;
    if (size.role.length) this.text(pg, r.x + 62, ty, size.role, this.T.mrole, "t-ink2");
    if (unbuilt) this.pill(pg, r.x + 62, r.y + r.h - 26, it.status, "warn");
    this.drawMarks(pg, m, marks || []);
    this.hoverEdges(pg, m);
    pg.addEventListener("click", (ev) => { if (this.dragMoved) return; ev.stopPropagation(); this.pickPart(m); });
    pg.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); ev.stopPropagation(); this.pickPart(m); } });
  }

  drawHeap(g, m, r, lines, marks) {
    const it = this.items[m];
    const pg = this.el("g", { class: "part heap", "data-part": m, role: "button", tabindex: 0, "aria-label": `${it.label}, inside the process` }, g);
    this.seed = hash(`heap-${m}`);
    const sel = this.el("g", { class: "sel" }, pg);
    this.R("k-marker", this.rc.rectangle(r.x - 5, r.y - 5, r.w + 10, r.h + 10, this.O({ strokeWidth: 3, roughness: 1.6 })), sel);
    this.el("rect", { class: "paper2", x: r.x, y: r.y, width: r.w, height: r.h, rx: 10 }, pg);
    this.R("f-green", this.rc.rectangle(r.x + 3, r.y + 3, r.w - 6, r.h - 6, this.O({ stroke: "none", fill: "#000", fillStyle: "hachure", hachureGap: 11, hachureAngle: 60, fillWeight: 0.8 })), pg);
    this.R("k-ink2", this.rc.rectangle(r.x, r.y, r.w, r.h, this.O({ strokeWidth: 1.8, roughness: 1.1, strokeLineDash: [10, 5] })), pg);
    this.storeGlyph("memory", pg, r.x + 44, r.y + r.h / 2 - 4, 52, "k-ink2");
    this.text(pg, r.x + 96, r.y + 14 + this.T.member.size - 2, lines.name, this.T.member, "");
    this.text(pg, r.x + 96, r.y + 14 + this.T.member.size - 2 + (lines.name.length - 1) * this.T.member.lh + 4 + this.T.mrole.lh, lines.role, this.T.mrole, "t-ink2");
    this.pill(pg, r.x + r.w - 14 - this.pillW("in memory"), r.y + r.h - 28, "in memory", "");
    this.drawMarks(pg, m, marks || []);
    this.hoverEdges(pg, m);
    pg.addEventListener("click", (ev) => { if (this.dragMoved) return; ev.stopPropagation(); this.pickPart(m); });
    pg.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); ev.stopPropagation(); this.pickPart(m); } });
  }

  drawSib(g, o, r, size) {
    const blk = this.K[o];
    const gg = this.el("g", { class: "sibcard", "data-sib": o, role: "button", tabindex: 0, "aria-label": `Look inside ${blk.name}, in the same process` }, g);
    this.seed = hash(`sib-${o}`);
    this.el("rect", { class: "paper", x: r.x, y: r.y, width: r.w, height: r.h, rx: 8 }, gg);
    this.R("k-ink2", this.rc.rectangle(r.x, r.y, r.w, r.h, this.O({ strokeWidth: 1.7, strokeLineDash: [8, 5] })), gg);
    this.text(gg, r.x + 14, r.y + 14 + this.T.member.size - 2, size.name, this.T.member, "");
    this.text(gg, r.x + 14, r.y + 14 + this.T.member.size - 2 + (size.name.length - 1) * this.T.member.lh + 4 + this.T.mrole.lh, size.role, this.T.mrole, "t-ink2");
    const parts = blk.members.filter((m) => this.items[m] && this.items[m].type === "component").slice(0, 5);
    parts.forEach((m, i) => this.glyph((this.shapeOf(m) || {}).shape || "module", gg, r.x + 26 + i * 40, r.y + r.h - 22, 26, "k-ink3"));
    gg.addEventListener("click", (ev) => { if (this.dragMoved) return; ev.stopPropagation(); this.goTo(o); });
    gg.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.goTo(o); } });
  }

  drawGhost(g, o, r, size) {
    const blk = this.K[o];
    const gg = this.el("g", { class: "ghostcard", role: "button", tabindex: 0, "aria-label": `Look inside ${blk.name}` }, g);
    this.seed = hash(`ghost-${o}`);
    const D = this.doodle();
    const tone = { cls: "k-ink2", strokeWidth: 1.7, roughness: 1 };
    this.el("rect", { class: "paper", x: r.x, y: r.y, width: r.w, height: r.h, rx: 8 }, gg);
    if (size.sil === "actor") {
      this.R("k-ink2", this.rc.rectangle(r.x, r.y, r.w, r.h, this.O({ strokeWidth: 1.6, strokeLineDash: [7, 5] })), gg);
      D.person(gg, r.x + r.w / 2 + 18, r.y + 10, 0.72);
      this.R("k-ink2", this.rc.rectangle(r.x + r.w / 2 - 22, r.y + 16, 17, 28, this.O({ strokeWidth: 1.4 })), gg);
      this.R("k-ink2", this.rc.rectangle(r.x + r.w / 2 - 44, r.y + 22, 15, 22, this.O({ strokeWidth: 1.3 })), gg);
    } else if (size.sil === "chip") {
      this.outline("chip", gg, { x: r.x, y: r.y, w: r.w, h: r.h - 9 }, tone);
    } else if (size.sil === "cylinder" || size.sil === "bucket" || size.sil === "pages") {
      this.outline(size.sil, gg, r, tone);
    } else {
      this.outline("key", gg, r, { ...tone, strokeLineDash: size.sil === "key" ? null : [7, 5] });
      if (size.sil === "key") D.key(gg, r.x + 14, r.y + r.h / 2, 0.3);
    }
    this.text(gg, r.x + size.lead, r.y + size.top + this.T.link.size - 3, size.name, this.T.link, "t-ink2");
    gg.addEventListener("click", (ev) => { if (this.dragMoved) return; ev.stopPropagation(); this.goTo(o); });
    gg.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.goTo(o); } });
  }

  refresh() {
    if (this.gone || !this.M) return;
    const cw = this.canvas.getBoundingClientRect().width;
    if (!cw) return;
    let lay = cw < TALL_AT ? "tall" : "wide";
    let compact = false;
    let stack = false;
    const print = !this.o.dialog && printing();
    if (this.o.dialog && lay === "tall") stack = cw / this.sizeOf("tall", false, false).w < READ;
    if (!this.o.dialog && !print && !this.o.fill) {
      const mh = this.maxH();
      const f = this.sizeOf(lay, false, false);
      compact = Math.min(cw / f.w, mh / f.h) < READ;
      const least = GLIMPSE_NAME / this.skin.glimpse.name.size;
      if (compact && lay === "wide") {
        const g = this.sizeOf("wide", true, false);
        if (Math.min(cw / g.w, mh / g.h) < least) lay = "tall";
      }
      if (compact && lay === "tall") stack = cw / this.sizeOf("tall", true, false).w < least;
    }
    if (compact !== !!this.mode) {
      if (compact) { this.want = this.view(); this.clearView(); }
      else if (this.want) { this.clearView(); this.applyView(this.want); this.want = null; }
      this.mode = compact;
      this.key = null;
    }
    this.root.classList.toggle("compact", compact);
    this.root.classList.toggle("printing", print);
    if (this.layoutKey(lay, compact, stack) !== this.key) this.lay(lay, compact, stack);
    this.renderChips();
    this.renderCrumbs();
    this.renderReadout();
    if (!this.o.dialog) this.sizeCanvas(print);
    cancelAnimationFrame(this.anim);
    this.setVB(print ? this.fitBox() : this.st.shape && this.inBox ? this.focusBox() : this.home());
    if (this.settled) this.markReady();
  }

  setCompact(c) {
    this.o.compact = c;
    this.ty = c ? this.skin.glimpse : this.skin.ty;
  }

  layoutKey(lay, compact, stack) {
    return `${this.rev}|${lay}|${fontGen}|${compact}|${stack}`;
  }

  lay(lay, compact, stack) {
    this.key = this.layoutKey(lay, compact, stack);
    this.st.layout = lay;
    this.st.stack = stack;
    this.setCompact(compact);
    this.render();
  }

  sizeOf(lay, compact, stack) {
    const k = this.layoutKey(lay, compact, stack);
    if (!this.full[k]) {
      if (k !== this.key) this.lay(lay, compact, stack);
      const m = compact ? 40 : 32;
      this.full[k] = { w: this.G.ext.w + m * 2, h: this.G.ext.h + m * 2 };
    }
    return this.full[k];
  }

  maxH() {
    const c = this.canvas;
    const was = c.style.height;
    c.style.height = "100000px";
    const h = c.getBoundingClientRect().height;
    c.style.height = was;
    return h;
  }

  sizeCanvas(print) {
    const e = this.G.ext;
    const m = this.o.compact ? 40 : 32;
    const w = e.w + m * 2;
    const h = e.h + m * 2;
    this.canvas.style.aspectRatio = print ? `${Math.round(w)} / ${Math.round(h)}` : "";
    if (print) { this.canvas.style.height = "auto"; return; }
    const cw = this.canvas.getBoundingClientRect().width;
    this.canvas.style.height = `${Math.round(this.o.fill ? this.maxH() : Math.min(this.maxH(), (cw * h) / w, h * 1.25))}px`;
  }

  center(box, k) {
    const r = this.canvas.getBoundingClientRect();
    const w = r.width / k;
    const h = r.height / k;
    return { x: box.x + box.w / 2 - w / 2, y: box.y + box.h / 2 - h / 2, w, h };
  }

  home() {
    const r = this.canvas.getBoundingClientRect();
    if (!r.width || !r.height || this.o.compact) return this.fitBox();
    const p = this.st.story && this.path(this.st.story);
    const m = 32;
    const sb = p ? this.storyBox(p) : null;
    const E = sb ? this.union([this.G.ext, sb]) : this.G.ext;
    const box = { x: E.x - m, y: E.y - m, w: E.w + m * 2, h: E.h + m * 2 };
    const fit = Math.min(1.25, r.width / box.w, r.height / box.h);
    if (fit >= READ) return this.center(box, fit);
    const tall = this.st.layout === "tall";
    const t = this.G.thesis;
    const core = grow(this.union([...Object.values(this.G.box), ...this.G.frames.map((f) => f.rect), ...(t.lines.length ? [{ x: t.x, y: t.y, w: Math.max(...t.lines.map((ln) => measure(ln, this.ty.thesis))), h: t.lines.length * this.ty.thesis.lh }] : [])]), m);
    const span = r.width / box.w >= READ ? box : core;
    const k = tall ? Math.min(r.width / span.w, 1.25) : READ;
    const w = r.width / k;
    const h = r.height / k;
    if (sb && (sb.w + m * 2 > w || sb.h + m * 2 > h)) return this.fitAspect(grow(sb, m));
    const at = (v, lo, hi) => (hi < lo ? (lo + hi) / 2 : clamp(v, lo, hi));
    return {
      x: tall && !sb ? span.x + span.w / 2 - w / 2 : at(sb ? sb.x + sb.w / 2 - w / 2 : box.x, box.x, box.x + box.w - w),
      y: at(sb ? sb.y + sb.h / 2 - h / 2 : box.y, box.y, box.y + box.h - h),
      w,
      h,
    };
  }

  union(rs) {
    const x0 = Math.min(...rs.map((q) => q.x));
    const y0 = Math.min(...rs.map((q) => q.y));
    return { x: x0, y: y0, w: Math.max(...rs.map((q) => q.x + q.w)) - x0, h: Math.max(...rs.map((q) => q.y + q.h)) - y0 };
  }

  storyBox(p) {
    const rs = [...new Set(p.steps.flatMap((s) => [s.from, s.to]))].map((id) => this.G.box[id]).filter(Boolean);
    const b = this.layers.path.getBBox();
    if (b.width) rs.push({ x: b.x, y: b.y, w: b.width, h: b.height });
    return this.union(rs);
  }

  fitBox() {
    const r = this.canvas.getBoundingClientRect();
    const m = this.o.compact ? 40 : 32;
    const E = this.G.ext;
    const box = { x: E.x - m, y: E.y - m, w: E.w + m * 2, h: E.h + m * 2 };
    if (!r.width || !r.height) return box;
    const fit = Math.min(r.width / box.w, r.height / box.h);
    if (!this.o.compact) return this.center(box, Math.min(fit, 1.25));
    const least = GLIMPSE_NAME / this.ty.name.size;
    if (fit >= least) return this.center(box, Math.min(fit, 1.25));
    const k = Math.min(1, Math.max(least, r.width / box.w, r.height / box.h));
    const w = r.width / k;
    const h = r.height / k;
    return { x: box.w > w ? box.x : box.x - (w - box.w) / 2, y: box.h > h ? box.y : box.y - (h - box.h) / 2, w, h };
  }

  fitAspect(b) {
    const r = this.canvas.getBoundingClientRect();
    if (!r.width || !r.height) return b;
    const a = r.width / r.height;
    let { x, y, w, h } = b;
    if (w / h > a) { const nh = w / a; y -= (nh - h) / 2; h = nh; } else { const nw = h * a; x -= (nw - w) / 2; w = nw; }
    return { x, y, w, h };
  }

  focusFit() {
    const b = this.inBox;
    return this.fitAspect({ x: b.x - b.w * 0.03, y: b.y - b.h * 0.03, w: b.w * 1.06, h: b.h * 1.06 });
  }

  focusBox() {
    const fit = this.focusFit();
    const r = this.canvas.getBoundingClientRect();
    if (!r.width) return fit;
    const s = (r.width / fit.w) * this.inK;
    if (s >= READ) return fit;
    const f = s / READ;
    const w = fit.w * f;
    const h = fit.h * f;
    const b = this.inBox;
    const top = b.y - b.h * 0.03;
    return { x: this.inAnchor.x, y: this.inAnchor.y - top < h * 0.75 ? top : clamp(this.inAnchor.y - h * 0.42, top, b.y + b.h * 1.03 - h), w, h };
  }

  setVB(vb) {
    this.st.vb = vb;
    this.svg.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
    if (!this.G) return;
    const cw = this.canvas.getBoundingClientRect().width;
    if (cw) this.dots.setAttribute("patternTransform", `scale(${Math.min(1, (READ * vb.w) / cw)})`);
    const c = this.st.shape && this.inBox ? this.inBox : this.G.ext;
    const sides = { fl: c.x < vb.x - 4, ft: c.y < vb.y - 4, fr: c.x + c.w > vb.x + vb.w + 4, fb: c.y + c.h > vb.y + vb.h + 4 };
    for (const [k, on] of Object.entries(sides)) this.svg.style.setProperty(`--${k}`, on ? (k === "fl" || k === "fr" ? "44px" : "32px") : "0px");
    this.root.classList.toggle("cut", Object.values(sides).some(Boolean));
  }

  animateTo(target, ms) {
    cancelAnimationFrame(this.anim);
    if (this.root.classList.contains("still") || matchMedia("(prefers-reduced-motion: reduce)").matches) { this.setVB(target); return; }
    const from = { ...this.st.vb };
    const t0 = performance.now();
    const dur = ms || 520;
    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      const e = ease(t);
      this.setVB({ x: from.x + (target.x - from.x) * e, y: from.y + (target.y - from.y) * e, w: from.w + (target.w - from.w) * e, h: from.h + (target.h - from.h) * e });
      if (t < 1) this.anim = requestAnimationFrame(step);
    };
    this.anim = requestAnimationFrame(step);
  }

  interactions() {
    const svg = this.svg;
    const pts = new Map();
    let start = null;
    const units = (px, py) => {
      const r = this.canvas.getBoundingClientRect();
      const vb = this.st.vb;
      return [vb.x + ((px - r.left) / r.width) * vb.w, vb.y + ((py - r.top) / r.height) * vb.h];
    };
    svg.addEventListener("pointerdown", (e) => {
      if (!this.st.vb || this.o.compact) return;
      pts.set(e.pointerId, [e.clientX, e.clientY]);
      this.dragMoved = false;
      start = { vb: { ...this.st.vb }, pts: new Map(pts) };
    });
    svg.addEventListener("pointermove", (e) => {
      if (!pts.has(e.pointerId) || !start) return;
      pts.set(e.pointerId, [e.clientX, e.clientY]);
      const r = this.canvas.getBoundingClientRect();
      const k = start.vb.w / r.width;
      if (pts.size === 1) {
        const [sx, sy] = start.pts.get(e.pointerId) || [e.clientX, e.clientY];
        const dx = e.clientX - sx;
        const dy = e.clientY - sy;
        if (Math.hypot(dx, dy) > 5) { this.dragMoved = true; svg.classList.add("dragging"); if (!svg.hasPointerCapture(e.pointerId)) svg.setPointerCapture(e.pointerId); }
        if (this.dragMoved) { cancelAnimationFrame(this.anim); this.setVB({ ...start.vb, x: start.vb.x - dx * k, y: start.vb.y - dy * k }); }
      } else if (pts.size === 2 && start.pts.size === 2) {
        const [a, b] = [...pts.values()];
        const [a0, b0] = [...start.pts.values()];
        const f = Math.hypot(a0[0] - b0[0], a0[1] - b0[1]) / Math.max(Math.hypot(a[0] - b[0], a[1] - b[1]), 1);
        const mx = (a0[0] + b0[0]) / 2;
        const my = (a0[1] + b0[1]) / 2;
        const ux = start.vb.x + (mx - r.left) * k;
        const uy = start.vb.y + (my - r.top) * k;
        this.dragMoved = true;
        this.setVB({ x: ux - (ux - start.vb.x) * f, y: uy - (uy - start.vb.y) * f, w: start.vb.w * f, h: start.vb.h * f });
      }
    });
    const end = (e) => {
      pts.delete(e.pointerId);
      svg.classList.remove("dragging");
      start = pts.size ? { vb: { ...this.st.vb }, pts: new Map(pts) } : null;
      setTimeout(() => { if (!pts.size) this.dragMoved = false; }, 0);
    };
    svg.addEventListener("pointerup", end);
    svg.addEventListener("pointercancel", end);
    svg.addEventListener("wheel", (e) => {
      if (!this.st.vb || this.o.compact || !(this.o.dialog || e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      cancelAnimationFrame(this.anim);
      const f = Math.exp(e.deltaY * (e.ctrlKey ? 0.01 : 0.0016));
      const [ux, uy] = units(e.clientX, e.clientY);
      const vb = this.st.vb;
      this.setVB({ x: ux - (ux - vb.x) * f, y: uy - (uy - vb.y) * f, w: vb.w * f, h: vb.h * f });
      this.zoomed(ux, uy);
    }, { passive: false });
    svg.addEventListener("click", () => {
      if (this.dragMoved) return;
      if (this.o.compact) this.o.onOpen(this.want || {});
      else if (this.st.shape) this.unfocus();
      else if (this.st.sel) { this.st.sel = null; this.applyFocus(); this.renderReadout(); this.emit(); }
    });
    const zoomBy = (f) => {
      const vb = this.st.vb;
      const cx = vb.x + vb.w / 2;
      const cy = vb.y + vb.h / 2;
      this.animateTo({ x: cx - (vb.w * f) / 2, y: cy - (vb.h * f) / 2, w: vb.w * f, h: vb.h * f }, 260);
    };
    this.root.querySelector(".smap-tools").addEventListener("click", (e) => {
      const z = e.target.closest("button") && e.target.closest("button").dataset.z;
      if (z === "open") { this.o.onOpen(this.view()); return; }
      if (z === "close") { this.o.onClose(); return; }
      if (!z || !this.st.vb) return;
      if (z === "fit") this.animateTo(this.st.shape && this.inBox ? this.focusFit() : this.fitBox());
      else zoomBy(z === "in" ? 0.7 : 1 / 0.7);
    });
    if (!this.o.dialog) this.root.addEventListener("keydown", (e) => { if (e.key === "Escape" && this.escape()) { e.preventDefault(); e.stopPropagation(); } });
  }

  zoomed(ux, uy) {
    if (!this.G) return;
    const ratio = this.fitBox().w / this.st.vb.w;
    if (!this.st.shape && ratio >= 2.2) {
      const hit = Object.entries(this.G.box).find(([id, r]) => this.openable(id) && ux >= r.x && ux <= r.x + r.w && uy >= r.y && uy <= r.y + r.h);
      if (hit) { this.focus(hit[0], null); this.emit(); }
    } else if (this.st.shape && this.inBox && this.st.vb.w > this.focusBox().w * 2.4) {
      this.unfocus();
    }
  }

  emit() {
    if (this.o.onState) this.o.onState(this.view());
  }

  clearView() {
    this.st.story = null;
    this.st.shape = null;
    this.st.part = null;
    this.st.sel = null;
    this.st.step = null;
  }

  pickShape(sid) {
    if (this.o.compact) { this.o.onOpen({ block: sid }); return; }
    if (!this.openable(sid)) {
      this.st.sel = this.st.sel === sid ? null : sid;
      this.applyFocus();
      this.renderReadout();
      this.emit();
      return;
    }
    this.focus(sid, null);
    this.emit();
  }

  pickPart(pid) {
    this.focus(this.blockOf[pid], pid);
    this.emit();
  }

  goTo(o) {
    if (this.openable(o)) { this.focus(o, null); this.emit(); return; }
    this.unfocus();
    this.st.sel = o;
    this.applyFocus();
    this.renderReadout();
    this.emit();
  }

  pickStep(n, story) {
    if (this.o.compact) { this.o.onOpen({ story: story || undefined }); return; }
    this.st.step = n;
    this.hot(n);
    this.renderReadout();
  }

  focus(sid, pid) {
    const was = this.st.shape;
    this.st.shape = sid;
    this.st.part = pid;
    this.st.sel = null;
    if (was !== sid && this.layers) this.drawInside();
    this.applyFocus();
    this.renderCrumbs();
    this.renderReadout();
    if (was !== sid && this.G && this.inBox) this.animateTo(this.focusBox());
    else if (this.G) this.setVB(this.st.vb);
  }

  unfocus() {
    const had = this.st.shape;
    this.st.shape = null;
    this.st.part = null;
    this.applyFocus();
    this.renderCrumbs();
    this.renderReadout();
    if (this.G) this.animateTo(this.home());
    if (had) this.emit();
  }

  escape() {
    if (!this.B || this.o.compact) return false;
    if (this.st.part) { this.focus(this.st.shape, null); this.emit(); return true; }
    if (this.st.shape) { this.unfocus(); return true; }
    if (this.st.sel) { this.st.sel = null; this.applyFocus(); this.renderReadout(); this.emit(); return true; }
    if (this.st.story) { this.setStory(null); return true; }
    return false;
  }

  show(q) {
    if (!this.B || this.o.compact) return;
    const story = q.story && this.path(q.story) ? q.story : null;
    if (story !== this.st.story) this.setStory(story, true);
    const pid = q.part && this.items[q.part] && this.openable(this.blockOf[q.part]) ? q.part : null;
    const sid = pid ? this.blockOf[pid] : q.block && this.openable(q.block) ? q.block : null;
    if (sid && (sid !== this.st.shape || pid !== this.st.part)) this.focus(sid, pid);
    else if (!sid && this.st.shape) this.unfocus();
    const sel = !sid && q.block && this.K[q.block] ? q.block : null;
    if (sel !== this.st.sel) { this.st.sel = sel; this.applyFocus(); this.renderReadout(); }
  }

  setStory(id, quiet) {
    this.st.story = id;
    this.st.step = null;
    this.renderChips();
    if (this.layers) {
      this.drawPath();
      if (this.st.shape) this.drawInside();
    }
    this.applyFocus();
    this.renderReadout();
    if (this.layers && !this.st.shape) this.animateTo(this.home());
    if (!quiet) this.emit();
  }

  renderChips() {
    const nav = this.root.querySelector(".smap-chips");
    const row = this.root.querySelector(".smap-glimpse");
    const stories = this.B.paths.map((p) => ({ id: p.id, label: p.label, tone: this.pathTone(p), title: p.title }));
    const chip = (s, pressed) => `<button type="button" class="smap-chip${s.tone ? ` ${s.tone}` : ""}" data-s="${esc(s.id || "")}"${pressed == null ? "" : ` aria-pressed="${pressed}"`}${s.title ? ` title="${esc(s.title)}"` : ""}>${esc(s.label)}</button>`;
    nav.hidden = !stories.length;
    nav.innerHTML = [{ id: null, label: "Whole system" }, ...stories].map((s) => chip(s, String((s.id || null) === this.st.story))).join("");
    row.innerHTML = `<button type="button" class="smap-open">Open the map</button>${stories.map((s) => chip(s)).join("")}`;
    nav.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      if (this.st.shape) { this.st.shape = null; this.st.part = null; this.applyFocus(); this.renderCrumbs(); }
      this.setStory(b.dataset.s || null);
    }));
    row.querySelector(".smap-open").addEventListener("click", () => this.o.onOpen(this.want || {}));
    row.querySelectorAll(".smap-chip").forEach((b) => b.addEventListener("click", () => this.o.onOpen({ story: b.dataset.s })));
  }

  renderCrumbs() {
    const nav = this.root.querySelector(".smap-crumbs");
    const lv = this.o.nav;
    const trail = lv ? lv.trail() : [];
    this.root.classList.toggle("leveled", trail.length > 0);
    if (!this.st.shape && !trail.length) { nav.replaceChildren(); return; }
    const here = esc(lv ? lv.here() : "Whole system");
    const bits = trail.map((t, i) => `<button type="button" data-up="${i}">${esc(t)}</button>`);
    if (!this.st.shape) bits.push(`<b>${here}</b>`);
    else {
      const name = esc(this.K[this.st.shape].name);
      bits.push(`<button type="button" data-c="all">${here}</button>`, this.st.part ? `<button type="button" data-c="shape">${name}</button>` : `<b>${name}</b>`);
      if (this.st.part) bits.push(`<b>${esc(this.labelOf(this.st.part))}</b>`);
    }
    nav.innerHTML = bits.join(" › ");
    nav.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.up) lv.up(Number(b.dataset.up));
      else if (b.dataset.c === "all") this.unfocus();
      else { this.focus(this.st.shape, null); this.emit(); }
    }));
  }

  renderReadout() {
    const p = this.st.story && this.path(this.st.story);
    const steps = (list) => `<ol>${list.map((s, i) => `<li class="${s.risk ? "risk" : s.stop ? "stop" : ""}"><span class="n">${i + 1}</span>${esc(s.say)}${s.waitsFor ? ` (waits for step ${s.waitsFor})` : ""}</li>`).join("")}</ol>`;
    const id = this.st.part || this.st.shape || this.st.sel;
    let html;
    if (id) {
      const part = this.st.part ? this.shapeOf(id) : null;
      const name = this.st.part ? this.labelOf(id) : this.K[id].name;
      const role = this.st.part ? (part ? part.role : "") : this.K[id].role;
      const about = this.st.part ? this.items[id].what : this.K[id].what;
      html = `<b>${esc(name)}</b>${role ? ` · ${esc(role)}` : ""}${about ? `. ${esc(about)}` : ""}`;
      const deeper = this.o.nav && this.o.nav.deeper(id);
      if (deeper) html += ` <button type="button" class="smap-deeper" data-down="${esc(id)}">${esc(deeper)} ›</button>`;
    } else if (p) {
      html = `<b>${esc(p.title)}</b> ${steps(p.steps)}`;
    } else {
      const open = this.B.blocks.some((b) => b.open);
      html = `<span class="smap-hint"><span class="click">${open ? "Click a box to look inside" : "Click a box to read about it"}</span><span class="pan">Drag to see the rest</span></span>`;
    }
    const sp = this.B.spine.steps;
    this.readout.innerHTML = `${html}${sp.length ? `<div class="smap-pathline"><b>${esc(this.B.spine.title)}</b> ${steps(sp)}</div>` : ""}`;
    const down = this.readout.querySelector(".smap-deeper");
    if (down) down.addEventListener("click", () => this.o.nav.down(down.dataset.down));
  }

  async reset() {
    this.clearView();
    this.want = null;
    if (this.layers) { this.drawPath(); this.drawInside(); this.applyFocus(); }
    this.refresh();
    await frames(2);
  }

  clashes(inside) {
    const svg = this.svg;
    const vb = svg.viewBox.baseVal;
    const scale = svg.getBoundingClientRect().width / vb.width;
    const S = inside ? ".inside-art" : ".overview";
    const all = (sel) => [...svg.querySelectorAll(sel.split(",").map((s) => `${S} ${s.trim()}`).join(", "))];
    const rect = (el, d) => { const b = el.getBoundingClientRect(); return { x: b.x + d, y: b.y + d, w: b.width - 2 * d, h: b.height - 2 * d }; };
    const hit = (a, b) => a.w > 0 && a.h > 0 && b.w > 0 && b.h > 0 && a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    const px = (t) => Number(t.getAttribute("font-size")) * t.getScreenCTM().a;
    const smallest = (sel) => Math.min(99, ...all(sel).filter((t) => t.textContent.trim()).map(px));
    const solid = inside
      ? [
          ...all(".part > rect.paper, .part > rect.paper2").map((e) => [`part "${this.labelOf(e.parentNode.dataset.part)}"`, rect(e, 1)]),
          ...all(".ghostcard > rect.paper, .sibcard > rect.paper").map((e) => [e.parentNode.getAttribute("aria-label"), rect(e, 1)]),
          ...all(".in-title text, .in-tag").map((e) => ["the title", rect(e, 1)]),
        ]
      : [
          ...all(".block").map((g) => { const r = g.querySelector("rect.hit").getBoundingClientRect(); const k = 12 * scale; return [`block "${this.nameOf(g.dataset.block)}"`, { x: r.x + k, y: r.y + k, w: r.width - 2 * k, h: r.height - 2 * k }]; }),
          ...all(".thesis text, .lanes text").map((e) => [`the title "${e.textContent.slice(0, 24)}"`, rect(e, 1)]),
        ];
    for (const e of all(".bd, .bub")) solid.push([e.classList.contains("bd") ? "a badge" : "a question badge", rect(e, 1)]);
    const roots = inside ? ".elab, .note" : ".lab:not(.gone), .spine:not(.gone) > .step, .spine:not(.gone) .ack-lab, .pathlayer > .step, .note, .frame";
    const labels = [];
    for (const n of all(".note")) labels.push({ el: n, r: rect(n, 4), say: n.textContent.slice(0, 32) });
    for (const t of all(inside ? ".elab text" : ".lab:not(.gone) text, .spine:not(.gone) > .step text, .spine:not(.gone) .ack-lab text, .pathlayer > .step text, .frame text")) {
      if (!t.textContent.trim()) continue;
      labels.push({ el: t.closest(roots.split(",").map((s) => `${S} ${s.trim()}`).join(", ")), r: rect(t, 1), say: t.textContent.slice(0, 32) });
    }
    const clash = [];
    labels.forEach((l, i) => {
      for (const [what, r] of solid) if (hit(l.r, r)) clash.push(`label "${l.say}" overlaps ${what}`);
      for (let j = i + 1; j < labels.length; j++) if (labels[j].el !== l.el && hit(l.r, labels[j].r)) clash.push(`labels "${l.say}" and "${labels[j].say}" overlap`);
    });
    const font = inside
      ? { note: smallest(".note text"), part: smallest(".part > text"), edge: smallest(".elab text:not(.t-paper)") }
      : { note: smallest(".note text"), step: smallest(".spine > .step text:not(.t-paper), .lab:not(.gone) .step text:not(.t-paper)"), block: smallest(".block > text") };
    return { clash: [...new Set(clash)], font, scale };
  }

  covers() {
    const svg = this.svg;
    const all = [...svg.querySelectorAll("*")];
    const order = new Map(all.map((e, i) => [e, i]));
    const shown = (e) => { for (let n = e; n && n !== svg; n = n.parentNode) { const cs = getComputedStyle(n); if (cs.display === "none" || cs.visibility === "hidden" || Number(cs.opacity) < 0.35) return false; } return true; };
    const alpha = (c) => { if (!c.startsWith("rgb")) return 0; const p = c.slice(c.indexOf("(") + 1, c.indexOf(")")).split(",").map(Number); return p.length === 4 ? p[3] : 1; };
    const box = (e) => { const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; };
    const meet = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
    const view = box(svg);
    const unit = (t) => t.closest("g.note, g.bd, g.bub, g.part, g.block, g.ghostcard, g.sibcard, g.frame, g.lab, g.ack, g.step, g.elab, g.in-title, g.thesis, g.lanes") || t.parentNode;
    const texts = all.filter((e) => e.tagName === "text" && e.textContent.trim() && shown(e)).map((t) => ({ t, r: box(t), i: order.get(t), u: unit(t) })).filter((x) => x.r.w > 1 && meet(x.r, view) > 0);
    const marks = all.filter((e) => ["rect", "path", "circle", "ellipse", "polygon", "line", "polyline"].includes(e.tagName) && shown(e)).map((e) => {
      const cs = getComputedStyle(e);
      return { e, r: box(e), i: order.get(e), fill: cs.fill !== "none" && !cs.fill.startsWith("url(") && alpha(cs.fill) * Number(cs.fillOpacity) >= 0.6, stroke: cs.stroke !== "none" && alpha(cs.stroke) * Number(cs.strokeOpacity) >= 0.3 && parseFloat(cs.strokeWidth) > 0.4 };
    }).filter((m) => m.fill || m.stroke);
    const out = [];
    for (const T of texts) for (const M of marks) {
      if (M.i <= T.i || T.u.contains(M.e)) continue;
      const a = meet(T.r, M.r);
      if (!a) continue;
      const say = T.t.textContent.slice(0, 32);
      if (M.fill) { if (a > 6 && a / (T.r.w * T.r.h) > 0.04) out.push(`a shape is drawn over "${say}"`); continue; }
      const inv = M.e.getScreenCTM().inverse();
      let hits = 0;
      for (let y = T.r.y + 2; y < T.r.y + T.r.h - 1; y += 2.5) for (let x = T.r.x + 1; x < T.r.x + T.r.w - 1; x += 2.5) if (M.e.isPointInStroke(new DOMPoint(x, y).matrixTransform(inv))) hits++;
      if (hits >= 3) out.push(`a line is drawn through "${say}"`);
    }
    const notes = [...svg.querySelectorAll(".note")].filter(shown).map((n) => [n.textContent.slice(0, 32), box(n)]);
    for (const p of svg.querySelectorAll(".link path, .step path, .edge path")) {
      if (!shown(p) || getComputedStyle(p).fill !== "none") continue;
      const L = p.getTotalLength();
      const m = p.getScreenCTM();
      for (const [say, n] of notes) {
        let k = 0;
        for (let s = 0; s <= L; s += 4) { const q = p.getPointAtLength(s).matrixTransform(m); if (q.x > n.x + 2 && q.x < n.x + n.w - 2 && q.y > n.y + 2 && q.y < n.y + n.h - 2) k++; }
        if (k >= 3) out.push(`an arrow runs under the sticky note "${say}"`);
      }
    }
    return [...new Set(out)];
  }

  brokenNames() {
    return [...this.svg.querySelectorAll(".overview .block")]
      .map((g) => [g.dataset.block, [...g.querySelector(":scope > text").querySelectorAll("tspan")].map((t) => t.textContent).join(" ")])
      .filter(([id, s]) => s !== oneLine(this.K[id].name))
      .map(([id, s]) => `block "${this.K[id].name}" is drawn as "${s}", a word broken across lines`);
  }

  doubled() {
    const lines = [...this.svg.querySelectorAll(".overview .loose")].filter((g) => g.getClientRects().length && !g.closest(".gone")).map((g) => {
      const p = g.querySelector("path");
      const L = p.getTotalLength();
      const pts = [];
      for (let s = 0; s <= L; s += 3) pts.push(p.getPointAtLength(s));
      return { n: Number(g.closest(".step").dataset.step) + 1, pts };
    });
    const out = [];
    lines.forEach((a, i) => {
      for (const b of lines.slice(i + 1)) {
        const near = a.pts.filter((q) => b.pts.some((r) => Math.hypot(q.x - r.x, q.y - r.y) < 5)).length;
        if (near > a.pts.length * 0.3) out.push(`steps ${Math.min(a.n, b.n)} and ${Math.max(a.n, b.n)} draw on one line`);
      }
    });
    return out;
  }

  farWaits() {
    return [...this.svg.querySelectorAll(".overview .ack")].filter((g) => g.getClientRects().length && !g.closest(".gone") && g.querySelector(".ack-lab")).flatMap((g) => {
      const bar = g.querySelector("rect.paper").getBBox();
      const lab = g.querySelector(".ack-lab").getBBox();
      const gap = Math.hypot(Math.max(0, bar.x - lab.x - lab.width, lab.x - bar.x - bar.width), Math.max(0, bar.y - lab.y - lab.height, lab.y - bar.y - bar.height));
      return gap > 16 ? [`the caption "${g.querySelector(".ack-lab").textContent}" sits ${Math.round(gap)} px from its barrier`] : [];
    });
  }

  cutLeads() {
    const shown = (e) => e.getClientRects().length && !e.closest(".gone");
    const texts = [...this.svg.querySelectorAll(".overview text")].filter((t) => t.textContent.trim() && shown(t)).map((t) => [t, grow(this.boxOf(t), -1)]);
    const blocks = [...this.svg.querySelectorAll(".overview .block")].map((g) => [g, grow(this.boxOf(g.querySelector("rect.hit")), -10)]);
    const out = [];
    for (const lead of this.svg.querySelectorAll(".overview .lead")) {
      if (!shown(lead)) continue;
      const own = lead.parentNode;
      const p = lead.querySelector("path");
      const L = p.getTotalLength();
      const pts = [];
      for (let d = 0; d <= L; d += 2) pts.push(p.getPointAtLength(d));
      const say = own.textContent.trim().slice(0, 32);
      const through = (r) => pts.filter((q) => q.x > r.x && q.x < r.x + r.w && q.y > r.y && q.y < r.y + r.h).length >= 2;
      for (const [g, r] of blocks) if (through(r)) out.push(`the leader to "${say}" runs through block "${this.nameOf(g.dataset.block)}"`);
      for (const [t, r] of texts) if (!own.contains(t) && !t.closest(".block") && through(r)) out.push(`the leader to "${say}" runs through "${t.textContent.trim().slice(0, 32)}"`);
    }
    return [...new Set(out)];
  }

  orphans() {
    const art = this.svg.querySelector(".inside-art");
    const ends = new Set([...art.querySelectorAll(".edge")].flatMap((e) => [e.dataset.a, e.dataset.b]));
    return [...art.querySelectorAll(".part:not(.contents)")].map((p) => p.dataset.part).filter((id) => !ends.has(id));
  }

  offScreen() {
    const s = this.canvas.getBoundingClientRect();
    const tall = this.st.layout === "tall";
    const out = [];
    const off = (b) => b.left < s.left - 2 || b.right > s.right + 2 || (!tall && (b.top < s.top - 2 || b.bottom > s.bottom + 2));
    for (const g of this.svg.querySelectorAll(".overview .block")) if (off(g.getBoundingClientRect())) out.push(`block "${this.nameOf(g.dataset.block)}" is off screen when the map opens`);
    for (const g of this.svg.querySelectorAll(".overview > g > .frame")) if (off(g.getBoundingClientRect())) out.push(`group "${this.F[g.dataset.frame].label}" is off screen when the map opens`);
    const title = this.svg.querySelector(".overview .thesis");
    if (title && off(title.getBoundingClientRect())) out.push(`the title "${this.B.thesis.slice(0, 32)}" is cut off when the map opens`);
    if (this.st.story && off(this.layers.path.getBoundingClientRect())) out.push(`story "${this.st.story}" is not fully in view when the map opens`);
    return [...new Set(out)];
  }

  check(view, o) {
    const out = [];
    const add = (rule, text) => out.push({ view, rule, text });
    const inside = !!this.st.shape;
    const m = this.clashes(inside);
    for (const t of m.clash) add("overlap", t);
    for (const t of this.covers()) add("cover", t);
    if (o.home) {
      const L = this.skin.least;
      const want = inside ? { note: L.note, part: L.part, edge: L.edge } : this.o.compact ? { block: L.glimpse } : { note: L.note, step: L.step, block: L.block };
      for (const [k, px] of Object.entries(want)) if (m.font[k] < px) add("font", `${k} text renders at ${m.font[k].toFixed(1)} px, want at least ${px}`);
    }
    if (inside) for (const id of this.orphans()) add("orphan", `part "${this.labelOf(id)}" has no arrow to anything inside the view`);
    else {
      for (const t of this.brokenNames()) add("name", t);
      for (const t of this.doubled()) add("overlap", t);
      for (const t of this.cutLeads()) add("cover", t);
      for (const t of this.farWaits()) add("caption", t);
    }
    if (o.fit && !inside) for (const t of this.offScreen()) add("fit", t);
    return out;
  }

  async walk(prefix) {
    const out = [];
    const at = async (view, fn, o) => { fn(); await frames(2); out.push(...this.check(`${prefix}${view}`, o)); };
    await at("fit", () => this.setVB(this.st.shape && this.inBox ? this.focusFit() : this.fitBox()), {});
    for (const p of this.B.paths) await at(`story ${p.id}`, () => this.show({ story: p.id }), {});
    for (const b of this.B.blocks.filter((x) => x.open)) {
      await at(`block ${b.id}`, () => this.show({ block: b.id }), { home: true });
      await at(`block ${b.id} fit`, () => this.setVB(this.focusFit()), {});
    }
    this.show({});
    await frames(2);
    return out;
  }

  destroy() {
    this.gone = true;
    live.delete(this);
    this.ro.disconnect();
    clearTimeout(this.rz);
    cancelAnimationFrame(this.anim);
    this.root.remove();
  }
}

export function mount(host, map, opts) {
  injectCss();
  if (opts.font !== false && opts.skin !== "clean") injectFont();
  let inline = null;
  let dlg = null;
  let modal = null;
  let model = null;
  let key = null;
  let problems = [];
  let settle;
  const ready = new Promise((r) => { settle = r; });
  const onState = (v) => { if (opts.onState) opts.onState(v); };
  const close = () => { if (modal && modal.open) modal.close(); };
  const open = async (view) => {
    if (!modal) {
      modal = document.createElement("dialog");
      modal.className = "smap-dialog";
      modal.addEventListener("cancel", (e) => { e.preventDefault(); if (!dlg.escape()) close(); });
      host.append(modal);
    }
    modal.setAttribute("aria-label", model.thesis);
    if (!modal.open) modal.showModal();
    if (!dlg) dlg = new Board(modal, model, { rough: opts.rough, skin: opts.skin, nav: opts.nav, dialog: true, state: view, onState: (v) => { if (inline.mode) inline.want = v; onState(v); }, onOpen: () => {}, onClose: close });
    else { dlg.refresh(); dlg.show(view); }
    await dlg.ready;
    return dlg;
  };
  const set = (next, view) => {
    const k = JSON.stringify(next);
    if (k === key) { if (view) handle.show(view); return; }
    key = k;
    problems = validate(next, { derived: opts.derived });
    if (problems.length) {
      if (inline) inline.destroy();
      if (dlg) dlg.destroy();
      if (modal) modal.remove();
      inline = null;
      dlg = null;
      modal = null;
      host.innerHTML = `<div class="smap"><div class="smap-problems" role="alert"><b>This system map has ${problems.length === 1 ? "a problem" : `${problems.length} problems`} to fix</b><ul>${problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div></div>`;
      settle();
      return;
    }
    model = normalize(next);
    if (!inline) {
      host.replaceChildren();
      inline = new Board(host, model, { rough: opts.rough, skin: opts.skin, nav: opts.nav, fill: opts.fill, dialog: false, state: opts.state && Object.values(opts.state).some(Boolean) ? opts.state : null, onState, onOpen: (v) => { open(v); }, onClose: close });
      inline.ready.then(settle);
    } else inline.setModel(model, view);
    if (dlg) dlg.setModel(model, view);
  };
  const audit = async (o = {}) => {
    await ready;
    if (problems.length) return problems.map((text) => ({ view: "home", rule: "valid", text }));
    const b = inline;
    b.root.classList.add("still");
    await frames(2);
    const out = b.check("home", { home: true, fit: true });
    if (o.all) {
      if (!b.o.compact) out.push(...(await b.walk("")));
      else {
        const d = await open({});
        d.root.classList.add("still");
        await d.reset();
        out.push(...d.check("dialog home", { home: true, fit: d.st.layout === "tall" }), ...(await d.walk("dialog ")));
        d.root.classList.remove("still");
        close();
      }
      await b.reset();
      printFlag = true;
      b.refresh();
      await frames(2);
      if (b.o.compact) out.push({ view: "print", rule: "print", text: "the map prints as its names-only glimpse, without its step labels" });
      else out.push(...b.check("print", {}));
      printFlag = false;
      b.refresh();
      await frames(2);
    }
    b.root.classList.remove("still");
    return out;
  };
  const handle = {
    get problems() { return problems; },
    ready,
    update: set,
    show(view) {
      if (modal && modal.open) dlg.show(view);
      else if (inline) inline.show(view);
    },
    async reset() {
      close();
      if (inline) await inline.reset();
    },
    audit,
    destroy() {
      if (inline) inline.destroy();
      if (dlg) dlg.destroy();
      if (modal) modal.remove();
      host.replaceChildren();
      delete host.smap;
      delete host.dataset.smap;
    },
  };
  host.dataset.smap = "";
  host.smap = handle;
  set(map);
  return handle;
}
