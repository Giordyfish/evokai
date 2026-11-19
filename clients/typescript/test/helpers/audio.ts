/** A recording stand-in for an (offline) audio context: every node, connection and parameter automation is kept. */
export interface ParamCall {
  fn: 'set' | 'linear' | 'exp'
  value: number
  at: number
}

export class FakeParam {
  value = 0
  calls: ParamCall[] = []
  setValueAtTime(value: number, at: number) {
    this.calls.push({ fn: 'set', value, at })
    return this
  }
  linearRampToValueAtTime(value: number, at: number) {
    this.calls.push({ fn: 'linear', value, at })
    return this
  }
  exponentialRampToValueAtTime(value: number, at: number) {
    this.calls.push({ fn: 'exp', value, at })
    return this
  }
}

export class FakeNode {
  connections: unknown[] = []
  started: number[] = []
  stopped: number[] = []
  onended: (() => void) | null = null
  constructor(readonly kind: string) {}
  connect(to: unknown) {
    this.connections.push(to)
    return to
  }
  disconnect() {}
  start(at = 0) {
    this.started.push(at)
  }
  stop(at = 0) {
    this.stopped.push(at)
  }
}

export class FakeOsc extends FakeNode {
  type = 'sine'
  frequency = new FakeParam()
  constructor() {
    super('osc')
  }
}
export class FakeGain extends FakeNode {
  gain = new FakeParam()
  constructor() {
    super('gain')
  }
}
export class FakeFilter extends FakeNode {
  type = 'lowpass'
  Q = new FakeParam()
  frequency = new FakeParam()
  constructor() {
    super('filter')
  }
}
export class FakeSource extends FakeNode {
  buffer: unknown = null
  constructor() {
    super('source')
  }
}
export class FakeAnalyser extends FakeNode {
  fftSize = 2048
  smoothingTimeConstant = 0
  constructor() {
    super('analyser')
  }
  getFloatTimeDomainData(a: Float32Array) {
    a.fill(0.1)
  }
  getByteFrequencyData(a: Uint8Array) {
    a.fill(128)
  }
}

export class FakeAudioContext {
  currentTime = 10
  sampleRate = 8000
  state: 'running' | 'suspended' = 'running'
  destination = new FakeNode('destination')
  nodes: FakeNode[] = []
  decoded: ArrayBuffer[] = []
  private add<T extends FakeNode>(n: T): T {
    this.nodes.push(n)
    return n
  }
  createGain() {
    return this.add(new FakeGain())
  }
  createOscillator() {
    return this.add(new FakeOsc())
  }
  createBiquadFilter() {
    return this.add(new FakeFilter())
  }
  createBufferSource() {
    return this.add(new FakeSource())
  }
  createAnalyser() {
    return this.add(new FakeAnalyser())
  }
  createBuffer(channels: number, length: number, sampleRate: number) {
    const data = new Float32Array(length)
    return { numberOfChannels: channels, length, sampleRate, getChannelData: () => data }
  }
  async decodeAudioData(b: ArrayBuffer) {
    this.decoded.push(b)
    return this.createBuffer(1, 10, this.sampleRate)
  }
  async resume() {
    this.state = 'running'
  }
  of<T extends FakeNode>(kind: string): T[] {
    return this.nodes.filter((n) => n.kind === kind) as T[]
  }
}
