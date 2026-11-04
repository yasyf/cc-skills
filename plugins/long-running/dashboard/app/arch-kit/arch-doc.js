import { mount, toMarkdown } from "./vendor/arch-map.js";

const fromHash = () => Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
const toHash = (view) => {
  const q = new URLSearchParams(Object.entries(view).filter(([, v]) => v));
  history.replaceState(null, "", q.size ? `#${q}` : location.pathname + location.search);
};

class ArchDoc extends HTMLElement {
  async connectedCallback() {
    if (this.handle) return;
    const src = new URL(this.getAttribute("src"), document.baseURI);
    const doc = await fetch(src).then((r) => r.json());
    const hash = this.hasAttribute("hash");
    this.handle = mount(this, doc, { fill: this.hasAttribute("fill"), state: hash ? fromHash() : undefined, onState: hash ? toHash : undefined });
    this.dispatchEvent(new CustomEvent("arch-ready", { bubbles: true, detail: { doc, markdown: toMarkdown(doc) } }));
  }

  disconnectedCallback() {
    if (this.handle) this.handle.destroy();
    this.handle = null;
  }
}

customElements.define("arch-doc", ArchDoc);
