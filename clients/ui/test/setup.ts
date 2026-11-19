// jsdom has no canvas: the orbs fall back to their CSS gradient, as in a browser without WebGL.
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext
