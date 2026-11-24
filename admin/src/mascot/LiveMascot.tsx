/**
 * The mascot next to the assistant's chat. Most states play the existing animations (`public/fx`); listening and
 * speaking are drawn live: the body image (`public/mascot/body.png`) is warped in WebGL (breathing, the hat's sway,
 * a lean) with the eyes drawn by the shader, and a 2D canvas on top carries the sparks.
 *
 * - Listening: it looks up a little towards the person, the hat tip leans to them, and it nods on each pause.
 * - Speaking: the eyes light up with the voice's envelope (no squash), sparks rise from the staff when it ends.
 *
 * The "still" mascot preference and reduced motion show the still frame; "hidden" shows nothing. Without WebGL the
 * live states fall back to the animations.
 */
import { useEffect, useRef, useState } from 'preact/hooks'

import type { AudioSignal, AudioSignals } from '@client/browser/index'
import { mascotMoves, onPrefs } from '../prefs'
import { Listener, nodDip } from './listener'
import { CLIP_MS, clipSrc, type MascotView, stillFor } from './state'

interface Meta {
  W: number
  eyes: Array<{ x: number; y: number; r: number }>
  eyecol: [number, number, number]
  feetY: number
  pivot: [number, number]
  swayY: [number, number]
  swayX: [number, number]
  tip: [number, number]
}

const VS = 'attribute vec2 p; varying vec2 uv; void main(){ uv = p*0.5+0.5; uv.y = 1.0-uv.y; gl_Position = vec4(p,0,1); }'
const FS = `precision highp float; varying vec2 uv;
uniform sampler2D tex; uniform float W, feetY, breathe, sway, tilt, dy;
uniform vec2 pivot, swayY, swayX; uniform vec3 eye0, eye1, eyeCol, pale;
uniform float eyeOpen, eyeScale, eyeGlow; uniform vec2 eyeShift;
float ss(float e0, float e1, float x){ float t = clamp((x-e0)/(e1-e0),0.0,1.0); return t*t*(3.0-2.0*t); }
vec4 eye(vec2 q, vec3 e, vec4 col){
  float r = e.z*eyeScale, ry = max(r*eyeOpen, 1.2);
  vec2 d = q - (e.xy + eyeShift);
  float k = length(vec2(d.x/r, d.y/ry));
  float dist = (k-1.0)*min(r, ry);
  float cov = clamp(0.5-dist, 0.0, 1.0);
  vec3 c = mix(eyeCol, pale, clamp(eyeGlow*0.7, 0.0, 0.85));
  float h = exp(-dot(d,d)/(r*r*3.2)) * eyeGlow * 0.55 * col.a * (1.0-cov);
  col.rgb += c*h;
  return col*(1.0-cov) + vec4(c,1.0)*cov*col.a;
}
void main(){
  vec2 p = uv*W;
  vec2 q = p - vec2(0.0, dy);
  vec2 f = vec2(W*0.5, feetY);
  float ct = cos(-tilt), st = sin(-tilt);
  vec2 r0 = q - f; q = f + vec2(ct*r0.x - st*r0.y, st*r0.x + ct*r0.y);
  float sy = 1.0+breathe, sx = 1.0-0.5*breathe;
  float X = (q.x - W*0.5)/sx + W*0.5, Y = (q.y - feetY)/sy + feetY;
  float w = ss(swayY.x, swayY.y, Y) * ss(swayX.x, swayX.y, X);
  float a = -sway*w; float ca = cos(a), sa = sin(a);
  vec2 rr = vec2(X,Y) - pivot;
  vec2 s = pivot + vec2(ca*rr.x - sa*rr.y, sa*rr.x + ca*rr.y);
  vec4 col = texture2D(tex, s/W);
  col = eye(s, eye0, col); col = eye(s, eye1, col);
  gl_FragColor = col;
}`

interface Spring {
  x: number
  v: number
  t: number
  step: (dt: number) => void
}
const spring = (k: number, c: number, x = 0): Spring => ({
  x,
  v: 0,
  t: x,
  step(dt) {
    this.v += (k * (this.t - this.x) - c * this.v) * dt
    this.x += this.v * dt
  },
})

