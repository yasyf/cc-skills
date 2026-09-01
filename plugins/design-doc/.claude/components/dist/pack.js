const q = window.CcPresent;
if (!q)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const e = q.jsxRuntime.jsx, l = q.jsxRuntime.jsxs, U = q.jsxRuntime.Fragment, K = window.CcPresent;
if (!K)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const X = K.React, { createElement: oe, Fragment: le, useCallback: R, useEffect: se, useMemo: ce, useRef: de, useState: ae } = X;
function O() {
  const t = window.CcPresent;
  if (!t)
    throw new Error("cc-present: window.CcPresent unavailable; the host must install it before a pack bundle loads");
  return t;
}
function y() {
  return O().ui.tokens;
}
function V(t) {
  O().ui.toast(t);
}
function P(t, i) {
  return O().ui.usePackState(t, i);
}
function S() {
  const t = y();
  return {
    fontFamily: t.fontMono,
    fontSize: "0.7rem",
    letterSpacing: t.trackCaps,
    textTransform: "uppercase",
    color: t.dim
  };
}
function $(t = "0.9rem") {
  return { fontFamily: y().fontProse, fontSize: t };
}
function w() {
  const t = y();
  return { ...$("0.85rem"), color: t.dim, margin: 0 };
}
function L({
  title: t,
  meta: i,
  children: r
}) {
  const s = y();
  return /* @__PURE__ */ l(
    "div",
    {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "0.85rem",
        width: "100%",
        boxSizing: "border-box",
        padding: "1rem",
        background: s.surface,
        color: s.text,
        border: `1px solid ${s.border}`,
        borderRadius: s.radiusLg
      },
      children: [
        /* @__PURE__ */ l("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "0.5rem" }, children: [
          /* @__PURE__ */ e("div", { style: { ...$("1rem"), fontWeight: 600 }, children: t }),
          i && /* @__PURE__ */ e("span", { style: S(), children: i })
        ] }),
        r
      ]
    }
  );
}
function E({ children: t }) {
  const i = y();
  return /* @__PURE__ */ e("div", { style: { ...S(), paddingBottom: "0.3rem", borderBottom: `1px solid ${i.border}` }, children: t });
}
function M({ id: t }) {
  const i = y();
  return /* @__PURE__ */ e(
    "span",
    {
      style: {
        fontFamily: i.fontMono,
        fontSize: "0.72rem",
        padding: "0.1rem 0.35rem",
        borderRadius: i.radiusSm,
        border: `1px solid ${i.border}`,
        color: i.dim,
        whiteSpace: "nowrap"
      },
      children: t
    }
  );
}
function N({ label: t, tone: i = "dim" }) {
  const r = y(), s = i === "ok" ? r.ok : i === "warn" ? r.warn : i === "danger" ? r.danger : i === "accent" ? r.accent : r.dim;
  return /* @__PURE__ */ e(
    "span",
    {
      style: {
        fontFamily: r.fontMono,
        fontSize: "0.68rem",
        letterSpacing: r.trackCaps,
        textTransform: "uppercase",
        padding: "0.1rem 0.4rem",
        borderRadius: r.radiusSm,
        border: `1px solid ${s}`,
        color: s,
        whiteSpace: "nowrap"
      },
      children: t
    }
  );
}
function W({
  label: t,
  active: i = !1,
  disabled: r = !1,
  primary: s = !1,
  onClick: d
}) {
  const f = y(), u = i || s;
  return /* @__PURE__ */ e(
    "button",
    {
      type: "button",
      disabled: r,
      "aria-pressed": i,
      onClick: d,
      style: {
        padding: "0.35rem 0.7rem",
        fontFamily: f.fontProse,
        fontSize: "0.8rem",
        borderRadius: f.radiusMd,
        border: `1px solid ${u ? f.accent : f.border}`,
        background: u ? f.accent : f.surface,
        color: u ? f.accentFg : f.text,
        cursor: r ? "not-allowed" : "pointer",
        opacity: r ? 0.55 : 1,
        whiteSpace: "nowrap"
      },
      children: t
    }
  );
}
function G({
  value: t,
  placeholder: i,
  disabled: r = !1,
  rows: s = 2,
  onChange: d,
  onCommit: f
}) {
  const u = y();
  return /* @__PURE__ */ e(
    "textarea",
    {
      rows: s,
      value: t,
      placeholder: i,
      disabled: r,
      onChange: (v) => d(v.target.value),
      onBlur: f,
      style: {
        width: "100%",
        boxSizing: "border-box",
        resize: "vertical",
        padding: "0.4rem 0.55rem",
        fontFamily: u.fontProse,
        fontSize: "0.85rem",
        color: u.text,
        background: u.bg,
        border: `1px solid ${u.border}`,
        borderRadius: u.radiusMd
      }
    }
  );
}
function Y({
  value: t,
  placeholder: i,
  disabled: r = !1,
  onChange: s
}) {
  const d = y();
  return /* @__PURE__ */ e(
    "input",
    {
      type: "text",
      value: t,
      placeholder: i,
      disabled: r,
      onChange: (f) => s(f.target.value),
      style: {
        width: "100%",
        boxSizing: "border-box",
        padding: "0.4rem 0.55rem",
        fontFamily: d.fontProse,
        fontSize: "0.85rem",
        color: d.text,
        background: d.bg,
        border: `1px solid ${d.border}`,
        borderRadius: d.radiusMd
      }
    }
  );
}
function T({ children: t, selected: i = !1 }) {
  const r = y();
  return /* @__PURE__ */ e(
    "li",
    {
      style: {
        listStyle: "none",
        display: "flex",
        flexDirection: "column",
        gap: "0.4rem",
        padding: "0.6rem 0.7rem",
        borderRadius: r.radiusMd,
        border: `1px solid ${i ? r.accent : r.border}`,
        background: i ? r.bgSoft : "transparent"
      },
      children: t
    }
  );
}
function A({ children: t }) {
  return /* @__PURE__ */ e("ul", { style: { display: "flex", flexDirection: "column", gap: "0.5rem", margin: 0, padding: 0 }, children: t });
}
function B(...t) {
  const i = /* @__PURE__ */ Object.create(null);
  for (const r of t)
    if (r)
      for (const [s, d] of Object.entries(r)) i[s] = d;
  return i;
}
function Z(t, i) {
  const r = /* @__PURE__ */ Object.create(null);
  for (const s of t) {
    const d = i(s);
    (r[d] ??= []).push(s);
  }
  return r;
}
const _ = ["holds", "partly", "wrong"];
function ee({ block: t, value: i, submit: r, disabled: s }) {
  const d = t.claims, f = t.verdicts ?? _, [u, v] = P("pending", {}), [k, x] = P("corrections", {}), b = B(i?.verdicts, u), F = (o) => k[o] ?? b[o]?.correction ?? "", m = R(
    (o, a, D) => {
      const g = D.trim() ? { verdict: a, correction: D.trim() } : { verdict: a }, z = B(b, { [o]: g });
      v(z), r({ verdicts: z });
    },
    [r, v, b]
  ), I = R(
    (o, a) => m(o, a, a === f[0] ? "" : F(o)),
    [m, f, k, b]
  ), h = R(
    (o) => {
      const a = b[o];
      a && m(o, a.verdict, F(o));
    },
    [m, b, k]
  ), j = d.filter((o) => b[o.id] !== void 0).length;
  return /* @__PURE__ */ e(L, { title: t.prompt, meta: `${j} of ${d.length} answered`, children: /* @__PURE__ */ e(A, { children: d.map((o) => {
    const a = b[o.id], D = a !== void 0 && a.verdict !== f[0];
    return /* @__PURE__ */ l(T, { selected: a !== void 0, children: [
      /* @__PURE__ */ l("div", { style: { display: "flex", alignItems: "baseline", gap: "0.45rem", flexWrap: "wrap" }, children: [
        /* @__PURE__ */ e(M, { id: o.id }),
        /* @__PURE__ */ e("span", { style: { ...$(), fontWeight: 600 }, children: o.label })
      ] }),
      o.because && /* @__PURE__ */ e("p", { style: w(), children: o.because }),
      o.ifFalse && /* @__PURE__ */ l("p", { style: { ...w(), fontStyle: "italic" }, children: [
        "If this is wrong — ",
        o.ifFalse
      ] }),
      s ? a && /* @__PURE__ */ l("div", { style: { display: "flex", flexDirection: "column", gap: "0.2rem" }, children: [
        /* @__PURE__ */ e("span", { style: S(), children: a.verdict }),
        a.correction && /* @__PURE__ */ e("p", { style: w(), children: a.correction })
      ] }) : /* @__PURE__ */ l("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" }, children: [
        /* @__PURE__ */ e("div", { style: { display: "flex", gap: "0.4rem", flexWrap: "wrap" }, children: f.map((g) => /* @__PURE__ */ e(
          W,
          {
            label: g,
            active: a?.verdict === g,
            onClick: () => I(o.id, g)
          },
          g
        )) }),
        D && /* @__PURE__ */ e(
          G,
          {
            value: F(o.id),
            placeholder: "In your words, what is actually true?",
            onChange: (g) => x({ ...k, [o.id]: g }),
            onCommit: () => h(o.id)
          }
        )
      ] })
    ] }, o.id);
  }) }) });
}
function J({ heading: t, items: i }) {
  return /* @__PURE__ */ l("div", { style: { display: "flex", flexDirection: "column", gap: "0.2rem" }, children: [
    /* @__PURE__ */ e("span", { style: S(), children: t }),
    /* @__PURE__ */ e("ul", { style: { margin: 0, paddingLeft: "1.1rem" }, children: i.map((r) => /* @__PURE__ */ e("li", { style: w(), children: r }, r)) })
  ] });
}
function ne({ block: t, value: i, submit: r, disabled: s }) {
  const d = y(), f = t.options, u = t.escape ?? {}, v = t.round, k = t.decides, x = i ?? {}, [b, F] = P("detailFor", ""), [m, I] = P("deferTitle", x.defer?.title ?? ""), [h, j] = P("deferWhy", x.defer?.why ?? ""), [o, a] = P("deferring", !1), D = R((c) => r({ choice: c }), [r]), g = R(() => {
    const c = m.trim();
    if (!c) {
      V({ kind: "error", text: "Name the open question before deferring it" });
      return;
    }
    const n = h.trim();
    r({ defer: n ? { title: c, why: n } : { title: c } }), V({ kind: "info", text: "Added to the open list" });
  }, [r, m, h]), z = [v !== void 0 ? `round ${v}` : null, k ? `decides ${k}` : null].filter(Boolean).join(" · ");
  return /* @__PURE__ */ l(L, { title: t.question, meta: z || void 0, children: [
    /* @__PURE__ */ e(A, { children: f.map((c) => {
      const n = x.choice === c.id, p = b === c.id, C = (c.pros?.length ?? 0) + (c.cons?.length ?? 0) > 0;
      return /* @__PURE__ */ l(T, { selected: n, children: [
        /* @__PURE__ */ l("div", { style: { display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ e("span", { style: { ...$(), fontWeight: 600 }, children: c.label }),
          c.recommended && /* @__PURE__ */ e(N, { label: "recommended", tone: "accent" })
        ] }),
        /* @__PURE__ */ e("p", { style: w(), children: c.consequence }),
        p && /* @__PURE__ */ l("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" }, children: [
          c.pros && c.pros.length > 0 && /* @__PURE__ */ e(J, { heading: "For", items: c.pros }),
          c.cons && c.cons.length > 0 && /* @__PURE__ */ e(J, { heading: "Against", items: c.cons })
        ] }),
        s && n && /* @__PURE__ */ e("span", { style: S(), children: "chosen" }),
        /* @__PURE__ */ l("div", { style: { display: "flex", gap: "0.4rem", flexWrap: "wrap" }, children: [
          !s && /* @__PURE__ */ e(
            W,
            {
              label: n ? "Chosen" : "Choose",
              active: n,
              onClick: () => D(c.id)
            }
          ),
          C && /* @__PURE__ */ e(
            W,
            {
              label: p ? "Hide detail" : "Detail",
              onClick: () => F(p ? "" : c.id)
            }
          )
        ] })
      ] }, c.id);
    }) }),
    x.defer ? /* @__PURE__ */ l("div", { style: { display: "flex", flexDirection: "column", gap: "0.25rem" }, children: [
      /* @__PURE__ */ e("span", { style: S(), children: "deferred to the open list" }),
      /* @__PURE__ */ e("span", { style: { ...$(), fontWeight: 600 }, children: x.defer.title }),
      x.defer.why && /* @__PURE__ */ e("p", { style: w(), children: x.defer.why })
    ] }) : !s && (o ? /* @__PURE__ */ l(
      "div",
      {
        style: {
          display: "flex",
          flexDirection: "column",
          gap: "0.45rem",
          padding: "0.6rem 0.7rem",
          border: `1px dashed ${d.border}`,
          borderRadius: d.radiusMd
        },
        children: [
          /* @__PURE__ */ e("span", { style: S(), children: u.label ?? "Add to open list" }),
          /* @__PURE__ */ e(
            Y,
            {
              value: m,
              placeholder: "Name the open question",
              onChange: I
            }
          ),
          /* @__PURE__ */ e(
            G,
            {
              value: h,
              placeholder: u.placeholder ?? "What has to be true before this can be decided?",
              onChange: j
            }
          ),
          /* @__PURE__ */ l("div", { style: { display: "flex", gap: "0.4rem" }, children: [
            /* @__PURE__ */ e(W, { label: "Add", primary: !0, onClick: g }),
            /* @__PURE__ */ e(W, { label: "Cancel", onClick: () => a(!1) })
          ] })
        ]
      }
    ) : /* @__PURE__ */ e("div", { style: { display: "flex" }, children: /* @__PURE__ */ e(W, { label: u.label ?? "Add to open list", onClick: () => a(!0) }) }))
  ] });
}
const te = { working: "ok", validate: "warn" }, re = { resolved: "ok", superseded: "dim", open: "warn" };
function ie({ block: t, value: i, submit: r, disabled: s }) {
  const d = y(), f = t.assumptions ?? [], u = t.decisions ?? [], v = t.open ?? [], k = t.openGroups ?? {}, x = t.challengeable === !0 && !s, [b, F] = P("pending", {}), [m, I] = P("notes", {}), h = B(i?.entries, b), j = (n) => m[n] ?? h[n]?.note ?? "", o = R(
    (n, p, C) => {
      const Q = C.trim() ? { action: p, note: C.trim() } : { action: p }, H = B(h, { [n]: Q });
      F(H), r({ entries: H });
    },
    [r, F, h]
  ), a = R(
    (n, p) => o(n, p, p === "challenge" ? j(n) : ""),
    [o, m, h]
  ), D = R(
    (n) => {
      const p = h[n];
      p && o(n, p.action, j(n));
    },
    [o, h, m]
  ), g = (n) => {
    const p = h[n];
    return p ? x ? p.action !== "challenge" ? null : /* @__PURE__ */ e(
      G,
      {
        value: j(n),
        placeholder: "What is wrong with it?",
        onChange: (C) => I({ ...m, [n]: C }),
        onCommit: () => D(n)
      }
    ) : /* @__PURE__ */ l("div", { style: { display: "flex", flexDirection: "column", gap: "0.2rem" }, children: [
      /* @__PURE__ */ e("span", { style: S(), children: p.action === "confirm" ? "confirmed" : "challenged" }),
      p.note && /* @__PURE__ */ e("p", { style: w(), children: p.note })
    ] }) : null;
  }, z = (n) => {
    if (!x) return null;
    const p = h[n];
    return /* @__PURE__ */ l("div", { style: { display: "flex", gap: "0.4rem" }, children: [
      /* @__PURE__ */ e(W, { label: "Confirm", active: p?.action === "confirm", onClick: () => a(n, "confirm") }),
      /* @__PURE__ */ e(W, { label: "Challenge", active: p?.action === "challenge", onClick: () => a(n, "challenge") })
    ] });
  }, c = Z(v, (n) => n.g ?? "open");
  return /* @__PURE__ */ l(L, { title: t.title, meta: t.phase, children: [
    f.length > 0 && /* @__PURE__ */ l("section", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: [
      /* @__PURE__ */ e(E, { children: "Assumptions" }),
      /* @__PURE__ */ e(A, { children: f.map((n) => /* @__PURE__ */ l(T, { selected: n.star === !0, children: [
        /* @__PURE__ */ l("div", { style: { display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ e(M, { id: n.id }),
          n.star && /* @__PURE__ */ e("span", { "aria-label": "load-bearing", title: "load-bearing", style: { color: d.accent }, children: "★" }),
          /* @__PURE__ */ e("span", { style: { ...$(), fontWeight: 600 }, children: n.t }),
          /* @__PURE__ */ e(N, { label: n.s, tone: te[n.s] })
        ] }),
        n.b && /* @__PURE__ */ e("p", { style: w(), children: n.b }),
        n.n && /* @__PURE__ */ e("p", { style: { ...w(), fontStyle: "italic" }, children: n.n }),
        z(n.id),
        g(n.id)
      ] }, n.id)) })
    ] }),
    u.length > 0 && /* @__PURE__ */ l("section", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: [
      /* @__PURE__ */ e(E, { children: "Decisions" }),
      /* @__PURE__ */ e(A, { children: u.map((n) => /* @__PURE__ */ l(T, { children: [
        /* @__PURE__ */ l("div", { style: { display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ e(M, { id: n.id }),
          n.by && /* @__PURE__ */ l(U, { children: [
            /* @__PURE__ */ e("span", { style: { color: d.dim }, children: "→" }),
            /* @__PURE__ */ e(M, { id: n.by })
          ] }),
          /* @__PURE__ */ e("span", { style: { ...$(), fontWeight: 600 }, children: n.t }),
          /* @__PURE__ */ e(N, { label: n.s, tone: re[n.s] }),
          n.round !== void 0 && /* @__PURE__ */ l("span", { style: S(), children: [
            "round ",
            n.round
          ] })
        ] }),
        n.r && /* @__PURE__ */ e("p", { style: w(), children: n.r }),
        n.x && /* @__PURE__ */ e("p", { style: w(), children: n.x }),
        z(n.id),
        g(n.id)
      ] }, n.id)) })
    ] }),
    v.length > 0 && /* @__PURE__ */ l("section", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: [
      /* @__PURE__ */ e(E, { children: "Open" }),
      Object.entries(c).map(([n, p]) => /* @__PURE__ */ l("div", { style: { display: "flex", flexDirection: "column", gap: "0.35rem" }, children: [
        /* @__PURE__ */ e("span", { style: S(), children: k[n] ?? n }),
        /* @__PURE__ */ e(A, { children: p.map((C) => /* @__PURE__ */ e(T, { children: /* @__PURE__ */ l("div", { style: { display: "flex", alignItems: "baseline", gap: "0.45rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ e(M, { id: C.id }),
          /* @__PURE__ */ e("span", { style: $(), children: C.t })
        ] }) }, C.id)) })
      ] }, n))
    ] })
  ] });
}
const pe = {
  hostApi: 1,
  blocks: { registers: ie, claims: ee, fork: ne }
};
export {
  pe as default
};
