const os = window.CcPresent;
if (!os)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const As = os.jsxRuntime.jsx;
os.jsxRuntime.jsxs;
os.jsxRuntime.Fragment;
const Ls = window.CcPresent;
if (!Ls)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const js = Ls.React, { createElement: ye, Fragment: xe, useCallback: ve, useEffect: Cs, useMemo: $e, useRef: Ps, useState: Me } = js, ps = "http://www.w3.org/2000/svg", Fs = "https://fonts.googleapis.com/css2?family=Shantell+Sans:wght@400;500;600;700&display=swap", Gs = String.raw`
  --smap-d-paper: #fbfaf5; --smap-d-ink: #1f2633; --smap-d-line: #e3e0d4;
  --wb-marker: #2453d4; --wb-marker-soft: rgba(36, 83, 212, .12);
  --wb-red: #c9302a; --wb-red-soft: rgba(201, 48, 42, .1);
  --wb-green: #1d8a4b; --wb-green-soft: rgba(29, 138, 75, .12);
  --wb-amber: #a86f12; --wb-amber-soft: rgba(214, 150, 20, .14); --wb-hl: #ffd84d;
  --wb-tint-blue: rgba(36, 83, 212, .18); --wb-tint-green: rgba(29, 138, 75, .2); --wb-tint-amber: rgba(214, 150, 20, .24);
  --wb-tint-red: rgba(201, 48, 42, .16); --wb-tint-grey: rgba(31, 38, 51, .14);
  --wb-sticky: #fff3a8; --wb-sticky-ink: #3b3417;
  --wb-shadow: 0 10px 30px rgba(31, 38, 51, .12), 0 2px 6px rgba(31, 38, 51, .08);`, us = String.raw`
  --smap-d-paper: #1a1d21; --smap-d-ink: #ece8dc; --smap-d-line: #353a42;
  --wb-marker: #86a8ff; --wb-marker-soft: rgba(134, 168, 255, .14);
  --wb-red: #ff7c6e; --wb-red-soft: rgba(255, 124, 110, .12);
  --wb-green: #63d293; --wb-green-soft: rgba(99, 210, 147, .13);
  --wb-amber: #f0b44c; --wb-amber-soft: rgba(240, 180, 76, .14); --wb-hl: #8a7215;
  --wb-tint-blue: rgba(134, 168, 255, .24); --wb-tint-green: rgba(99, 210, 147, .22); --wb-tint-amber: rgba(240, 180, 76, .24);
  --wb-tint-red: rgba(255, 124, 110, .2); --wb-tint-grey: rgba(236, 232, 220, .13);
  --wb-sticky: #4a4220; --wb-sticky-ink: #f6edc4;
  --wb-shadow: 0 10px 30px rgba(0, 0, 0, .45), 0 2px 6px rgba(0, 0, 0, .3);`, Es = '"Shantell Sans", ui-rounded, system-ui, sans-serif', zs = 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', Is = String.raw`
.smap {${Gs}
  --wb-paper: var(--smap-paper, var(--smap-d-paper)); --wb-ink: var(--smap-ink, var(--smap-d-ink)); --wb-line: var(--smap-line, var(--smap-d-line));
  --wb-paper-2: color-mix(in srgb, var(--wb-ink) 6%, var(--wb-paper)); --wb-dot: color-mix(in srgb, var(--wb-ink) 16%, var(--wb-paper));
  --wb-ink-2: color-mix(in srgb, var(--wb-ink) 74%, var(--wb-paper)); --wb-ink-3: color-mix(in srgb, var(--wb-ink) 55%, var(--wb-paper));
  --wb-card: color-mix(in srgb, var(--wb-ink) 3%, var(--wb-paper));
  --hand: ${Es}; --smap-sans: ${zs};
  display: grid; grid-template-rows: auto auto auto auto; background: var(--wb-paper); color: var(--wb-ink); font: 14px/1.45 var(--smap-sans); min-width: 0; border-radius: inherit; overflow: hidden;
}
@media screen and (prefers-color-scheme: dark) { :root:not([data-theme="light"]):not([data-print]) .smap {${us} } }
@media screen { :root[data-theme="dark"]:not([data-print]) .smap {${us} } }
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
`, Bs = {
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
  small: { size: 15, weight: 500, lh: 18, sans: !0 },
  badge: { size: 13, weight: 700, lh: 16, sans: !0 },
  tag: { size: 12, weight: 700, lh: 14, sans: !0 },
  lane: { size: 13, weight: 650, lh: 16, sans: !0 },
  disc: { size: 13, weight: 700, lh: 16, sans: !0 },
  title: { size: 28, weight: 700, lh: 32 }
}, Ns = { ...Bs, thesis: { size: 34, weight: 600, lh: 40 }, name: { size: 30, weight: 700, lh: 34 }, frame: { size: 17, weight: 600, lh: 21 }, badge: { size: 20, weight: 700, lh: 24, sans: !0 } }, Ts = {
  thesis: { size: 20, weight: 650, lh: 26, sans: !0 },
  name: { size: 16.5, weight: 650, lh: 21, sans: !0 },
  role: { size: 14, weight: 400, lh: 18.5, sans: !0 },
  step: { size: 15, weight: 600, lh: 19.5, sans: !0 },
  link: { size: 13.5, weight: 500, lh: 17, sans: !0 },
  frame: { size: 12.5, weight: 650, lh: 15, sans: !0 },
  note: { size: 14.5, weight: 500, lh: 19.5, sans: !0 },
  heap: { size: 13, weight: 500, lh: 16.5, sans: !0 },
  member: { size: 14.5, weight: 650, lh: 18.5, sans: !0 },
  mrole: { size: 13, weight: 400, lh: 16.5, sans: !0 },
  hold: { size: 13, weight: 500, lh: 16.5, sans: !0 },
  small: { size: 13, weight: 500, lh: 17, sans: !0 },
  badge: { size: 12, weight: 700, lh: 15, sans: !0 },
  tag: { size: 11, weight: 700, lh: 13, sans: !0 },
  lane: { size: 11.5, weight: 650, lh: 14, sans: !0 },
  disc: { size: 12, weight: 700, lh: 15, sans: !0 },
  title: { size: 24, weight: 700, lh: 28, sans: !0 }
}, Hs = { ...Ts, thesis: { size: 28, weight: 650, lh: 34, sans: !0 }, name: { size: 25, weight: 650, lh: 30, sans: !0 }, frame: { size: 14.5, weight: 650, lh: 18, sans: !0 }, badge: { size: 18, weight: 700, lh: 22, sans: !0 } }, ms = { pad: 14, minW: 176, maxW: 204, phone: 165, stack: 22, rowGap: 40, phoneRowGap: 70, gutter: [60, 190], phoneGutter: [40, 120], frame: 16, inset: 16, margin: 22, lane: 28, labelW: 116 }, Ks = {
  sketch: { ty: Bs, glimpse: Ns, ly: ms, least: { note: 16, step: 14.5, block: 14, part: 12.5, edge: 13, glimpse: 17.5 }, clean: !1 },
  clean: { ty: Ts, glimpse: Hs, ly: { ...ms, minW: 150, maxW: 184, phone: 150, rowGap: 64, gutter: [48, 160], phoneGutter: [34, 104], labelW: 104 }, least: { note: 12, step: 12.4, block: 11.5, part: 10.5, edge: 10.8, glimpse: 14 }, clean: !0 }
}, Ft = 0.84, gs = 18, Ds = 900, Qs = { main: 0, background: 1, control: 2 }, as = ["Main path", "Background", "Control"], Vs = {
  sync: { cls: "k-ink", fh: "fh-ink", sw: 2.2, dash: null, head: "fill" },
  async: { cls: "k-ink2", fh: "fh-ink2", sw: 1.8, dash: [9, 7], head: "open" },
  copy: { cls: "k-ink", fh: "fh-ink", sw: 2, dash: [14, 6], head: "double" },
  control: { cls: "k-ink3", fh: "fh-ink3", sw: 1.6, dash: [2, 6], head: "open" }
}, Zs = {
  zone: { cls: "k-amber", t: "t-amber", dash: [12, 9], sw: 1.6 },
  machine: { cls: "k-ink3", t: "t-ink3", dash: [6, 7], sw: 1.5 },
  process: { cls: "k-ink3", t: "t-ink3", dash: [2, 5], sw: 1.2 }
}, ks = { memory: "chip", disk: "cylinder", bucket: "bucket", keys: "key", pages: "pages" }, qs = {
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
  page: { t: 14, r: 26, b: 14, l: 14, g: 0 }
}, ws = /* @__PURE__ */ new Set(["service", "worker", "app"]), Yt = /* @__PURE__ */ new Set(["store", "log"]), Ys = { ok: "pass", fail: "fail", warn: "warn", info: "wait" }, ts = { ok: "✓", fail: "✗", warn: "!", info: "" }, Xs = { ok: "ok", fail: "fail", pending: "wait" }, ft = (x) => String(x ?? "").replace(/[&<>"']/g, (s) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[s]), pt = (x) => {
  let s = 2166136261;
  for (const e of String(x))
    s ^= e.charCodeAt(0), s = Math.imul(s, 16777619);
  return (s >>> 0) % 2147483646 + 1;
}, Xt = (x) => Object.fromEntries((x || []).map((s) => [s.id, s])), bt = (x, s, e) => Math.max(s, Math.min(e, x)), U = (x, s) => ({ x: x.x - s, y: x.y - s, w: x.w + s * 2, h: x.h + s * 2 }), ss = (x, s) => Math.max(0, Math.min(x.x + x.w, s.x + s.w) - Math.max(x.x, s.x)) * Math.max(0, Math.min(x.y + x.h, s.y + s.h) - Math.max(x.y, s.y)), Zt = (x) => new Promise((s) => {
  const e = (t) => t ? requestAnimationFrame(() => e(t - 1)) : s();
  e(x);
}), _s = (x) => String(x).split(/\s+/).filter(Boolean).join(" ");
let es = null;
const is = /* @__PURE__ */ new Map(), J = (x, s) => {
  const e = `${s.weight}|${s.size}|${s.sans ? 1 : 0}|${x}`;
  return is.has(e) || (es = es || document.createElement("canvas").getContext("2d"), es.font = `${s.weight} ${s.size}px ${s.sans ? zs : Es}`, is.set(e, es.measureText(x).width)), is.get(e);
}, Us = (x, s, e) => {
  if (J(x, e) <= s) return [x];
  const t = [];
  let i = "";
  for (const n of x.split(new RegExp("(?<=[/_.:-])")))
    for (const o of J(n, e) > s ? [...n] : [n])
      i && J(i + o, e) > s ? (t.push(i), i = o) : i += o;
  return i && t.push(i), t;
}, st = (x, s, e) => {
  const t = [];
  let i = "";
  for (const n of String(x).split(/\s+/).filter(Boolean).flatMap((o) => Us(o, s, e))) {
    const o = i ? `${i} ${n}` : n;
    !i || J(o, e) <= s ? i = o : (t.push(i), i = n);
  }
  return i && t.push(i), t;
}, Js = (x, s, e) => {
  let t = st(x, s, e), i = s;
  if (t.length > 2) {
    const a = st(x, s * 1.3, e);
    a.length < t.length && (t = a, i = s * 1.3);
  }
  if (t.length < 2) return t;
  let n = Math.min(i, Math.max(30, ...String(x).split(/\s+/).filter(Boolean).map((a) => J(a, e)))), o = i;
  for (; o - n > 2; ) {
    const a = (n + o) / 2;
    st(x, a, e).length <= t.length ? o = a : n = a;
  }
  return st(x, o, e);
};
let Rs = 0;
const fs = /* @__PURE__ */ new Set();
let _t = !1;
const te = () => _t || document.documentElement.hasAttribute("data-print") || matchMedia("print").matches;
let bs = !1;
const se = () => {
  if (bs) return;
  bs = !0;
  const x = () => {
    for (const s of fs) s.refresh();
  };
  new MutationObserver(x).observe(document.documentElement, { attributeFilter: ["data-print"] }), matchMedia("print").addEventListener("change", x), addEventListener("beforeprint", () => {
    _t = !0, x();
  }), addEventListener("afterprint", () => {
    _t = !1, x();
  });
};
let rs = null;
const ee = () => {
  const x = document.querySelector('link[href*="family=Shantell+Sans"]');
  return !x || x.sheet ? Promise.resolve() : new Promise((s) => {
    x.addEventListener("load", s, { once: !0 }), x.addEventListener("error", s, { once: !0 });
  });
}, ie = () => {
  if (!rs) {
    const x = ee().then(() => Promise.all([400, 500, 600, 700].map((s) => document.fonts.load(`${s} 20px "Shantell Sans"`))));
    rs = Promise.race([x, new Promise((s) => setTimeout(s, 3e3))]).catch(() => {
    }).then(() => {
      is.clear(), Rs += 1;
    });
  }
  return rs;
}, ne = () => {
  if (document.getElementById("smap-css")) return;
  const x = document.createElement("style");
  x.id = "smap-css", x.textContent = Is, document.head.append(x);
}, oe = () => {
  if (document.querySelector('link[href*="family=Shantell+Sans"]')) return;
  const x = document.createElement("link");
  x.rel = "stylesheet", x.href = Fs, document.head.append(x);
}, ae = () => {
  const x = (t, i) => {
    const n = document.createElementNS(ps, "g"), o = document.createElementNS(ps, "path");
    return o.setAttribute("d", t), o.setAttribute("fill", i.fill || "none"), o.setAttribute("stroke", i.stroke || "#000"), o.setAttribute("stroke-width", i.strokeWidth ?? 1), o.setAttribute("stroke-linecap", "round"), o.setAttribute("stroke-linejoin", "round"), i.strokeLineDash && o.setAttribute("stroke-dasharray", i.strokeLineDash.join(" ")), n.append(o), n;
  }, s = (t, i) => `M ${t.map(([n, o]) => `${n} ${o}`).join(" L ")}${i ? " Z" : ""}`, e = (t, i, n, o) => `M ${t - n / 2} ${i} A ${n / 2} ${o / 2} 0 1 0 ${t + n / 2} ${i} A ${n / 2} ${o / 2} 0 1 0 ${t - n / 2} ${i} Z`;
  return {
    line: (t, i, n, o, a) => x(`M ${t} ${i} L ${n} ${o}`, a),
    linearPath: (t, i) => x(s(t, !1), i),
    polygon: (t, i) => x(s(t, !0), i),
    path: (t, i) => x(t, i),
    circle: (t, i, n, o) => x(e(t, i, n, n), o),
    ellipse: (t, i, n, o, a) => x(e(t, i, n, o), a),
    rectangle: (t, i, n, o, a) => {
      const f = Math.max(0, Math.min(8, n / 4, o / 4));
      return x(`M ${t + f} ${i} H ${t + n - f} Q ${t + n} ${i} ${t + n} ${i + f} V ${i + o - f} Q ${t + n} ${i + o} ${t + n - f} ${i + o} H ${t + f} Q ${t} ${i + o} ${t} ${i + o - f} V ${i + f} Q ${t} ${i} ${t + f} ${i} Z`, a);
    }
  };
};
function ys(x, s, e, t) {
  const i = Object.entries(x), n = t || [], o = (m, u, d, l, r, k) => {
    const v = Math.min(m, d), $ = Math.max(m, d), E = Math.min(u, l), W = Math.max(u, l);
    return $ > r.x - k && v < r.x + r.w + k && W > r.y - k && E < r.y + r.h + k;
  }, a = (m, u, d) => {
    for (let l = 0; l < m.length - 1; l++) {
      const [r, k] = m[l], [v, $] = m[l + 1];
      for (const [E, W] of i)
        if (!(E === u && l === 0 || E === d && l === m.length - 2) && o(r, k, v, $, W, 7))
          return !0;
    }
    return !1;
  }, f = (m) => m.slice(1).reduce((u, d, l) => u + Math.abs(d[0] - m[l][0]) + Math.abs(d[1] - m[l][1]), 0), g = (m, u) => {
    let d = 0;
    for (let l = 0; l < m.length - 1; l++) {
      const [r, k] = m[l], [v, $] = m[l + 1];
      for (const E of u)
        for (let W = 0; W < E.length - 1; W++) {
          const [j, M] = E[W], [O, b] = E[W + 1], L = k === $;
          if (L === (M === b)) {
            L && Math.abs(k - M) < 6 && Math.min(r, v) < Math.max(j, O) && Math.min(j, O) < Math.max(r, v) && (d += 2), !L && Math.abs(r - j) < 6 && Math.min(k, $) < Math.max(M, b) && Math.min(M, b) < Math.max(k, $) && (d += 2);
            continue;
          }
          const [P, Q, V] = L ? [r, v, k] : [j, O, M], [tt, ot, G] = L ? [j, M, b] : [r, k, $];
          tt > Math.min(P, Q) && tt < Math.max(P, Q) && V > Math.min(ot, G) && V < Math.max(ot, G) && (d += 1);
        }
    }
    return d;
  }, p = (m, u) => {
    const d = x[m], l = x[u], r = [], k = d.x + d.w / 2, v = d.y + d.h / 2, $ = l.x + l.w / 2, E = l.y + l.h / 2, W = Math.max(d.y, l.y), j = Math.min(d.y + d.h, l.y + l.h);
    if (j - W >= 22) {
      const G = (W + j) / 2;
      d.x + d.w <= l.x ? r.push({ pts: [[d.x + d.w, G], [l.x, G]], straight: !0 }) : l.x + l.w <= d.x && r.push({ pts: [[d.x, G], [l.x + l.w, G]], straight: !0 });
    }
    const M = Math.max(d.x, l.x), O = Math.min(d.x + d.w, l.x + l.w);
    if (O - M >= 22) {
      const G = (M + O) / 2;
      d.y + d.h <= l.y ? r.push({ pts: [[G, d.y + d.h], [G, l.y]], straight: !0 }) : l.y + l.h <= d.y && r.push({ pts: [[G, d.y], [G, l.y + l.h]], straight: !0 });
    }
    const b = bt(E, d.y + 14, d.y + d.h - 14), L = bt(v, l.y + 14, l.y + l.h - 14), T = bt($, d.x + 14, d.x + d.w - 14), P = bt(k, l.x + 14, l.x + l.w - 14), Q = (G, X) => X > G.x + G.w ? G.x + G.w : G.x, V = (G, X) => X > G.y + G.h ? G.y + G.h : G.y, tt = (G, X) => X >= G.x + G.w || X <= G.x, ot = (G, X) => X >= G.y + G.h || X <= G.y;
    ot(l, b) && tt(d, P) && r.push({ pts: [[Q(d, P), b], [P, b], [P, V(l, b)]] }), tt(l, T) && ot(d, L) && r.push({ pts: [[T, V(d, L)], [T, L], [Q(l, T), L]] });
    for (const G of s)
      !tt(d, G) || !tt(l, G) || (Math.abs(b - L) < 1 ? r.push({ pts: [[Q(d, G), b], [Q(l, G), L]], straight: !0, via: !0 }) : r.push({ pts: [[Q(d, G), b], [G, b], [G, L], [Q(l, G), L]], gut: ["v", G] }));
    for (const G of e)
      !ot(d, G) || !ot(l, G) || Math.abs(T - P) < 1 || r.push({ pts: [[T, V(d, G)], [T, G], [P, G], [P, V(l, G)]], gut: ["h", G] });
    return r;
  }, h = (m, u, d) => {
    let l = 0;
    for (let r = 0; r < m.length - 1; r++) {
      const [k, v] = m[r], [$, E] = m[r + 1];
      for (const [W, j] of i)
        W === u && r === 0 || W === d && r === m.length - 2 || o(k, v, $, E, j, 7) && (l += 1);
    }
    return l;
  }, c = (m, u) => {
    const d = x[m], l = x[u], r = d.y + d.h / 2, k = l.y + l.h / 2, v = d.x + d.w / 2, $ = l.x + l.w / 2, E = (b, L) => L >= b.x + b.w || L <= b.x, W = (b, L) => L >= b.y + b.h || L <= b.y, j = (b, L) => L > b.x + b.w ? b.x + b.w : b.x, M = (b, L) => L > b.y + b.h ? b.y + b.h : b.y, O = [];
    for (const b of e) {
      for (const L of s)
        if (E(d, L)) {
          for (const T of s)
            E(l, T) && O.push({ pts: [[j(d, L), r], [L, r], [L, b], [T, b], [T, k], [j(l, T), k]] });
          W(l, b) && O.push({ pts: [[j(d, L), r], [L, r], [L, b], [$, b], [$, M(l, b)]] });
        }
      if (W(d, b)) for (const L of s) E(l, L) && O.push({ pts: [[v, M(d, b)], [v, b], [L, b], [L, k], [j(l, L), k]] });
    }
    return O.map((b) => ({ ...b, pts: b.pts.filter((L, T, P) => T === 0 || L[0] !== P[T - 1][0] || L[1] !== P[T - 1][1]) }));
  };
  return (m, u, d) => {
    let l = null;
    const r = (k) => f(k.pts) + (k.pts.length - 2) * 48 + g(k.pts, d) * 36 + n.reduce((v, $) => v + k.pts.slice(1).filter((E, W) => o(k.pts[W][0], k.pts[W][1], E[0], E[1], $, 2)).length, 0) * 400;
    for (const k of [...p(m, u), ...c(m, u)]) {
      if (k.via || a(k.pts, m, u)) continue;
      const v = r(k);
      (!l || v < l.score) && (l = { ...k, score: v });
    }
    if (l) return l;
    for (const k of [...p(m, u), ...c(m, u)]) {
      if (k.via) continue;
      const v = h(k.pts, m, u) * 2e3 + r(k);
      (!l || v < l.score) && (l = { ...k, score: v, loose: !0 });
    }
    return l;
  };
}
function xs(x) {
  const s = /* @__PURE__ */ new Map();
  x.forEach((e) => {
    if (!e || !e.gut) return;
    const t = 1, [i, n] = e.gut, [o, a] = [e.pts[t], e.pts[t + 1]], f = i === "v" ? Math.min(o[1], a[1]) : Math.min(o[0], a[0]), g = i === "v" ? Math.max(o[1], a[1]) : Math.max(o[0], a[0]), p = `${i}${Math.round(n)}`;
    s.has(p) || s.set(p, []), s.get(p).push({ r: e, kind: i, lo: f, hi: g });
  });
  for (const e of s.values()) {
    const t = [];
    for (const i of e) {
      let n = 0;
      for (const a of [0, 10, -10, 20, -20, 30, -30, 40, -40])
        if (!t.some((f) => f.off === a && f.lo < i.hi + 14 && i.lo < f.hi + 14)) {
          n = a;
          break;
        }
      t.push({ off: n, lo: i.lo, hi: i.hi });
      const o = i.kind === "v" ? 0 : 1;
      i.r.pts[1][o] += n, i.r.pts[2][o] += n;
    }
  }
}
function hs(x) {
  const s = x.map((h) => ({ ...h })), e = [], t = (h, [[c, m], [u, d]]) => {
    const l = Math.ceil(Math.hypot(u - c, d - m) / 3);
    for (let r = 0; r <= l; r++) {
      const k = c + (u - c) * r / l, v = m + (d - m) * r / l;
      if (k > h.x && k < h.x + h.w && v > h.y && v < h.y + h.h) return !0;
    }
    return !1;
  }, i = (h) => s.every((c) => !(h.x < c.x + c.w && c.x < h.x + h.w && h.y < c.y + c.h && c.y < h.y + h.h)) && !e.some((c) => t(h, c)), n = (h, c) => {
    for (let m = 0; m < h.length - 1; m++) {
      const [u, d] = h[m], [l, r] = h[m + 1];
      if (Math.max(u, l) > c.x && Math.min(u, l) < c.x + c.w && Math.max(d, r) > c.y && Math.min(d, r) < c.y + c.h) return !0;
    }
    return !1;
  }, o = (h, c, m) => [[bt(c, h.x, h.x + h.w), bt(m, h.y, h.y + h.h)], [c, m]], a = (h, [c, m], [u, d]) => {
    const l = (u - c) ** 2 + (d - m) ** 2, r = l ? bt(((h.x + h.w / 2 - c) * (u - c) + (h.y + h.h / 2 - m) * (d - m)) / l, 0.15, 0.85) : 0.5;
    return o(h, c + (u - c) * r, m + (d - m) * r);
  }, f = ([[h, c], [m, u]]) => {
    const d = Math.ceil(Math.hypot(m - h, u - c) / 4);
    for (let l = 1; l < d; l++) {
      const r = h + (m - h) * l / d, k = c + (u - c) * l / d;
      if (s.some((v) => r > v.x && r < v.x + v.w && k > v.y && k < v.y + v.h)) return !0;
    }
    return !1;
  }, g = (h, c, m, u, d, l, r) => {
    for (let k = 30; k <= (l || 520); k += 14) {
      let v = null;
      for (let $ = 0; $ < 24; $++) {
        const E = $ / 24 * Math.PI * 2, W = { x: c + Math.cos(E) * k - h.w / 2, y: m + Math.sin(E) * k - h.h / 2, w: h.w, h: h.h };
        if (!i(W)) continue;
        const j = u.filter((M) => n(M, W)).length;
        d && (d === "clear" && j || !r(W)) || (!v || j < v.cost) && (v = { r: W, cost: j });
      }
      if (v) return v.r;
    }
    return null;
  }, p = (h, c, m, u) => {
    const d = [];
    for (let L = 0; L < c.length - 1; L++) d.push([c[L], c[L + 1], Math.abs(c[L + 1][0] - c[L][0]) + Math.abs(c[L + 1][1] - c[L][1])]);
    d.sort((L, T) => T[2] - L[2]);
    let l = null;
    if (d.forEach(([L, T, P], Q) => {
      if (P < 10) return;
      const V = Math.abs(L[1] - T[1]) < Math.abs(L[0] - T[0]);
      for (const tt of [0.5, 0.38, 0.62, 0.26, 0.74, 0.14, 0.86]) {
        const ot = L[0] + (T[0] - L[0]) * tt, G = L[1] + (T[1] - L[1]) * tt;
        for (const X of [5, 13, 24, 38]) {
          const mt = V ? [[ot - h.w / 2, G - h.h - X], [ot - h.w / 2, G + X]] : [[ot + X, G - h.h / 2], [ot - h.w - X, G - h.h / 2]];
          for (const [kt, Tt] of mt) {
            const Mt = { x: kt, y: Tt, w: h.w, h: h.h };
            if (!i(Mt)) continue;
            const Et = m.filter((Rt) => n(Rt, Mt)).length, et = Math.abs(tt - 0.5) * 30 + X * 1.1 + Q * 8 + Et * 40;
            (!l || et < l.cost) && (l = { r: Mt, cost: et, n: Et });
          }
        }
      }
    }), l && !l.n)
      return s.push(l.r), { r: l.r, leader: null };
    const [r, k] = d[0] || [c[0], c[c.length - 1]], v = (r[0] + k[0]) / 2, $ = (r[1] + k[1]) / 2, E = u ? (L) => a(L, r, k) : (L) => o(L, v, $), W = (L) => d.flatMap(([T, P]) => [0.5, 0.3, 0.7, 0.15, 0.85].map((Q) => o(L, T[0] + (P[0] - T[0]) * Q, T[1] + (P[1] - T[1]) * Q))).find((T) => !f(T)) || null, j = (L) => {
      const T = E(L);
      return f(T) ? W(L) : T;
    }, M = g(h, v, $, m, "clear", 120, j);
    if (l && !M)
      return s.push(l.r), { r: l.r, leader: null };
    const O = M || g(h, v, $, m, "tied", 520, j) || g(h, v, $, m);
    if (!O) return null;
    const b = j(O);
    return s.push(O), b && e.push(b), { r: O, leader: b };
  };
  return p.around = (h, c, m, u) => {
    const d = [];
    for (const $ of [12, 26, 44])
      d.push([c.x + c.w + $, c.y + c.h - h.h + 8], [c.x + c.w + $, c.y - 4], [c.x + c.w - h.w + 20, c.y + c.h + $], [c.x, c.y + c.h + $], [c.x + c.w - h.w, c.y - h.h - $], [c.x - h.w - $, c.y + c.h - h.h], [c.x - h.w - $, c.y]);
    const l = ($) => u ? Math.max(0, u.x - $.x) + Math.max(0, $.x + $.w - u.x - u.w) + Math.max(0, u.y - $.y) + Math.max(0, $.y + $.h - u.y - u.h) : 0, r = ($) => m.filter((E) => n(E, $)).length;
    let k = null;
    if (d.forEach(([$, E], W) => {
      const j = { x: $, y: E, w: h.w, h: h.h };
      if (!i(j)) return;
      const M = r(j), O = W + M * 400 + l(j) * 0.6;
      (!k || O < k.cost) && (k = { r: j, cost: O, n: M });
    }), k && k.n) {
      const $ = c.x + c.w / 2, E = c.y + c.h / 2;
      for (const W of [!1, !0])
        for (let j = 30; j <= 260 && k.n; j += 14)
          for (let M = 0; M < 24; M++) {
            const O = M / 24 * Math.PI * 2, b = { x: $ + Math.cos(O) * j - h.w / 2, y: E + Math.sin(O) * j - h.h / 2, w: h.w, h: h.h };
            if (i(b) && !r(b) && (W || !l(b))) {
              k = { r: b, n: 0 };
              break;
            }
          }
    }
    const v = k ? k.r : g(h, c.x + c.w / 2, c.y + c.h / 2, m);
    return v && s.push(v), v;
  }, p.add = (h) => s.push(h), p.free = i, p;
}
const ls = (x, s) => {
  const e = x.slice(1).map((t, i) => {
    const n = x[i], o = Math.abs(t[1] - n[1]) < Math.abs(t[0] - n[0]);
    return { h: o, off: s * Math.sign(o ? t[0] - n[0] : t[1] - n[1]) };
  });
  return x.map(([t, i], n) => {
    let o = t, a = i;
    for (const f of n === 0 ? [e[0]] : n === x.length - 1 ? [e[e.length - 1]] : [e[n - 1], e[n]])
      f.h ? a = i + f.off : o = t - f.off;
    return [o, a];
  });
};
function Ws(x, s = {}) {
  if (!x || typeof x != "object" || !Array.isArray(x.blocks)) return ["the map needs a title and a list of blocks"];
  const e = [], t = x.groups || [], i = x.links || [], n = x.stories || [], o = x.path && x.path.steps || [], a = {}, f = {}, g = (l, r) => {
    Object.hasOwn(a, l) ? e.push(`id "${l}" is used twice; ids are unique across groups, blocks, parts and stories`) : a[l] = r;
  };
  for (const l of t) g(l.id, "group");
  for (const l of x.blocks) {
    g(l.id, "block"), f[l.id] = l.id;
    for (const r of l.parts || [])
      g(r.id, "part"), f[r.id] = l.id;
  }
  for (const l of n) g(l.id, "story");
  const p = Xt(t);
  for (const l of t)
    (l.kind || "zone") === "process" ? l.in ? (!p[l.in] || p[l.in].kind !== "machine") && e.push(`group "${l.id}": "in" names "${l.in}", which is not a machine group`) : e.push(`group "${l.id}": a process group needs "in" naming the machine group it runs on`) : l.in && e.push(`group "${l.id}": only a process group takes "in"`);
  const h = (l, r, k) => {
    a[k] !== "block" && a[k] !== "part" && e.push(`${l}: "${r}" names "${k}", which is not a block or part`);
  };
  for (const l of x.blocks) {
    l.group && a[l.group] !== "group" && e.push(`block "${l.id}": "group" names "${l.group}", which is not a group`);
    const r = Yt.has(l.kind);
    if (r && !l.medium && e.push(`block "${l.id}": a ${l.kind} block needs a "medium"`), !r) for (const k of ["medium", "tag", "contents"]) l[k] != null && e.push(`block "${l.id}": only store and log blocks take "${k}"`);
    for (const k of l.parts || [])
      if (k.shape === "store" && !k.medium && e.push(`part "${k.id}": a store part needs a "medium"`), k.shape !== "store") for (const v of ["medium", "tag"]) k[v] != null && e.push(`part "${k.id}": only a store part takes "${v}"`);
  }
  i.forEach((l, r) => {
    h(`link ${r + 1}`, "from", l.from), h(`link ${r + 1}`, "to", l.to), l.from === l.to ? e.push(`link ${r + 1}: joins "${l.from}" to itself`) : f[l.from] && f[l.from] === f[l.to] && (l.from === f[l.from] || l.to === f[l.to]) && e.push(`link ${r + 1}: joins block "${f[l.from]}" to one of its own parts`);
  }), o.forEach((l, r) => {
    h(`path step ${r + 1}`, "from", l.from), h(`path step ${r + 1}`, "to", l.to), l.waitsFor != null && !(Number.isInteger(l.waitsFor) && l.waitsFor >= 1 && l.waitsFor <= r) ? e.push(`path step ${r + 1}: "waitsFor" is ${l.waitsFor}, which is not an earlier step`) : l.waitsFor != null && f[l.from] && f[l.from] === f[l.to] && e.push(`path step ${r + 1}: "waitsFor" needs an arrow to sit on, but this step stays inside block "${f[l.from]}"`), l.mark != null && e.push(`path step ${r + 1}: "mark" belongs only on story steps`);
  });
  const c = /* @__PURE__ */ new Set();
  for (const l of n)
    c.has(l.label) && e.push(`story label "${l.label}" is used twice`), c.add(l.label), (l.steps || []).forEach((r, k) => {
      h(`story "${l.id}" step ${k + 1}`, "from", r.from), h(`story "${l.id}" step ${k + 1}`, "to", r.to), r.waitsFor != null && e.push(`story "${l.id}" step ${k + 1}: "waitsFor" belongs only on the main path`);
    });
  (x.notes || []).forEach((l, r) => {
    if (l.link == null) {
      h(`note ${r + 1}`, "on", l.on);
      return;
    }
    l.on != null && e.push(`note ${r + 1}: give "on" or "link", not both`), h(`note ${r + 1}`, "link.from", l.link.from), h(`note ${r + 1}`, "link.to", l.link.to), i.some((k) => k.from === l.link.from && k.to === l.link.to || k.from === l.link.to && k.to === l.link.from) || e.push(`note ${r + 1}: no link joins "${l.link.from}" and "${l.link.to}"`);
  });
  const m = x.start || {};
  if (m.story != null && a[m.story] !== "story" && e.push(`start: "story" names "${m.story}", which is not a story`), m.block != null) {
    const l = x.blocks.find((r) => r.id === m.block);
    l ? !(l.parts || []).length && !(l.contents || []).length && e.push(`start: block "${l.id}" has no parts to open`) : e.push(`start: "block" names "${m.block}", which is not a block`);
  }
  if (s.derived) return e;
  const u = new Set([...i, ...o, ...n.flatMap((l) => l.steps || [])].flatMap((l) => [l.from, l.to]));
  for (const l of x.blocks) for (const r of l.parts || []) u.has(r.id) || e.push(`part "${r.id}" in block "${l.id}" is in no link or step`);
  const d = new Set(i.filter((l) => f[l.from] && f[l.to] && f[l.from] !== f[l.to]).map((l) => `${[f[l.from], f[l.to]].sort().join("|")}|${l.style || "sync"}`));
  return d.size > 12 && e.push(`the links roll up to ${d.size} arrows between blocks; keep it to 12`), e;
}
function re(x) {
  const s = {};
  for (const r of x.blocks) {
    s[r.id] = r.id;
    for (const k of r.parts || []) s[k.id] = r.id;
  }
  const e = {}, t = [], i = x.blocks.map((r, k) => {
    const v = Yt.has(r.kind), $ = r.parts || [], E = [];
    (v || !$.length) && (e[r.id] = { id: r.id, label: r.name, type: v ? "store" : r.kind === "actor" ? "actor" : "component", status: r.status || "live", what: r.about || "", medium: r.medium, tag: r.tag, holds: r.contents || [], badges: [] }, E.push(r.id));
    for (const W of $)
      e[W.id] = { id: W.id, label: W.name, type: W.shape === "store" ? "store" : "component", status: W.status || "live", what: W.about || "", medium: W.medium, tag: W.tag, holds: [], badges: W.badges || [] }, t.push({ part: W.id, shape: W.shape, role: W.role }), E.push(W.id);
    return { id: r.id, kind: r.kind, name: r.name, role: r.role, what: r.about || "", frame: r.group || null, members: E, medium: r.medium, tag: r.tag, status: r.status || "live", critical: !!r.critical, badges: r.badges || [], lane: r.lane || null, open: $.length > 0 || (r.contents || []).length > 0, index: k };
  }), n = Xt(x.blocks), o = (r) => {
    if (e[r]) return r;
    const k = n[r].parts;
    return (k.find((v) => v.shape !== "store") || k[0]).id;
  }, a = {}, f = [];
  (x.links || []).forEach((r, k) => {
    const v = r.style || "sync", $ = { id: `l${k}`, src: r.from, dst: r.to, from: o(r.from), to: o(r.to), label: r.label || "", kind: v, mode: v === "sync" || v === "control" ? "sync" : "async", critical: !1, hidden: !!r.hidden };
    a[$.id] = $;
    const E = s[r.from], W = s[r.to];
    if (E === W) return;
    const j = `${[E, W].sort().join("|")}|${v}`;
    let M = f.find((O) => O.key === j);
    M || (M = { key: j, from: E, to: W, kind: v, label: "", flows: [], hidden: !0 }, f.push(M)), M.label || (M.label = $.label), M.flows.push($.id), M.hidden = M.hidden && $.hidden;
  });
  const g = Object.values(a), p = (r) => {
    const k = s[r.from], v = s[r.to];
    let $ = g.filter((E) => E.src === r.from && E.dst === r.to || E.src === r.to && E.dst === r.from);
    return !$.length && k !== v && ($ = g.filter((E) => s[E.src] === k && s[E.dst] === v), $.length || ($ = g.filter((E) => s[E.src] === v && s[E.dst] === k))), { from: k, to: v, at: [r.from, r.to], say: r.say, flows: $.map((E) => E.id) };
  }, h = x.path || { title: "", steps: [] }, c = { title: h.title, steps: h.steps.map((r) => ({ ...p(r), waitsFor: r.waitsFor || null })) };
  for (const r of c.steps) for (const k of r.flows) a[k].critical = !0;
  const m = (x.stories || []).map((r) => ({ id: r.id, label: r.label, title: r.title || r.label, tone: r.tone || null, steps: r.steps.map((k) => ({ ...p(k), stop: k.mark === "stop", risk: k.mark === "risk" })) })), u = (r) => {
    const k = /* @__PURE__ */ new Map();
    return r.forEach((v, $) => {
      for (const E of v.flows) k.has(E) || k.set(E, $ + 1);
    }), k;
  }, d = { "": u(c.steps) };
  for (const r of m) d[r.id] = u(r.steps);
  const l = x.lanes || {};
  return {
    thesis: x.title,
    frames: (x.groups || []).map((r) => ({ id: r.id, scope: r.kind || "zone", label: r.label, parent: r.in || null })),
    blocks: i,
    parts: t,
    items: e,
    owner: s,
    flows: a,
    links: f.map(({ key: r, ...k }) => k),
    spine: c,
    paths: m,
    walks: d,
    callouts: (x.notes || []).map((r) => r.link ? { block: null, link: [r.link.from, r.link.to], text: r.text, source: null } : { block: s[r.on], text: r.text, source: r.on }),
    lanes: [l.main || as[0], l.background || as[1], l.control || as[2]],
    start: x.start || null
  };
}
let he = 0;
class vs {
  constructor(s, e, t) {
    this.o = { ...t, compact: !1 }, this.skin = Ks[t.skin || "sketch"], this.T = this.skin.ty, this.L = this.skin.ly, this.ty = this.T, this.id = ++he, this.st = { layout: null, stack: !1, story: null, shape: null, part: null, sel: null, step: null, vb: null, hot: null }, this.root = document.createElement("div"), this.root.className = `smap${t.dialog ? " dialog" : ""}${this.skin.clean ? " clean" : ""}`;
    const i = t.dialog ? '<button type="button" data-z="close" aria-label="Close the map">&times;</button>' : '<button type="button" data-z="open" aria-label="Open the map full screen"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5 9 7M2.5 13.5 7 9" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>';
    this.root.innerHTML = `<div class="smap-bar smap-chrome"><nav class="smap-crumbs" aria-label="Where you are"></nav><nav class="smap-chips" aria-label="Stories"></nav><div class="smap-tools"><button type="button" data-z="fit" aria-label="Zoom out to show all of it">Fit</button><button type="button" data-z="in" aria-label="Zoom in">+</button><button type="button" data-z="out" aria-label="Zoom out">&minus;</button>${i}</div></div>
