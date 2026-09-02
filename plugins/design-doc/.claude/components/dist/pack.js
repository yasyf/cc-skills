const O = window.CcPresent;
if (!O)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const n = O.jsxRuntime.jsx, o = O.jsxRuntime.jsxs, X = O.jsxRuntime.Fragment, U = window.CcPresent;
if (!U)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const Y = U.React, { createElement: le, Fragment: se, useCallback: P, useEffect: ce, useMemo: de, useRef: ae, useState: pe } = Y;
function G() {
  const t = window.CcPresent;
  if (!t)
    throw new Error("cc-present: window.CcPresent unavailable; the host must install it before a pack bundle loads");
  return t;
}
function h() {
  return G().ui.tokens;
}
function K(t) {
  G().ui.toast(t);
}
function W(t, i) {
  return G().ui.usePackState(t, i);
}
function C() {
  const t = h();
  return {
    fontFamily: t.fontMono,
    fontSize: "0.7rem",
    letterSpacing: t.trackCaps,
    textTransform: "uppercase",
    color: t.dim
  };
}
function R(t = "0.9rem") {
  return { fontFamily: h().fontProse, fontSize: t };
}
function x() {
  const t = h();
  return { ...R("0.85rem"), color: t.dim, margin: 0 };
}
function H({
  title: t,
  meta: i,
  children: r
}) {
  const c = h();
  return /* @__PURE__ */ o(
    "div",
    {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "0.85rem",
        width: "100%",
        boxSizing: "border-box",
        padding: "1rem",
        background: c.surface,
        color: c.text,
        border: `1px solid ${c.border}`,
        borderRadius: c.radiusLg
      },
      children: [
        /* @__PURE__ */ o("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "0.5rem" }, children: [
          /* @__PURE__ */ n("div", { style: { ...R("1rem"), fontWeight: 600 }, children: t }),
          i && /* @__PURE__ */ n("span", { style: C(), children: i })
        ] }),
        r
      ]
    }
  );
}
function N({ children: t }) {
  const i = h();
  return /* @__PURE__ */ n("div", { style: { ...C(), paddingBottom: "0.3rem", borderBottom: `1px solid ${i.border}` }, children: t });
}
function T({ id: t }) {
  const i = h();
  return /* @__PURE__ */ n(
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
function L({ label: t, tone: i = "dim" }) {
  const r = h(), c = i === "ok" ? r.ok : i === "warn" ? r.warn : i === "danger" ? r.danger : i === "accent" ? r.accent : r.dim;
  return /* @__PURE__ */ n(
    "span",
    {
      style: {
        fontFamily: r.fontMono,
        fontSize: "0.68rem",
        letterSpacing: r.trackCaps,
        textTransform: "uppercase",
        padding: "0.1rem 0.4rem",
        borderRadius: r.radiusSm,
        border: `1px solid ${c}`,
        color: c,
        whiteSpace: "nowrap"
      },
      children: t
    }
  );
}
function D({
  label: t,
  active: i = !1,
  disabled: r = !1,
  primary: c = !1,
  onClick: a
}) {
  const p = h(), f = i || c;
  return /* @__PURE__ */ n(
    "button",
    {
      type: "button",
      disabled: r,
      "aria-pressed": i,
      onClick: a,
      style: {
        padding: "0.35rem 0.7rem",
        fontFamily: p.fontProse,
        fontSize: "0.8rem",
        borderRadius: p.radiusMd,
        border: `1px solid ${f ? p.accent : p.border}`,
        background: f ? p.accent : p.surface,
        color: f ? p.accentFg : p.text,
        cursor: r ? "not-allowed" : "pointer",
        opacity: r ? 0.55 : 1,
        whiteSpace: "nowrap"
      },
      children: t
    }
  );
}
function V({
  value: t,
  placeholder: i,
  disabled: r = !1,
  rows: c = 2,
  onChange: a,
  onCommit: p
}) {
  const f = h();
  return /* @__PURE__ */ n(
    "textarea",
    {
      rows: c,
      value: t,
      placeholder: i,
      disabled: r,
      onChange: (w) => a(w.target.value),
      onBlur: p,
      style: {
        width: "100%",
        boxSizing: "border-box",
        resize: "vertical",
        padding: "0.4rem 0.55rem",
        fontFamily: f.fontProse,
        fontSize: "0.85rem",
        color: f.text,
        background: f.bg,
        border: `1px solid ${f.border}`,
        borderRadius: f.radiusMd
      }
    }
  );
}
function Z({
  value: t,
  placeholder: i,
  disabled: r = !1,
  onChange: c
}) {
  const a = h();
  return /* @__PURE__ */ n(
    "input",
    {
      type: "text",
      value: t,
      placeholder: i,
      disabled: r,
      onChange: (p) => c(p.target.value),
      style: {
        width: "100%",
        boxSizing: "border-box",
        padding: "0.4rem 0.55rem",
        fontFamily: a.fontProse,
        fontSize: "0.85rem",
        color: a.text,
        background: a.bg,
        border: `1px solid ${a.border}`,
        borderRadius: a.radiusMd
      }
    }
  );
}
function I({ children: t, selected: i = !1 }) {
  const r = h();
  return /* @__PURE__ */ n(
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
function M({ children: t }) {
  return /* @__PURE__ */ n("ul", { style: { display: "flex", flexDirection: "column", gap: "0.5rem", margin: 0, padding: 0 }, children: t });
}
function B(...t) {
  const i = /* @__PURE__ */ Object.create(null);
  for (const r of t)
    if (r)
      for (const [c, a] of Object.entries(r)) i[c] = a;
  return i;
}
function _(t, i) {
  const r = /* @__PURE__ */ Object.create(null);
  for (const c of t) {
    const a = i(c);
    (r[a] ??= []).push(c);
  }
  return r;
}
const ee = ["holds", "partly", "wrong"];
function ne({ block: t, value: i, submit: r, disabled: c }) {
  const a = t.claims, p = t.verdicts ?? ee, [f, w] = W("pending", {}), [S, $] = W("corrections", {}), u = B(i?.verdicts, f), F = (l) => S[l] ?? u[l]?.correction ?? "", b = P(
    (l, d, v) => {
      const y = v.trim() ? { verdict: d, correction: v.trim() } : { verdict: d }, z = B(u, { [l]: y });
      w(z), r({ verdicts: z });
    },
    [r, w, u]
  ), j = P(
    (l, d) => b(l, d, d === p[0] ? "" : F(l)),
    [b, p, S, u]
  ), m = P(
    (l) => {
      const d = u[l];
      d && b(l, d.verdict, F(l));
    },
    [b, u, S]
  ), k = a.filter((l) => u[l.id] !== void 0).length;
  return /* @__PURE__ */ n(H, { title: t.prompt, meta: `${k} of ${a.length} answered`, children: /* @__PURE__ */ n(M, { children: a.map((l) => {
    const d = u[l.id], v = d !== void 0 && d.verdict !== p[0];
    return /* @__PURE__ */ o(I, { selected: d !== void 0, children: [
      /* @__PURE__ */ o("div", { style: { display: "flex", alignItems: "baseline", gap: "0.45rem", flexWrap: "wrap" }, children: [
        /* @__PURE__ */ n(T, { id: l.id }),
        /* @__PURE__ */ n("span", { style: { ...R(), fontWeight: 600 }, children: l.label })
      ] }),
      l.because && /* @__PURE__ */ n("p", { style: x(), children: l.because }),
      l.ifFalse && /* @__PURE__ */ o("p", { style: { ...x(), fontStyle: "italic" }, children: [
        "If this is wrong — ",
        l.ifFalse
      ] }),
      c ? d && /* @__PURE__ */ o("div", { style: { display: "flex", flexDirection: "column", gap: "0.2rem" }, children: [
        /* @__PURE__ */ n("span", { style: C(), children: d.verdict }),
        d.correction && /* @__PURE__ */ n("p", { style: x(), children: d.correction })
      ] }) : /* @__PURE__ */ o("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" }, children: [
        /* @__PURE__ */ n("div", { style: { display: "flex", gap: "0.4rem", flexWrap: "wrap" }, children: p.map((y) => /* @__PURE__ */ n(
          D,
          {
            label: y,
            active: d?.verdict === y,
            onClick: () => j(l.id, y)
          },
          y
        )) }),
        v && /* @__PURE__ */ n(
          V,
          {
            value: F(l.id),
            placeholder: "In your words, what is actually true?",
            onChange: (y) => $({ ...S, [l.id]: y }),
            onCommit: () => m(l.id)
          }
        )
      ] })
    ] }, l.id);
  }) }) });
}
function Q({ heading: t, items: i }) {
  return /* @__PURE__ */ o("div", { style: { display: "flex", flexDirection: "column", gap: "0.2rem" }, children: [
    /* @__PURE__ */ n("span", { style: C(), children: t }),
    /* @__PURE__ */ n("ul", { style: { margin: 0, paddingLeft: "1.1rem" }, children: i.map((r) => /* @__PURE__ */ n("li", { style: x(), children: r }, r)) })
  ] });
}
function te({ block: t, value: i, submit: r, disabled: c }) {
  const a = h(), p = t.options, f = t.escape ?? {}, w = t.round, S = t.decides, $ = t.decidesTitle, u = i ?? {}, [F, b] = W("detailFor", ""), [j, m] = W("deferTitle", u.defer?.title ?? ""), [k, l] = W("deferWhy", u.defer?.why ?? ""), [d, v] = W("deferring", !1), y = P((e) => r({ choice: e }), [r]), z = P(() => {
    const e = j.trim();
    if (!e) {
      K({ kind: "error", text: "Name the open question before deferring it" });
      return;
    }
    const s = k.trim();
    r({ defer: s ? { title: e, why: s } : { title: e } }), K({ kind: "info", text: "Added to the open list" });
  }, [r, j, k]), A = $ ? `settles "${$}"` : S ? `decides ${S}` : null, q = [w !== void 0 ? `round ${w}` : null, A].filter(Boolean).join(" · ");
  return /* @__PURE__ */ o(H, { title: t.question, meta: q || void 0, children: [
    /* @__PURE__ */ n(M, { children: p.map((e) => {
      const s = u.choice === e.id, g = F === e.id, E = (e.pros?.length ?? 0) + (e.cons?.length ?? 0) > 0;
      return /* @__PURE__ */ o(I, { selected: s, children: [
        /* @__PURE__ */ o("div", { style: { display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ n("span", { style: { ...R(), fontWeight: 600 }, children: e.label }),
          e.recommended && /* @__PURE__ */ n(L, { label: "recommended", tone: "accent" })
        ] }),
        /* @__PURE__ */ n("p", { style: x(), children: e.consequence }),
        g && /* @__PURE__ */ o("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" }, children: [
          e.pros && e.pros.length > 0 && /* @__PURE__ */ n(Q, { heading: "For", items: e.pros }),
          e.cons && e.cons.length > 0 && /* @__PURE__ */ n(Q, { heading: "Against", items: e.cons })
        ] }),
        c && s && /* @__PURE__ */ n("span", { style: C(), children: "chosen" }),
        /* @__PURE__ */ o("div", { style: { display: "flex", gap: "0.4rem", flexWrap: "wrap" }, children: [
          !c && /* @__PURE__ */ n(
            D,
            {
              label: s ? "Chosen" : "Choose",
              active: s,
              onClick: () => y(e.id)
            }
          ),
          E && /* @__PURE__ */ n(
            D,
            {
              label: g ? "Hide detail" : "Detail",
              onClick: () => b(g ? "" : e.id)
            }
          )
        ] })
      ] }, e.id);
    }) }),
    u.defer ? /* @__PURE__ */ o("div", { style: { display: "flex", flexDirection: "column", gap: "0.25rem" }, children: [
      /* @__PURE__ */ n("span", { style: C(), children: "deferred to the open list" }),
      /* @__PURE__ */ n("span", { style: { ...R(), fontWeight: 600 }, children: u.defer.title }),
      u.defer.why && /* @__PURE__ */ n("p", { style: x(), children: u.defer.why })
    ] }) : !c && (d ? /* @__PURE__ */ o(
      "div",
      {
        style: {
          display: "flex",
          flexDirection: "column",
          gap: "0.45rem",
          padding: "0.6rem 0.7rem",
          border: `1px dashed ${a.border}`,
          borderRadius: a.radiusMd
        },
        children: [
          /* @__PURE__ */ n("span", { style: C(), children: f.label ?? "Add to open list" }),
          /* @__PURE__ */ n(
            Z,
            {
              value: j,
              placeholder: "Name the open question",
              onChange: m
            }
          ),
          /* @__PURE__ */ n(
            V,
            {
              value: k,
              placeholder: f.placeholder ?? "What has to be true before this can be decided?",
              onChange: l
            }
          ),
          /* @__PURE__ */ o("div", { style: { display: "flex", gap: "0.4rem" }, children: [
            /* @__PURE__ */ n(D, { label: "Add", primary: !0, onClick: z }),
            /* @__PURE__ */ n(D, { label: "Cancel", onClick: () => v(!1) })
          ] })
        ]
      }
    ) : /* @__PURE__ */ n("div", { style: { display: "flex" }, children: /* @__PURE__ */ n(D, { label: f.label ?? "Add to open list", onClick: () => v(!0) }) }))
  ] });
}
const re = { working: "ok", validate: "warn" }, ie = { resolved: "ok", superseded: "dim", open: "warn" };
function oe({ block: t, value: i, submit: r, disabled: c }) {
  const a = h(), p = t.assumptions ?? [], f = t.decisions ?? [], w = t.open ?? [], S = t.openGroups ?? {}, $ = t.challengeable === !0 && !c, [u, F] = W("pending", {}), [b, j] = W("notes", {}), m = B(i?.entries, u), k = (e) => b[e] ?? m[e]?.note ?? "", l = (e) => f.find((s) => s.id === e)?.t, d = P(
    (e, s, g) => {
      const E = g.trim() ? { action: s, note: g.trim() } : { action: s }, J = B(m, { [e]: E });
      F(J), r({ entries: J });
    },
    [r, F, m]
  ), v = P(
    (e, s) => d(e, s, s === "challenge" ? k(e) : ""),
    [d, b, m]
  ), y = P(
    (e) => {
      const s = m[e];
      s && d(e, s.action, k(e));
    },
    [d, m, b]
  ), z = (e) => {
    const s = m[e];
    return s ? $ ? s.action !== "challenge" ? null : /* @__PURE__ */ n(
      V,
      {
        value: k(e),
        placeholder: "What is wrong with it?",
        onChange: (g) => j({ ...b, [e]: g }),
        onCommit: () => y(e)
      }
    ) : /* @__PURE__ */ o("div", { style: { display: "flex", flexDirection: "column", gap: "0.2rem" }, children: [
      /* @__PURE__ */ n("span", { style: C(), children: s.action === "confirm" ? "confirmed" : "challenged" }),
      s.note && /* @__PURE__ */ n("p", { style: x(), children: s.note })
    ] }) : null;
  }, A = (e) => {
    if (!$) return null;
    const s = m[e];
    return /* @__PURE__ */ o("div", { style: { display: "flex", gap: "0.4rem" }, children: [
      /* @__PURE__ */ n(D, { label: "Confirm", active: s?.action === "confirm", onClick: () => v(e, "confirm") }),
      /* @__PURE__ */ n(D, { label: "Challenge", active: s?.action === "challenge", onClick: () => v(e, "challenge") })
    ] });
  }, q = _(w, (e) => e.g ?? "open");
  return /* @__PURE__ */ o(H, { title: t.title, meta: t.phase, children: [
    p.length > 0 && /* @__PURE__ */ o("section", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: [
      /* @__PURE__ */ n(N, { children: "Assumptions" }),
      /* @__PURE__ */ n(M, { children: p.map((e) => /* @__PURE__ */ o(I, { selected: e.star === !0, children: [
        /* @__PURE__ */ o("div", { style: { display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ n("span", { style: { ...R(), fontWeight: 600 }, children: e.t }),
          e.star && /* @__PURE__ */ n("span", { "aria-label": "load-bearing", title: "load-bearing", style: { color: a.accent }, children: "★" }),
          /* @__PURE__ */ n(L, { label: e.s, tone: re[e.s] }),
          /* @__PURE__ */ n(T, { id: e.id })
        ] }),
        e.b && /* @__PURE__ */ n("p", { style: x(), children: e.b }),
        e.n && /* @__PURE__ */ n("p", { style: { ...x(), fontStyle: "italic" }, children: e.n }),
        A(e.id),
        z(e.id)
      ] }, e.id)) })
    ] }),
    f.length > 0 && /* @__PURE__ */ o("section", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: [
      /* @__PURE__ */ n(N, { children: "Decisions" }),
      /* @__PURE__ */ n(M, { children: f.map((e) => /* @__PURE__ */ o(I, { children: [
        /* @__PURE__ */ o("div", { style: { display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ n("span", { style: { ...R(), fontWeight: 600 }, children: e.t }),
          /* @__PURE__ */ n(L, { label: e.s, tone: ie[e.s] }),
          /* @__PURE__ */ n(T, { id: e.id }),
          e.by && /* @__PURE__ */ o(X, { children: [
            /* @__PURE__ */ o("span", { style: x(), children: [
              "→ replaced by ",
              l(e.by)
            ] }),
            /* @__PURE__ */ n(T, { id: e.by })
          ] }),
          e.round !== void 0 && /* @__PURE__ */ o("span", { style: C(), children: [
            "round ",
            e.round
          ] })
        ] }),
        e.r && /* @__PURE__ */ n("p", { style: x(), children: e.r }),
        e.x && /* @__PURE__ */ n("p", { style: x(), children: e.x }),
        A(e.id),
        z(e.id)
      ] }, e.id)) })
    ] }),
    w.length > 0 && /* @__PURE__ */ o("section", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: [
      /* @__PURE__ */ n(N, { children: "Open" }),
      Object.entries(q).map(([e, s]) => /* @__PURE__ */ o("div", { style: { display: "flex", flexDirection: "column", gap: "0.35rem" }, children: [
        /* @__PURE__ */ n("span", { style: C(), children: S[e] ?? e }),
        /* @__PURE__ */ n(M, { children: s.map((g) => /* @__PURE__ */ n(I, { children: /* @__PURE__ */ o("div", { style: { display: "flex", alignItems: "baseline", gap: "0.45rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ n(T, { id: g.id }),
          /* @__PURE__ */ n("span", { style: R(), children: g.t })
        ] }) }, g.id)) })
      ] }, e))
    ] })
  ] });
}
const fe = {
  hostApi: 1,
  blocks: { registers: oe, claims: ne, fork: te }
};
export {
  fe as default
};
