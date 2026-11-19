/**
 * The orb: soft clouds drifting inside a circle in the theme's four orb colours, with a faint iridescent veil on their
 * edges, drawn by a tiny WebGL shader, with a plain gradient where WebGL is missing. The level of the voice speeds the
 * plasma up (`mode` 2: the agent speaks) or swells it (`mode` 1: the user speaks); `stepMotion` turns the phase and
 * the audio signals into the next frame, for the panel's travelling orb and the full-screen stage alike.
 */
import type { AudioSignals } from '@evokai/evokai-client/browser'

import { orbFromStyle, orbRgb, type OrbColors } from './theme'

const FRAG = `precision highp float;
uniform vec2 r; uniform float t; uniform float energy; uniform float zoom; uniform float level; uniform float mode;
uniform float sp; uniform vec4 fw; uniform float wvw; uniform float swirl; uniform float flowp; uniform float breath; uniform float wavep;
uniform vec3 cInk; uniform vec3 cMain; uniform vec3 cLight; uniform vec3 cGlow; uniform float inset;
vec3 p3(vec3 x){return mod(((x*34.)+1.)*x,289.);}
float sn(vec2 v){const vec4 C=vec4(.211324865405187,.366025403784439,-.577350269189626,.024390243902439);
 vec2 i=floor(v+dot(v,C.yy));vec2 x0=v-i+dot(i,C.xx);vec2 i1=(x0.x>x0.y)?vec2(1.,0.):vec2(0.,1.);
 vec4 x12=x0.xyxy+C.xxzz;x12.xy-=i1;i=mod(i,289.);vec3 p=p3(p3(i.y+vec3(0.,i1.y,1.))+i.x+vec3(0.,i1.x,1.));
 vec3 m=max(.5-vec3(dot(x0,x0),dot(x12.xy,x12.xy),dot(x12.zw,x12.zw)),0.);m=m*m;m=m*m;
 vec3 x=2.*fract(p*C.www)-1.;vec3 h=abs(x)-.5;vec3 ox=floor(x+.5);vec3 a0=x-ox;
 m*=1.79284291400159-.85373472095314*(a0*a0+h*h);vec3 g;g.x=a0.x*x0.x+h.x*x0.y;g.yz=a0.yz*x12.xz+h.yz*x12.yw;return 130.*dot(m,g);}
float fbm(vec2 p){float s=0.,a=.55;for(int i=0;i<3;i++){s+=a*sn(p);p*=1.9;a*=.45;}return s;}
void main(){
  vec2 uv=(gl_FragCoord.xy-.5*r)/min(r.x,r.y)*2./inset;
  float d=length(uv); if(d>1.){gl_FragColor=vec4(0.);return;}
  float T=t*.034; float L=level*sp;
  vec2 z=uv*zoom; float dz=length(z);
  float va=fw.x*sp*(swirl*1.6+L*.9)*(1.15-dz*.6); z=mat2(cos(va),-sin(va),sin(va),cos(va))*z;
  z.y-=fw.y*sp*flowp;
  z/=1.+fw.z*sp*breath*.22;
  z.y+=wvw*sp*sin(z.x*2.1-wavep)*.2*(.35+L);
  float warp=.7+fw.w*sp*(.35+L*1.1);
  vec2 q=vec2(fbm(z*.55+vec2(0.,T)),fbm(z*.55+vec2(5.2,-T*.8)));
  float n=fbm(z*.6+q*warp+vec2(T*.4,-T*.3))+fw.w*sp*L*.18*sn(z*3.4+vec2(T*3.,-T*2.));
  float cloud=smoothstep(-.55,.75,n-uv.y*zoom*.35+(mode>.5&&mode<1.5?.1*level:0.));
  float diag=clamp((uv.x-uv.y)*.35+.5,0.,1.);
  vec3 col=mix(mix(cInk*1.4,cMain,diag),cLight,cloud*.92);
  col=mix(col,cGlow,smoothstep(.7,1.,cloud)*.6);
  float edge=smoothstep(.1,.5,cloud)*(1.-smoothstep(.5,.95,cloud));
  vec3 iri=.5+.5*cos(6.2831*(vec3(.0,.33,.67)+n*.6+T*1.5+d*.8));
  col+=edge*(iri*vec3(.25,.35,.3)-.08)*(.35+.4*energy);
  col*=1.-.18*smoothstep(.75,1.,d);
  float a=1.-smoothstep(.985,1.,d);
  gl_FragColor=vec4(col*a,a);
}`
const VERT = 'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}'