interface Spark {
  x: number
  y: number
  vx: number
  vy: number
  life: number
  max: number
  r: number
  rot: number
}

/** The live renderer: WebGL body and eyes, 2D sparks. Returns null without WebGL. */
function createRenderer(gl: WebGLRenderingContext, fx: CanvasRenderingContext2D, meta: Meta, body: HTMLImageElement) {
  const sh = (type: number, src: string) => {
    const s = gl.createShader(type)!
    gl.shaderSource(s, src)
    gl.compileShader(s)
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader')
    return s
  }
  const prog = gl.createProgram()!
  gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS))
  gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS))
  gl.linkProgram(prog)
  gl.useProgram(prog)
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer())
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
  const loc = gl.getAttribLocation(prog, 'p')
  gl.enableVertexAttribArray(loc)
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
  gl.bindTexture(gl.TEXTURE_2D, gl.createTexture())
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true)
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, body)
  for (const [k, v] of [
    [gl.TEXTURE_MIN_FILTER, gl.LINEAR],
    [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
    [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE],
    [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE],
  ] as const)
    gl.texParameteri(gl.TEXTURE_2D, k, v)
  const U = (n: string) => gl.getUniformLocation(prog, n)
  const [e0, e1] = [meta.eyes[0]!, meta.eyes[1]!]
  gl.uniform1f(U('W'), meta.W)
  gl.uniform1f(U('feetY'), meta.feetY)
  gl.uniform2f(U('pivot'), ...meta.pivot)
  gl.uniform2f(U('swayY'), ...meta.swayY)
  gl.uniform2f(U('swayX'), ...meta.swayX)
  gl.uniform3f(U('eye0'), e0.x, e0.y, e0.r)
  gl.uniform3f(U('eye1'), e1.x, e1.y, e1.r)
  gl.uniform3f(U('eyeCol'), ...meta.eyecol)
  gl.uniform3f(U('pale'), 0.965, 0.91, 1.0)

  const S = { sway: spring(90, 9), tilt: spring(60, 12), eyeScale: spring(140, 16, 1), glow: spring(160, 18), open: spring(500, 30, 1), dy: spring(200, 18) }
  const sparks: Spark[] = []
  let nodAt = -9
  let nodNow = false
  let env = 0
  let kick = 0
  let mic = 0
  let mode: 'listen' | 'speak' = 'listen'
  let nextBlink = 2.5
  let blinkUntil = 0
  const listener = new Listener({
    onSyllable: () => undefined,
    onPause: () => {
      if (mode === 'listen') nodNow = true
    },
  })
  const burst = (n: number) => {
    for (let i = 0; i < n; i++)
      sparks.push({ x: meta.tip[0], y: meta.tip[1] - 20, vx: (Math.random() - 0.3) * 120, vy: -120 - Math.random() * 160, life: 0, max: 0.9 + Math.random() * 0.6, r: 5 + Math.random() * 7, rot: Math.random() * 6 })
  }
  const star = (x: number, y: number, r: number, rot: number) => {
    fx.save()
    fx.translate(x, y)
    fx.rotate(rot)
    fx.beginPath()
    for (let i = 0; i < 8; i++) {
      const a = (i * Math.PI) / 4
      const rr = i % 2 ? r * 0.28 : r
      fx.lineTo(Math.cos(a) * rr, Math.sin(a) * rr)
    }
    fx.closePath()
    fx.fill()
    fx.restore()
  }

  return {
    setMode(m: 'listen' | 'speak') {
      if (mode === 'speak' && m !== 'speak') burst(14)
      mode = m
    },
    /** A frame of the person's microphone or of the agent's voice. */
    signal(s: AudioSignal) {
      if (s.source === 'user') mic += ((s.rms ?? s.level) - mic) * (s.rms > mic ? 0.5 : 0.12)
      else if (s.onset) kick += (s.intensity ?? 0) > 0.6 ? 0.55 : 0.32
      else if (!s.estimated) kick = Math.max(kick, s.rms * 0.8)
    },
    frame(now: number, dt: number, width: number) {
      const breathe = 0.006 * Math.sin((2 * Math.PI * now) / 2.0)
      let swayBase = 0.03 * Math.sin((2 * Math.PI * now) / 4.0)
      if (mode === 'listen') {
        listener.step(mic, now)
        mic *= 0.97
        // It leans a little towards the person, the hat tip turns to them, and it nods on each pause.
        if (nodNow) {
          nodNow = false
          nodAt = now
          S.dy.v += 120
        }
        S.eyeScale.t = 1.06
        S.glow.t = 0.3
        S.tilt.t = -0.04
        swayBase = 0.12 + 0.01 * Math.sin(2 * Math.PI * now * 0.8)
      } else {
        env += (Math.min(1, kick) - env) * Math.min(1, dt * 18)
        kick *= Math.exp(-dt / 0.22)
        S.tilt.t = 0
        S.eyeScale.t = 1
        S.glow.t = 0.22 + env * 1.1
      }
      S.open.t = 1
      if (mode !== 'speak' && now > nextBlink) {
        blinkUntil = now + 0.16
        nextBlink = now + 2.6 + Math.random() * 2.6
      }
      if (now < blinkUntil) S.open.t = 0.08
      for (const k of Object.values(S)) k.step(dt)
      gl.uniform1f(U('breathe'), breathe)
      gl.uniform1f(U('sway'), swayBase + S.sway.x * 0.06)
      gl.uniform1f(U('tilt'), S.tilt.x)
      gl.uniform1f(U('dy'), S.dy.x * 0.06)
      gl.uniform1f(U('eyeOpen'), Math.max(0.06, Math.min(1.15, S.open.x)))
      gl.uniform1f(U('eyeScale'), S.eyeScale.x)
      gl.uniform1f(U('eyeGlow'), Math.max(0, S.glow.x))
      gl.uniform2f(U('eyeShift'), 0, mode === 'listen' ? -6 + nodDip(now - nodAt) * 10 : 0)
      gl.clearColor(0, 0, 0, 0)
      gl.clear(gl.COLOR_BUFFER_BIT)
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
      fx.setTransform(1, 0, 0, 1, 0, 0)
      fx.clearRect(0, 0, fx.canvas.width, fx.canvas.height)
      const k = width / meta.W
      fx.setTransform(k, 0, 0, k, 0, 0)
      for (let i = sparks.length - 1; i >= 0; i--) {
        const p = sparks[i]!
        p.life += dt
        if (p.life > p.max) {
          sparks.splice(i, 1)
          continue
        }
        p.x += p.vx * dt
        p.y += p.vy * dt
        p.vy += 60 * dt
        p.rot += dt * 3
        const a = 1 - p.life / p.max
        fx.globalAlpha = a
        fx.fillStyle = '#f6e8ff'
        star(p.x, p.y, p.r, p.rot)
        fx.globalAlpha = a * 0.5
        fx.fillStyle = '#c77dff'
        star(p.x, p.y, p.r * 1.8, p.rot)
      }
      fx.globalAlpha = 1
    },
    /** For screenshots and tests: what the microphone would have said. */
    feed(level: number) {
      mic = level
    },
    /** For screenshots and tests: what is on screen. */
    inspect() {
      return { mode, nodding: nodDip(performance.now() / 1000 - nodAt) > 0, sparks: sparks.length, mic, glow: S.glow.x }
    },
  }
}