<div class="smap-canvas"><svg class="smap-svg" role="img"></svg></div>
<div class="smap-glimpse smap-chrome"><button type="button" class="smap-open">Open the map</button></div>
<div class="smap-readout" aria-live="polite"></div>`, s.append(this.root), this.svg = this.root.querySelector(".smap-svg"), this.canvas = this.root.querySelector(".smap-canvas"), this.readout = this.root.querySelector(".smap-readout"), this.rc = this.skin.clean ? ae() : t.rough.svg(this.svg), this.dragMoved = !1, this.rev = 0, this.full = {}, this.ready = new Promise((n) => {
      this.markReady = n;
    }), this.interactions(), fs.add(this), se(), this.ro = new ResizeObserver(() => {
      const n = this.canvas.getBoundingClientRect(), o = `${Math.round(n.width)}x${t.dialog ? Math.round(n.height) : 0}`;
      o !== this.sized && (this.sized = o, clearTimeout(this.rz), this.rz = setTimeout(() => this.refresh(), 60));
    }), this.ro.observe(this.canvas), this.setModel(e, t.state || (e.start ? { story: e.start.story, block: e.start.block } : null)), ie().then(() => {
      this.gone || (this.settled = !0, this.refresh());
    });
  }
  setModel(s, e) {
    const t = this.view();
    this.M = s, this.rev += 1, this.full = {}, this.derive(), this.st.story = null, this.st.shape = null, this.st.part = null, this.st.sel = null, this.st.step = null, this.applyView(e || t), this.key = null, this.refresh();
  }
  view() {
    return { story: this.st.story || void 0, block: this.st.shape || this.st.sel || void 0, part: this.st.part || void 0 };
  }
  applyView(s) {
    if (!s || !this.K) return;
    s.story && this.P[s.story] && (this.st.story = s.story);
    const e = s.part && this.items[s.part] && this.K[this.blockOf[s.part]] && this.K[this.blockOf[s.part]].open ? s.part : null, t = e ? this.blockOf[e] : s.block && this.K[s.block] ? s.block : null;
    t && this.K[t].open ? (this.st.shape = t, this.st.part = e) : t && (this.st.sel = t);
  }
  derive() {
    const s = this.M;
    this.B = s, this.K = Xt(s.blocks), this.F = Xt(s.frames), this.P = Xt(s.paths), this.items = s.items, this.blockOf = s.owner, this.flows = s.flows, this.rec = new Set(s.links.map((e, t) => e.hidden ? t : -1).filter((e) => e >= 0));
  }
  path(s) {
    return this.P[s] || null;
  }
  labelOf(s) {
    return this.items[s] ? this.items[s].label : this.nameOf(s);
  }
  nameOf(s) {
    return this.K[s] ? this.K[s].name : s;
  }
  pathTone(s) {
    return Xs[s.tone] || "";
  }
  openable(s) {
    return !!(this.K[s] && this.K[s].open);
  }
  sil(s) {
    return Yt.has(s.kind) ? ks[s.medium] || "pages" : s.kind === "doc" ? "page" : s.kind === "artifact" ? "package" : s.kind;
  }
  unbuilt(s) {
    return s.status !== "live";
  }
  tagOf(s) {
    return Yt.has(s.kind) && s.tag ? s.tag : null;
  }
  inset(s) {
    const e = qs[this.sil(s)];
    return this.o.compact ? { ...e, g: 0 } : e;
  }
  wantW(s) {
    const e = this.inset(s);
    return Math.max(J(s.name, this.ty.name) + e.g, this.o.compact ? 0 : J(s.role, this.T.role)) + e.l + e.r + 4;
  }
  wordW(s) {
    const e = this.inset(s);
    return Math.max(...s.name.split(/\s+/).map((t) => J(t, this.ty.name))) + e.g + e.l + e.r + 4;
  }
  blockText(s, e) {
    const t = this.inset(s), i = e - t.l - t.r, n = st(s.name, i - t.g, this.ty.name), o = this.o.compact ? [] : st(s.role, this.sil(s) === "actor" ? i - t.g : i, this.T.role), a = this.unbuilt(s) || !this.o.compact && (this.tagOf(s) || s.kind === "log") ? 26 : 0, f = this.o.compact ? [] : this.stripOf(s), g = this.o.compact ? [] : this.heapOf(s).map((h) => {
      const c = this.shapeOf(h);
      return { m: h, lines: st(c && c.role ? c.role : this.labelOf(h), e - 76, this.T.heap) };
    }), p = g.reduce((h, c) => h + Math.max(46, c.lines.length * this.T.heap.lh + 18) + 8, 0);
    return { k: t, name: n, role: o, foot: a, strip: f, heaps: g, h: t.t + n.length * this.ty.name.lh + 4 + o.length * this.T.role.lh + (f.length ? 44 : 0) + (p ? p + 6 : 0) + a + t.b };
  }
  heapOf(s) {
    return ws.has(s.kind) ? s.members.filter((e) => this.items[e].type === "store" && this.items[e].medium === "memory") : [];
  }
  stripOf(s) {
    if (!ws.has(s.kind)) return [];
    const e = s.kind === "service" ? this.anatomy(s.id) : null, t = e ? e.path.filter((i, n) => n === e.path.findIndex((o) => e.depth[o] === e.depth[i])) : s.members.filter((i) => this.items[i].type === "component" && this.shapeOf(i));
    return t.length >= 2 ? t.slice(0, 4) : [];
  }
  spineMap() {
    this.B.links;
    const s = {}, e = [], t = [];
    return this.B.spine.steps.forEach((i, n) => {
      if (i.from === i.to) {
        e.push(n);
        return;
      }
      const o = this.linkFor(i.from, i.to, !0);
      if (o >= 0) {
        (s[o] = s[o] || []).push(n);
        return;
      }
      const a = this.linkFor(i.to, i.from, !0);
      if (a >= 0) {
        t.push({ i: n, link: a });
        return;
      }
      e.push(n);
    }), { on: s, free: e, rails: t };
  }
  linkFor(s, e, t) {
    const i = this.B.links, n = (a, f) => {
      const g = i.map((p, h) => p.from === a && p.to === f ? h : -1).filter((p) => p >= 0);
      return g.find((p) => i[p].kind === "sync") ?? g[0] ?? -1;
    }, o = n(s, e);
    return o >= 0 || t ? o : n(e, s);
  }
  rowsOf(s, e, t) {
    const i = s.map((n) => {
      const o = n.text ? Js(n.text, t || this.L.labelW, e) : [], a = (o.length ? Math.max(...o.map((f) => J(f, e))) : 0) + (n.n != null ? 28 : 0);
      return { ...n, lines: o, w: a, h: Math.max(n.n != null ? 22 : 0, o.length * e.lh) };
    });
    return { rows: i, w: Math.max(...i.map((n) => n.w)) + 8, h: i.reduce((n, o) => n + o.h, 0) + (i.length - 1) * 5 + 6, ty: e };
  }
  linkLabel(s) {
    const e = this.SM.on[s] || [];
    return e.length ? this.rowsOf(e.map((t) => ({ n: t, text: this.B.spine.steps[t].say, tone: "marker" })), this.T.step) : this.B.links[s].label ? this.rowsOf([{ text: this.B.links[s].label }], this.T.link) : null;
  }
  arrange() {
    const s = this.B, e = this.st.layout === "tall", t = this.o.compact, i = {};
    s.spine.steps.forEach((w, S) => {
      for (const z of [w.from, w.to]) Object.hasOwn(i, z) || (i[z] = S);
    });
    const n = {};
    s.links.forEach((w, S) => {
      for (const z of [w.from, w.to]) Object.hasOwn(n, z) || (n[z] = S);
    });
    const o = new Set(Object.keys(i)), a = s.spine.steps.length > 0, f = (w) => s.links.filter((S) => S.from === w || S.to === w).map((S) => S.from === w ? S.to : S.from), g = {};
    for (const w of s.blocks) {
      const S = s.links.filter((z) => z.from === w.id || z.to === w.id);
      w.lane ? g[w.id] = Qs[w.lane] : o.has(w.id) || S.some((z) => z.kind === "sync" && o.has(z.from === w.id ? z.to : z.from)) ? g[w.id] = 0 : S.length && S.every((z) => z.kind === "control") || w.medium === "keys" ? g[w.id] = 2 : g[w.id] = a ? 1 : 0;
    }
    const p = (w) => {
      const S = w.frame && this.F[w.frame];
      return S ? S.scope === "process" ? S.parent : S.id : null;
    }, h = [];
    let c = null;
    for (const w of s.blocks) {
      const S = p(w);
      if (!S) {
        c || (c = { id: null, list: [] }, h.push(c)), c.list.push(w);
        continue;
      }
      c = null;
      let z = h.find((B) => B.id === S);
      z || (z = { id: S, list: [] }, h.push(z)), z.list.push(w);
    }
    const m = (w) => Math.min(99, ...w.map((S) => i[S.id] ?? 99)), u = {};
    let d = 0;
    for (const w of h) {
      const S = w.id && this.F[w.id], z = S && S.scope === "machine" ? [...s.frames.filter((B) => B.scope === "process" && B.parent === S.id).map((B, I) => ({ list: w.list.filter((H) => H.frame === B.id), i: I })).filter((B) => B.list.length).sort((B, I) => m(B.list) - m(I.list) || B.i - I.i).map((B) => B.list), w.list.filter((B) => B.frame === S.id)].filter((B) => B.length) : [w.list];
      for (const B of z) {
        const I = {}, H = /* @__PURE__ */ new Set();
        for (const K of [0, 1, 2]) {
          let wt = 0;
          for (const rt of B.filter((it) => g[it.id] === K)) {
            const it = f(rt.id).filter((Bt) => Object.hasOwn(I, Bt)).map((Bt) => I[Bt]), at = K === 0 || !it.length ? wt : Math.min(...it);
            let gt = at;
            for (let Bt = 0; Bt < 99; Bt++) {
              const Pt = [at + Bt, at - Bt].find((F) => F >= 0 && !H.has(`${K},${F}`));
              if (Pt != null) {
                gt = Pt;
                break;
              }
            }
            H.add(`${K},${gt}`), I[rt.id] = gt, wt = gt + 1;
          }
        }
        const _ = Math.max(...Object.values(I)) + 1;
        for (const K of B) u[K.id] = d + I[K.id];
        d += _;
      }
    }
    const l = {};
    for (const w of s.blocks) l[w.id] = e ? [g[w.id] && !this.st.stack ? 1 : 0, u[w.id]] : [u[w.id], g[w.id]];
    const r = (w) => {
      const S = [...new Set(s.blocks.map((z) => l[z.id][w]))].sort((z, B) => z - B);
      return Object.fromEntries(S.map((z, B) => [z, B]));
    }, k = r(0), v = r(1), $ = {}, E = {};
    for (const w of s.blocks)
      $[w.id] = k[l[w.id][0]], E[w.id] = v[l[w.id][1]];
    const W = Object.keys(k).length, j = Object.keys(v).length, M = Object.fromEntries(Object.entries(v).map(([w, S]) => [S, Number(w)])), O = Object.fromEntries(Object.entries(k).map(([w, S]) => [S, Number(w)])), b = (w) => [g[w], i[w] ?? 99, n[w] ?? 99, this.K[w].index], L = (w, S) => {
      const z = b(w), B = b(S);
      for (let I = 0; I < z.length; I++) if (z[I] !== B[I]) return z[I] - B[I];
      return 0;
    }, T = {};
    for (const w of s.blocks) (T[`${$[w.id]},${E[w.id]}`] = T[`${$[w.id]},${E[w.id]}`] || []).push(w.id);
    for (const w of Object.keys(T)) T[w].sort(L);
    const P = Array(W).fill(e ? this.L.phone : this.L.minW);
    for (const w of s.blocks) P[$[w.id]] = Math.max(P[$[w.id]], e ? 0 : Math.min(this.L.maxW, Math.ceil(this.wantW(w))), Math.ceil(this.wordW(w)));
    const Q = {}, V = {};
    for (const w of s.blocks)
      Q[w.id] = this.blockText(w, P[$[w.id]]), V[w.id] = Q[w.id].h;
    this.SM = this.spineMap();
    const tt = (w, S) => {
      let z = null;
      return s.links.forEach((B, I) => {
        if (B.from === w && B.to === S || B.from === S && B.to === w) {
          const H = this.linkLabel(I);
          H && (z = !z || H.w > z.w ? H : z);
        }
      }), z;
    }, ot = (w, S) => {
      let z = null;
      for (const B of this.SM.rails) {
        const I = s.links[B.link];
        if (I.from === w && I.to === S || I.from === S && I.to === w) {
          const H = this.rowsOf([{ n: B.i, text: s.spine.steps[B.i].say, tone: "marker" }], this.T.step);
          z = !z || H.w > z.w ? H : z;
        }
      }
      return z;
    }, [G, X] = e ? this.L.phoneGutter : this.L.gutter, mt = Array(Math.max(0, W - 1)).fill(G), kt = Array(Math.max(0, j - 1)).fill(e ? this.L.phoneRowGap : this.L.rowGap), Tt = {}, Mt = (w) => {
      const S = w.frame && this.F[w.frame];
      return S ? S.parent ? [S.id, S.parent] : [S.id] : [];
    }, Et = (w, S) => {
      const z = Mt(w), B = Mt(S);
      return (z.filter((I) => !B.includes(I)).length + B.filter((I) => !z.includes(I)).length) * this.L.frame;
    }, et = (w, S) => s.links.some((z, B) => (z.from === w && z.to === S || z.from === S && z.to === w) && [...this.SM.on[B] || [], ...this.SM.rails.filter((I) => I.link === B).map((I) => I.i)].some((I) => s.spine.steps[I].waitsFor));
    if (!t) for (const w of s.blocks) for (const S of s.blocks) {
      if (w.id >= S.id) continue;
      const z = tt(w.id, S.id), B = ot(w.id, S.id);
      if (!z && !B) continue;
      const I = Math.max(z ? z.w : 0, B ? B.w : 0), H = Math.max(z ? z.h : 0, B ? B.h : 0), _ = Et(w, S) + (et(w.id, S.id) ? 36 : 0);
      if (E[w.id] === E[S.id] && Math.abs($[w.id] - $[S.id]) === 1) {
        const K = Math.min($[w.id], $[S.id]);
        mt[K] = bt(Math.max(mt[K], I + 18 + _), G, X + _);
      }
      if ($[w.id] === $[S.id] && Math.abs(E[w.id] - E[S.id]) === 1) {
        const K = Math.min(E[w.id], E[S.id]);
        kt[K] = bt(Math.max(kt[K], H + 20 + _), kt[K], 130 + _);
      }
      if ($[w.id] === $[S.id] && E[w.id] === E[S.id]) {
        const K = T[`${$[w.id]},${E[w.id]}`], wt = K.indexOf(w.id), rt = K.indexOf(S.id);
        Math.abs(wt - rt) === 1 && (Tt[K[Math.max(wt, rt)]] = Math.max(Tt[K[Math.max(wt, rt)]] || 0, H + 16));
      }
    }
    if (!t) for (const w of this.SM.free) {
      const S = s.spine.steps[w];
      if (S.waitsFor) {
        if (E[S.from] === E[S.to] && Math.abs($[S.from] - $[S.to]) === 1) {
          const z = Math.min($[S.from], $[S.to]);
          mt[z] = Math.max(mt[z], (this.waitSize(S.waitsFor, 80).w + 14) / 0.7);
        }
        if ($[S.from] === $[S.to] && Math.abs(E[S.from] - E[S.to]) === 1) {
          const z = Math.min(E[S.from], E[S.to]);
          kt[z] = Math.max(kt[z], (e ? this.L.phoneRowGap : this.L.rowGap) + 60);
        }
      }
    }
    const Rt = (w, S) => S === 0 ? 0 : Math.max(this.L.stack, Tt[w] || 0), It = (w) => w.reduce((S, z, B) => S + V[z] + Rt(z, B), 0), Wt = {};
    for (const w of Object.values(T)) {
      let S = 0;
      w.forEach((z, B) => {
        S += Rt(z, B), Wt[z] = S, S += V[z];
      });
    }
    for (const w of Object.values(T)) {
      const S = w[0];
      if (this.K[S].kind !== "service") continue;
      const z = f(S).filter((B) => E[B] === E[S] && $[B] !== $[S] && this.K[T[`${$[B]},${E[B]}`][0]].kind !== "service").map((B) => Wt[B] + Math.min(V[B], 80));
      V[S] = Math.max(V[S], ...z);
    }
    const dt = Array(j).fill(0);
    for (const [w, S] of Object.entries(T)) {
      const z = Number(w.split(",")[1]);
      dt[z] = Math.max(dt[z], It(S));
    }
    const zt = a && !t && new Set(Object.values(e ? O : M)).size > 1, At = this.L.margin + (!e && zt ? this.L.lane : 0), St = [];
    let Dt = At;
    for (let w = 0; w < W; w++)
      St[w] = Dt, Dt += P[w] + (w < W - 1 ? mt[w] : 0);
    const jt = Dt, Ot = jt - At, Ct = t ? [] : st(s.thesis, Math.max(Ot, 300), this.ty.thesis), Lt = this.L.margin, yt = Lt + Ct.length * this.ty.thesis.lh, xt = yt + (t ? 14 : e && zt ? 64 : 50), Nt = [];
    let Qt = xt;
    for (let w = 0; w < j; w++)
      Nt[w] = Qt, Qt += dt[w] + (w < j - 1 ? kt[w] : 0);
    const qt = Qt, ut = {};
    for (const [w, S] of Object.entries(T)) {
      const [z, B] = w.split(",").map(Number);
      let I = Nt[B];
      S.forEach((H, _) => {
        I += Rt(H, _), ut[H] = { x: St[z], y: I, w: P[z], h: V[H] }, I += V[H];
      });
    }
    const Ut = (w, S) => {
      const z = w.map((K) => ut[K]), B = Math.min(...z.map((K) => K.x)), I = Math.min(...z.map((K) => K.y)), H = Math.max(...z.map((K) => K.x + K.w)), _ = Math.max(...w.map((K) => ut[K].y + ut[K].h + (this.sil(this.K[K]) === "chip" ? 8 : 0)));
      return { x: B - S, y: I - S, w: H - B + S * 2, h: _ - I + S * 2 };
    }, Jt = (w) => {
      const S = Math.min(...w.map((B) => B.x)), z = Math.min(...w.map((B) => B.y));
      return { x: S, y: z, w: Math.max(...w.map((B) => B.x + B.w)) - S, h: Math.max(...w.map((B) => B.y + B.h)) - z };
    }, vt = [], Ht = {};
    for (const w of s.frames) {
      if (w.scope === "machine") continue;
      const S = s.blocks.filter((B) => B.frame === w.id).map((B) => B.id);
      if (!S.length) continue;
      const z = Ut(S, this.L.frame);
      w.scope === "process" && (Ht[w.id] = z), vt.push({ f: w, rect: z });
    }
    for (const w of s.frames.filter((S) => S.scope === "machine")) {
      const S = s.blocks.filter((B) => B.frame === w.id).map((B) => B.id), z = [...S.length ? [Ut(S, this.L.frame)] : [], ...s.frames.filter((B) => B.parent === w.id && Ht[B.id]).map((B) => U(Ht[B.id], this.L.inset))];
      z.length && vt.push({ f: w, rect: Jt(z) });
    }
    vt.sort((w, S) => (w.f.scope === "machine" ? 0 : 1) - (S.f.scope === "machine" ? 0 : 1));
    const Vt = [...s.blocks.map((w) => U(ut[w.id], w.critical && !t ? 14 : 2)), ...s.blocks.map((w) => this.marksZone(w.id, ut[w.id])).filter(Boolean), { x: At, y: Lt, w: Ot, h: yt - Lt + 8 }], Kt = [...Vt];
    for (const w of vt) {
      const S = J(w.f.label, this.ty.frame) + 10, z = this.ty.frame.lh + 2, B = w.rect, I = { x: B.x + 14, y: B.y - z / 2, w: S, h: z }, H = { x: B.x + 14, y: B.y + B.h - z / 2, w: S, h: z }, _ = w.f.scope === "process" ? [H, { ...H, x: B.x + B.w - 14 - S }, I] : [I, { ...I, x: B.x + B.w - 14 - S }], K = (it) => Kt.filter((at) => ss(U(it, 2), at)), wt = (it) => !K(it).length;
      let rt = _.find(wt);
      if (!rt && w.f.scope !== "process") {
        const it = { ...I, y: Math.min(...K(I).map((gt) => gt.y)) - z - 3 }, at = it.y + z / 2;
        B.y - at <= 40 && wt(it) && (rt = it, w.rect = { ...B, y: at, h: B.h + B.y - at });
      }
      if (!rt && t) {
        w.label = null;
        continue;
      }
      rt || (rt = [..._, H, { ...H, x: B.x + B.w - 14 - S }].map((it) => [it, Kt.reduce((at, gt) => at + ss(it, gt), 0)]).sort((it, at) => it[1] - at[1])[0][0]), w.label = rt, Kt.push(U(rt, 3));
    }
    const y = [];
    if (zt && e)
      for (let w = 0; w < W; w++) {
        const S = s.blocks.filter((B) => $[B.id] === w).map((B) => g[B.id]), z = O[w] === 0 ? s.lanes[0] : S.every((B) => B === 2) ? s.lanes[2] : s.lanes[1];
        y.push({ text: z, x: St[w], y: xt - 40, w: P[w], vertical: !1 });
      }
    else if (zt)
      for (let w = 0; w < j; w++) y.push({ text: s.lanes[M[w]], x: this.L.margin - 6, y: Nt[w], h: dt[w], vertical: !0 });
    const R = [...Object.values(ut), ...vt.flatMap((w) => w.label ? [w.rect, w.label] : [w.rect]), ...Ct.length ? [{ x: At, y: Lt, w: Math.max(Ot, ...Ct.map((w) => J(w, this.ty.thesis) + 6)), h: yt - Lt }] : []], A = Jt(R), N = [St[0] - 40, ...St.slice(0, -1).map((w, S) => w + P[S] + mt[S] / 2), jt + 40], C = [xt - 26, ...Nt.slice(0, -1).map((w, S) => w + dt[S] + kt[S] / 2), qt + 40];
    return { box: ut, text: Q, frames: vt, lanes: y, thesis: { x: At, y: Lt, lines: Ct, w: Ot }, vg: N, hg: C, ext: A, colX: St, colW: P, rowY: Nt, rowH: dt, gx: $, gy: E, col: u, fixed: Vt };
  }
  route() {
    const s = this.G, e = this.B, t = ys(s.box, s.vg, s.hg, s.frames.map((p) => p.label).filter(Boolean)), i = e.links.map((p, h) => h).sort((p, h) => (this.SM.on[h] ? 1 : 0) - (this.SM.on[p] ? 1 : 0) || (this.rec.has(p) ? 1 : 0) - (this.rec.has(h) ? 1 : 0) || (e.links[p].kind === "sync" ? 0 : 1) - (e.links[h].kind === "sync" ? 0 : 1) || p - h), n = [], o = [], a = {};
    for (const p of i) {
      const h = e.links[p], c = t(h.from, h.to, n), m = [h.from, h.to].sort().join("|"), u = a[m] = (a[m] || 0) + 1;
      if (u > 1 && c.straight) {
        const d = (u % 2 === 0 ? 1 : -1) * Math.ceil((u - 1) / 2) * 10, l = c.pts[0][1] === c.pts[1][1] ? 1 : 0;
        for (const r of c.pts) r[l] += d;
      }
      o[p] = c, n.push(c.pts);
    }
    xs(o), this.spread(o);
    const f = [...o.filter(Boolean).map((p) => p.pts), ...this.SM.rails.map((p) => ls([...o[p.link].pts].reverse(), -13))], g = (p) => f.some((h) => this.crossesRect(h, U(p, 3)));
    for (const p of s.frames) {
      const h = p.label;
      if (!h || !g(h)) continue;
      const c = [...s.fixed, ...s.frames.filter((l) => l !== p && l.label).map((l) => U(l.label, 3))], m = (l) => !g(l) && c.every((r) => !ss(l, r)), d = [.../* @__PURE__ */ new Set([h.y, p.rect.y - h.h / 2, p.rect.y + p.rect.h - h.h / 2])].flatMap((l) => Array.from({ length: Math.max(0, Math.floor((p.rect.w - h.w - 28) / 12) + 1) }, (r, k) => ({ ...h, x: p.rect.x + 14 + k * 12, y: l }))).find(m);
      d && Object.assign(h, d);
    }
    this.go = t, this.taken = n, this.routes = o;
  }
  spread(s) {
    const e = /* @__PURE__ */ new Map(), t = this.B;
    s.forEach((n, o) => {
      if (!n || n.loose) return;
      const a = t.links[o];
      [[0, a.from], [n.pts.length - 1, a.to]].forEach(([f, g]) => {
        const p = n.pts[f], h = this.G.box[g], c = Math.abs(p[0] - h.x) < 1 ? "l" : Math.abs(p[0] - h.x - h.w) < 1 ? "r" : Math.abs(p[1] - h.y) < 1 ? "t" : "b", m = `${g}:${c}`;
        e.has(m) || e.set(m, []), e.get(m).push({ r: n, pi: f, ax: c === "l" || c === "r" ? 1 : 0, bx: h });
      });
    });
    const i = (n, o) => {
      if (n.r.straight) for (const a of n.r.pts) a[n.ax] += o;
      else {
        n.r.pts[n.pi][n.ax] += o;
        const a = n.pi === 0 ? 1 : n.pi - 1;
        n.r.pts[a][n.ax] += o;
      }
    };
    for (const n of e.values())
      if (!(n.length < 2)) {
        n.sort((o, a) => o.r.pts[o.pi][o.ax] - a.r.pts[a.pi][a.ax]);
        for (let o = 1; o < n.length; o++) {
          const a = n[o - 1].r.pts[n[o - 1].pi][n[o - 1].ax], f = n[o], g = f.r.pts[f.pi][f.ax];
          if (g - a >= 16) continue;
          const p = f.ax === 1 ? f.bx.y + f.bx.h - 8 : f.bx.x + f.bx.w - 8;
          i(f, Math.min(a + 16, p) - g);
        }
      }
    for (const [n, o] of e) {
      if (!n.endsWith(":t")) continue;
      const a = o[0].bx, f = this.marksZone(n.slice(0, -2), a);
      if (!f) continue;
      let g = f.x - 10;
      for (const p of [...o].sort((h, c) => c.r.pts[c.pi][0] - h.r.pts[h.pi][0])) {
        const h = p.r.pts[p.pi][0];
        if (h <= g) {
          g = h - 16;
          continue;
        }
        if (g < a.x + 8) break;
        i(p, g - h), g -= 16;
      }
    }
  }
  obstacles() {
    const s = this.G, e = Object.values(s.box).map((t) => U(t, 3));
    for (const t of s.frames) t.label && e.push(t.label);
    s.thesis.lines.length && e.push({ x: -1e5, y: -1e5, w: 2e5, h: 1e5 + s.thesis.y + s.thesis.lines.length * this.ty.thesis.lh + 8 });
    for (const t of s.lanes) e.push(t.vertical ? { x: t.x - 10, y: t.y, w: 18, h: t.h } : { x: t.x, y: t.y - 12, w: t.w, h: 16 });
    for (const t of Object.values(this.marks || {})) e.push(t);
    for (const t of Object.values(this.checks || {})) e.push(t);
    return e;
  }
  crossesRect(s, e) {
    for (let t = 0; t < s.length - 1; t++) {
      const [i, n] = s[t], [o, a] = s[t + 1];
      if (Math.max(i, o) > e.x && Math.min(i, o) < e.x + e.w && Math.max(n, a) > e.y && Math.min(n, a) < e.y + e.h) return !0;
    }
    return !1;
  }
  el(s, e, t) {
    const i = document.createElementNS(ps, s);
    for (const [n, o] of Object.entries(e || {})) o != null && i.setAttribute(n, o);
    return t && t.append(i), i;
  }
  R(s, e, t) {
    return e.setAttribute("class", s), t.append(e), e;
  }
  O(s) {
    return { roughness: 1.1, bowing: 0.9, strokeWidth: 2, seed: this.seed++, ...s };
  }
  text(s, e, t, i, n, o, a) {
    const f = this.el("text", { x: e, y: t, "font-size": n.size, "font-weight": n.weight, class: `${n.sans ? "t-sans " : ""}${o || ""}`, "text-anchor": a || "start" }, s);
    return i.forEach((g, p) => {
      const h = this.el("tspan", { x: e, dy: p === 0 ? 0 : n.lh }, f);
      h.textContent = g;
    }), f;
  }
  render() {
    this.G = this.arrange(), this.route();
    const s = this.G;
    this.svg.replaceChildren(), this.svg.setAttribute("aria-label", this.B.thesis);
    const e = this.el("defs", {}, this.svg), t = `smap-dots-${this.id}`, i = this.el("pattern", { id: t, width: 26, height: 26, patternUnits: "userSpaceOnUse", class: "dotgrid" }, e);
    this.dots = i, this.el("circle", { cx: 2, cy: 2, r: 1.5 }, i), this.el("rect", { x: -6e3, y: -6e3, width: 14e3, height: 14e3, fill: `url(#${t})` }, this.svg);
    const n = this.el("g", { class: "overview" }, this.svg);
    this.layers = {
      ov: n,
      frames: this.el("g", {}, n),
      lanes: this.el("g", { class: "lanes" }, n),
      links: this.el("g", {}, n),
      captions: this.el("g", {}, n),
      spineRing: this.el("g", { class: "spine" }, n),
      pathRing: this.el("g", {}, n),
      blocks: this.el("g", {}, n),
      spine: this.el("g", { class: "spine" }, n),
      labels: this.el("g", {}, n),
      notes: this.el("g", {}, n),
      path: this.el("g", { class: "pathlayer" }, n),
      inside: this.el("g", { class: "inside-layer" }, this.svg)
    }, this.drawThesis(), this.drawFrames(), this.drawLanes(), this.blockEls = {}, this.marks = {}, this.checks = {}, this.heapRects = {};
    for (const o of this.B.blocks) this.drawBlock(o);
    this.drawLinks(), this.drawAck(), s.ext = this.extent(), this.drawPath(), this.drawInside(), this.applyFocus();
  }
  extent() {
    const s = [this.G.ext, ...this.o.compact ? [] : this.placed];
    this.routes.forEach((i, n) => {
      if (i && !this.rec.has(n)) for (const [o, a] of i.pts) s.push({ x: o - 6, y: a - 6, w: 12, h: 12 });
    });
    const e = Math.min(...s.map((i) => i.x)), t = Math.min(...s.map((i) => i.y));
    return { x: e, y: t, w: Math.max(...s.map((i) => i.x + i.w)) - e, h: Math.max(...s.map((i) => i.y + i.h)) - t };
  }
  drawThesis() {
    const s = this.G.thesis;
    if (!s.lines.length) return;
    const e = this.el("g", { class: "thesis" }, this.layers.ov);
    if (this.text(e, s.x, s.y + this.ty.thesis.size, s.lines, this.ty.thesis, ""), this.seed = pt(`thesis-${this.B.thesis}`), this.skin.clean) return;
    const t = Math.max(...s.lines.map((n) => J(n, this.ty.thesis))), i = s.y + this.ty.thesis.size + (s.lines.length - 1) * this.ty.thesis.lh + 9;
    this.R("k-marker", this.rc.line(s.x - 2, i, s.x + t + 4, i + 1.5, this.O({ strokeWidth: 2.2, roughness: 1.4 })), e);
  }
  drawFrames() {
    for (const s of this.G.frames) {
      const e = Zs[s.f.scope], t = this.el("g", { class: "frame", "data-frame": s.f.id }, this.layers.frames);
      this.seed = pt(`frame-${s.f.id}-${s.rect.x}`);
      const i = s.rect;
      this.R(e.cls, this.rc.rectangle(i.x, i.y, i.w, i.h, this.O({ strokeWidth: e.sw, roughness: 1.4, strokeLineDash: e.dash })), t);
      const n = s.label;
      n && this.text(this.el("g", { class: "frame", "data-frame": s.f.id }, this.layers.captions), n.x + 5, n.y + 4 + this.ty.frame.size * 0.8, [s.f.label], this.ty.frame, `${e.t} halo`);
    }
  }
  drawLanes() {
    for (const s of this.G.lanes) {
      const e = s.text.toUpperCase();
      if (s.vertical) {
        const t = s.y + s.h / 2, i = e.split(" "), n = J(e, this.T.lane) * 1.1 > s.h && i.length > 1 ? [i.slice(0, Math.ceil(i.length / 2)).join(" "), i.slice(Math.ceil(i.length / 2)).join(" ")] : [e], o = this.text(this.layers.lanes, s.x, t, n, this.T.lane, "t-caps t-ink3", "middle");
        o.setAttribute("transform", `rotate(-90 ${s.x} ${t})`), o.setAttribute("letter-spacing", "1.2");
      } else
        this.text(this.layers.lanes, s.x, s.y, [e], this.T.lane, "t-caps t-ink3").setAttribute("letter-spacing", "1.2");
    }
  }
  doodle() {
    const s = this.rc, e = (c, m, u) => this.R(c, m, u), t = (c) => this.O(c);
    return { gear: (c, m, u, d) => {
      e("k-ink2", s.circle(m, u, d * 2, t({ strokeWidth: 1.6 })), c), e("k-ink2", s.circle(m, u, d * 0.8, t({ strokeWidth: 1.3 })), c);
      for (let l = 0; l < 8; l++) {
        const r = l * Math.PI / 4;
        e("k-ink2", s.line(m + Math.cos(r) * d, u + Math.sin(r) * d, m + Math.cos(r) * (d + 4), u + Math.sin(r) * (d + 4), t({ strokeWidth: 2, roughness: 0.6 })), c);
      }
    }, person: (c, m, u, d, l) => {
      const r = l || "k-ink2";
      e(r, s.circle(m, u, 16 * d, t({ strokeWidth: 1.6 })), c), e(r, s.line(m, u + 8 * d, m, u + 30 * d, t({ strokeWidth: 1.6 })), c), e(r, s.line(m - 11 * d, u + 16 * d, m + 11 * d, u + 16 * d, t({ strokeWidth: 1.6 })), c), e(r, s.line(m, u + 30 * d, m - 9 * d, u + 44 * d, t({ strokeWidth: 1.6 })), c), e(r, s.line(m, u + 30 * d, m + 9 * d, u + 44 * d, t({ strokeWidth: 1.6 })), c);
    }, key: (c, m, u, d, l) => {
      const r = l || "k-ink2";
      e(r, s.circle(m, u, 34 * d, t({ strokeWidth: 1.8 })), c), e(r, s.circle(m, u, 10 * d, t({ strokeWidth: 1.4 })), c), e(r, s.line(m + 17 * d, u, m + 78 * d, u, t({ strokeWidth: 2 })), c), e(r, s.line(m + 60 * d, u, m + 60 * d, u + 13 * d, t({ strokeWidth: 2 })), c), e(r, s.line(m + 72 * d, u, m + 72 * d, u + 10 * d, t({ strokeWidth: 2 })), c);
    }, cycle: (c, m, u, d, l, r) => {
      const k = l || "k-ink2", v = r || 1.8, $ = -0.3, E = Math.PI * 1.65, W = (T) => [m + Math.cos(T) * d, u + Math.sin(T) * d], [j, M] = W($), [O, b] = W(E);
      e(k, s.path(`M ${j} ${M} A ${d} ${d} 0 1 1 ${O} ${b}`, t({ strokeWidth: v })), c);
      const L = Math.atan2(Math.cos(E), -Math.sin(E));
      e(k, s.line(O, b, O - Math.cos(L - 0.5) * 7, b - Math.sin(L - 0.5) * 7, t({ strokeWidth: v })), c), e(k, s.line(O, b, O - Math.cos(L + 0.5) * 7, b - Math.sin(L + 0.5) * 7, t({ strokeWidth: v })), c);
    }, plug: (c, m, u) => {
      e("k-ink2", s.rectangle(m - 8, u - 4, 16, 12, t({ strokeWidth: 1.6 })), c), e("k-ink2", s.line(m - 4, u - 4, m - 4, u - 11, t({ strokeWidth: 1.8 })), c), e("k-ink2", s.line(m + 4, u - 4, m + 4, u - 11, t({ strokeWidth: 1.8 })), c), e("k-ink2", s.line(m, u + 8, m, u + 14, t({ strokeWidth: 1.6 })), c);
    }, shield: (c, m, u) => e("k-ink2", s.path(`M ${m} ${u - 9} L ${m + 8} ${u - 6} L ${m + 7} ${u + 3} Q ${m + 4} ${u + 8} ${m} ${u + 10} Q ${m - 4} ${u + 8} ${m - 7} ${u + 3} L ${m - 8} ${u - 6} Z`, t({ strokeWidth: 1.5 })), c), prompt: (c, m, u) => {
      e("k-ink2", s.linearPath([[m - 7, u - 5], [m - 1, u], [m - 7, u + 5]], t({ strokeWidth: 1.6 })), c), e("k-ink2", s.line(m + 1, u + 6, m + 8, u + 6, t({ strokeWidth: 1.6 })), c);
    }, sheet: (c, m, u) => {
      e("k-ink2", s.polygon([[m - 7, u - 9], [m + 3, u - 9], [m + 7, u - 5], [m + 7, u + 9], [m - 7, u + 9]], t({ strokeWidth: 1.4 })), c);
    } };
  }
  glyph(s, e, t, i, n, o) {
    const a = this.rc, f = o || "k-ink2", g = n / 44, p = Math.max(1.1, 1.7 * Math.min(1, g * 1.15)), h = (O) => this.O({ strokeWidth: p, roughness: 0.7, bowing: 0.6, ...O }), c = (O) => t + O * g, m = (O) => i + O * g, u = (O) => this.R(f, O, e), d = (O, b, L, T, P) => u(a.line(c(O), m(b), c(L), m(T), h(P))), l = (O, b) => u(a.linearPath(O.map(([L, T]) => [c(L), m(T)]), h(b))), r = (O, b) => u(a.polygon(O.map(([L, T]) => [c(L), m(T)]), h(b))), k = (O, b, L, T) => u(a.circle(c(O), m(b), L * g, h(T))), v = (O, b, L, T, P) => u(a.rectangle(c(O), m(b), L * g, T * g, h(P))), $ = (O, b, L, T, P) => u(a.ellipse(c(O), m(b), L * g, T * g, h(P))), E = (O, b) => u(a.path(O.replace(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g, (L, T, P) => `${c(+T)} ${m(+P)}`), h(b))), W = (O, b, L) => {
      l([[O - Math.cos(L - 0.55) * 6, b - Math.sin(L - 0.55) * 6], [O, b], [O - Math.cos(L + 0.55) * 6, b - Math.sin(L + 0.55) * 6]]);
    }, j = (O, b, L, T) => {
      d(O, b, L, T), W(L, T, Math.atan2(T - b, L - O));
    }, M = {
      relay: () => {
        d(-20, -7, 20, -7), d(-20, 7, 20, 7), j(-14, -1, 10, -1), j(14, 13, -10, 13), l([[-20, -15], [-20, 15]]), l([[20, -15], [20, 15]]);
      },
      gate: () => {
        d(-20, 20, 20, 20), d(0, 2, 0, 20, { strokeWidth: p * 1.4 }), k(0, -2, 9), d(4, -2, 22, -2), d(-2, 2, -11, 17), d(-2, -6, -11, -21), v(-6, 16, 12, 4);
      },
      router: () => {
        d(-21, 0, -6, 0), j(-6, 0, 18, -15), j(-6, 0, 20, 0), j(-6, 0, 18, 15), k(-6, 0, 5);
      },
      guard: () => {
        r([[0, -20], [16, -14], [14, 4], [0, 20], [-14, 4], [-16, -14]]), l([[-7, 0], [-2, 6], [8, -7]], { strokeWidth: p * 1.3 });
      },
      queue: () => {
        for (const O of [-13, 0, 13])
          d(-21, O + 6, 8, O + 6, { strokeWidth: p * 0.7 }), v(-19, O - 4, 7, 8), v(-9, O - 4, 7, 8);
        d(12, -19, 12, 19, { strokeWidth: p * 1.6 }), j(13, 0, 22, 0);
      },
      pool: () => {
        v(-12, -19, 30, 22), v(-16, -12, 30, 22), v(-20, -5, 30, 22), k(-5, 6, 8), d(-5, 1, -5, 6), d(-5, 6, -1, 8);
      },
      log: () => {
        for (let O = 0; O < 4; O++) v(-21 + O * 10, -3, 9, 12);
        d(19, -3, 19, 9, { strokeLineDash: [2, 3] }), j(14, -20, 14, -7);
      },
      recovery: () => {
        $(0, -2, 16, 6), d(-8, -2, -8, 8), d(8, -2, 8, 8), E("M -8,8 Q 0,12 8,8"), E("M 13,-14 A 18 18 0 1 1 -14,-13"), W(-14, -13, -2.3);
      },
      handoff: () => {
        v(-22, -9, 14, 18, { strokeLineDash: [4, 3] }), v(8, -9, 14, 18), j(-6, 0, 6, 0), r([[-3, -5], [3, -5], [3, -2], [-3, -2]]);
      },
      watcher: () => {
        E("M -21,0 Q 0,-20 21,0"), E("M -21,0 Q 0,20 21,0"), k(0, 0, 13), k(0, 0, 4, { fill: "currentColor", fillStyle: "solid" });
      },
      keys: () => {
        k(-10, 0, 18), k(-10, 0, 6), d(-1, 0, 21, 0), d(15, 0, 15, 7), d(20, 0, 20, 5);
      },
      budget: () => {
        E("M -19,8 A 19 19 0 0 1 19,8");
        for (const O of [-160, -125, -90, -55, -20]) {
          const b = O * Math.PI / 180;
          d(Math.cos(b) * 15, 8 + Math.sin(b) * 15, Math.cos(b) * 19, 8 + Math.sin(b) * 19);
        }
        j(0, 8, 11, -5), d(-21, 13, 21, 13);
      },
      timer: () => {
        k(0, 0, 38), d(0, 0, 0, -12), d(0, 0, 9, 5);
        for (const [O, b] of [[0, -16], [16, 0], [0, 16], [-16, 0]]) d(O * 0.85, b * 0.85, O, b);
      },
      uploader: () => {
        E("M -16,-4 Q -20,-16 -8,-16 Q -4,-24 6,-19 Q 18,-20 17,-9 Q 22,-3 14,-2 Z"), j(0, 20, 0, -6), d(-12, 20, 12, 20);
      },
      codec: () => {
        E("M -6,-19 Q -14,-19 -12,-9 Q -11,-2 -18,0 Q -11,2 -12,9 Q -14,19 -6,19"), E("M 6,-19 Q 14,-19 12,-9 Q 11,-2 18,0 Q 11,2 12,9 Q 14,19 6,19"), d(-3, -3, 3, -3), d(-3, 4, 3, 4);
      },
      compactor: () => {
        for (const O of [-9, 0, 9]) d(-6, O, 6, O, { strokeWidth: p * 1.3 });
        j(-21, 0, -10, 0), j(21, 0, 10, 0), d(-21, -14, -21, 14), d(21, -14, 21, 14);
      },
      sweeper: () => {
        d(16, -20, -2, 4, { strokeWidth: p * 1.3 }), r([[-2, 2], [5, 8], [-6, 20], [-18, 12]]);
        for (const O of [0.3, 0.6]) d(-2 + 7 * O, 2 + 6 * O, -18 + 18 * O, 12 + 8 * O);
        k(12, 16, 2.5), k(18, 12, 2);
      },
      cache: () => {
        r([[3, -21], [-12, 3], [-1, 3], [-5, 21], [12, -4], [1, -4]]);
      },
      api: () => {
        k(0, 0, 36), d(-6, -9, -6, 4, { strokeWidth: p * 1.5 }), d(6, -9, 6, 4, { strokeWidth: p * 1.5 }), d(0, 10, 0, 16);
      },
      ui: () => {
        v(-21, -15, 42, 30), d(-21, -7, 21, -7);
        for (let O = 0; O < 3; O++) k(-16 + O * 6, -11, 3);
        v(-15, -2, 14, 11), d(4, 0, 15, 0), d(4, 6, 12, 6);
      },
      tool: () => {
        v(-21, -15, 42, 30), l([[-13, -6], [-5, 0], [-13, 6]], { strokeWidth: p * 1.3 }), d(-1, 7, 11, 7, { strokeWidth: p * 1.3 });
      },
      docs: () => {
        r([[-14, -20], [6, -20], [15, -11], [15, 20], [-14, 20]]), l([[6, -20], [6, -11], [15, -11]]);
        for (const O of [-4, 3, 10]) d(-8, O, 9, O, { strokeWidth: p * 0.8 });
      },
      sdk: () => {
        r([[-15, -6], [5, -6], [5, 16], [-15, 16]]), r([[-15, -6], [-6, -16], [14, -16], [5, -6]]), r([[5, -6], [14, -16], [14, 6], [5, 16]]);
      },
      module: () => {
        k(0, 0, 22), k(0, 0, 8);
        for (let O = 0; O < 8; O++) {
          const b = O * Math.PI / 4;
          d(Math.cos(b) * 11, Math.sin(b) * 11, Math.cos(b) * 16, Math.sin(b) * 16, { strokeWidth: p * 1.4 });
        }
      }
    };
    (M[s] || M.module)();
  }
  storeGlyph(s, e, t, i, n, o) {
    const a = n / 44, f = { x: t - 20 * a, y: i - 16 * a, w: 40 * a, h: 30 * a }, g = ks[s] || "pages";
    if (g === "key") {
      this.glyph("keys", e, t, i, n, o);
      return;
    }
    this.outline(g, e, f, { cls: o || "k-ink2", strokeWidth: Math.max(1.1, 1.6 * a), roughness: 0.7, mini: a });
  }
  outline(s, e, t, i) {
    const n = this.rc, o = (r, k) => this.R(r, k, e), { mini: a, cls: f, ...g } = i, p = (r) => this.O({ ...g, ...r }), { x: h, y: c, w: m, h: u } = t, d = f || "k-ink", l = a || 1;
    if (s === "cylinder") {
      const r = 10 * l;
      o(d, n.ellipse(h + m / 2, c + r, m, r * 2, p())), o(d, n.line(h, c + r, h, c + u - r, p())), o(d, n.line(h + m, c + r, h + m, c + u - r, p())), o(d, n.path(`M ${h} ${c + u - r} A ${m / 2} ${r} 0 0 0 ${h + m} ${c + u - r}`, p()));
      return;
    }
    if (s === "bucket") {
      const r = 13 * l, k = Math.min(16 * l, m * 0.08);
      o(d, n.ellipse(h + m / 2, c + r, m, r * 2, p())), o(d, n.line(h, c + r, h + k, c + u, p())), o(d, n.line(h + m, c + r, h + m - k, c + u, p())), o(d, n.path(`M ${h + k} ${c + u} Q ${h + m / 2} ${c + u + 9 * l} ${h + m - k} ${c + u}`, p()));
      return;
    }
    if (s === "key" || s === "external") {
      const r = 12 * l;
      o(d, n.path(`M ${h + r} ${c} L ${h + m - r} ${c} Q ${h + m} ${c} ${h + m} ${c + r} L ${h + m} ${c + u - r} Q ${h + m} ${c + u} ${h + m - r} ${c + u} L ${h + r} ${c + u} Q ${h} ${c + u} ${h} ${c + u - r} L ${h} ${c + r} Q ${h} ${c} ${h + r} ${c} Z`, p()));
      return;
    }
    if (s === "page") {
      o(d, n.polygon([[h, c], [h + m - 18, c], [h + m, c + 18], [h + m, c + u], [h, c + u]], p())), o(d, n.polygon([[h + m - 18, c], [h + m - 18, c + 18], [h + m, c + 18]], p({ strokeWidth: 1.4 })));
      return;
    }
    if (o(d, n.rectangle(h, c, m, u, p())), s === "service" && o(d, n.line(h + 6, c + 4, h + 6, c + u - 4, p({ strokeWidth: 1.4 }))), s === "app") {
      o(d, n.line(h, c + 20, h + m, c + 20, p({ strokeWidth: 1.6 })));
      for (let r = 0; r < 3; r++) o("k-ink2", n.circle(h + 13 + r * 12, c + 10, 6, p({ strokeWidth: 1.2 })));
    }
    if (s === "package" && (o(d, n.line(h, c + 14, h + m, c + 14, p({ strokeWidth: 1.4 }))), o("k-ink2", n.line(h + m / 2, c, h + m / 2, c + 14, p({ strokeWidth: 1.4 })))), s === "pages" && o("k-ink3", n.linearPath([[h + 6, c], [h + 6, c - 6], [h + m + 6, c - 6], [h + m + 6, c + u - 6], [h + m, c + u - 6]], p({ strokeWidth: 1.3 }))), s === "chip") for (let r = 0; r < 7; r++) {
      const k = h + (r + 0.5) * m / 7;
      o(d, n.line(k, c + u, k, c + u + 8 * l, p({ strokeWidth: 1.8 * Math.max(l, 0.7), roughness: 0.5 })));
    }
  }
  drawBlock(s) {
    const e = this.G, t = e.box[s.id], i = e.text[s.id], n = i.k, o = this.sil(s), { x: a, y: f, w: g, h: p } = t;
    this.seed = pt(`block-${s.id}`);
    const h = !!this.o.compact, c = this.el("g", { class: "block", "data-block": s.id, tabindex: h ? null : 0, role: h ? null : "button", "aria-label": h ? null : this.blockAria(s) }, this.layers.blocks);
    this.el("rect", { class: "hit", x: a - 8, y: f - 8, width: g + 16, height: p + 16 }, c);
    const m = this.el("g", { class: "ring" }, c);
    this.R("k-hl", this.rc.rectangle(a - 7, f - 7, g + 14, p + 14, this.O({ strokeWidth: 5, roughness: 1.8 })), m), this.el("rect", { class: "paper", x: a + 2, y: f + 2, width: g - 4, height: p - 4, rx: 6 }, c);
    const u = this.doodle(), d = this.unbuilt(s), l = { strokeWidth: 2.3, ...d ? { strokeLineDash: [9, 6], roughness: 1.5 } : {} };
    if (o === "service" && !i.strip.length && !i.heaps.length && this.R("f-blue", this.rc.rectangle(a + 9, f + p - 15, g - 13, 11, this.O({ stroke: "none", fill: "#000", fillStyle: "hachure", hachureGap: 7, hachureAngle: -50, fillWeight: 1 })), c), this.outline(o, c, t, l), c.addEventListener("click", (W) => {
      this.dragMoved || (W.stopPropagation(), this.pickShape(s.id));
    }), c.addEventListener("keydown", (W) => {
      (W.key === "Enter" || W.key === " ") && (W.preventDefault(), this.pickShape(s.id));
    }), this.blockEls[s.id] = { g: c, box: t }, h) {
      this.text(c, a + n.l, f + n.t + this.ty.name.size - 3, i.name, this.ty.name, ""), d && this.pill(c, a + n.l, f + p - n.b - 19, s.status, "warn"), this.drawBadges(s, c);
      return;
    }
    if (o === "actor" && (u.person(c, a + g - 22, f + 16, 0.62), this.R("k-ink2", this.rc.rectangle(a + g - 44, f + 22, 11, 19, this.O({ strokeWidth: 1.3 })), c)), o === "service" && u.gear(c, a + g - 21, f + 25, 7), o === "worker" && u.cycle(c, a + g - 21, f + 25, 8), o === "external" && u.plug(c, a + g - 21, f + 25), o === "key" && u.key(c, a + g - 42, f + 28, 0.38), s.kind === "log") for (let W = 0; W < 4; W++) this.R("k-ink2", this.rc.rectangle(a + g - n.r - 68 + W * 17, f + p - n.b - 15, 13, 9, this.O({ strokeWidth: 1.1, roughness: 0.7 })), c);
    let r = f + n.t + this.ty.name.size - 3;
    this.text(c, a + n.l, r, i.name, this.ty.name, ""), r += (i.name.length - 1) * this.ty.name.lh + 6 + this.T.role.size, this.text(c, a + n.l, r, i.role, this.T.role, "t-ink2");
    let k = f + n.t + i.name.length * this.ty.name.lh + 4 + i.role.length * this.T.role.lh + 8;
    i.strip.length && (this.drawStrip(c, a + n.l, k, i.strip), k += 44);
    for (const W of i.heaps) {
      const j = Math.max(46, W.lines.length * this.T.heap.lh + 18);
      this.drawHeapIn(c, W, { x: a + 10, y: k, w: g - 20, h: j }), k += j + 8;
    }
    const v = this.tagOf(s), $ = f + p - n.b - 19;
    let E = a + n.l;
    v && (E = this.pill(c, E, $, v, "") + 6), d && this.pill(c, E, $, s.status, "warn"), this.drawBadges(s, c);
  }
  blockAria(s) {
    return `${s.name}: ${s.role}${s.badges.length ? `. ${s.badges.map((e) => e.text).join(", ")}` : ""}${s.open ? ". Press Enter to look inside" : ""}`;
  }
  drawStrip(s, e, t, i) {
    const n = this.el("g", { class: "strip" }, s);
    i.forEach((o, a) => {
      const f = e + 16 + a * 46;
      this.glyph((this.shapeOf(o) || {}).shape || "module", n, f, t + 18, 30, "k-ink2"), a < i.length - 1 && this.R("k-ink3", this.rc.line(f + 19, t + 18, f + 27, t + 18, this.O({ strokeWidth: 1.4, roughness: 0.4 })), n);
    }), this.el("title", {}, n).textContent = i.map((o) => this.labelOf(o)).join(" → ");
  }
  drawHeapIn(s, e, t) {
    this.heapRects[e.m] = t;
    const i = this.el("g", { class: "heapin", "data-part": e.m }, s);
    this.el("title", {}, i).textContent = `${this.labelOf(e.m)}: inside the process`, this.el("rect", { class: "paper2", x: t.x, y: t.y, width: t.w, height: t.h, rx: 8 }, i), this.R("f-green", this.rc.rectangle(t.x + 2, t.y + 2, t.w - 4, t.h - 4, this.O({ stroke: "none", fill: "#000", fillStyle: "hachure", hachureGap: 9, hachureAngle: 60, fillWeight: 0.7 })), i), this.R("k-ink2", this.rc.rectangle(t.x, t.y, t.w, t.h, this.O({ strokeWidth: 1.4, roughness: 1, strokeLineDash: [7, 4] })), i), this.storeGlyph("memory", i, t.x + 24, t.y + t.h / 2 - 3, 30, "k-ink2"), this.text(i, t.x + 50, t.y + 9 + this.T.heap.size, e.lines, this.T.heap, "t-ink2 halo");
  }
  pillW(s) {
    return J(s.toUpperCase(), this.T.tag) + 0.8 * s.length + 14;
  }
  pill(s, e, t, i, n) {
    const o = this.pillW(i), a = this.el("g", { class: `pill ${n}` }, s);
    return this.el("rect", { x: e, y: t, width: o, height: 17, rx: 8.5 }, a), this.text(a, e + 7, t + 12.5, [i.toUpperCase()], this.T.tag, `t-caps ${n === "warn" ? "t-amber" : "t-ink3"}`).setAttribute("letter-spacing", "0.8"), e + o;
  }
  markList(s, e, t, i) {
    const n = i.size + 9;
    return s.map((o) => {
      const a = o.tone === "ask", f = a ? `? ${o.text}` : ts[o.tone] ? `${ts[o.tone]} ${o.text}` : o.text, g = J(f, i) + 16, p = e - g;
      return e = p - 6, a ? { d: o, label: f, ask: a, x: p, y: t - 3, w: g, h: 27, ty: i } : { d: o, label: f, ask: a, x: p, y: t - n / 2 + 7, w: g, h: n, ty: i };
    });
  }
  badgeRects(s, e) {
    const t = this.o.compact ? s.badges.filter((i) => i.tone === "fail" || i.tone === "warn") : s.badges;
    return this.markList(t, e.x + e.w - 12, e.y - 9, this.ty.badge);
  }
  marksZone(s, e) {
    const t = this.badgeRects(this.K[s], e);
    if (!t.length) return null;
    const i = Math.min(...t.map((o) => o.x)), n = Math.min(...t.map((o) => o.y));
    return { x: i - 4, y: n - 3, w: Math.max(...t.map((o) => o.x + o.w)) - i + 8, h: Math.max(...t.map((o) => o.y + o.h)) - n + 22 };
  }
  drawBadges(s, e) {
    for (const t of this.badgeRects(s, this.G.box[s.id]))
      this.drawMark(e, t, s.name), this.marks[`${s.id}:${t.x}`] = U(t, 2);
  }
  drawMark(s, e, t) {
    const i = e.ty;
    if (e.ask) {
      const f = this.el("g", { class: "bub" }, s);
      this.el("title", {}, f).textContent = `${e.d.text}: a question about ${t}`, this.bubblePath(f, e.x, e.y, e.w), this.text(f, e.x + e.w / 2, e.y + 15, [e.label], i, "t-ink2", "middle");
      return;
    }
    const n = { ok: "t-green", fail: "t-red", warn: "t-amber", info: "t-ink3" }[e.d.tone], o = this.el("g", { class: `bd ${Ys[e.d.tone]}` }, s);
    this.el("title", {}, o).textContent = `${t}: ${e.d.text}`, this.el("rect", { x: e.x, y: e.y, width: e.w, height: e.h, rx: e.h / 2, class: "base" }, o), this.el("rect", { x: e.x, y: e.y, width: e.w, height: e.h, rx: e.h / 2, class: "face" }, o);
    const a = this.el("text", { x: e.x + e.w / 2, y: e.y + e.h / 2 + i.size * 0.36, "text-anchor": "middle", "font-size": i.size, "font-weight": i.weight, class: "t-sans" }, o);
    if (ts[e.d.tone]) {
      const f = this.el("tspan", { class: n }, a);
      f.textContent = ts[e.d.tone];
      const g = this.el("tspan", { class: e.d.tone === "ok" ? "t-ink3" : n }, a);
      g.textContent = ` ${e.d.text}`;
    } else
      a.setAttribute("class", `t-sans ${n}`), a.textContent = e.label;
  }
  arrowPath(s, e) {
    const t = e ?? 14;
    let i = `M ${s[0][0]} ${s[0][1]}`;
    for (let o = 1; o < s.length - 1; o++) {
      const [a, f] = s[o - 1], [g, p] = s[o], [h, c] = s[o + 1], m = Math.hypot(g - a, p - f) || 1, u = Math.hypot(h - g, c - p) || 1, d = Math.min(t, m / 2, u / 2);
      i += ` L ${g - (g - a) / m * d} ${p - (p - f) / m * d} Q ${g} ${p} ${g + (h - g) / u * d} ${p + (c - p) / u * d}`;
    }
    const n = s[s.length - 1];
    return `${i} L ${n[0]} ${n[1]}`;
  }
  drawArrow(s, e, t, i, n, o, a) {
    this.R(o, this.rc.path(this.arrowPath(e), this.O({ strokeWidth: t, roughness: 0.8, bowing: 0.6, ...i ? { strokeLineDash: i } : {} })), s);
    const [f, g] = e[e.length - 2], [p, h] = e[e.length - 1], c = Math.atan2(h - g, p - f), m = 9 + t * 1.6, u = (r) => [p - Math.cos(c) * r, h - Math.sin(c) * r], d = (r, k) => [p - Math.cos(c) * (r + m) + Math.cos(c + Math.PI / 2) * k * m * 0.5, h - Math.sin(c) * (r + m) + Math.sin(c + Math.PI / 2) * k * m * 0.5];
    if (n === "fill") {
      const [r, k, v] = [u(0), d(0, 1), d(0, -1)];
      this.el("path", { d: `M ${r[0]} ${r[1]} L ${k[0]} ${k[1]} L ${v[0]} ${v[1]} Z`, class: a }, s);
      return;
    }
    const l = (r) => {
      const k = u(r), v = d(r, 1), $ = d(r, -1);
      this.R(o, this.rc.linearPath([v, k, $], this.O({ strokeWidth: t, roughness: 0.6 })), s);
    };
    l(0), n === "double" && l(7);
  }
  drawAck() {
    if (!this.o.compact) {
      this.seed = pt("ack");
      for (const s of this.B.blocks) {
        if (!s.critical) continue;
        const e = this.G.box[s.id], t = this.el("g", { class: "ackring" }, this.layers.spineRing);
        this.R("k-hl", this.rc.rectangle(e.x - 9, e.y - 9, e.w + 18, e.h + 18, this.O({ strokeWidth: 8, roughness: 1.3 })), t);
      }
      this.B.spine.steps.forEach((s, e) => {
        this.bars[e] && this.drawWait(e, s.waitsFor);
      });
    }
  }
  waitBar(s, e) {
    const t = Object.keys(this.SM.on).find((k) => this.SM.on[k].includes(s)), [i, n] = this.railPts[s] || this.loosePts[s] || this.routes[t].pts, o = Math.abs(i[1] - n[1]) < Math.abs(i[0] - n[0]), a = (k) => {
      const v = i[0] + (n[0] - i[0]) * k, $ = i[1] + (n[1] - i[1]) * k;
      return { cx: v, cy: $, bar: o ? { x: v - 4, y: $ - 19, w: 8, h: 38 } : { x: v - 19, y: $ - 4, w: 38, h: 8 } };
    }, f = this.obstacles(), g = (k) => f.reduce((v, $) => v + ss(U(k.bar, 2), $), 0), p = [0.3, 0.2, 0.42, 0.12, 0.55, 0.65].map(a).filter((k) => this.place.free(U(k.bar, 6))), h = [...this.routes.filter(Boolean).map((k) => k.pts), ...Object.values(this.railPts), ...Object.values(this.loosePts)], c = (k) => !h.some((v) => this.crossesRect(v, k)), m = [this.waitSize(e, 140), this.waitSize(e, 80)], u = ({ cx: k, cy: v, bar: $ }, E) => (o ? [[$.x + $.w + 3, v - E.h - 3], [$.x + $.w + 3, v + 3], [$.x - E.w - 3, v - E.h - 3], [$.x - E.w - 3, v + 3], [k - E.w / 2, $.y - E.h - 2], [k - E.w / 2, $.y + $.h + 2]] : [[$.x + $.w + 4, v - E.h / 2], [$.x - E.w - 4, v - E.h / 2], [k + 3, $.y - E.h - 2], [k + 3, $.y + $.h + 2]]).map(([W, j]) => ({ x: W, y: j, w: E.w, h: E.h })), d = [!0, !1].flatMap((k) => p.flatMap((v) => m.flatMap(($) => u(v, $).filter((E) => this.place.free(E) && (!k || c(E))).map((E) => ({ x: v, r: E, z: $ })))))[0], l = d ? d.x : p[0] || [0.3, 0.2, 0.42, 0.12, 0.55, 0.65].map(a).reduce((k, v) => g(v) < g(k) ? v : k);
    this.place.add(U(l.bar, 3)), this.checks[`bar${s}`] = U(l.bar, 3);
    const r = d ? { r: d.r, z: d.z } : { r: this.place.around(m[0], U(l.bar, 4), h), z: m[0] };
    return d && this.place.add(r.r), r.r && (this.checks[`lab${s}`] = r.r), { ...l, horiz: o, cap: r };
  }
  waitSize(s, e) {
    return this.rowsOf([{ text: `waits for step ${s}` }], { size: 14, weight: 600, lh: 17 }, e);
  }
  drawWait(s, e) {
    const { cx: t, cy: i, bar: n, horiz: o, cap: a } = this.bars[s], f = this.el("g", { class: "ack", "data-step": s }, this.layers.spine);
    this.el("title", {}, f).textContent = `Step ${s + 1} waits here until step ${e} is done`, this.el("rect", { class: "paper", x: n.x, y: n.y, width: n.w, height: n.h }, f), this.R("k-red", this.rc.rectangle(n.x, n.y, n.w, n.h, this.O({ strokeWidth: 1.8, roughness: 0.6 })), f);
    for (let p = 1; p < 4; p++) {
      const h = p / 4;
      o ? this.R("k-red", this.rc.line(n.x, n.y + n.h * h - 3, n.x + n.w, n.y + n.h * h + 3, this.O({ strokeWidth: 2.2, roughness: 0.4 })), f) : this.R("k-red", this.rc.line(n.x + n.w * h - 3, n.y, n.x + n.w * h + 3, n.y + n.h, this.O({ strokeWidth: 2.2, roughness: 0.4 })), f);
    }
    if (this.el("circle", { cx: o ? t : n.x, cy: o ? n.y : i, r: 4, class: "disc stop" }, f), !a.r) return;
    const g = this.el("g", { class: "ack-lab" }, f);
    this.drawRows(g, a.r, a.z, "plain"), g.querySelectorAll("text").forEach((p) => p.setAttribute("class", "t-red halo")), this.placed.push(a.r);
  }
  drawLinks() {
    const s = this.B, e = hs(this.obstacles());
    this.place = e, this.placed = [];
    const t = this.G.frames.map(({ rect: g }) => [[g.x, g.y], [g.x + g.w, g.y], [g.x + g.w, g.y + g.h], [g.x, g.y + g.h], [g.x, g.y]]), i = [...this.routes.filter(Boolean).map((g) => g.pts), ...t];
    this.labEls = [], this.linkEls = [];
    const n = (g, p, h) => {
      const c = e(g, p, h);
      return c && this.placed.push(c.r), c;
    };
    this.leads = [];
    const o = (g, p, h) => {
      !p || !p.leader || (this.leads.push(p.leader), this.R(`${h || "k-ink3"} lead`, this.rc.line(p.leader[0][0], p.leader[0][1], p.leader[1][0], p.leader[1][1], this.O({ strokeWidth: 1.1, roughness: 0.4, strokeLineDash: [3, 4] })), g));
    };
    this.leader = o, this.railPts = {};
    for (const g of this.SM.rails) this.railPts[g.i] = ls([...this.routes[g.link].pts].reverse(), -13);
    this.loosePts = {};
    for (const g of this.SM.free) {
      const p = s.spine.steps[g];
      p.from !== p.to && (this.loosePts[g] = this.loose(p.from, p.to, s.spine.steps).pts);
    }
    i.push(...Object.values(this.railPts), ...Object.values(this.loosePts)), this.bars = {}, this.o.compact || s.spine.steps.forEach((g, p) => {
      g.waitsFor && (this.bars[p] = this.waitBar(p, g.waitsFor));
    }), this.drawCallouts();
    const a = s.links.map((g, p) => p).sort((g, p) => (this.SM.on[p] ? 1 : 0) - (this.SM.on[g] ? 1 : 0) || (this.rec.has(g) ? 1 : 0) - (this.rec.has(p) ? 1 : 0) || g - p);
    for (const g of a) {
      const p = s.links[g], h = this.routes[g], c = !!this.SM.on[g], m = Vs[p.kind];
      this.seed = pt(`link-${g}-${p.from}-${p.to}`);
      const u = this.rec.has(g) && !c ? " rec" : "", d = this.el("g", { class: `link${u}`, "data-link": g }, this.layers.links);
      this.drawArrow(d, h.pts, c ? 2.8 : m.sw, c ? null : m.dash, c ? "fill" : m.head, c ? "k-marker" : m.cls, c ? "fh-marker" : m.fh), this.linkEls[g] = d;
      const l = this.linkLabel(g);
      if (!l) continue;
      const r = n(l, h.pts, i.filter((v) => v !== h.pts));
      if (!r) continue;
      const k = this.el("g", { class: `lab${u}`, "data-link": g }, this.layers.labels);
      if (o(k, r), this.drawRows(k, r.r, l, c ? "spine" : "plain", c ? (v, $) => this.wireStep(v, $.n, null) : null), c && p.label) {
        const v = this.rowsOf([{ text: p.label }], this.T.link), $ = this.el("g", { class: "lab plain gone", "data-link": g }, this.layers.labels);
        this.drawRows($, { x: r.r.x + (r.r.w - v.w) / 2, y: r.r.y + (r.r.h - v.h) / 2, w: v.w, h: v.h }, v, "plain"), this.labEls.push({ g: $, i: g, plain: !0 });
      }
      this.labEls.push({ g: k, i: g, spine: c });
    }
    const f = this.layers.spine;
    for (const g of this.SM.rails) {
      const p = this.railPts[g.i];
      this.seed = pt(`rail-${g.i}`);
      const h = this.el("g", { class: "step", "data-step": g.i }, f);
      this.drawArrow(h, p, 1.6, null, "open", "k-marker", "fh-marker");
      const c = this.rowsOf([{ n: g.i, text: s.spine.steps[g.i].say, tone: "marker" }], this.T.step), m = n(c, p, i);
      m && (o(h, m, "k-marker"), this.drawRows(h, m.r, c, "spine")), this.wireStep(h, g.i, null);
    }
    for (const g of this.SM.free) {
      const p = s.spine.steps[g], h = this.el("g", { class: "step", "data-step": g }, f);
      this.seed = pt(`spine-free-${g}`), this.drawLoose(h, p, s.spine.steps, "marker", !1, this.T.step, g, this.el("g", { class: "step", "data-step": g }, this.layers.spineRing), e, i), this.wireStep(h, g, null);
    }
  }
  wireStep(s, e, t) {
    s.classList.add("step"), s.dataset.step = String(e), s.addEventListener("mouseenter", () => this.hot(e)), s.addEventListener("mouseleave", () => this.hot(this.st.step)), s.addEventListener("click", (i) => {
      this.dragMoved || (i.stopPropagation(), this.pickStep(e, t ? t.id : null));
    });
  }
  loose(s, e, t) {
    if (!t.some((a) => a.from === e && a.to === s)) return this.go(s, e, this.taken);
    const [i, n] = [s, e].sort(), o = this.go(i, n, this.taken).pts;
    return { pts: ls(i === s ? o : [...o].reverse(), 9) };
  }
  drawLoose(s, e, t, i, n, o, a, f, g, p = []) {
    const h = this.rowsOf([{ n: a, text: e.say, tone: i }], o), c = this.el("g", { class: "step", "data-step": a }), m = i === "red" ? "k-red" : i === "ink2" ? "k-ink2" : "k-marker", u = i === "red" ? "risk" : i === "ink2" ? "stop" : "spine";
    if (e.from === e.to) {
      const k = this.G.box[e.from], v = this.heapBox(e), $ = f || s;
      if (v) {
        const j = this.el("g", { class: "hl" }, $);
        this.R("k-hl", this.rc.rectangle(v.x - 4, v.y - 4, v.w + 8, v.h + 8, this.O({ strokeWidth: 8, roughness: 1.2 })), j), this.R(m, this.rc.rectangle(v.x - 3, v.y - 3, v.w + 6, v.h + 6, this.O({ strokeWidth: 2.4, roughness: 1.1 })), $);
        const M = g.around(h, { x: k.x, y: v.y, w: k.w, h: k.y + k.h - v.y }, p);
        if (M) {
          const O = (V, tt, ot, G) => tt < ot ? [tt, ot - 3] : V > G ? [V, G + 3] : Array(2).fill((Math.max(V, ot) + Math.min(tt, G)) / 2), [b, L] = O(M.x, M.x + M.w, v.x, v.x + v.w), [T, P] = O(M.y, M.y + M.h, v.y, v.y + v.h), Q = [[b, T], [L, P]];
          this.drawArrow(s, Q, 2, n ? [8, 6] : null, "fill", m, m.replace("k-", "fh-")), this.drawRows(c, M, h, u), this.placed.push(M);
        }
        return s.append(c), c;
      }
      const E = this.el("g", { class: "hl" }, $);
      this.R("k-hl", this.rc.rectangle(k.x - 9, k.y - 9, k.w + 18, k.h + 18, this.O({ strokeWidth: 9, roughness: 1.2 })), E), this.R(m, this.rc.rectangle(k.x - 6, k.y - 6, k.w + 12, k.h + 12, this.O({ strokeWidth: 2.4, roughness: 1.3, ...n ? { strokeLineDash: [10, 7] } : {} })), $);
      const W = g.around(h, U(k, 8), p);
      return W && (this.drawRows(c, W, h, u), this.placed.push(W)), s.append(c), c;
    }
    const d = this.loose(e.from, e.to, t), l = this.el("g", { class: "hl" }, s);
    this.R("k-hl", this.rc.path(this.arrowPath(d.pts), this.O({ strokeWidth: 12, roughness: 0.5 })), l), this.drawArrow(this.el("g", { class: "loose" }, s), d.pts, 2.6, n ? [11, 8] : null, "fill", m, m.replace("k-", "fh-"));
    const r = g(h, d.pts, [...p, d.pts], p.length > 0);
    return r && (this.leader(c, r, m), this.drawRows(c, r.r, h, u), this.placed.push(r.r)), s.append(c), c;
  }
  heapBox(s) {
    const e = this.K[s.from], i = this.heapOf(e).find((n) => s.at.includes(n) || s.flows.some((o) => this.flows[o] && (this.flows[o].from === n || this.flows[o].to === n)));
    return i && this.heapRects && this.heapRects[i] ? this.heapRects[i] : null;
  }
  drawRows(s, e, t, i, n) {
    let o = e.y + 3;
    for (const a of t.rows) {
      const f = this.el("g", {}, s);
      this.el("rect", { x: e.x, y: o - 2, width: e.w, height: a.h + 4, fill: "transparent" }, f);
      let g = e.x + 4;
      if (a.n != null) {
        const m = a.tone === "red" ? " risk" : a.tone === "ink2" ? " stop" : "";
        this.el("circle", { cx: g + 11, cy: o + 11, r: 11, class: `disc${m}` }, f), this.text(f, g + 11, o + 15.5, [String(a.n + 1)], this.T.disc, "t-paper", "middle"), g += 28;
      }
      const p = i === "plain" ? "t-ink2" : a.tone === "red" ? "t-red" : a.tone === "ink2" ? "t-ink2" : "t-marker", h = t.ty.lh, c = a.n != null && a.lines.length === 1 ? o + 11 - h / 2 : o;
      this.text(f, g, c + t.ty.size - 2, a.lines, t.ty, `${p} halo`), n && a.n != null && n(f, a), o += a.h + 5;
    }
  }
  drawCallouts() {
    if (this.o.compact) return;
    const s = [...this.B.paths.map((t) => t.steps), this.B.spine.steps].flatMap((t) => t.filter((i) => i.from !== i.to && this.linkFor(i.from, i.to) < 0).map((i) => this.loose(i.from, i.to, t).pts)), e = [...this.routes.filter(Boolean).map((t) => t.pts), ...Object.values(this.railPts), ...s];
    (this.B.callouts || []).forEach((t) => {
      const i = st(t.text, 176, this.T.note), n = Math.max(...i.map((g) => J(g, this.T.note))) + 24, o = i.length * this.T.note.lh + 18;
      if (t.link) {
        const [g, p] = t.link.map((u) => this.B.owner[u]), h = this.B.links.findIndex((u) => u.from === g && u.to === p || u.from === p && u.to === g), c = h >= 0 && this.routes[h];
        if (!c) return;
        const m = this.place({ w: n + 8, h: o + 8 }, c.pts, e);
        if (!m) return;
        this.placed.push(m.r), m.leader && this.R("k-ink3 lead", this.rc.line(m.leader[0][0], m.leader[0][1], m.leader[1][0], m.leader[1][1], this.O({ strokeWidth: 1.1, roughness: 0.4, strokeLineDash: [3, 4] })), this.layers.notes), this.drawSticky(this.layers.notes, U(m.r, -4), i, t.text);
        return;
      }
      const a = this.G.box[t.block];
      if (!a) return;
      const f = this.place.around({ w: n + 8, h: o + 8 }, U(a, 2), e, this.extent());
      f && (this.placed.push(f), this.drawSticky(this.layers.notes, U(f, -4), i, t.text));
    });
  }
  drawPath() {
    const s = this.layers.path;
    s.replaceChildren();
    const e = this.st.story && this.path(this.st.story);
    this.layers.spine.classList.toggle("gone", !!e), this.layers.spineRing.classList.toggle("gone", !!e), this.layers.pathRing.replaceChildren();
    const t = e ? new Set(e.steps.flatMap((h) => h.flows)) : null;
    this.litLinks = e ? new Set(this.B.links.map((h, c) => h.flows.some((m) => t.has(m)) ? c : -1).filter((h) => h >= 0)) : null;
    for (const h of this.labEls || []) {
      const c = e && e.steps.some((u) => u.from !== u.to && this.linkFor(u.from, u.to) === h.i), m = !!e && !this.litLinks.has(h.i);
      h.g.classList.toggle("gone", h.plain ? !e || c || m : h.spine && !!e || !!c || m);
    }
    if (!e) return;
    const i = [...this.obstacles(), ...this.svg.querySelectorAll(".overview .note")].map((h) => h instanceof Element ? this.boxOf(h) : h);
    for (const h of this.labEls || []) h.g.classList.contains("gone") || i.push(this.boxOf(h.g));
    const n = hs(i), o = {};
    e.steps.forEach((h, c) => {
      if (h.from !== h.to) {
        const m = this.linkFor(h.from, h.to);
        m >= 0 && (o[m] = o[m] || []).push(c);
      }
    });
    const a = this.routes.filter(Boolean).map((h) => h.pts), f = e.steps.map((h) => h.from !== h.to && this.linkFor(h.from, h.to) < 0 ? this.loose(h.from, h.to, e.steps).pts : null), g = [], p = /* @__PURE__ */ new Set();
    e.steps.forEach((h, c) => {
      const m = h.risk ? "red" : h.stop ? "ink2" : "marker", u = h.from === h.to ? -1 : this.linkFor(h.from, h.to);
      this.seed = pt(`path-${e.id}-${c}`);
      const d = this.el("g", { class: "step", "data-step": c }, s);
      if (u < 0) {
        const l = this.drawLoose(d, h, e.steps, m, !!h.risk, this.T.step, c, this.el("g", { class: "step", "data-step": c }, this.layers.pathRing), n, [...a, ...f.filter((r, k) => r && k !== c)]);
        if (h.stop) {
          const r = this.boxOf(d);
          this.octagon(d, r.x - 12, r.y + 11);
        }
        this.wireStep(l, c, e), g.push(l);
      } else {
        const l = this.routes[u].pts, r = this.B.links[u].from === h.from ? l : [...l].reverse(), k = this.el("g", { class: "hl" }, d);
        this.R("k-hl", this.rc.path(this.arrowPath(r), this.O({ strokeWidth: 12, roughness: 0.5 })), k);
        const v = m === "red" ? "k-red" : m === "ink2" ? "k-ink2" : "k-marker";
        if (this.drawArrow(d, r, 2.8, h.risk ? [11, 8] : null, "fill", v, v.replace("k-", "fh-")), h.stop) {
          const $ = r.length > 2 ? r[Math.floor(r.length / 2)] : [(r[0][0] + r[1][0]) / 2, (r[0][1] + r[1][1]) / 2];
          this.octagon(d, $[0], $[1]);
        }
        if (!p.has(u)) {
          p.add(u);
          const $ = o[u].map((j) => {
            const M = e.steps[j];
            return { n: j, text: M.say, tone: M.risk ? "red" : M.stop ? "ink2" : "marker" };
          }), E = this.rowsOf($, this.T.step), W = n(E, l, a, !0);
          if (W) {
            const j = this.el("g", { class: "step", "data-step": c }, s);
            this.leader(j, W, "k-marker"), this.drawRows(j, W.r, E, "spine", (M, O) => this.wireStep(M, O.n, e)), g.push(j);
          }
        }
      }
      this.wireStep(d, c, e);
    }), s.append(...g);
  }
  boxOf(s) {
    const e = s.getBBox();
    return { x: e.x, y: e.y, w: e.width, h: e.height };
  }
  octagon(s, e, t) {
    const i = Array.from({ length: 8 }, (n, o) => [e + Math.cos(Math.PI / 8 + o * Math.PI / 4) * 10, t + Math.sin(Math.PI / 8 + o * Math.PI / 4) * 10]);
    this.el("path", { d: `M ${i.map((n) => n.join(" ")).join(" L ")} Z`, class: "paper" }, s), this.R("k-ink2", this.rc.polygon(i, this.O({ strokeWidth: 1.8, roughness: 0.6 })), s), this.R("k-ink2", this.rc.line(e - 5, t, e + 5, t, this.O({ strokeWidth: 2.4, roughness: 0.3 })), s);
  }
  hot(s) {
    this.st.hot = s;
    const e = this.st.story ? [this.layers.path, this.layers.pathRing] : [this.layers.ov];
    for (const t of e) t.querySelectorAll(".step").forEach((i) => i.classList.toggle("hot", Number(i.dataset.step) === s));
  }
  applyFocus() {
    if (!this.blockEls) return;
    const s = this.st.story && this.path(this.st.story), e = s ? new Set(s.steps.flatMap((t) => [t.from, t.to])) : null;
    for (const [t, i] of Object.entries(this.blockEls))
      i.g.classList.toggle("dim", !!e && !e.has(t)), i.g.classList.toggle("sel", t === this.st.sel);
    (this.linkEls || []).forEach((t, i) => t && t.classList.toggle("dim", !!s && !(this.litLinks && this.litLinks.has(i))));
    for (const t of this.labEls || []) t.g.classList.toggle("dim", !!s && !(this.litLinks && this.litLinks.has(t.i)));
    for (const t of [...this.linkEls || [], ...(this.labEls || []).map((i) => i.g)]) t && t.classList.toggle("lit", !!s && !!this.litLinks && this.litLinks.has(Number(t.dataset.link)));
    this.svg.querySelectorAll(".frame, .lanes, .thesis").forEach((t) => t.classList.toggle("dim", !!s)), this.svg.querySelectorAll(".part").forEach((t) => t.classList.toggle("on", t.dataset.part === this.st.part)), this.svg.querySelectorAll(".edge, .elab").forEach((t) => t.classList.toggle("on", !!this.st.part && (t.dataset.a === this.st.part || t.dataset.b === this.st.part))), this.svg.querySelector(".inside-art")?.classList.toggle("picked", !!this.st.part), this.root.classList.toggle("focused", !!this.st.shape), this.root.classList.toggle("inside", !!this.st.shape);
  }
  anatomy(s) {
    const e = this.K[s], t = e.members.filter((b) => this.items[b]), i = new Set(t), n = (b) => this.items[b] && this.items[b].type === "component", o = e.frame && this.F[e.frame] ? this.F[e.frame] : null, a = o && o.scope === "process" ? this.B.blocks.filter((b) => b.id !== s && b.frame === o.id).map((b) => b.id) : [], f = (b) => {
      if (i.has(b)) return b;
      const L = this.blockOf[b];
      return !L || L === s ? null : a.includes(L) ? `sib:${L}` : `ghost:${L}`;
    }, g = Object.values(this.flows).filter((b) => i.has(b.from) || i.has(b.to)), p = this.walkFlows(), h = e.kind === "worker", c = (b) => b.kind !== "control" && (h || b.critical || b.mode === "sync"), m = g.filter((b) => i.has(b.to) && n(b.to) && !i.has(b.from) && f(b.from) && b.kind !== "control"), u = m.filter((b) => p.has(b.id)).sort((b, L) => p.get(b.id) - p.get(L.id)), d = m.filter((b) => b.kind === "sync"), l = [...new Set((u.length ? u : d.length ? d : m).map((b) => b.to))], r = {}, k = (b) => {
      r[b] = 0;
      const L = [b];
      for (; L.length; ) {
        const T = L.shift();
        for (const P of g)
          P.from !== T || !i.has(P.to) || !n(P.to) || Object.hasOwn(r, P.to) || !c(P) || (r[P.to] = r[T] + 1, L.push(P.to));
      }
    };
    for (const b of l) Object.hasOwn(r, b) || k(b);
    !Object.keys(r).length && t.some(n) && k(t.find(n));
    const v = (b) => g.some((L) => p.has(L.id) && (L.from === b || L.to === b)), $ = Object.keys(r).sort((b, L) => r[b] - r[L] || (v(L) ? 1 : 0) - (v(b) ? 1 : 0) || t.indexOf(b) - t.indexOf(L)), E = new Set($), W = {};
    for (const b of t) {
      if (!n(b) || E.has(b)) continue;
      const L = g.filter((P) => P.kind === "control" && (P.from === b && E.has(P.to) || P.to === b && E.has(P.from)));
      if (!L.length) continue;
      const T = L.map((P) => P.from === b ? P.to : P.from).sort((P, Q) => r[P] - r[Q])[0];
      W[b] = T;
    }
    const j = t.filter((b) => this.items[b].type === "store" && this.items[b].medium === "memory"), M = t.filter((b) => n(b) && !E.has(b) && !W[b]), O = t.filter((b) => !n(b) && !j.includes(b));
    return { b: e, ids: t, mine: i, node: f, touching: g, path: $, depth: r, guards: W, heap: j, rest: M, others: O, sibs: a };
  }
  walkFlows() {
    return this.B.walks[this.st.story || ""];
  }
  walkSteps() {
    return this.st.story ? this.path(this.st.story).steps : this.B.spine.steps;
  }
  memberSize(s, e) {
    const t = this.items[s], i = e - 70, n = st(t.label, i, this.T.member), o = this.shapeOf(s), a = o && o.role ? st(o.role, i, this.T.mrole) : [], f = t.status !== "live", g = o && o.shape === "guard" ? 20 : 0;
    return { name: n, role: a, h: Math.max(78, 14 + n.length * this.T.member.lh + (a.length ? 4 + a.length * this.T.mrole.lh : 0) + (f ? 20 : 0) + 14 + g) };
  }
  memberPath(s, e) {
    const { x: t, y: i, w: n, h: o } = e;
    if (s === "gate") return `M ${t} ${i} L ${t + n} ${i + o * 0.12} L ${t + n} ${i + o * 0.88} L ${t} ${i + o} Z`;
    if (s === "relay") return `M ${t} ${i} H ${t + n - 16} L ${t + n} ${i + o / 2} L ${t + n - 16} ${i + o} H ${t} L ${t + 8} ${i + o / 2} Z`;
    if (s === "guard") return `M ${t} ${i} H ${t + n} V ${i + o - 24} Q ${t + n} ${i + o - 8} ${t + n / 2} ${i + o} Q ${t} ${i + o - 8} ${t} ${i + o - 24} Z`;
    const a = s === "watcher" ? Math.min(o / 2, 30) : s === "recovery" ? 18 : 0;
    return a ? `M ${t + a} ${i} H ${t + n - a} Q ${t + n} ${i} ${t + n} ${i + a} V ${i + o - a} Q ${t + n} ${i + o} ${t + n - a} ${i + o} H ${t + a} Q ${t} ${i + o} ${t} ${i + o - a} V ${i + a} Q ${t} ${i} ${t + a} ${i} Z` : `M ${t} ${i} H ${t + n} V ${i + o} H ${t} Z`;
  }
  memberW(s, e) {
    return Math.max(e, Math.ceil(Math.max(...this.items[s].label.split(/\s+/).map((t) => J(t, this.T.member))) + 78));
  }
  shapeOf(s) {
    return (this.B.parts || []).find((e) => e.part === s) || null;
  }
  ghostSize(s) {
    const e = this.K[s], t = e.kind === "actor" ? "actor" : this.sil(e), i = t === "actor" ? 132 : 158, [n, o] = { actor: [58, 12], chip: [13, 22], cylinder: [27, 18], bucket: [32, 16], key: [14, 14], pages: [14, 14] }[t] || [14, 14], a = t === "key" ? 44 : 14, f = st(e.name, i - a - 12, this.T.link);
    return { w: i, h: n + f.length * this.T.link.lh + o, name: f, sil: t, top: n, lead: a };
  }
  drawInside() {
    const s = this.layers.inside;
    s.replaceChildren(), this.inBox = null;
    const e = this.st.shape;
    if (!e || !this.K[e]) return;
    const t = this.anatomy(e), i = t.b, n = this.G.box[e], o = Yt.has(i.kind), a = this.walkFlows(), f = {}, g = {}, p = 60, h = 30, c = st(i.name, 520, this.T.title), m = st(i.role, 560, this.T.role), u = c.length * this.T.title.lh + m.length * this.T.role.lh + 50, d = /* @__PURE__ */ new Map();
    for (const y of t.touching) {
      const R = t.node(y.from), A = t.node(y.to);
      if (!R || !A || R === A) continue;
      const N = [R, A].sort().join("|");
      d.has(N) || d.set(N, { a: R, z: A, flows: [], back: !1, nums: [] });
      const C = d.get(N);
      C.flows.push(y), R !== C.a && (C.back = !0);
    }
    for (const [y, R] of a) {
      const A = this.flows[y];
      if (!A) continue;
      const N = t.node(A.from), C = t.node(A.to);
      if (N && C && N !== C) {
        const w = d.get([N, C].sort().join("|"));
        w && w.nums.push([R, A]);
        continue;
      }
      if (N && N === C && N.startsWith("ghost:")) {
        const w = [...d.values()].filter((S) => (S.a === N || S.z === N) && S.nums.length).sort((S, z) => z.flows.filter((B) => B.critical).length - S.flows.filter((B) => B.critical).length)[0];
        w && w.nums.push([R, A]);
      }
    }
    const l = [...d.values()];
    for (const y of l) {
      y.nums.sort((N, C) => N[0] - C[0]), y.strength = Math.max(...y.flows.map((N) => N.kind === "control" ? 0 : N.mode === "async" ? 1 : 2));
      const R = y.flows.find((N) => t.node(N.from) === y.a && a.has(N.id)) || y.flows.find((N) => t.node(N.from) === y.a) || y.flows[0], A = y.flows.map((N) => N.label).find(Boolean);
      y.size = y.nums.length ? this.rowsOf(y.nums.map(([N, C]) => ({ n: N - 1, text: C.label || this.walkSteps()[N - 1].say, tone: "marker" })), this.T.link, 104) : A ? this.rowsOf([{ text: R.label || A }], this.T.link, 104) : null, R && t.node(R.from) !== y.a && ([y.a, y.z] = [y.z, y.a]);
    }
    const r = new Set(t.path), k = (y) => y.nums.length > 0 || [y.a, y.z].some((R) => t.heap.includes(R) || R.startsWith("sib:")) || r.has(y.a) && r.has(y.z) || t.guards[y.a] === y.z || t.guards[y.z] === y.a || (r.has(y.a) || r.has(y.z)) && t.mine.has(y.a) && t.mine.has(y.z);
    for (const y of l) y.tier = k(y) ? 0 : 2;
    const v = (y) => y.strength * 10 + y.flows.filter((R) => R.critical).length;
    for (const y of new Set(l.flatMap((R) => [R.a, R.z]))) {
      if (r.has(y) || l.some((A) => A.tier < 2 && (A.a === y || A.z === y))) continue;
      const R = l.filter((A) => A.a === y || A.z === y).sort((A, N) => v(N) - v(A))[0];
      R && (R.tier = 1);
    }
    const $ = (y, R) => Math.max(0, ...l.filter((A) => A.size && A.tier < 2 && (A.a === y && A.z === R || A.a === R && A.z === y)).map((A) => A.size.w));
    if (o) {
      const y = t.ids.filter((S) => this.items[S].type === "store"), R = t.ids.filter((S) => this.items[S].type !== "store");
      let A = u;
      for (const S of y) {
        const z = this.contentsCard(this.items[S]);
        f[S] = { x: 0, y: A, w: z.w, h: z.h }, g[S] = { kind: "card", card: z }, A += z.h + p;
      }
      const N = Math.max(...y.map((S) => f[S].w)), C = Math.max(90, ...R.map((S) => Math.max(...y.map((z) => $(S, z) + 30))));
      let w = u;
      for (const S of R) {
        const z = this.memberW(S, 200), B = this.memberSize(S, z);
        f[S] = { x: N + C, y: w, w: z, h: B.h }, g[S] = { kind: "member", size: B }, w += B.h + p;
      }
    } else {
      const y = [];
      for (const F of t.path) (y[t.depth[F]] = y[t.depth[F]] || []).push(F);
      const R = {}, A = {};
      y.forEach((F, Z) => F.forEach((nt, lt) => {
        R[nt] = Z, A[nt] = lt;
      }));
      const N = {};
      for (const [F, Z] of Object.entries(t.guards)) (N[Z] = N[Z] || []).push(F);
      for (const [F, Z] of Object.entries(N)) {
        const nt = R[F], lt = Math.min(...y[nt].map((q) => A[q]));
        let ct = Math.max(...y[nt].map((q) => A[q])), D = lt;
        Z.forEach((q, Y) => {
          R[q] = nt, Y % 2 === 0 ? (D -= 1, A[q] = D) : (ct += 1, A[q] = ct), y[nt].push(q);
        });
      }
      const C = Object.keys(R), w = y.length, S = y.map((F) => Math.max(...F.map((Z) => this.memberW(Z, 196)))), z = {};
      for (const F of C) z[F] = this.memberSize(F, S[R[F]]);
      const B = [...new Set(C.map((F) => A[F]))].sort((F, Z) => F - Z), I = Object.fromEntries(B.map((F) => [F, Math.max(...C.filter((Z) => A[Z] === F).map((Z) => z[Z].h))])), H = [];
      for (let F = 0; F < w - 1; F++) {
        let Z = 64;
        for (const nt of y[F]) for (const lt of y[F + 1]) A[nt] === A[lt] && (Z = Math.max(Z, $(nt, lt) + 24));
        H[F] = Math.min(Z, 150);
      }
      const _ = [];
      let K = 0;
      for (let F = 0; F < w; F++)
        _[F] = K, K += S[F] + (F < w - 1 ? H[F] : 0);
      const wt = {};
      B.slice(0, -1).forEach((F, Z) => {
        const nt = B[Z + 1];
        let lt = 52;
        for (const ct of C) for (const D of C) A[ct] === F && A[D] === nt && R[ct] === R[D] && (lt = Math.max(lt, $(ct, D) ? l.find((q) => q.a === ct && q.z === D || q.a === D && q.z === ct).size.h + 22 : 0));
        wt[F] = Math.min(lt, 110);
      });
      const rt = {};
      let it = u;
      B.forEach((F) => {
        rt[F] = it, it += I[F] + (wt[F] || 0);
      });
      for (const F of C) {
        const Z = S[R[F]];
        f[F] = { x: _[R[F]], y: rt[F in A ? A[F] : 0] + (I[A[F]] - z[F].h) / 2, w: Z, h: z[F].h }, g[F] = { kind: "member", size: z[F] };
      }
      let at = it;
      const gt = t.heap;
      if (gt.length) {
        const F = C.filter((ht) => l.some(($t) => $t.a === ht && gt.includes($t.z) || $t.z === ht && gt.includes($t.a))).map((ht) => R[ht]);
        let Z = F.length ? Math.min(...F) : 0, nt = F.length ? Math.max(...F) : w - 1;
        nt === Z && w > 1 && (nt < w - 1 ? nt += 1 : Z -= 1);
        const lt = w ? _[Z] : 0, ct = Math.max(w ? _[nt] + S[nt] - lt : 0, 420), D = ct - 120 - this.pillW("in memory"), q = gt.map((ht) => {
          const $t = this.shapeOf(ht);
          return { m: ht, name: st(this.labelOf(ht), D, this.T.member), role: st($t && $t.role ? $t.role : this.items[ht].what || "", D, this.T.mrole) };
        });
        let Y = at + p - 12;
        for (const ht of q) {
          const $t = Math.max(70, 16 + ht.name.length * this.T.member.lh + ht.role.length * this.T.mrole.lh + 16);
          f[ht.m] = { x: lt, y: Y, w: ct, h: $t }, g[ht.m] = { kind: "heap", lines: ht }, Y += $t + 18;
        }
        at = Y - 18;
      }
      const Pt = [...[...t.rest, ...t.others].map((F) => ({ id: F, w: this.memberW(F, 196), h: this.memberSize(F, this.memberW(F, 196)).h })), ...t.sibs.map((F) => ({ id: `sib:${F}`, w: 226, h: this.sibSize(F).h }))];
      if (Pt.length) {
        const F = { ...f }, Z = (D) => {
          const q = l.filter((Y) => Y.a === D || Y.z === D).map((Y) => Y.a === D ? Y.z : Y.a).filter((Y) => F[Y]).map((Y) => F[Y].x + F[Y].w / 2);
          return q.length ? q.reduce((Y, ht) => Y + ht, 0) / q.length : null;
        };
        Pt.forEach((D, q) => {
          const Y = Z(D.id);
          D.want = Y ?? (D.id.startsWith("sib:") ? 1 / 0 : q * 1e3);
        }), Pt.sort((D, q) => (D.id.startsWith("sib:") ? 1 : 0) - (q.id.startsWith("sib:") ? 1 : 0) || D.want - q.want);
        const nt = at + p + 10, lt = Math.max(...Pt.map((D) => D.h));
        let ct = 0;
        for (const D of Pt) {
          const q = Number.isFinite(D.want) ? D.want - D.w / 2 : ct, Y = Math.max(ct, Math.min(q, ct + 40));
          f[D.id] = { x: Y, y: nt + (lt - D.h) / 2, w: D.w, h: D.h }, g[D.id] = D.id.startsWith("sib:") ? { kind: "sib", size: this.sibSize(D.id.slice(4)) } : { kind: "member", size: this.memberSize(D.id, D.w) }, ct = Y + D.w + 52;
        }
        at = nt + lt;
      }
    }
    const E = Object.values(f), W = Math.max(...E.map((y) => y.x + y.w), J(c[0], this.T.title) + 40), j = Math.max(...E.map((y) => y.y + y.h)), M = { x: -h, y: 0, w: W + h * 2, h: j + h }, O = [...new Set(l.flatMap((y) => [y.a, y.z]).filter((y) => y.startsWith("ghost:")))].map((y) => y.slice(6)), b = (y) => {
      if (this.K[y].kind === "actor") return !0;
      const A = t.touching.filter((C) => (this.blockOf[C.from] === y || this.blockOf[C.to] === y) && C.kind !== "control"), N = A.filter((C) => a.has(C.id)).sort((C, w) => a.get(C.id) - a.get(w.id))[0] || A.find((C) => C.kind === "sync");
      return N ? this.blockOf[N.from] === y && t.path.includes(N.to) : this.G.col[y] < this.G.col[e];
    }, L = (y) => {
      const R = l.filter((C) => C.a === `ghost:${y}` || C.z === `ghost:${y}`).map((C) => C.a === `ghost:${y}` ? C.z : C.a).filter((C) => f[C]), A = R.filter((C) => t.path.includes(C)).sort((C, w) => t.depth[C] - t.depth[w]), N = (b(y) && A.length ? A.slice(0, 1) : R).map((C) => f[C].y + f[C].h / 2);
      return N.length ? N.reduce((C, w) => C + w, 0) / N.length : j / 2;
    }, T = (y) => bt(Math.max(0, ...l.filter((R) => R.size && R.tier < 2 && y.some((A) => R.a === `ghost:${A}` || R.z === `ghost:${A}`)).map((R) => R.size.w)) + 24, 64, 160), P = O.filter(b), Q = O.filter((y) => !b(y)), V = (y, R) => {
      const A = y.map((C) => ({ o: C, s: this.ghostSize(C), c: L(C) })).sort((C, w) => C.c - w.c);
      let N = -1 / 0;
      for (const C of A) {
        const w = Math.max(N + 26, C.c - C.s.h / 2);
        f[`ghost:${C.o}`] = { x: R(C.s.w), y: w, w: C.s.w, h: C.s.h }, g[`ghost:${C.o}`] = { kind: "ghost", size: C.s, o: C.o }, N = w + C.s.h;
      }
    }, tt = T(P), ot = T(Q);
    V(P, (y) => M.x - tt - y), V(Q, () => M.x + M.w + ot);
    const G = Object.values(f), X = Math.min(M.x, ...G.map((y) => y.x)) - 30, mt = Math.min(0, ...G.map((y) => y.y)) - 26, kt = Math.max(M.x + M.w, ...G.map((y) => y.x + y.w)) + 30, Tt = Math.max(M.y + M.h, ...G.map((y) => y.y + y.h)) + 30, Mt = kt - X, Et = Tt - mt, et = Math.min(n.w / Mt, n.h / Et) * 1.25, Rt = [n.x + n.w / 2, n.y + n.h / 2], It = Rt[0] - Mt * et / 2 - X * et, Wt = Rt[1] - Et * et / 2 - mt * et, dt = this.el("g", { class: "inside-art", transform: `translate(${It} ${Wt}) scale(${et})` }, s);
    this.inK = et, this.inBox = { x: It + X * et, y: Wt + mt * et, w: Mt * et, h: Et * et };
    const zt = t.path[0] && f[t.path[0]], At = P.length ? f[`ghost:${P[0]}`] : zt;
    this.inAnchor = { x: It + (At ? Math.min(At.x, M.x) - 30 : X) * et, y: Wt + (zt ? zt.y + zt.h / 2 : Et / 2) * et }, this.seed = pt(`inside-${e}`);
    const St = i.frame && this.F[i.frame] && this.F[i.frame].scope === "process" ? this.F[i.frame].label : null;
    this.R("k-ink3", this.rc.rectangle(M.x, M.y, M.w, M.h, this.O({ strokeWidth: 1.8, roughness: 1.2, strokeLineDash: [7, 6] })), dt);
    const Dt = this.el("g", { class: "in-title" }, dt);
    this.text(Dt, M.x + 22, 40, c, this.T.title, ""), this.text(Dt, M.x + 22, 40 + (c.length - 1) * this.T.title.lh + 28, m, this.T.role, "t-ink2");
    const jt = [{ x: M.x + 14, y: 8, w: Math.max(J(c[0], this.T.title), ...m.map((y) => J(y, this.T.role))) + 20, h: u - 30 }];
    if (St) {
      const y = St.toUpperCase(), R = J(y, this.T.lane) + 16, A = this.text(dt, M.x + M.w - R - 8, 24, [y], this.T.lane, "t-caps t-ink3");
      A.setAttribute("letter-spacing", "1.2"), A.classList.add("in-tag"), jt.push({ x: M.x + M.w - R - 14, y: 8, w: R + 12, h: 24 });
    }
    const Ot = {};
    for (const [y, R] of Object.entries(f))
      jt.push(U(R, 5)), (g[y].kind === "member" || g[y].kind === "card" || g[y].kind === "heap") && (Ot[y] = this.marksOf(y, R), jt.push(...Ot[y].map((A) => U(A, 2))));
    const Ct = [], Lt = [], yt = [...new Set(Object.values(f).flatMap((y) => [y.x, y.x + y.w]))].sort((y, R) => y - R);
    for (let y = 0; y < yt.length - 1; y++) yt[y + 1] - yt[y] > 30 && !Object.values(f).some((R) => R.x < yt[y] + 1 && R.x + R.w > yt[y + 1] - 1) && Ct.push((yt[y] + yt[y + 1]) / 2);
    Ct.push(X + 14, kt - 14);
    const xt = [...new Set(Object.values(f).flatMap((y) => [y.y, y.y + y.h]))].sort((y, R) => y - R);
    for (let y = 0; y < xt.length - 1; y++) xt[y + 1] - xt[y] > 26 && !Object.values(f).some((R) => R.y < xt[y] + 1 && R.y + R.h > xt[y + 1] - 1) && Lt.push((xt[y] + xt[y + 1]) / 2);
    Lt.push(Tt - 14, u - 11, mt + 14);
    const Nt = ys(f, Ct, Lt, jt.slice(0, St ? 2 : 1)), Qt = [], qt = [...l].sort((y, R) => y.tier - R.tier || R.nums.length - y.nums.length || R.strength - y.strength || (y.a.startsWith("ghost:") ? 1 : 0) - (R.a.startsWith("ghost:") ? 1 : 0)), ut = /* @__PURE__ */ new Map();
    for (const y of qt) {
      const R = Nt(y.a, y.z, Qt);
      ut.set(y, R), Qt.push(R.pts);
    }
    xs([...ut.values()]);
    const Ut = this.el("g", {}, dt), Jt = this.el("g", {}, dt), vt = this.el("g", {}, dt), Ht = this.el("g", {}, dt);
    for (const [y, R] of Object.entries(f)) {
      const A = g[y];
      A.kind === "member" ? this.drawMember(vt, y, R, A.size, Ot[y]) : A.kind === "card" ? this.drawCard(vt, { id: y, ...R, ...A.card }, Ot[y]) : A.kind === "heap" ? this.drawHeap(vt, y, R, A.lines, Ot[y]) : A.kind === "sib" ? this.drawSib(vt, y.slice(4), R, A.size) : A.kind === "ghost" && this.drawGhost(Ut, A.o, R, A.size);
    }
    const Vt = hs(jt), Kt = [...ut.values()].map((y) => y.pts);
    for (const y of qt) {
      const R = ut.get(y), A = y.nums.length > 0, N = y.a.startsWith("ghost:") || y.z.startsWith("ghost:"), C = y.tier === 2, w = this.el("g", { class: `edge${A ? " lit" : ""}${C ? " faint" : ""}`, "data-a": y.a, "data-b": y.z }, Jt);
      this.el("path", { d: this.arrowPath(R.pts), class: "hit" }, w);
      const S = A ? "k-marker" : C || y.strength === 0 ? "k-ink3" : y.strength === 1 || N ? "k-ink2" : "k-ink";
      if (this.seed = pt(`edge-${y.a}-${y.z}`), this.drawArrow(w, R.pts, A ? 2.6 : y.strength === 0 ? 1.5 : 1.9, A ? null : y.strength === 0 ? [2, 6] : y.strength === 1 ? [9, 7] : null, y.strength === 2 || A ? "fill" : "open", S, S.replace("k-", "fh-")), y.back && this.drawArrow(w, [...R.pts].reverse().slice(0, 2), 1.4, null, "open", S, S.replace("k-", "fh-")), !y.size) continue;
      const z = Vt(y.size, R.pts, Kt.filter((I) => I !== R.pts));
      if (!z) continue;
      const B = this.el("g", { class: `elab${A ? " lit" : ""}${C ? " faint" : ""}`, "data-a": y.a, "data-b": y.z }, Ht);
      z.leader && this.R("k-ink3", this.rc.line(z.leader[0][0], z.leader[0][1], z.leader[1][0], z.leader[1][1], this.O({ strokeWidth: 1, roughness: 0.4, strokeLineDash: [3, 4] })), B), this.drawRows(B, z.r, y.size, A ? "spine" : "plain"), this.el("title", {}, B).textContent = y.flows.map((I) => I.label).filter(Boolean).join("; ");
    }
    for (const y of this.B.callouts || []) {
      const R = st(y.text, 170, this.T.note), A = Math.max(...R.map((I) => J(I, this.T.note))) + 24, N = R.length * this.T.note.lh + 18;
      let C = null;
      if (y.link) {
        const I = qt.find((_) => _.a === y.link[0] && _.z === y.link[1] || _.a === y.link[1] && _.z === y.link[0]), H = I && Vt({ w: A, h: N }, ut.get(I).pts, Kt);
        H && H.leader && this.R("k-ink3", this.rc.line(H.leader[0][0], H.leader[0][1], H.leader[1][0], H.leader[1][1], this.O({ strokeWidth: 1, roughness: 0.4, strokeLineDash: [3, 4] })), Ht), C = H && H.r;
      } else {
        const I = f[y.source] || (y.block === e ? f[t.path[0]] : null);
        if (!I) continue;
        C = Vt.around({ w: A, h: N }, I, Kt, M);
      }
      if (!C) continue;
      this.drawSticky(Ht, C, R, y.text);
      const w = It + (C.x - 30) * et, S = Wt + (C.y - 30) * et, z = Math.max(this.inBox.x + this.inBox.w, It + (C.x + C.w + 30) * et), B = Math.max(this.inBox.y + this.inBox.h, Wt + (C.y + C.h + 30) * et);
      this.inBox = { x: Math.min(this.inBox.x, w), y: Math.min(this.inBox.y, S), w: 0, h: 0 }, this.inBox.w = z - this.inBox.x, this.inBox.h = B - this.inBox.y, this.inAnchor.x = Math.min(this.inAnchor.x, w);
    }
  }
  sibSize(s) {
    const e = this.K[s], t = st(e.name, 200, this.T.member), i = st(e.role, 200, this.T.mrole);
    return { name: t, role: i, h: 16 + t.length * this.T.member.lh + 4 + i.length * this.T.mrole.lh + 44 };
  }
  marksOf(s, e) {
    return this.markList(this.items[s].badges, e.x + e.w - 6, e.y - 8, this.T.badge);
  }
  drawMarks(s, e, t) {
    for (const i of t) this.drawMark(s, i, this.labelOf(e));
  }
  bubblePath(s, e, t, i) {
    this.el("path", { d: `M ${e + 6} ${t} H ${e + i - 6} Q ${e + i} ${t} ${e + i} ${t + 6} V ${t + 15} Q ${e + i} ${t + 21} ${e + i - 6} ${t + 21} H ${e + 15} L ${e + 8} ${t + 27} L ${e + 9} ${t + 21} H ${e + 6} Q ${e} ${t + 21} ${e} ${t + 15} V ${t + 6} Q ${e} ${t} ${e + 6} ${t} Z` }, s);
  }
  drawSticky(s, e, t, i) {
    this.seed = pt(`note-${i}`);
    const n = this.el("g", { class: "note", transform: `rotate(${this.skin.clean ? 0 : pt(i) % 3 - 1} ${e.x + e.w / 2} ${e.y + e.h / 2})` }, s);
    return this.R("f-sticky", this.rc.rectangle(e.x, e.y, e.w, e.h, this.O({ stroke: "none", fill: "#000", fillStyle: "solid" })), n), this.R("k-ink3", this.rc.rectangle(e.x, e.y, e.w, e.h, this.O({ strokeWidth: 1, roughness: 0.8 })), n), this.text(n, e.x + 12, e.y + 9 + this.T.note.size - 1, t, this.T.note, "t-sticky"), n;
  }
  contentsCard(s) {
    const e = s.holds.map((o) => {
      const a = st(o.text, 340, this.T.hold), f = o.note ? st(o.note, 340, this.T.small) : [];
      return { h: o, what: a, sub: f, height: a.length * this.T.hold.lh + f.length * this.T.small.lh + 14 };
    }), t = st(s.label, s.tag ? 300 : 380, this.T.member), i = 22 + t.length * this.T.member.lh;
    return { w: 420, h: i + e.reduce((o, a) => o + a.height, 0) + 12, rows: e, st: s, head: t, top: i };
  }
  drawCard(s, e, t) {
    const i = this.el("g", { class: "part contents", "data-part": e.id, role: "button", tabindex: 0, "aria-label": e.st.label }, s);
    this.el("rect", { class: "paper", x: e.x, y: e.y, width: e.w, height: e.h, rx: 8 }, i);
    const n = this.el("g", { class: "sel" }, i);
    this.R("k-marker", this.rc.rectangle(e.x - 5, e.y - 5, e.w + 10, e.h + 10, this.O({ strokeWidth: 3, roughness: 1.6 })), n), this.R("k-ink", this.rc.rectangle(e.x, e.y, e.w, e.h, this.O({ strokeWidth: 1.8 })), i), this.text(i, e.x + 14, e.y + 28, e.head, this.T.member, ""), e.st.tag && this.pill(i, e.x + e.w - 14 - this.pillW(e.st.tag), e.y + 14, e.st.tag, "");
    let o = e.y + e.top;
    for (const a of e.rows) {
      a.h.sealed != null && this.lock(i, e.x + 22, o + 9, a.h.sealed), this.text(i, e.x + 40, o + 13, a.what, this.T.hold, "");
      let f = o + 13 + (a.what.length - 1) * this.T.hold.lh;
      for (const g of a.sub)
        f += this.T.small.lh, this.text(i, e.x + 40, f, [g], this.T.small, "t-ink3");
      o += a.height;
    }
    this.drawMarks(i, e.id, t || []), this.hoverEdges(i, e.id), i.addEventListener("click", (a) => {
      this.dragMoved || (a.stopPropagation(), this.pickPart(e.id));
    }), i.addEventListener("keydown", (a) => {
      a.key === "Enter" && (a.preventDefault(), a.stopPropagation(), this.pickPart(e.id));
    });
  }
  hoverEdges(s, e) {
    const t = (i) => {
      this.svg.querySelector(".inside-art")?.classList.toggle("hovering", i), this.svg.querySelectorAll(".edge, .elab").forEach((n) => {
        (n.dataset.a === e || n.dataset.b === e) && n.classList.toggle("hov", i);
      });
    };
    s.addEventListener("mouseenter", () => t(!0)), s.addEventListener("mouseleave", () => t(!1));
  }
  lock(s, e, t, i) {
    this.R(i ? "k-green" : "k-red", this.rc.rectangle(e - 6, t - 2, 12, 10, this.O({ strokeWidth: 1.4, roughness: 0.5 })), s), this.R(i ? "k-green" : "k-red", this.rc.path(i ? `M ${e - 4} ${t - 2} L ${e - 4} ${t - 6} Q ${e} ${t - 11} ${e + 4} ${t - 6} L ${e + 4} ${t - 2}` : `M ${e - 4} ${t - 2} L ${e - 4} ${t - 6} Q ${e} ${t - 11} ${e + 4} ${t - 7}`, this.O({ strokeWidth: 1.4, roughness: 0.4 })), s);
  }
  drawMember(s, e, t, i, n) {
    const o = this.items[e], a = this.el("g", { class: "part", "data-part": e, role: "button", tabindex: 0, "aria-label": `${o.label}${this.shapeOf(e) ? `: ${this.shapeOf(e).role}` : ""}` }, s);
    this.el("rect", { class: "paper box", x: t.x, y: t.y, width: t.w, height: t.h, rx: 6 }, a);
    const f = this.el("g", { class: "sel" }, a);
    this.R("k-marker", this.rc.rectangle(t.x - 5, t.y - 5, t.w + 10, t.h + 10, this.O({ strokeWidth: 3, roughness: 1.6 })), f);
    const g = o.status !== "live", p = o.type === "actor", h = o.type === "store" ? null : (this.shapeOf(e) || {}).shape || "module";
    if (this.seed = pt(`member-${e}`), h === "pool") for (const d of [10, 5]) this.R("k-ink3", this.rc.rectangle(t.x + d, t.y - d, t.w, t.h, this.O({ strokeWidth: 1.2 })), a);
    const c = this.memberPath(h, t), m = this.O({ strokeWidth: 2, ...g || p ? { strokeLineDash: [7, 6] } : {} });
    if (h === "handoff" && (this.el("path", { class: "paper", d: this.memberPath(null, { ...t, x: t.x - 9, y: t.y + 9 }) }, a), this.R("k-ink3", this.rc.path(this.memberPath(null, { ...t, x: t.x - 9, y: t.y + 9 }), this.O({ strokeWidth: 1.4, strokeLineDash: [6, 5] })), a)), this.el("path", { class: "paper", d: c }, a), this.R(p ? "k-ink2" : "k-ink", this.rc.path(c, m), a), h === "queue") for (const d of [0.52, 0.66, 0.8]) this.R("k-ink2", this.rc.line(t.x + t.w * d, t.y, t.x + t.w * d, t.y + 9, this.O({ strokeWidth: 1.6, roughness: 0.4 })), a);
    h === "recovery" && this.R("k-ink2", this.rc.path(`M ${t.x + t.w - 30} ${t.y - 6} A 16 16 0 1 1 ${t.x + t.w + 6} ${t.y + 26}`, this.O({ strokeWidth: 1.6, roughness: 0.6 })), a), h === "handoff" && this.drawArrow(a, [[t.x - 4, t.y + t.h + 4], [t.x + 22, t.y + t.h + 4]], 1.6, null, "fill", "k-ink2", "fh-ink2"), o.type === "store" ? this.storeGlyph(o.medium, a, t.x + 32, t.y + 38, 40, "k-ink2") : this.glyph(h, a, t.x + 32, t.y + 38, 40, "k-ink2");
    let u = t.y + 14 + this.T.member.size - 2;
    this.text(a, t.x + 62, u, i.name, this.T.member, ""), u += (i.name.length - 1) * this.T.member.lh + 4 + this.T.mrole.lh, i.role.length && this.text(a, t.x + 62, u, i.role, this.T.mrole, "t-ink2"), g && this.pill(a, t.x + 62, t.y + t.h - 26, o.status, "warn"), this.drawMarks(a, e, n || []), this.hoverEdges(a, e), a.addEventListener("click", (d) => {
      this.dragMoved || (d.stopPropagation(), this.pickPart(e));
    }), a.addEventListener("keydown", (d) => {
      d.key === "Enter" && (d.preventDefault(), d.stopPropagation(), this.pickPart(e));
    });
  }
  drawHeap(s, e, t, i, n) {
    const o = this.items[e], a = this.el("g", { class: "part heap", "data-part": e, role: "button", tabindex: 0, "aria-label": `${o.label}, inside the process` }, s);
    this.seed = pt(`heap-${e}`);
    const f = this.el("g", { class: "sel" }, a);
    this.R("k-marker", this.rc.rectangle(t.x - 5, t.y - 5, t.w + 10, t.h + 10, this.O({ strokeWidth: 3, roughness: 1.6 })), f), this.el("rect", { class: "paper2", x: t.x, y: t.y, width: t.w, height: t.h, rx: 10 }, a), this.R("f-green", this.rc.rectangle(t.x + 3, t.y + 3, t.w - 6, t.h - 6, this.O({ stroke: "none", fill: "#000", fillStyle: "hachure", hachureGap: 11, hachureAngle: 60, fillWeight: 0.8 })), a), this.R("k-ink2", this.rc.rectangle(t.x, t.y, t.w, t.h, this.O({ strokeWidth: 1.8, roughness: 1.1, strokeLineDash: [10, 5] })), a), this.storeGlyph("memory", a, t.x + 44, t.y + t.h / 2 - 4, 52, "k-ink2"), this.text(a, t.x + 96, t.y + 14 + this.T.member.size - 2, i.name, this.T.member, ""), this.text(a, t.x + 96, t.y + 14 + this.T.member.size - 2 + (i.name.length - 1) * this.T.member.lh + 4 + this.T.mrole.lh, i.role, this.T.mrole, "t-ink2"), this.pill(a, t.x + t.w - 14 - this.pillW("in memory"), t.y + t.h - 28, "in memory", ""), this.drawMarks(a, e, n || []), this.hoverEdges(a, e), a.addEventListener("click", (g) => {
      this.dragMoved || (g.stopPropagation(), this.pickPart(e));
    }), a.addEventListener("keydown", (g) => {
      g.key === "Enter" && (g.preventDefault(), g.stopPropagation(), this.pickPart(e));
    });
  }
  drawSib(s, e, t, i) {
    const n = this.K[e], o = this.el("g", { class: "sibcard", "data-sib": e, role: "button", tabindex: 0, "aria-label": `Look inside ${n.name}, in the same process` }, s);
    this.seed = pt(`sib-${e}`), this.el("rect", { class: "paper", x: t.x, y: t.y, width: t.w, height: t.h, rx: 8 }, o), this.R("k-ink2", this.rc.rectangle(t.x, t.y, t.w, t.h, this.O({ strokeWidth: 1.7, strokeLineDash: [8, 5] })), o), this.text(o, t.x + 14, t.y + 14 + this.T.member.size - 2, i.name, this.T.member, ""), this.text(o, t.x + 14, t.y + 14 + this.T.member.size - 2 + (i.name.length - 1) * this.T.member.lh + 4 + this.T.mrole.lh, i.role, this.T.mrole, "t-ink2"), n.members.filter((f) => this.items[f] && this.items[f].type === "component").slice(0, 5).forEach((f, g) => this.glyph((this.shapeOf(f) || {}).shape || "module", o, t.x + 26 + g * 40, t.y + t.h - 22, 26, "k-ink3")), o.addEventListener("click", (f) => {
      this.dragMoved || (f.stopPropagation(), this.goTo(e));
    }), o.addEventListener("keydown", (f) => {
      (f.key === "Enter" || f.key === " ") && (f.preventDefault(), this.goTo(e));
    });
  }
  drawGhost(s, e, t, i) {
    const n = this.K[e], o = this.el("g", { class: "ghostcard", role: "button", tabindex: 0, "aria-label": `Look inside ${n.name}` }, s);
    this.seed = pt(`ghost-${e}`);
    const a = this.doodle(), f = { cls: "k-ink2", strokeWidth: 1.7, roughness: 1 };
    this.el("rect", { class: "paper", x: t.x, y: t.y, width: t.w, height: t.h, rx: 8 }, o), i.sil === "actor" ? (this.R("k-ink2", this.rc.rectangle(t.x, t.y, t.w, t.h, this.O({ strokeWidth: 1.6, strokeLineDash: [7, 5] })), o), a.person(o, t.x + t.w / 2 + 18, t.y + 10, 0.72), this.R("k-ink2", this.rc.rectangle(t.x + t.w / 2 - 22, t.y + 16, 17, 28, this.O({ strokeWidth: 1.4 })), o), this.R("k-ink2", this.rc.rectangle(t.x + t.w / 2 - 44, t.y + 22, 15, 22, this.O({ strokeWidth: 1.3 })), o)) : i.sil === "chip" ? this.outline("chip", o, { x: t.x, y: t.y, w: t.w, h: t.h - 9 }, f) : i.sil === "cylinder" || i.sil === "bucket" || i.sil === "pages" ? this.outline(i.sil, o, t, f) : (this.outline("key", o, t, { ...f, strokeLineDash: i.sil === "key" ? null : [7, 5] }), i.sil === "key" && a.key(o, t.x + 14, t.y + t.h / 2, 0.3)), this.text(o, t.x + i.lead, t.y + i.top + this.T.link.size - 3, i.name, this.T.link, "t-ink2"), o.addEventListener("click", (g) => {
      this.dragMoved || (g.stopPropagation(), this.goTo(e));
    }), o.addEventListener("keydown", (g) => {
      (g.key === "Enter" || g.key === " ") && (g.preventDefault(), this.goTo(e));
    });
  }
  refresh() {
    if (this.gone || !this.M) return;
    const s = this.canvas.getBoundingClientRect().width;
    if (!s) return;
    let e = s < Ds ? "tall" : "wide", t = !1, i = !1;
    const n = !this.o.dialog && te();
    if (this.o.dialog && e === "tall" && (i = s / this.sizeOf("tall", !1, !1).w < Ft), !this.o.dialog && !n && !this.o.fill) {
      const o = this.maxH(), a = this.sizeOf(e, !1, !1);
      t = Math.min(s / a.w, o / a.h) < Ft;
      const f = gs / this.skin.glimpse.name.size;
      if (t && e === "wide") {
        const g = this.sizeOf("wide", !0, !1);
        Math.min(s / g.w, o / g.h) < f && (e = "tall");
      }
      t && e === "tall" && (i = s / this.sizeOf("tall", !0, !1).w < f);
    }
    t !== !!this.mode && (t ? (this.want = this.view(), this.clearView()) : this.want && (this.clearView(), this.applyView(this.want), this.want = null), this.mode = t, this.key = null), this.root.classList.toggle("compact", t), this.root.classList.toggle("printing", n), this.layoutKey(e, t, i) !== this.key && this.lay(e, t, i), this.renderChips(), this.renderCrumbs(), this.renderReadout(), this.o.dialog || this.sizeCanvas(n), cancelAnimationFrame(this.anim), this.setVB(n ? this.fitBox() : this.st.shape && this.inBox ? this.focusBox() : this.home()), this.settled && this.markReady();
  }
  setCompact(s) {
    this.o.compact = s, this.ty = s ? this.skin.glimpse : this.skin.ty;
  }
  layoutKey(s, e, t) {
    return `${this.rev}|${s}|${Rs}|${e}|${t}`;
  }
  lay(s, e, t) {
    this.key = this.layoutKey(s, e, t), this.st.layout = s, this.st.stack = t, this.setCompact(e), this.render();
  }
  sizeOf(s, e, t) {
    const i = this.layoutKey(s, e, t);
    if (!this.full[i]) {
      i !== this.key && this.lay(s, e, t);
      const n = e ? 40 : 32;
      this.full[i] = { w: this.G.ext.w + n * 2, h: this.G.ext.h + n * 2 };
    }
    return this.full[i];
  }
  maxH() {
    const s = this.canvas, e = s.style.height;
    s.style.height = "100000px";
    const t = s.getBoundingClientRect().height;
    return s.style.height = e, t;
  }
  sizeCanvas(s) {
    const e = this.G.ext, t = this.o.compact ? 40 : 32, i = e.w + t * 2, n = e.h + t * 2;
    if (this.canvas.style.aspectRatio = s ? `${Math.round(i)} / ${Math.round(n)}` : "", s) {
      this.canvas.style.height = "auto";
      return;
    }
    const o = this.canvas.getBoundingClientRect().width;
    this.canvas.style.height = `${Math.round(this.o.fill ? this.maxH() : Math.min(this.maxH(), o * n / i, n * 1.25))}px`;
  }
  center(s, e) {
    const t = this.canvas.getBoundingClientRect(), i = t.width / e, n = t.height / e;
    return { x: s.x + s.w / 2 - i / 2, y: s.y + s.h / 2 - n / 2, w: i, h: n };
  }
  home() {
    const s = this.canvas.getBoundingClientRect();
    if (!s.width || !s.height || this.o.compact) return this.fitBox();
    const e = this.st.story && this.path(this.st.story), t = 32, i = e ? this.storyBox(e) : null, n = i ? this.union([this.G.ext, i]) : this.G.ext, o = { x: n.x - t, y: n.y - t, w: n.w + t * 2, h: n.h + t * 2 }, a = Math.min(1.25, s.width / o.w, s.height / o.h);
    if (a >= Ft) return this.center(o, a);
    const f = this.st.layout === "tall", g = this.G.thesis, p = U(this.union([...Object.values(this.G.box), ...this.G.frames.map((l) => l.rect), ...g.lines.length ? [{ x: g.x, y: g.y, w: Math.max(...g.lines.map((l) => J(l, this.ty.thesis))), h: g.lines.length * this.ty.thesis.lh }] : []]), t), h = s.width / o.w >= Ft ? o : p, c = f ? Math.min(s.width / h.w, 1.25) : Ft, m = s.width / c, u = s.height / c;
    if (i && (i.w + t * 2 > m || i.h + t * 2 > u)) return this.fitAspect(U(i, t));
    const d = (l, r, k) => k < r ? (r + k) / 2 : bt(l, r, k);
    return {
      x: f && !i ? h.x + h.w / 2 - m / 2 : d(i ? i.x + i.w / 2 - m / 2 : o.x, o.x, o.x + o.w - m),
      y: d(i ? i.y + i.h / 2 - u / 2 : o.y, o.y, o.y + o.h - u),
      w: m,
      h: u
    };
  }
  union(s) {
    const e = Math.min(...s.map((i) => i.x)), t = Math.min(...s.map((i) => i.y));
    return { x: e, y: t, w: Math.max(...s.map((i) => i.x + i.w)) - e, h: Math.max(...s.map((i) => i.y + i.h)) - t };
  }
  storyBox(s) {
    const e = [...new Set(s.steps.flatMap((i) => [i.from, i.to]))].map((i) => this.G.box[i]).filter(Boolean), t = this.layers.path.getBBox();
    return t.width && e.push({ x: t.x, y: t.y, w: t.width, h: t.height }), this.union(e);
  }
  fitBox() {
    const s = this.canvas.getBoundingClientRect(), e = this.o.compact ? 40 : 32, t = this.G.ext, i = { x: t.x - e, y: t.y - e, w: t.w + e * 2, h: t.h + e * 2 };
    if (!s.width || !s.height) return i;
    const n = Math.min(s.width / i.w, s.height / i.h);
    if (!this.o.compact) return this.center(i, Math.min(n, 1.25));
    const o = gs / this.ty.name.size;
    if (n >= o) return this.center(i, Math.min(n, 1.25));
    const a = Math.min(1, Math.max(o, s.width / i.w, s.height / i.h)), f = s.width / a, g = s.height / a;
    return { x: i.w > f ? i.x : i.x - (f - i.w) / 2, y: i.h > g ? i.y : i.y - (g - i.h) / 2, w: f, h: g };
  }
  fitAspect(s) {
    const e = this.canvas.getBoundingClientRect();
    if (!e.width || !e.height) return s;
    const t = e.width / e.height;
    let { x: i, y: n, w: o, h: a } = s;
    if (o / a > t) {
      const f = o / t;
      n -= (f - a) / 2, a = f;
    } else {
      const f = a * t;
      i -= (f - o) / 2, o = f;
    }
    return { x: i, y: n, w: o, h: a };
  }
  focusFit() {
    const s = this.inBox;
    return this.fitAspect({ x: s.x - s.w * 0.03, y: s.y - s.h * 0.03, w: s.w * 1.06, h: s.h * 1.06 });
  }
  focusBox() {
    const s = this.focusFit(), e = this.canvas.getBoundingClientRect();
    if (!e.width) return s;
    const t = e.width / s.w * this.inK;
    if (t >= Ft) return s;
    const i = t / Ft, n = s.w * i, o = s.h * i, a = this.inBox, f = a.y - a.h * 0.03;
    return { x: this.inAnchor.x, y: this.inAnchor.y - f < o * 0.75 ? f : bt(this.inAnchor.y - o * 0.42, f, a.y + a.h * 1.03 - o), w: n, h: o };
  }
  setVB(s) {
    if (this.st.vb = s, this.svg.setAttribute("viewBox", `${s.x} ${s.y} ${s.w} ${s.h}`), !this.G) return;
    const e = this.canvas.getBoundingClientRect().width;
    e && this.dots.setAttribute("patternTransform", `scale(${Math.min(1, Ft * s.w / e)})`);
    const t = this.st.shape && this.inBox ? this.inBox : this.G.ext, i = { fl: t.x < s.x - 4, ft: t.y < s.y - 4, fr: t.x + t.w > s.x + s.w + 4, fb: t.y + t.h > s.y + s.h + 4 };
    for (const [n, o] of Object.entries(i)) this.svg.style.setProperty(`--${n}`, o ? n === "fl" || n === "fr" ? "44px" : "32px" : "0px");
    this.root.classList.toggle("cut", Object.values(i).some(Boolean));
  }
  animateTo(s, e) {
    if (cancelAnimationFrame(this.anim), this.root.classList.contains("still") || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      this.setVB(s);
      return;
    }
    const t = { ...this.st.vb }, i = performance.now(), n = e || 520, o = (f) => f < 0.5 ? 4 * f * f * f : 1 - Math.pow(-2 * f + 2, 3) / 2, a = (f) => {
      const g = Math.min(1, (f - i) / n), p = o(g);
      this.setVB({ x: t.x + (s.x - t.x) * p, y: t.y + (s.y - t.y) * p, w: t.w + (s.w - t.w) * p, h: t.h + (s.h - t.h) * p }), g < 1 && (this.anim = requestAnimationFrame(a));
    };
    this.anim = requestAnimationFrame(a);
  }
  interactions() {
    const s = this.svg, e = /* @__PURE__ */ new Map();
    let t = null;
    const i = (a, f) => {
      const g = this.canvas.getBoundingClientRect(), p = this.st.vb;
      return [p.x + (a - g.left) / g.width * p.w, p.y + (f - g.top) / g.height * p.h];
    };
    s.addEventListener("pointerdown", (a) => {
      !this.st.vb || this.o.compact || (e.set(a.pointerId, [a.clientX, a.clientY]), this.dragMoved = !1, t = { vb: { ...this.st.vb }, pts: new Map(e) });
    }), s.addEventListener("pointermove", (a) => {
      if (!e.has(a.pointerId) || !t) return;
      e.set(a.pointerId, [a.clientX, a.clientY]);
      const f = this.canvas.getBoundingClientRect(), g = t.vb.w / f.width;
      if (e.size === 1) {
        const [p, h] = t.pts.get(a.pointerId) || [a.clientX, a.clientY], c = a.clientX - p, m = a.clientY - h;
        Math.hypot(c, m) > 5 && (this.dragMoved = !0, s.classList.add("dragging"), s.hasPointerCapture(a.pointerId) || s.setPointerCapture(a.pointerId)), this.dragMoved && (cancelAnimationFrame(this.anim), this.setVB({ ...t.vb, x: t.vb.x - c * g, y: t.vb.y - m * g }));
      } else if (e.size === 2 && t.pts.size === 2) {
        const [p, h] = [...e.values()], [c, m] = [...t.pts.values()], u = Math.hypot(c[0] - m[0], c[1] - m[1]) / Math.max(Math.hypot(p[0] - h[0], p[1] - h[1]), 1), d = (c[0] + m[0]) / 2, l = (c[1] + m[1]) / 2, r = t.vb.x + (d - f.left) * g, k = t.vb.y + (l - f.top) * g;
        this.dragMoved = !0, this.setVB({ x: r - (r - t.vb.x) * u, y: k - (k - t.vb.y) * u, w: t.vb.w * u, h: t.vb.h * u });
      }
    });
    const n = (a) => {
      e.delete(a.pointerId), s.classList.remove("dragging"), t = e.size ? { vb: { ...this.st.vb }, pts: new Map(e) } : null, setTimeout(() => {
        e.size || (this.dragMoved = !1);
      }, 0);
    };
    s.addEventListener("pointerup", n), s.addEventListener("pointercancel", n), s.addEventListener("wheel", (a) => {
      if (!this.st.vb || this.o.compact || !(this.o.dialog || a.ctrlKey || a.metaKey)) return;
      a.preventDefault(), cancelAnimationFrame(this.anim);
      const f = Math.exp(a.deltaY * (a.ctrlKey ? 0.01 : 16e-4)), [g, p] = i(a.clientX, a.clientY), h = this.st.vb;
      this.setVB({ x: g - (g - h.x) * f, y: p - (p - h.y) * f, w: h.w * f, h: h.h * f }), this.zoomed(g, p);
    }, { passive: !1 }), s.addEventListener("click", () => {
      this.dragMoved || (this.o.compact ? this.o.onOpen(this.want || {}) : this.st.shape ? this.unfocus() : this.st.sel && (this.st.sel = null, this.applyFocus(), this.renderReadout(), this.emit()));
    });
    const o = (a) => {
      const f = this.st.vb, g = f.x + f.w / 2, p = f.y + f.h / 2;
      this.animateTo({ x: g - f.w * a / 2, y: p - f.h * a / 2, w: f.w * a, h: f.h * a }, 260);
    };
    this.root.querySelector(".smap-tools").addEventListener("click", (a) => {
      const f = a.target.closest("button") && a.target.closest("button").dataset.z;
      if (f === "open") {
        this.o.onOpen(this.view());
        return;
      }
      if (f === "close") {
        this.o.onClose();
        return;
      }
      !f || !this.st.vb || (f === "fit" ? this.animateTo(this.st.shape && this.inBox ? this.focusFit() : this.fitBox()) : o(f === "in" ? 0.7 : 1 / 0.7));
    }), this.o.dialog || this.root.addEventListener("keydown", (a) => {
      a.key === "Escape" && this.escape() && (a.preventDefault(), a.stopPropagation());
    });
  }
  zoomed(s, e) {
    if (!this.G) return;
    const t = this.fitBox().w / this.st.vb.w;
    if (!this.st.shape && t >= 2.2) {
      const i = Object.entries(this.G.box).find(([n, o]) => this.openable(n) && s >= o.x && s <= o.x + o.w && e >= o.y && e <= o.y + o.h);
      i && (this.focus(i[0], null), this.emit());
    } else this.st.shape && this.inBox && this.st.vb.w > this.focusBox().w * 2.4 && this.unfocus();
  }
  emit() {
    this.o.onState && this.o.onState(this.view());
  }
  clearView() {
    this.st.story = null, this.st.shape = null, this.st.part = null, this.st.sel = null, this.st.step = null;
  }
  pickShape(s) {
    if (this.o.compact) {
      this.o.onOpen({ block: s });
      return;
    }
    if (!this.openable(s)) {
      this.st.sel = this.st.sel === s ? null : s, this.applyFocus(), this.renderReadout(), this.emit();
      return;
    }
    this.focus(s, null), this.emit();
  }
  pickPart(s) {
    this.focus(this.blockOf[s], s), this.emit();
  }
  goTo(s) {
    if (this.openable(s)) {
      this.focus(s, null), this.emit();
      return;
    }
    this.unfocus(), this.st.sel = s, this.applyFocus(), this.renderReadout(), this.emit();
  }
  pickStep(s, e) {
    if (this.o.compact) {
      this.o.onOpen({ story: e || void 0 });
      return;
    }
    this.st.step = s, this.hot(s), this.renderReadout();
  }
  focus(s, e) {
    const t = this.st.shape;
    this.st.shape = s, this.st.part = e, this.st.sel = null, t !== s && this.layers && this.drawInside(), this.applyFocus(), this.renderCrumbs(), this.renderReadout(), t !== s && this.G && this.inBox ? this.animateTo(this.focusBox()) : this.G && this.setVB(this.st.vb);
  }
  unfocus() {
    const s = this.st.shape;
    this.st.shape = null, this.st.part = null, this.applyFocus(), this.renderCrumbs(), this.renderReadout(), this.G && this.animateTo(this.home()), s && this.emit();
  }
  escape() {
    return !this.B || this.o.compact ? !1 : this.st.part ? (this.focus(this.st.shape, null), this.emit(), !0) : this.st.shape ? (this.unfocus(), !0) : this.st.sel ? (this.st.sel = null, this.applyFocus(), this.renderReadout(), this.emit(), !0) : this.st.story ? (this.setStory(null), !0) : !1;
  }
  show(s) {
    if (!this.B || this.o.compact) return;
    const e = s.story && this.path(s.story) ? s.story : null;
    e !== this.st.story && this.setStory(e, !0);
    const t = s.part && this.items[s.part] && this.openable(this.blockOf[s.part]) ? s.part : null, i = t ? this.blockOf[t] : s.block && this.openable(s.block) ? s.block : null;
    i && (i !== this.st.shape || t !== this.st.part) ? this.focus(i, t) : !i && this.st.shape && this.unfocus();
    const n = !i && s.block && this.K[s.block] ? s.block : null;
    n !== this.st.sel && (this.st.sel = n, this.applyFocus(), this.renderReadout());
  }
  setStory(s, e) {
    this.st.story = s, this.st.step = null, this.renderChips(), this.layers && (this.drawPath(), this.st.shape && this.drawInside()), this.applyFocus(), this.renderReadout(), this.layers && !this.st.shape && this.animateTo(this.home()), e || this.emit();
  }
  renderChips() {
    const s = this.root.querySelector(".smap-chips"), e = this.root.querySelector(".smap-glimpse"), t = this.B.paths.map((n) => ({ id: n.id, label: n.label, tone: this.pathTone(n), title: n.title })), i = (n, o) => `<button type="button" class="smap-chip${n.tone ? ` ${n.tone}` : ""}" data-s="${ft(n.id || "")}"${o == null ? "" : ` aria-pressed="${o}"`}${n.title ? ` title="${ft(n.title)}"` : ""}>${ft(n.label)}</button>`;
    s.hidden = !t.length, s.innerHTML = [{ id: null, label: "Whole system" }, ...t].map((n) => i(n, String((n.id || null) === this.st.story))).join(""), e.innerHTML = `<button type="button" class="smap-open">Open the map</button>${t.map((n) => i(n)).join("")}`, s.querySelectorAll("button").forEach((n) => n.addEventListener("click", () => {
      this.st.shape && (this.st.shape = null, this.st.part = null, this.applyFocus(), this.renderCrumbs()), this.setStory(n.dataset.s || null);
    })), e.querySelector(".smap-open").addEventListener("click", () => this.o.onOpen(this.want || {})), e.querySelectorAll(".smap-chip").forEach((n) => n.addEventListener("click", () => this.o.onOpen({ story: n.dataset.s })));
  }
  renderCrumbs() {
    const s = this.root.querySelector(".smap-crumbs"), e = this.o.nav, t = e ? e.trail() : [];
    if (this.root.classList.toggle("leveled", t.length > 0), !this.st.shape && !t.length) {
      s.replaceChildren();
      return;
    }
    const i = ft(e ? e.here() : "Whole system"), n = t.map((o, a) => `<button type="button" data-up="${a}">${ft(o)}</button>`);
    if (!this.st.shape) n.push(`<b>${i}</b>`);
    else {
      const o = ft(this.K[this.st.shape].name);
      n.push(`<button type="button" data-c="all">${i}</button>`, this.st.part ? `<button type="button" data-c="shape">${o}</button>` : `<b>${o}</b>`), this.st.part && n.push(`<b>${ft(this.labelOf(this.st.part))}</b>`);
    }
    s.innerHTML = n.join(" › "), s.querySelectorAll("button").forEach((o) => o.addEventListener("click", () => {
      o.dataset.up ? e.up(Number(o.dataset.up)) : o.dataset.c === "all" ? this.unfocus() : (this.focus(this.st.shape, null), this.emit());
    }));
  }
  renderReadout() {
    const s = this.st.story && this.path(this.st.story), e = (a) => `<ol>${a.map((f, g) => `<li class="${f.risk ? "risk" : f.stop ? "stop" : ""}"><span class="n">${g + 1}</span>${ft(f.say)}${f.waitsFor ? ` (waits for step ${f.waitsFor})` : ""}</li>`).join("")}</ol>`, t = this.st.part || this.st.shape || this.st.sel;
    let i;
    if (t) {
      const a = this.st.part ? this.shapeOf(t) : null, f = this.st.part ? this.labelOf(t) : this.K[t].name, g = this.st.part ? a ? a.role : "" : this.K[t].role, p = this.st.part ? this.items[t].what : this.K[t].what;
      i = `<b>${ft(f)}</b>${g ? ` · ${ft(g)}` : ""}${p ? `. ${ft(p)}` : ""}`;
      const h = this.o.nav && this.o.nav.deeper(t);
      h && (i += ` <button type="button" class="smap-deeper" data-down="${ft(t)}">${ft(h)} ›</button>`);
    } else s ? i = `<b>${ft(s.title)}</b> ${e(s.steps)}` : i = `<span class="smap-hint"><span class="click">${this.B.blocks.some((f) => f.open) ? "Click a box to look inside" : "Click a box to read about it"}</span><span class="pan">Drag to see the rest</span></span>`;
    const n = this.B.spine.steps;
    this.readout.innerHTML = `${i}${n.length ? `<div class="smap-pathline"><b>${ft(this.B.spine.title)}</b> ${e(n)}</div>` : ""}`;
    const o = this.readout.querySelector(".smap-deeper");
    o && o.addEventListener("click", () => this.o.nav.down(o.dataset.down));
  }
  async reset() {
    this.clearView(), this.want = null, this.layers && (this.drawPath(), this.drawInside(), this.applyFocus()), this.refresh(), await Zt(2);
  }
  clashes(s) {
    const e = this.svg, t = e.viewBox.baseVal, i = e.getBoundingClientRect().width / t.width, n = s ? ".inside-art" : ".overview", o = (l) => [...e.querySelectorAll(l.split(",").map((r) => `${n} ${r.trim()}`).join(", "))], a = (l, r) => {
      const k = l.getBoundingClientRect();
      return { x: k.x + r, y: k.y + r, w: k.width - 2 * r, h: k.height - 2 * r };
    }, f = (l, r) => l.w > 0 && l.h > 0 && r.w > 0 && r.h > 0 && l.x < r.x + r.w && r.x < l.x + l.w && l.y < r.y + r.h && r.y < l.y + l.h, g = (l) => Number(l.getAttribute("font-size")) * l.getScreenCTM().a, p = (l) => Math.min(99, ...o(l).filter((r) => r.textContent.trim()).map(g)), h = s ? [
      ...o(".part > rect.paper, .part > rect.paper2").map((l) => [`part "${this.labelOf(l.parentNode.dataset.part)}"`, a(l, 1)]),
      ...o(".ghostcard > rect.paper, .sibcard > rect.paper").map((l) => [l.parentNode.getAttribute("aria-label"), a(l, 1)]),
      ...o(".in-title text, .in-tag").map((l) => ["the title", a(l, 1)])
    ] : [
      ...o(".block").map((l) => {
        const r = l.querySelector("rect.hit").getBoundingClientRect(), k = 12 * i;
        return [`block "${this.nameOf(l.dataset.block)}"`, { x: r.x + k, y: r.y + k, w: r.width - 2 * k, h: r.height - 2 * k }];
      }),
      ...o(".thesis text, .lanes text").map((l) => [`the title "${l.textContent.slice(0, 24)}"`, a(l, 1)])
    ];
    for (const l of o(".bd, .bub")) h.push([l.classList.contains("bd") ? "a badge" : "a question badge", a(l, 1)]);
    const c = s ? ".elab, .note" : ".lab:not(.gone), .spine:not(.gone) > .step, .spine:not(.gone) .ack-lab, .pathlayer > .step, .note, .frame", m = [];
    for (const l of o(".note")) m.push({ el: l, r: a(l, 4), say: l.textContent.slice(0, 32) });
    for (const l of o(s ? ".elab text" : ".lab:not(.gone) text, .spine:not(.gone) > .step text, .spine:not(.gone) .ack-lab text, .pathlayer > .step text, .frame text"))
      l.textContent.trim() && m.push({ el: l.closest(c.split(",").map((r) => `${n} ${r.trim()}`).join(", ")), r: a(l, 1), say: l.textContent.slice(0, 32) });
    const u = [];
    m.forEach((l, r) => {
      for (const [k, v] of h) f(l.r, v) && u.push(`label "${l.say}" overlaps ${k}`);
      for (let k = r + 1; k < m.length; k++) m[k].el !== l.el && f(l.r, m[k].r) && u.push(`labels "${l.say}" and "${m[k].say}" overlap`);
    });
    const d = s ? { note: p(".note text"), part: p(".part > text"), edge: p(".elab text:not(.t-paper)") } : { note: p(".note text"), step: p(".spine > .step text:not(.t-paper), .lab:not(.gone) .step text:not(.t-paper)"), block: p(".block > text") };
    return { clash: [...new Set(u)], font: d, scale: i };
  }
  covers() {
    const s = this.svg, e = [...s.querySelectorAll("*")], t = new Map(e.map((u, d) => [u, d])), i = (u) => {
      for (let d = u; d && d !== s; d = d.parentNode) {
        const l = getComputedStyle(d);
        if (l.display === "none" || l.visibility === "hidden" || Number(l.opacity) < 0.35) return !1;
      }
      return !0;
    }, n = (u) => {
      if (!u.startsWith("rgb")) return 0;
      const d = u.slice(u.indexOf("(") + 1, u.indexOf(")")).split(",").map(Number);
      return d.length === 4 ? d[3] : 1;
    }, o = (u) => {
      const d = u.getBoundingClientRect();
      return { x: d.x, y: d.y, w: d.width, h: d.height };
    }, a = (u, d) => Math.max(0, Math.min(u.x + u.w, d.x + d.w) - Math.max(u.x, d.x)) * Math.max(0, Math.min(u.y + u.h, d.y + d.h) - Math.max(u.y, d.y)), f = o(s), g = (u) => u.closest("g.note, g.bd, g.bub, g.part, g.block, g.ghostcard, g.sibcard, g.frame, g.lab, g.ack, g.step, g.elab, g.in-title, g.thesis, g.lanes") || u.parentNode, p = e.filter((u) => u.tagName === "text" && u.textContent.trim() && i(u)).map((u) => ({ t: u, r: o(u), i: t.get(u), u: g(u) })).filter((u) => u.r.w > 1 && a(u.r, f) > 0), h = e.filter((u) => ["rect", "path", "circle", "ellipse", "polygon", "line", "polyline"].includes(u.tagName) && i(u)).map((u) => {
      const d = getComputedStyle(u);
      return { e: u, r: o(u), i: t.get(u), fill: d.fill !== "none" && !d.fill.startsWith("url(") && n(d.fill) * Number(d.fillOpacity) >= 0.6, stroke: d.stroke !== "none" && n(d.stroke) * Number(d.strokeOpacity) >= 0.3 && parseFloat(d.strokeWidth) > 0.4 };
    }).filter((u) => u.fill || u.stroke), c = [];
    for (const u of p) for (const d of h) {
      if (d.i <= u.i || u.u.contains(d.e)) continue;
      const l = a(u.r, d.r);
      if (!l) continue;
      const r = u.t.textContent.slice(0, 32);
      if (d.fill) {
        l > 6 && l / (u.r.w * u.r.h) > 0.04 && c.push(`a shape is drawn over "${r}"`);
        continue;
      }
      const k = d.e.getScreenCTM().inverse();
      let v = 0;
      for (let $ = u.r.y + 2; $ < u.r.y + u.r.h - 1; $ += 2.5) for (let E = u.r.x + 1; E < u.r.x + u.r.w - 1; E += 2.5) d.e.isPointInStroke(new DOMPoint(E, $).matrixTransform(k)) && v++;
      v >= 3 && c.push(`a line is drawn through "${r}"`);
    }
    const m = [...s.querySelectorAll(".note")].filter(i).map((u) => [u.textContent.slice(0, 32), o(u)]);
    for (const u of s.querySelectorAll(".link path, .step path, .edge path")) {
      if (!i(u) || getComputedStyle(u).fill !== "none") continue;
      const d = u.getTotalLength(), l = u.getScreenCTM();
      for (const [r, k] of m) {
        let v = 0;
        for (let $ = 0; $ <= d; $ += 4) {
          const E = u.getPointAtLength($).matrixTransform(l);
          E.x > k.x + 2 && E.x < k.x + k.w - 2 && E.y > k.y + 2 && E.y < k.y + k.h - 2 && v++;
        }
        v >= 3 && c.push(`an arrow runs under the sticky note "${r}"`);
      }
    }
    return [...new Set(c)];
  }
  brokenNames() {
    return [...this.svg.querySelectorAll(".overview .block")].map((s) => [s.dataset.block, [...s.querySelector(":scope > text").querySelectorAll("tspan")].map((e) => e.textContent).join(" ")]).filter(([s, e]) => e !== _s(this.K[s].name)).map(([s, e]) => `block "${this.K[s].name}" is drawn as "${e}", a word broken across lines`);
  }
  doubled() {
    const s = [...this.svg.querySelectorAll(".overview .loose")].filter((t) => t.getClientRects().length && !t.closest(".gone")).map((t) => {
      const i = t.querySelector("path"), n = i.getTotalLength(), o = [];
      for (let a = 0; a <= n; a += 3) o.push(i.getPointAtLength(a));
      return { n: Number(t.closest(".step").dataset.step) + 1, pts: o };
    }), e = [];
    return s.forEach((t, i) => {
      for (const n of s.slice(i + 1))
        t.pts.filter((a) => n.pts.some((f) => Math.hypot(a.x - f.x, a.y - f.y) < 5)).length > t.pts.length * 0.3 && e.push(`steps ${Math.min(t.n, n.n)} and ${Math.max(t.n, n.n)} draw on one line`);
    }), e;
  }
  farWaits() {
    return [...this.svg.querySelectorAll(".overview .ack")].filter((s) => s.getClientRects().length && !s.closest(".gone") && s.querySelector(".ack-lab")).flatMap((s) => {
      const e = s.querySelector("rect.paper").getBBox(), t = s.querySelector(".ack-lab").getBBox(), i = Math.hypot(Math.max(0, e.x - t.x - t.width, t.x - e.x - e.width), Math.max(0, e.y - t.y - t.height, t.y - e.y - e.height));
      return i > 16 ? [`the caption "${s.querySelector(".ack-lab").textContent}" sits ${Math.round(i)} px from its barrier`] : [];
    });
  }
  cutLeads() {
    const s = (n) => n.getClientRects().length && !n.closest(".gone"), e = [...this.svg.querySelectorAll(".overview text")].filter((n) => n.textContent.trim() && s(n)).map((n) => [n, U(this.boxOf(n), -1)]), t = [...this.svg.querySelectorAll(".overview .block")].map((n) => [n, U(this.boxOf(n.querySelector("rect.hit")), -10)]), i = [];
    for (const n of this.svg.querySelectorAll(".overview .lead")) {
      if (!s(n)) continue;
      const o = n.parentNode, a = n.querySelector("path"), f = a.getTotalLength(), g = [];
      for (let c = 0; c <= f; c += 2) g.push(a.getPointAtLength(c));
      const p = o.textContent.trim().slice(0, 32), h = (c) => g.filter((m) => m.x > c.x && m.x < c.x + c.w && m.y > c.y && m.y < c.y + c.h).length >= 2;
      for (const [c, m] of t) h(m) && i.push(`the leader to "${p}" runs through block "${this.nameOf(c.dataset.block)}"`);
      for (const [c, m] of e) !o.contains(c) && !c.closest(".block") && h(m) && i.push(`the leader to "${p}" runs through "${c.textContent.trim().slice(0, 32)}"`);
    }
    return [...new Set(i)];
  }
  orphans() {
    const s = this.svg.querySelector(".inside-art"), e = new Set([...s.querySelectorAll(".edge")].flatMap((t) => [t.dataset.a, t.dataset.b]));
    return [...s.querySelectorAll(".part:not(.contents)")].map((t) => t.dataset.part).filter((t) => !e.has(t));
  }
  offScreen() {
    const s = this.canvas.getBoundingClientRect(), e = this.st.layout === "tall", t = [], i = (o) => o.left < s.left - 2 || o.right > s.right + 2 || !e && (o.top < s.top - 2 || o.bottom > s.bottom + 2);
    for (const o of this.svg.querySelectorAll(".overview .block")) i(o.getBoundingClientRect()) && t.push(`block "${this.nameOf(o.dataset.block)}" is off screen when the map opens`);
    for (const o of this.svg.querySelectorAll(".overview > g > .frame")) i(o.getBoundingClientRect()) && t.push(`group "${this.F[o.dataset.frame].label}" is off screen when the map opens`);
    const n = this.svg.querySelector(".overview .thesis");
    return n && i(n.getBoundingClientRect()) && t.push(`the title "${this.B.thesis.slice(0, 32)}" is cut off when the map opens`), this.st.story && i(this.layers.path.getBoundingClientRect()) && t.push(`story "${this.st.story}" is not fully in view when the map opens`), [...new Set(t)];
  }
  check(s, e) {
    const t = [], i = (a, f) => t.push({ view: s, rule: a, text: f }), n = !!this.st.shape, o = this.clashes(n);
    for (const a of o.clash) i("overlap", a);
    for (const a of this.covers()) i("cover", a);
    if (e.home) {
      const a = this.skin.least, f = n ? { note: a.note, part: a.part, edge: a.edge } : this.o.compact ? { block: a.glimpse } : { note: a.note, step: a.step, block: a.block };
      for (const [g, p] of Object.entries(f)) o.font[g] < p && i("font", `${g} text renders at ${o.font[g].toFixed(1)} px, want at least ${p}`);
    }
    if (n) for (const a of this.orphans()) i("orphan", `part "${this.labelOf(a)}" has no arrow to anything inside the view`);
    else {
      for (const a of this.brokenNames()) i("name", a);
      for (const a of this.doubled()) i("overlap", a);
      for (const a of this.cutLeads()) i("cover", a);
      for (const a of this.farWaits()) i("caption", a);
    }
    if (e.fit && !n) for (const a of this.offScreen()) i("fit", a);
    return t;
  }
  async walk(s) {
    const e = [], t = async (i, n, o) => {
      n(), await Zt(2), e.push(...this.check(`${s}${i}`, o));
    };
    await t("fit", () => this.setVB(this.st.shape && this.inBox ? this.focusFit() : this.fitBox()), {});
    for (const i of this.B.paths) await t(`story ${i.id}`, () => this.show({ story: i.id }), {});
    for (const i of this.B.blocks.filter((n) => n.open))
      await t(`block ${i.id}`, () => this.show({ block: i.id }), { home: !0 }), await t(`block ${i.id} fit`, () => this.setVB(this.focusFit()), {});
    return this.show({}), await Zt(2), e;
  }
  destroy() {
    this.gone = !0, fs.delete(this), this.ro.disconnect(), clearTimeout(this.rz), cancelAnimationFrame(this.anim), this.root.remove();
  }
}
function le(x, s, e) {
  ne(), e.font !== !1 && e.skin !== "clean" && oe();
  let t = null, i = null, n = null, o = null, a = null, f = [], g;
  const p = new Promise((r) => {
    g = r;
  }), h = (r) => {
    e.onState && e.onState(r);
  }, c = () => {
    n && n.open && n.close();
  }, m = async (r) => (n || (n = document.createElement("dialog"), n.className = "smap-dialog", n.addEventListener("cancel", (k) => {
    k.preventDefault(), i.escape() || c();
  }), x.append(n)), n.setAttribute("aria-label", o.thesis), n.open || n.showModal(), i ? (i.refresh(), i.show(r)) : i = new vs(n, o, { rough: e.rough, skin: e.skin, nav: e.nav, dialog: !0, state: r, onState: (k) => {
    t.mode && (t.want = k), h(k);
  }, onOpen: () => {
  }, onClose: c }), await i.ready, i), u = (r, k) => {
    const v = JSON.stringify(r);
    if (v === a) {
      k && l.show(k);
      return;
    }
    if (a = v, f = Ws(r, { derived: e.derived }), f.length) {
      t && t.destroy(), i && i.destroy(), n && n.remove(), t = null, i = null, n = null, x.innerHTML = `<div class="smap"><div class="smap-problems" role="alert"><b>This system map has ${f.length === 1 ? "a problem" : `${f.length} problems`} to fix</b><ul>${f.map(($) => `<li>${ft($)}</li>`).join("")}</ul></div></div>`, g();
      return;
    }
    o = re(r), t ? t.setModel(o, k) : (x.replaceChildren(), t = new vs(x, o, { rough: e.rough, skin: e.skin, nav: e.nav, fill: e.fill, dialog: !1, state: e.state && Object.values(e.state).some(Boolean) ? e.state : null, onState: h, onOpen: ($) => {
      m($);
    }, onClose: c }), t.ready.then(g)), i && i.setModel(o, k);
  }, l = {
    get problems() {
      return f;
    },
    ready: p,
    update: u,
    show(r) {
      n && n.open ? i.show(r) : t && t.show(r);
    },
    async reset() {
      c(), t && await t.reset();
    },
    audit: async (r = {}) => {
      if (await p, f.length) return f.map(($) => ({ view: "home", rule: "valid", text: $ }));
      const k = t;
      k.root.classList.add("still"), await Zt(2);
      const v = k.check("home", { home: !0, fit: !0 });
      if (r.all) {
        if (!k.o.compact) v.push(...await k.walk(""));
        else {
          const $ = await m({});
          $.root.classList.add("still"), await $.reset(), v.push(...$.check("dialog home", { home: !0, fit: $.st.layout === "tall" }), ...await $.walk("dialog ")), $.root.classList.remove("still"), c();
        }
        await k.reset(), _t = !0, k.refresh(), await Zt(2), k.o.compact ? v.push({ view: "print", rule: "print", text: "the map prints as its names-only glimpse, without its step labels" }) : v.push(...k.check("print", {})), _t = !1, k.refresh(), await Zt(2);
      }
      return k.root.classList.remove("still"), v;
    },
    destroy() {
      t && t.destroy(), i && i.destroy(), n && n.remove(), x.replaceChildren(), delete x.smap, delete x.dataset.smap;
    }
  };
  return x.dataset.smap = "", x.smap = l, u(s), l;
}
const $s = /* @__PURE__ */ new Set(["actor", "app", "service", "worker", "store", "log", "external", "artifact", "doc"]), Ms = /* @__PURE__ */ new Set(["api", "relay", "gate", "router", "guard", "queue", "pool", "log", "recovery", "handoff", "watcher", "keys", "budget", "timer", "uploader", "codec", "compactor", "sweeper", "cache", "ui", "tool", "docs", "sdk", "module", "store"]), Ss = /* @__PURE__ */ new Set(["memory", "disk", "bucket", "keys", "pages"]), Os = /* @__PURE__ */ new Set(["sync", "async", "copy", "control"]), ce = /* @__PURE__ */ new Set(["live", "planned", "retired"]), pe = /* @__PURE__ */ new Set(["zone", "machine", "process"]), ns = /* @__PURE__ */ new Set(["store", "log"]), fe = { actor: "ui", app: "ui", service: "module", worker: "module", store: "store", log: "log", external: "sdk", artifact: "module", doc: "docs" }, Gt = (x) => x.nodes || [];
function ds(x) {
  const s = {}, e = {}, t = {}, i = (o, a, f) => {
    for (const g of o)
      s[g.id] = g, e[g.id] = a, t[g.id] = f, i(Gt(g), g.id, f + 1);
  };
  return i(x.nodes || [], null, 1), { node: s, parent: e, depth: t, chain: (o) => {
    const a = [];
    for (let f = o; f != null; f = e[f]) a.unshift(f);
    return a;
  } };
}
function de(x) {
  if (!x || typeof x != "object") return ["an architecture doc is a JSON object"];
  const s = [];
  x.arch !== 1 && s.push('"arch" must be 1, the format version');
  for (const p of ["name", "title"]) (typeof x[p] != "string" || !x[p].trim()) && s.push(`"${p}" is required`);
  if (!Array.isArray(x.nodes) || !x.nodes.length)
    return s.push('"nodes" needs at least one node'), s;
  const e = {}, t = (p, h) => {
    typeof p != "string" || !/^[a-z][a-z0-9-]{0,39}$/.test(p) ? s.push(`${h}: id "${p}" must start with a letter and use only lowercase letters, digits and dashes, 40 at most`) : e[p] ? s.push(`id "${p}" is used by a ${e[p]} and a ${h}; ids are unique across the doc`) : e[p] = h;
  };
  for (const p of x.groups || []) t(p.id, "group");
  const i = (p) => {
    for (const h of p) {
      t(h.id, "node");
      const c = `node "${h.id}"`;
      if (h.name || s.push(`${c}: "name" is required`), h.role || s.push(`${c}: "role" is required`), $s.has(h.kind) || s.push(`${c}: "kind" is "${h.kind}", not one of ${[...$s].join(", ")}`), h.shape != null && !Ms.has(h.shape) && s.push(`${c}: "shape" is "${h.shape}", not one of ${[...Ms].join(", ")}`), h.status != null && !ce.has(h.status) && s.push(`${c}: "status" is "${h.status}", not live, planned or retired`), ns.has(h.kind) && !Ss.has(h.medium) && s.push(`${c}: a ${h.kind} node needs "medium", one of ${[...Ss].join(", ")}`), !ns.has(h.kind)) for (const m of ["medium", "tag", "holds"]) h[m] != null && s.push(`${c}: only store and log nodes take "${m}"`);
      h.shape === "store" && !ns.has(h.kind) && s.push(`${c}: the store shape belongs to store and log nodes`), i(Gt(h));
    }
  };
  i(x.nodes);
  for (const p of x.stories || []) t(p.id, "story");
  const { node: n } = ds(x), o = Object.fromEntries((x.groups || []).map((p) => [p.id, p]));
  for (const p of x.groups || [])
    pe.has(p.kind || "zone") || s.push(`group "${p.id}": "kind" is "${p.kind}", not zone, machine or process`), (p.kind || "zone") === "process" && (!o[p.in] || o[p.in].kind !== "machine") && s.push(`group "${p.id}": a process group needs "in" naming a machine group`), (p.kind || "zone") !== "process" && p.in != null && s.push(`group "${p.id}": only a process group takes "in"`);
  for (const p of Object.values(n)) p.group != null && !o[p.group] && s.push(`node "${p.id}": "group" names "${p.group}", which is not a group`);
  const a = (p, h) => {
    n[h] || s.push(`${p} names "${h}", which is not a node`);
  };
  (x.edges || []).forEach((p, h) => {
    a(`edge ${h + 1} "from"`, p.from), a(`edge ${h + 1} "to"`, p.to), p.from === p.to && s.push(`edge ${h + 1} joins "${p.from}" to itself`), p.kind != null && !Os.has(p.kind) && s.push(`edge ${h + 1}: "kind" is "${p.kind}", not one of ${[...Os].join(", ")}`);
  });
  const f = (p, h) => h.forEach((c, m) => {
    a(`${p} step ${m + 1} "from"`, c.from), a(`${p} step ${m + 1} "to"`, c.to), c.say || s.push(`${p} step ${m + 1}: "say" is required`);
  });
  x.path && (x.path.title || s.push('path: "title" is required'), f("path", x.path.steps || []), (x.path.steps || []).forEach((p, h) => {
    p.waitsFor != null && !(Number.isInteger(p.waitsFor) && p.waitsFor >= 1 && p.waitsFor <= h) && s.push(`path step ${h + 1}: "waitsFor" is ${p.waitsFor}, which is not an earlier step`);
  }));
  const g = /* @__PURE__ */ new Set();
  for (const p of x.stories || [])
    p.label ? g.has(p.label) && s.push(`story label "${p.label}" is used twice`) : s.push(`story "${p.id}": "label" is required`), g.add(p.label), p.tone != null && !["ok", "fail", "pending"].includes(p.tone) && s.push(`story "${p.id}": "tone" is "${p.tone}", not ok, fail or pending`), f(`story "${p.id}"`, p.steps || []), (p.steps || []).forEach((h, c) => {
      h.mark != null && !["risk", "stop"].includes(h.mark) && s.push(`story "${p.id}" step ${c + 1}: "mark" is "${h.mark}", not risk or stop`), h.waitsFor != null && s.push(`story "${p.id}" step ${c + 1}: "waitsFor" belongs only on the path`);
    });
  return (x.notes || []).forEach((p, h) => {
    p.on == null == (p.edge == null) && s.push(`note ${h + 1}: give "on" (a node) or "edge" (its from and to), exactly one`), p.on != null && a(`note ${h + 1} "on"`, p.on), p.edge != null && !(x.edges || []).some((c) => c.from === p.edge.from && c.to === p.edge.to || c.from === p.edge.to && c.to === p.edge.from) && s.push(`note ${h + 1}: no edge joins "${p.edge.from}" and "${p.edge.to}"`);
  }), x.start && x.start.node != null && a('start "node"', x.start.node), x.start && x.start.story != null && !(x.stories || []).some((p) => p.id === x.start.story) && s.push(`start "story" names "${x.start.story}", which is not a story`), s;
}
function cs(x, s) {
  const e = ds(x), t = s == null ? x.nodes : Gt(e.node[s]), i = s == null ? [] : e.chain(s), n = {}, o = {};
  for (const M of t) {
    n[M.id] = M.id, o[M.id] = M.id;
    for (const O of Gt(M))
      n[O.id] = O.id, o[O.id] = M.id;
  }
  const a = {}, f = (M) => {
    if (Object.hasOwn(n, M)) return [n[M], !1];
    const O = e.chain(M);
    if (i.every((L, T) => O[T] === L)) return [O.length > i.length + 1 ? n[O[i.length + 1]] : null, !1];
    let b = 0;
    for (; b < O.length && O[b] === i[b]; ) b += 1;
    return [b === O.length ? null : O[b], !0];
  }, g = (M, O) => {
    let [b, L] = f(M), [T, P] = f(O);
    if (b == null || T == null || L && P) return null;
    for (const [Q, V] of [[b, L], [T, P]]) V && (a[Q] = !0, o[Q] = Q);
    return o[b] === o[T] && b === o[b] != (T === o[T]) && (b = b === o[b] ? T : b, T = b), [b, T];
  }, p = [], h = /* @__PURE__ */ new Set();
  for (const M of x.edges || []) {
    const O = g(M.from, M.to);
    if (!O || O[0] === O[1]) continue;
    const b = M.kind || "sync", L = `${O[0]}|${O[1]}|${b}`;
    h.has(L) || (h.add(L), p.push({ from: O[0], to: O[1], ...M.label ? { label: M.label } : {}, style: b }));
  }
  const c = (M, O) => {
    const b = [], L = [];
    let T = null;
    return M.forEach((P) => {
      const Q = g(P.from, P.to);
      if (!Q) {
        T = null, L.push(null);
        return;
      }
      const V = o[Q[0]] === o[Q[1]] ? o[Q[0]] : null;
      if (V && T && T.box === V) {
        T.n += 1, Object.assign(T.step, { from: V, to: V, say: `${T.say} (and ${T.n - 1} more)` }), L.push(b.length);
        return;
      }
      const tt = { from: Q[0], to: Q[1], say: P.say, ...O(P) };
      b.push(tt), L.push(b.length), T = V ? { box: V, step: tt, say: P.say, n: 1 } : null;
    }), { out: b, at: L };
  }, m = x.path && c(x.path.steps || [], () => ({}));
  m && m.out.forEach((M, O) => {
    const b = x.path.steps.find((T, P) => m.at[P] === O + 1 && T.waitsFor != null), L = b && m.at[b.waitsFor - 1];
    L && L <= O && o[M.from] !== o[M.to] && (M.waitsFor = L);
  });
  const u = (x.stories || []).map((M) => ({ st: M, w: c(M.steps || [], (O) => O.mark ? { mark: O.mark } : {}) })).filter((M) => M.w.out.length), d = (M) => {
    const O = Gt(M), b = { id: M.id, kind: M.kind, name: M.name, role: M.role };
    M.group && !a[M.id] && (b.group = M.group), ns.has(M.kind) && (b.medium = M.medium, M.tag && (b.tag = M.tag), M.holds && (b.contents = M.holds));
    for (const L of ["about", "critical", "status", "badges", "lane"]) M[L] != null && (b[L] = M[L]);
    return O.length && (b.parts = O.map(l)), b;
  }, l = (M) => {
    const O = M.shape || fe[M.kind], b = { id: M.id, shape: O, name: M.name, role: M.role };
    O === "store" && (b.medium = M.medium, M.tag && (b.tag = M.tag));
    for (const L of ["about", "status", "badges"]) M[L] != null && (b[L] = M[L]);
    return b;
  }, r = (M) => {
    const O = e.node[M];
    return { id: M, kind: "external", name: O.name, role: O.role, ...O.about ? { about: O.about } : {}, ...O.status ? { status: O.status } : {} };
  }, k = [...t.map(d), ...Object.keys(a).map(r)], v = new Set(k.map((M) => M.group).filter(Boolean));
  for (const M of x.groups || []) v.has(M.id) && M.kind === "process" && v.add(M.in);
  const $ = (x.groups || []).filter((M) => v.has(M.id)).map((M) => ({ id: M.id, label: M.label, kind: M.kind || "zone", ...M.in ? { in: M.in } : {} })), E = [];
  for (const M of x.notes || []) {
    if (M.on != null) {
      n[M.on] === M.on && E.push({ on: M.on, text: M.text });
      continue;
    }
    const O = g(M.edge.from, M.edge.to);
    O && O[0] !== O[1] && p.some((b) => b.from === O[0] && b.to === O[1] || b.from === O[1] && b.to === O[0]) && E.push({ link: { from: O[0], to: O[1] }, text: M.text });
  }
  const W = s == null ? null : e.node[s], j = { title: W ? W.claim || `${W.name}: ${W.role}` : x.title, blocks: k };
  return $.length && (j.groups = $), p.length && (j.links = p), m && m.out.length && (j.path = { title: x.path.title, steps: m.out }), u.length && (j.stories = u.map(({ st: M, w: O }) => ({ id: M.id, label: M.label, ...M.title ? { title: M.title } : {}, ...M.tone ? { tone: M.tone } : {}, steps: O.out }))), E.length && (j.notes = E), x.lanes && (j.lanes = x.lanes), j;
}
const ue = String.raw`
.arch { display: grid; border: 1px solid #e2e5eb; border-radius: 12px; overflow: hidden; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .arch { border-color: #30353d; } }
:root[data-theme="dark"] .arch { border-color: #30353d; }
.arch > .arch-map > .smap { border-radius: 0; }
.arch-key { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 16px; padding: 8px 12px 10px; border-top: 1px solid color-mix(in srgb, currentColor 14%, transparent); font: 12px/1.4 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; color: color-mix(in srgb, currentColor 70%, transparent); }
.arch-key span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.arch-key svg { flex: none; }
.arch-key b { font-weight: 650; color: inherit; }
.arch-problems { padding: 12px 14px; color: #c9302a; font: 13px/1.5 ui-sans-serif, system-ui, sans-serif; }
.arch-problems ul { margin: 6px 0 0; padding-left: 18px; }
`, me = [
  ["sync", "Calls and waits", "", "M2 6 H34"],
  ["async", "Hands off", "6 4", "M2 6 H34"],
  ["copy", "Copies data", "9 3", "M2 6 H34"],
  ["control", "Controls or checks", "1.5 4", "M2 6 H34"]
], ge = () => `<div class="arch-key" aria-label="Key">${me.map(([x, s, e, t]) => `<span data-k="${x}"><svg width="36" height="12" viewBox="0 0 36 12" aria-hidden="true"><path d="${t}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"${e ? ` stroke-dasharray="${e}"` : ""}/></svg>${s}</span>`).join("")}<span><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="6.5" fill="#2f5bd8"/><text x="7" y="10.5" font-size="9" font-weight="700" text-anchor="middle" fill="#fff" font-family="system-ui">1</text></svg>Main path step</span><span><svg width="16" height="12" viewBox="0 0 16 12" aria-hidden="true"><rect x="1" y="1" width="14" height="10" rx="2" fill="#fff4c2" stroke="#e8dca4"/></svg>Note</span></div>`, ke = (x) => String(x ?? "").replace(/[&<>"']/g, (s) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[s]);
function we(x, s, e = {}) {
  if (!document.getElementById("arch-css")) {
    const u = document.createElement("style");
    u.id = "arch-css", u.textContent = ue, document.head.append(u);
  }
  let t = s, i = null, n = [], o = null;
  const a = e.state || {};
  let f = a.focus ?? null;
  const g = document.createElement("div");
  g.className = "arch", x.replaceChildren(g);
  const p = (u) => {
    e.onState && e.onState({ ...f != null ? { focus: f } : {}, ...u });
  }, h = (u, d) => {
    f = u, o.update(cs(t, f), d || {}), p(d || {});
  }, c = {
    trail: () => f == null ? [] : [t.name, ...i.chain(f).slice(0, -1).map((u) => i.node[u].name)],
    here: () => f == null ? t.name : i.node[f].name,
    up: (u) => {
      const d = i.chain(f), l = u === 0 ? null : d[u - 1];
      h(l, { block: d[u] });
    },
    deeper: (u) => {
      const d = i.node[u];
      if (!d) return null;
      const l = f == null ? i.depth[u] === 1 : i.parent[u] === f, r = f == null ? i.depth[u] === 2 : i.parent[i.parent[u]] === f;
      return l && Gt(d).some((k) => Gt(k).length) ? `Zoom into ${d.name}` : r && Gt(d).length ? `Zoom into ${d.name}` : !l && !r ? `Go to ${d.name}` : null;
    },
    down: (u) => {
      const d = f == null ? i.depth[u] === 1 : i.parent[u] === f, l = f == null ? i.depth[u] === 2 : i.parent[i.parent[u]] === f;
      d ? h(u, {}) : l ? h(i.parent[u], { block: u }) : h(i.parent[u], { block: u });
    }
  }, m = () => {
    if (n = de(t), n.length) {
      o && (o.destroy(), o = null), g.innerHTML = `<div class="arch-problems" role="alert"><b>This architecture doc has ${n.length === 1 ? "a problem" : `${n.length} problems`} to fix</b><ul>${n.map((r) => `<li>${ke(r)}</li>`).join("")}</ul></div>`;
      return;
    }
    i = ds(t), f != null && !i.node[f] && (f = null);
    const u = cs(t, f), d = Ws(u, { derived: !0 });
    if (d.length) throw new Error(`architecture doc "${t.name}" projects to an invalid map: ${d.join("; ")}`);
    if (o) {
      o.update(u);
      return;
    }
    g.innerHTML = `<div class="arch-map"></div>${e.key === !1 ? "" : ge()}`;
    const l = { story: a.story, block: a.block, part: a.part };
    if (!e.state && t.start && (t.start.story && (l.story = t.start.story), t.start.node)) {
      const r = i.parent[t.start.node];
      f = r == null ? null : i.parent[r], l.block = r ?? t.start.node, r != null && (l.part = t.start.node);
    }
    o = le(g.querySelector(".arch-map"), cs(t, f), { skin: "clean", nav: c, derived: !0, fill: e.fill, state: l, onState: p });
  };
  return m(), {
    get problems() {
      return n;
    },
    get ready() {
      return o ? o.ready : Promise.resolve();
    },
    update(u) {
      t = u, m();
    },
    show(u) {
      if (!o) return;
      const d = u.focus ?? null;
      d !== f ? h(d, u) : o.show(u);
    },
    destroy() {
      o && o.destroy(), x.replaceChildren();
    }
  };
}
function be({ block: x }) {
  const s = Ps(null), e = x.doc;
  return Cs(() => {
    const t = we(s.current, e, {});
    return () => t.destroy();
  }, [e]), /* @__PURE__ */ As("div", { ref: s });
}
const Se = {
  hostApi: 1,
  blocks: { arch: be }
};
export {
  Se as default
};