/** Weights of the plasma motions while the agent speaks: vortex, current, breath, density, wave. */
export interface FlowWeights {
  vortex?: number
  current?: number
  breath?: number
  density?: number
  wave?: number
}

export interface OrbFrame {
  t: number
  energy: number
  level: number
  /** 0 idle or listening, 1 the user speaks, 2 the agent speaks. */
  mode: number
  sp: number
  flow: FlowWeights
  swirl: number
  flowp: number
  breath: number
  wavep: number
  /** The plasma's scale; by default it follows the canvas size (bigger clouds on a small orb). */
  zoom?: number
}

export interface OrbRenderer {
  draw(f: OrbFrame): void
  /** Read the colours again (the host's --evokai-orb-* may have changed since). */
  recolor(colors?: Partial<OrbColors>): void
  dispose(): void
}

/** A plain CSS gradient in the orb's colours (no WebGL, or before the first frame). */
export function orbFallback(colors?: Partial<OrbColors>): string {
  const c = { ink: '#1e0b3d', main: '#7c3aed', light: '#c4b5fd', ...colors }
  return `radial-gradient(circle at 40% 30%, ${c.light}, transparent 45%), linear-gradient(135deg, ${c.ink}, ${c.main})`
}

/** Compile the shader on a canvas of `size` CSS pixels; null without WebGL. */
export function createOrb(canvas: HTMLCanvasElement, size: number, colors?: Partial<OrbColors>, inset = 1): OrbRenderer | null {
  // The colours set on the host (--evokai-orb-*: a page stylesheet or the theme) win over the defaults.
  colors = orbFromStyle(canvas, colors)
  canvas.style.background = orbFallback(colors)
  const dpr = Math.min(2, (typeof devicePixelRatio === 'number' && devicePixelRatio) || 1)
  canvas.width = canvas.height = Math.round(size * dpr)
  let gl: WebGLRenderingContext | null = null
  try {
    gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: true })
  } catch {
    gl = null
  }
  if (!gl) return null
  const g = gl
  const shader = (type: number, src: string) => {
    const s = g.createShader(type) as WebGLShader
    g.shaderSource(s, src)
    g.compileShader(s)
    return s
  }
  const pr = g.createProgram() as WebGLProgram
  g.attachShader(pr, shader(g.VERTEX_SHADER, VERT))
  g.attachShader(pr, shader(g.FRAGMENT_SHADER, FRAG))
  g.linkProgram(pr)
  if (!g.getProgramParameter(pr, g.LINK_STATUS)) return null
  g.useProgram(pr)
  g.bindBuffer(g.ARRAY_BUFFER, g.createBuffer())
  g.bufferData(g.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), g.STATIC_DRAW)
  const loc = g.getAttribLocation(pr, 'p')
  g.enableVertexAttribArray(loc)
  g.vertexAttribPointer(loc, 2, g.FLOAT, false, 0, 0)
  const U = (n: string) => g.getUniformLocation(pr, n)
  const paint = (c?: Partial<OrbColors>) => {
    const rgb = orbRgb(c)
    g.uniform3f(U('cInk'), ...rgb.ink)
    g.uniform3f(U('cMain'), ...rgb.main)
    g.uniform3f(U('cLight'), ...rgb.light)
    g.uniform3f(U('cGlow'), ...rgb.glow)
  }
  paint(colors)
  g.uniform1f(U('inset'), inset)
  const u = {
    r: U('r'), t: U('t'), e: U('energy'), z: U('zoom'), l: U('level'), m: U('mode'), sp: U('sp'), fw: U('fw'),
    wvw: U('wvw'), sw: U('swirl'), fp: U('flowp'), br: U('breath'), wp: U('wavep'),
  }
  return {
    draw(f) {
      g.viewport(0, 0, canvas.width, canvas.height)
      g.uniform2f(u.r, canvas.width, canvas.height)
      g.uniform1f(u.t, f.t)
      g.uniform1f(u.e, f.energy)
      g.uniform1f(u.z, f.zoom ?? orbZoom(size))
      g.uniform1f(u.l, f.level)
      g.uniform1f(u.m, f.mode)
      g.uniform1f(u.sp, f.sp)
      g.uniform4f(u.fw, f.flow.vortex ?? 0, f.flow.current ?? 0, f.flow.breath ?? 0, f.flow.density ?? 0)
      g.uniform1f(u.wvw, f.flow.wave ?? 0)
      g.uniform1f(u.sw, f.swirl)
      g.uniform1f(u.fp, f.flowp)
      g.uniform1f(u.br, f.breath)
      g.uniform1f(u.wp, f.wavep)
      g.drawArrays(g.TRIANGLE_STRIP, 0, 4)
    },
    recolor(c) {
      const next = orbFromStyle(canvas, c)
      canvas.style.background = orbFallback(next)
      paint(next)
    },
    dispose() {
      g.getExtension('WEBGL_lose_context')?.loseContext()
    },
  }
}

