// 三个克制的动效 web component（零依赖，自注册）。
//   <count-up value="86">          数字/含数字的字符串变化时滚动过渡
//   <type-line text="…" active>    逐字打字（active 变 true 时开始）
//   <dot-grid>                     极低对比点阵，随鼠标轻微位移
// 全部尊重 prefers-reduced-motion。
(() => {
  const reduce = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- count-up ----------
  if (!customElements.get('count-up')) {
    const NUM = /-?\d[\d,]*\.?\d*/g;
    class CountUp extends HTMLElement {
      static get observedAttributes() { return ['value', 'duration']; }
      connectedCallback() { this.style.display = 'inline'; this._render(this.getAttribute('value') || '', true); }
      attributeChangedCallback(n, o, v) { if (n === 'value' && o !== v && this.isConnected) this._render(v || '', false); }
      _render(next, instant) {
        const prevNums = (this._shown || '').match(NUM) || [];
        const nextNums = next.match(NUM) || [];
        if (instant || reduce() || !nextNums.length || prevNums.length !== nextNums.length) { this.textContent = next; this._shown = next; return; }
        const from = prevNums.map(x => parseFloat(String(x).replace(/,/g, '')));
        const to = nextNums.map(x => parseFloat(String(x).replace(/,/g, '')));
        if (from.every((v, i) => v === to[i])) { this.textContent = next; this._shown = next; return; }
        const dur = parseFloat(this.getAttribute('duration')) || 420;
        const t0 = performance.now(); cancelAnimationFrame(this._raf);
        const tick = t => {
          const p = Math.min(1, (t - t0) / dur); const e = 1 - Math.pow(1 - p, 3);
          let i = 0;
          this.textContent = next.replace(NUM, m => {
            const dec = (String(m).split('.')[1] || '').length;
            const v = from[i] + (to[i] - from[i]) * e; i++;
            return v.toFixed(dec);
          });
          if (p < 1) this._raf = requestAnimationFrame(tick); else { this.textContent = next; this._shown = next; }
        };
        this._raf = requestAnimationFrame(tick);
      }
      disconnectedCallback() { cancelAnimationFrame(this._raf); }
    }
    customElements.define('count-up', CountUp);
  }

  // ---------- type-line ----------
  if (!customElements.get('type-line')) {
    class TypeLine extends HTMLElement {
      static get observedAttributes() { return ['text', 'active', 'speed']; }
      connectedCallback() { this.style.display = 'block'; this._sync(); }
      attributeChangedCallback() { if (this.isConnected) this._sync(); }
      _sync() {
        const text = this.getAttribute('text') || '';
        const on = this.getAttribute('active') === 'true' || this.hasAttribute('active') && this.getAttribute('active') !== 'false';
        if (!on) { clearInterval(this._iv); this.textContent = ''; this._done = ''; return; }
        if (this._done === text) return;
        clearInterval(this._iv);
        if (reduce()) { this.textContent = text; this._done = text; return; }
        const speed = parseFloat(this.getAttribute('speed')) || 22;
        let i = 0; this.textContent = '';
        this._iv = setInterval(() => {
          i += 1; this.textContent = text.slice(0, i);
          if (i >= text.length) { clearInterval(this._iv); this._done = text; }
        }, speed);
      }
      disconnectedCallback() { clearInterval(this._iv); }
    }
    customElements.define('type-line', TypeLine);
  }

  // ---------- dot-grid ----------
  if (!customElements.get('dot-grid')) {
    class DotGrid extends HTMLElement {
      connectedCallback() {
        if (this._init) return; this._init = true;
        Object.assign(this.style, { display: 'block', position: this.style.position || 'absolute', inset: '0', pointerEvents: 'none' });
        const c = document.createElement('canvas');
        Object.assign(c.style, { width: '100%', height: '100%', display: 'block' });
        this.appendChild(c); this._c = c; this._ctx = c.getContext('2d');
        this._m = [0.5, 0.5]; this._t = [0.5, 0.5];
        this._onMove = e => { const r = this.getBoundingClientRect(); this._t = [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]; };
        window.addEventListener('pointermove', this._onMove, { passive: true });
        this._resize = () => { const dpr = Math.min(devicePixelRatio || 1, 2), r = this.getBoundingClientRect(); c.width = Math.max(1, r.width * dpr | 0); c.height = Math.max(1, r.height * dpr | 0); this._dpr = dpr; this._draw(); };
        this._ro = new ResizeObserver(this._resize); this._ro.observe(this); this._resize();
        this._vis = true;
        this._io = new IntersectionObserver(([e]) => { this._vis = e.isIntersecting; this._vis ? this._start() : this._stop(); }, { threshold: 0 });
        this._io.observe(this); this._start();
      }
      _draw() {
        const ctx = this._ctx, c = this._c, dpr = this._dpr || 1;
        const gap = (parseFloat(this.getAttribute('gap')) || 26) * dpr;
        const r = (parseFloat(this.getAttribute('dot')) || 1) * dpr;
        const color = this.getAttribute('color') || 'rgba(243,241,234,0.13)';
        const shift = (parseFloat(this.getAttribute('shift')) || 5) * dpr;
        ctx.clearRect(0, 0, c.width, c.height); ctx.fillStyle = color;
        const mx = this._m[0] * c.width, my = this._m[1] * c.height;
        const reach = 190 * dpr;
        for (let x = gap / 2; x < c.width; x += gap) {
          for (let y = gap / 2; y < c.height; y += gap) {
            const dx = x - mx, dy = y - my; const d = Math.hypot(dx, dy);
            let ox = 0, oy = 0, s = r;
            if (d < reach && d > 0.001) { const f = (1 - d / reach); ox = (dx / d) * shift * f; oy = (dy / d) * shift * f; s = r * (1 + f * 0.9); }
            ctx.beginPath(); ctx.arc(x + ox, y + oy, s, 0, 6.2832); ctx.fill();
          }
        }
      }
      _frame = () => {
        this._m[0] += 0.06 * (this._t[0] - this._m[0]); this._m[1] += 0.06 * (this._t[1] - this._m[1]);
        this._draw();
        this._raf = requestAnimationFrame(this._frame);
      };
      _start() { if (reduce()) { this._draw(); return; } if (!this._raf && this._vis) this._raf = requestAnimationFrame(this._frame); }
      _stop() { if (this._raf) { cancelAnimationFrame(this._raf); this._raf = 0; } }
      disconnectedCallback() { this._stop(); this._ro && this._ro.disconnect(); this._io && this._io.disconnect(); window.removeEventListener('pointermove', this._onMove); this._init = false; }
    }
    customElements.define('dot-grid', DotGrid);
  }
})();