type Renderer = ReturnType<typeof createRenderer>

let metaCache: Promise<{ meta: Meta; body: HTMLImageElement }> | null = null
function loadBody(): Promise<{ meta: Meta; body: HTMLImageElement }> {
  metaCache ??= (async () => {
    const meta = (await (await fetch('mascot/meta.json')).json()) as Meta
    const body = new Image()
    body.src = 'mascot/body.png'
    await body.decode()
    return { meta, body }
  })()
  return metaCache
}

/**
 * How the mascot shows now: still when the person chose a still mage or reduced motion. The Preferences' "off" puts the
 * logo beside Evokai in the sidebar; it does not hide the assistant's mage, the one mascot on screen.
 */
function useMascotMode(): 'moving' | 'still' | 'hidden' {
  const read = (): 'moving' | 'still' | 'hidden' => (mascotMoves() ? 'moving' : 'still')
  const [m, setM] = useState(read)
  useEffect(() => onPrefs(() => setM(read())), [])
  return m
}

export function LiveMascot(p: { view: MascotView; signals?: AudioSignals | null; size?: number; onClipEnd?: () => void }) {
  const size = p.size ?? 200
  const mode = useMascotMode()
  const glRef = useRef<HTMLCanvasElement>(null)
  const fxRef = useRef<HTMLCanvasElement>(null)
  const renderer = useRef<Renderer | null>(null)
  const [noGl, setNoGl] = useState(false)
  const [clipKey, setClipKey] = useState(0)
  const live = p.view.kind === 'live' && mode === 'moving' && !noGl

  // One-shot clips end by themselves.
  useEffect(() => {
    if (p.view.kind !== 'clip' || p.view.loop) return
    setClipKey(Date.now())
    const timer = setTimeout(() => p.onClipEnd?.(), CLIP_MS[p.view.name])
    return () => clearTimeout(timer)
  }, [p.view.kind === 'clip' ? `${p.view.name}:${p.view.loop}` : 'live'])

  useEffect(() => {
    if (!live) return
    let stop = false
    let raf = 0
    let off: (() => void) | undefined
    void (async () => {
      try {
        const { meta, body } = await loadBody()
        const glc = glRef.current
        const fxc = fxRef.current
        if (stop || !glc || !fxc) return
        // One renderer per canvas pair: the canvases are new each time the live view mounts.
        const gl = glc.getContext('webgl', { premultipliedAlpha: true, alpha: true })
        const fx = fxc.getContext('2d')
        if (!gl || !fx) throw new Error('webgl')
        const r = createRenderer(gl, fx, meta, body)
        renderer.current = r
        if (p.view.kind === 'live') r.setMode(p.view.mode)
        const ratio = Math.min(devicePixelRatio || 1, 2)
        for (const c of [glc, fxc]) c.width = c.height = Math.round(size * ratio)
        glc.getContext('webgl')?.viewport(0, 0, glc.width, glc.height)
        off = p.signals?.subscribe((s) => r.signal(s))
        let last = performance.now() / 1000
        const tick = () => {
          if (stop) return
          const now = performance.now() / 1000
          r.frame(now, Math.min(0.05, now - last), fxc.width)
          last = now
          raf = requestAnimationFrame(tick)
        }
        raf = requestAnimationFrame(tick)
        ;(window as unknown as { __mascot?: Renderer }).__mascot = r
      } catch {
        setNoGl(true)
      }
    })()
    return () => {
      stop = true
      cancelAnimationFrame(raf)
      off?.()
      renderer.current = null
    }
  }, [live, p.signals, size])

  useEffect(() => {
    if (p.view.kind === 'live') renderer.current?.setMode(p.view.mode)
  }, [p.view.kind === 'live' ? p.view.mode : 'clip'])

  if (mode === 'hidden') return null
  const box = `width:${size}px;height:${size}px`
  if (mode === 'still') return <img class="mascot still" src={stillFor(size)} width={size} height={size} alt="" style={box} />
  if (live)
    return (
      <div class={`mascot live ${p.view.kind === 'live' ? p.view.mode : ''}`} style={box} data-mascot="live">
        <canvas ref={glRef} style={box} />
        <canvas ref={fxRef} style={box} />
      </div>
    )
  const name = p.view.kind === 'clip' ? p.view.name : p.view.mode === 'speak' ? 'idle-ember' : 'idle-combo'
  const loop = p.view.kind === 'clip' ? p.view.loop : true
  return (
    <img
      key={loop ? name : `${name}:${clipKey}`}
      class="mascot clip"
      data-mascot={name}
      src={clipSrc(name, size) + (loop ? '' : `?r=${clipKey}`)}
      width={size}
      height={size}
      alt=""
      decoding="async"
      style={box}
    />
  )
}