export const reducedMotion = (): boolean =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

/** The plasma's scale for an orb of `size` CSS pixels. */
export const orbZoom = (size: number): number => Math.min(1, Math.max(0.5, size / 200))

/** The plasma motion while the agent speaks (a slow breath). */
export const FLOW: FlowWeights = { wave: 0.7, breath: 0.9 }

/** What the orb shows: idle (drifting, faster while busy), or a voice phase. */
export type OrbPhase = 'idle' | 'listening' | 'user' | 'thinking' | 'speaking'

/** The state carried from frame to frame. */
export interface Motion {
  t: number
  level: number
  energy: number
  sp: number
  swirl: number
  flowp: number
  breath: number
  wavep: number
}

export const newMotion = (): Motion => ({ t: 30 + Math.random() * 60, level: 0, energy: 0, sp: 0, swirl: 0, flowp: 0, breath: 0, wavep: 0 })

export interface MotionInput {
  phase: OrbPhase
  /** Idle only: a turn is running. */
  busy?: boolean
  muted?: boolean
  /** The orb is travelling: the plasma stirs. */
  stirred?: boolean
  signals?: AudioSignals | null | undefined
  now: number
  /** Seconds since the previous frame. */
  dt: number
}

/**
 * The next frame. While the user speaks the level follows their voice; while the agent speaks only the plasma moves,
 * faster as the level rises (with the browser's voice the level is estimated from syllables).
 */
export function stepMotion(m: Motion, i: MotionInput): { frame: OrbFrame; mode: number } {
  const { phase: ph, now, dt } = i
  const mode = ph === 'user' ? 1 : ph === 'speaking' ? 2 : 0
  let target = 0
  let pulse = 0
  const breathe = 0.06 + 0.04 * Math.sin(now / 625)
  if ((ph === 'user' || ph === 'listening') && !i.muted) {
    const u = i.signals?.current('user')
    const lvl = u?.level ?? 0
    target = ph === 'user' ? lvl : Math.min(0.12, lvl * 0.4)
    if (!u && ph === 'listening') target = breathe
  } else if (ph === 'speaking') {
    const a = i.signals?.current('agent')
    target = a?.level ?? 0
    pulse = a?.onset ? a.intensity : 0
  } else if (ph === 'listening') target = breathe
  const speaking = mode === 2
  m.level += (target - m.level) * (speaking ? (target > m.level ? 0.09 : 0.045) : target > m.level ? 0.35 : 0.12)
  let e = ph === 'thinking' ? 1 : speaking ? 0.45 : ph === 'user' ? 0.5 : ph === 'idle' && i.busy ? 1 : 0
  if (i.stirred) e = Math.max(e, 0.8)
  m.energy += (e - m.energy) * (ph === 'idle' ? 0.03 : 0.05)
  m.t += dt * (speaking ? 2.3 + m.level * 1.6 : 1 + m.energy * 2.2 + m.level * 2)
  m.sp += ((speaking ? 1 : 0) - m.sp) * 0.05
  m.swirl += dt * m.level * (speaking ? 1.4 : 0)
  if (!speaking) m.swirl *= 0.985
  m.flowp += dt * (0.2 + m.level * 0.9) * m.sp
  m.breath += (pulse - m.breath) * (pulse > m.breath ? 0.12 : 0.06)
  m.wavep += dt * (1.8 + m.level * 3.2) * m.sp
  return {
    frame: { t: m.t, energy: m.energy, level: m.level, mode, sp: m.sp, flow: FLOW, swirl: m.swirl, flowp: m.flowp, breath: m.breath, wavep: m.wavep },
    mode,
  }
}

/** How much the voice swells the orb (only the user's voice changes its size; the agent's lives inside it). */
export const swell = (level: number, mode: number): number => 1 + level * (mode === 1 ? 0.22 : mode === 2 ? 0 : 0.06)
