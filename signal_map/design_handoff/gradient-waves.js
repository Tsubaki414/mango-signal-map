// <gradient-waves> — 自包含 WebGL2 波浪着色器（无外部依赖，不需要 ogl / npm）。
// 改写自 React + ogl 版本：同一片段着色器，颜色改为 Mango 主题色。
// 属性：horizon / wave / crest（hex）· speed amplitude wave-scale wave-ratio swell turbulence
//       tilt zoom height fog-depth detail(low|medium|high) brightness opacity
//       grain grain-intensity parallax mouse(true|false)
(() => {
  if (customElements.get('gradient-waves')) return;

  const VERT = `#version 300 es
in vec2 position;
void main(){ gl_Position = vec4(position, 0.0, 1.0); }`;

  const FRAG = `#version 300 es
precision highp float;
uniform vec2 iResolution; uniform float iTime;
uniform float uSpeed,uAmplitude,uWaveScale,uWaveRatio,uSwell,uTurbulence,uTilt,uZoom,uHeight,uFogDepth,uSteps,uBrightness,uOpacity,uGrain,uGrainIntensity,uParallax;
uniform vec2 uMouse; uniform bool uEnableMouse;
uniform vec3 uHorizonColor,uWaveColor,uCrestColor;
out vec4 fragColor;
const float MAX_DIST = 20000.0;
float hash21(vec2 p){ vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float plasma(vec3 r, vec2 freq, vec4 tc){
  float mx = r.x + tc.x; mx += uSwell * sin((r.y + mx) / 20.0 + tc.y);
  float my = r.y - tc.z; my += uTurbulence * cos(r.x / 23.0 + tc.w);
  return r.z - (sin(mx * freq.x) * uAmplitude + sin(my * freq.y) * uAmplitude + uHeight);
}
float raymarch(vec3 pos, vec3 dir, vec2 freq, vec4 tc){
  float dist = 0.0;
  for (int i = 0; i < 128; i++){
    if (float(i) >= uSteps) break;
    float d = plasma(pos + dist * dir, freq, tc);
    if (abs(d) < 0.1) break;
    dist += 0.9 * d;
    if (!(abs(dist) < MAX_DIST)) return MAX_DIST;
  }
  return dist;
}
void main(){
  float T = iTime * uSpeed;
  vec2 freq = vec2(uWaveScale / 7.0, (uWaveScale * uWaveRatio) / 3.0);
  vec4 tc = vec4(T / 0.130, T / 0.810, T / 0.200, T / 0.710);
  float c, s;
  float vfov = (3.14159 / 2.3) / max(uZoom, 0.05);
  vec3 cam = vec3(0.0, 0.0, 30.0);
  vec2 uv = (gl_FragCoord.xy / iResolution.xy) - 0.5;
  uv.x *= iResolution.x / iResolution.y; uv.y *= -1.0;
  vec3 dir = vec3(0.0, 0.0, -1.0);
  float ulen = length(uv);
  float xrot = vfov * ulen;
  c = cos(xrot); s = sin(xrot);
  dir = mat3(1.0,0.0,0.0, 0.0,c,-s, 0.0,s,c) * dir;
  vec2 nuv = ulen > 1e-5 ? uv / ulen : vec2(1.0, 0.0);
  c = nuv.x; s = nuv.y;
  dir = mat3(c,-s,0.0, s,c,0.0, 0.0,0.0,1.0) * dir;
  c = cos(uTilt); s = sin(uTilt);
  dir = mat3(c,0.0,s, 0.0,1.0,0.0, -s,0.0,c) * dir;
  if (uEnableMouse){
    float yaw = (uMouse.x - 0.5) * uParallax * 0.4;
    float pitch = (uMouse.y - 0.5) * uParallax * 0.4;
    c = cos(yaw); s = sin(yaw);
    dir = mat3(c,0.0,s, 0.0,1.0,0.0, -s,0.0,c) * dir;
    c = cos(pitch); s = sin(pitch);
    dir = mat3(1.0,0.0,0.0, 0.0,c,-s, 0.0,s,c) * dir;
  }
  float dist = raymarch(cam, dir, freq, tc);
  vec3 pos = cam + dist * dir;
  float t = clamp(uFogDepth / max(dist, 0.001), 0.0, 1.0);
  vec3 body = mix(uWaveColor, uCrestColor, clamp(pos.z * 0.08 + 0.5, 0.0, 1.0));
  vec3 col = mix(uHorizonColor, body, t);
  col *= uBrightness; col = clamp(col, 0.0, 1.0);
  float alpha = clamp(t, 0.0, 1.0) * uOpacity;
  if (uGrain > 0.5){ float g = hash21(gl_FragCoord.xy + mod(iTime, 64.0) * 11.0); alpha += (g - 0.5) * uGrainIntensity; }
  alpha = clamp(alpha, 0.0, 1.0);
  fragColor = vec4(col * alpha, alpha);
}`;

  const hexToRgb = hex => {
    const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(String(hex || ''));
    return m ? [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255] : [1, 1, 1];
  };
  const steps = d => (d === 'low' ? 40 : d === 'high' ? 110 : 70);

  const DEFAULTS = {
    horizon: '#132B2C', wave: '#1F4144', crest: '#5FC4BB',
    speed: 0.28, amplitude: 2.4, 'wave-scale': 0.6, 'wave-ratio': 0.9, swell: 35, turbulence: 20,
    tilt: 1.11, zoom: 1, height: 5.5, 'fog-depth': 15, detail: 'medium',
    brightness: 1, opacity: 1, grain: 'true', 'grain-intensity': 0.05, parallax: 0.5, mouse: 'true',
    blend: 'screen', 'layer-opacity': 1,
  };

  class GradientWaves extends HTMLElement {
    connectedCallback() {
      if (this._init) return; this._init = true;
      this.style.display = 'block'; this.style.position = this.style.position || 'relative';
      this.style.overflow = 'hidden'; this.style.width = '100%'; this.style.height = '100%';
      this.style.pointerEvents = 'none';
      this.style.mixBlendMode = this.attr('blend');
      this.style.opacity = this.attr('layer-opacity');
      const canvas = document.createElement('canvas');
      Object.assign(canvas.style, { width: '100%', height: '100%', display: 'block' });
      this.appendChild(canvas); this._canvas = canvas;
      const gl = canvas.getContext('webgl2', { alpha: true, premultipliedAlpha: true, antialias: false });
      if (!gl) { this.style.background = 'linear-gradient(180deg,#2C5A5D,#14302F)'; return; }
      this._gl = gl;
      const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.warn('[gradient-waves]', gl.getShaderInfoLog(s)); return s; };
      const prog = gl.createProgram();
      gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT));
      gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
      gl.linkProgram(prog); gl.useProgram(prog); this._prog = prog;
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(prog, 'position');
      gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      gl.clearColor(0, 0, 0, 0); gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      this._u = n => gl.getUniformLocation(prog, n);
      this._mouse = [0.5, 0.5]; this._target = [0.5, 0.5]; this._t0 = performance.now();

      this._onMove = e => { const r = canvas.getBoundingClientRect(); this._target[0] = (e.clientX - r.left) / r.width; this._target[1] = 1 - (e.clientY - r.top) / r.height; };
      this._onLeave = () => { this._target[0] = 0.5; this._target[1] = 0.5; };
      window.addEventListener('pointermove', this._onMove, { passive: true });
      canvas.addEventListener('pointerleave', this._onLeave);

      this._resize = () => { const dpr = Math.min(window.devicePixelRatio || 1, 2); const r = this.getBoundingClientRect(); canvas.width = Math.max(1, Math.floor(r.width * dpr)); canvas.height = Math.max(1, Math.floor(r.height * dpr)); gl.viewport(0, 0, canvas.width, canvas.height); };
      this._ro = new ResizeObserver(this._resize); this._ro.observe(this); this._resize();

      this._visible = true;
      this._io = new IntersectionObserver(([e]) => { this._visible = e.isIntersecting; this._visible ? this._start() : this._stop(); }, { threshold: 0 });
      this._io.observe(this);
      this._onVis = () => (document.hidden ? this._stop() : this._start());
      document.addEventListener('visibilitychange', this._onVis);
      this._start();
    }
    attr(name) { const v = this.getAttribute(name) ?? this.getAttribute(name.replace(/-/g, '')); return v == null || v === '' ? DEFAULTS[name] : v; }
    num(name) { return parseFloat(this.attr(name)); }
    _frame = t => {
      const gl = this._gl, u = this._u;
      const mouseOn = String(this.attr('mouse')) !== 'false';
      const tx = mouseOn ? this._target[0] : 0.5, ty = mouseOn ? this._target[1] : 0.5;
      this._mouse[0] += 0.05 * (tx - this._mouse[0]); this._mouse[1] += 0.05 * (ty - this._mouse[1]);
      gl.uniform1f(u('iTime'), (t - this._t0) * 0.001);
      gl.uniform2f(u('iResolution'), this._canvas.width, this._canvas.height);
      gl.uniform1f(u('uSpeed'), this.num('speed'));
      gl.uniform1f(u('uAmplitude'), this.num('amplitude'));
      gl.uniform1f(u('uWaveScale'), this.num('wave-scale'));
      gl.uniform1f(u('uWaveRatio'), this.num('wave-ratio'));
      gl.uniform1f(u('uSwell'), this.num('swell'));
      gl.uniform1f(u('uTurbulence'), this.num('turbulence'));
      gl.uniform1f(u('uTilt'), this.num('tilt'));
      gl.uniform1f(u('uZoom'), this.num('zoom'));
      gl.uniform1f(u('uHeight'), this.num('height'));
      gl.uniform1f(u('uFogDepth'), this.num('fog-depth'));
      gl.uniform1f(u('uSteps'), steps(this.attr('detail')));
      gl.uniform1f(u('uBrightness'), this.num('brightness'));
      gl.uniform1f(u('uOpacity'), this.num('opacity'));
      gl.uniform1f(u('uGrain'), String(this.attr('grain')) === 'false' ? 0 : 1);
      gl.uniform1f(u('uGrainIntensity'), this.num('grain-intensity'));
      gl.uniform1f(u('uParallax'), this.num('parallax'));
      gl.uniform2f(u('uMouse'), this._mouse[0], this._mouse[1]);
      gl.uniform1i(u('uEnableMouse'), mouseOn ? 1 : 0);
      gl.uniform3fv(u('uHorizonColor'), hexToRgb(this.attr('horizon')));
      gl.uniform3fv(u('uWaveColor'), hexToRgb(this.attr('wave')));
      gl.uniform3fv(u('uCrestColor'), hexToRgb(this.attr('crest')));
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      this._raf = requestAnimationFrame(this._frame);
    };
    _start() { if (!this._raf && this._gl && this._visible && !document.hidden) this._raf = requestAnimationFrame(this._frame); }
    _stop() { if (this._raf) { cancelAnimationFrame(this._raf); this._raf = 0; } }
    disconnectedCallback() {
      this._stop(); if (this._ro) this._ro.disconnect(); if (this._io) this._io.disconnect();
      document.removeEventListener('visibilitychange', this._onVis);
      window.removeEventListener('pointermove', this._onMove);
      if (this._gl) this._gl.getExtension('WEBGL_lose_context')?.loseContext();
      this._init = false;
    }
  }
  customElements.define('gradient-waves', GradientWaves);
})();
