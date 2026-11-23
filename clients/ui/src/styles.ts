/**
 * The components' stylesheet (inside their shadow roots). Internal tokens `--ek-*` come from the public custom
 * properties `--evokai-<name>` (both schemes), `--evokai-<name>-light` and `--evokai-<name>-dark`, with the defaults of
 * `theme.ts`. The scheme follows the system unless the host has `scheme="light"` or `scheme="dark"`.
 */
import { DARK, LIGHT, ORB, type Palette } from './theme'

const kebab = (s: string) => s.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)
const SHORT: Record<string, string> = { surface: 'surface', muted: 'muted', text: 'text', subtle: 'subtle', border: 'border', brand: 'brand', onBrand: 'on-brand', accent: 'accent', danger: 'danger', veil: 'veil' }

function tokens(p: Palette, scheme: 'light' | 'dark'): string {
  const out: string[] = []
  for (const k of Object.keys(SHORT)) {
    const name = kebab(k)
    out.push(`--ek-${SHORT[k]}:var(--evokai-${name},var(--evokai-${name}-${scheme},${p[k as keyof Palette] as string}));`)
  }
  p.series.forEach((c, i) => out.push(`--ek-s${i + 1}:var(--evokai-series-${i + 1},var(--evokai-series-${i + 1}-${scheme},${c}));`))
  return out.join('')
}

const DERIVED = `--ek-hi:var(--ek-s1);--ek-ghost:color-mix(in oklab,var(--ek-s1) 30%,var(--ek-border));--ek-rest:color-mix(in oklab,var(--ek-subtle) 45%,var(--ek-border));`

/** The custom properties and the scheme switch: kept even when the host is `unstyled`, so a skin can use them. */
const TOKENS = `
:host{all:initial;${tokens(LIGHT, 'light')}${DERIVED}
--ek-font:var(--evokai-font,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif);
--ek-display:var(--evokai-display-font,var(--ek-font));
--ek-radius:var(--evokai-radius,1.5rem);
--ek-z:var(--evokai-z,2147483000);
--ek-aurora-1:var(--evokai-aurora-1,var(--ek-s2));--ek-aurora-2:var(--evokai-aurora-2,var(--ek-accent));--ek-aurora-3:var(--evokai-aurora-3,var(--ek-s1));
--evokai-orb-ink-default:${ORB.ink};
font-family:var(--ek-font);color:var(--ek-text);font-size:14px;line-height:1.5}
@media (prefers-color-scheme:dark){:host(:not([scheme=light])){${tokens(DARK, 'dark')}${DERIVED}}}
:host([scheme=dark]){${tokens(DARK, 'dark')}${DERIVED}}
:host([hidden]){display:none}
*,*::before,*::after{box-sizing:border-box}
button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer}
button:disabled{cursor:default;opacity:.4}
slot{display:contents}
slot[name=orb]::slotted(*){display:block;width:100%;height:100%;pointer-events:none}
.orbimg{display:block;width:100%;height:100%;object-fit:contain;pointer-events:none}
`

/** The default look. */
const LOOK = `
:focus-visible{outline:2px solid var(--ek-accent);outline-offset:2px}
.sub{color:var(--ek-subtle)}.strong{font-weight:600}.num{font-variant-numeric:tabular-nums}
.ib{display:grid;place-items:center;width:32px;height:32px;border-radius:999px;color:var(--ek-subtle)}
.ib:hover{background:color-mix(in oklab,var(--ek-text) 6%,transparent);color:var(--ek-text)}

/* the travelling orb: one canvas at its largest size, scaled (--k is the inverse scale, for shadows that keep their size) */
.orbfly{will-change:transform}
.orbcore{position:absolute;inset:0;border-radius:50%;box-shadow:0 calc(4px*var(--k,1)) calc(20px*var(--k,1)) color-mix(in oklab,var(--evokai-orb-main,${ORB.main}) 35%,transparent)}
.orbcore canvas{display:block;width:100%;height:100%;border-radius:50%}
.orbr{position:absolute;inset:0;border-radius:50%;box-shadow:inset 0 0 0 calc(1px*var(--k,1)) rgb(255 255 255/.14);pointer-events:none}
.orbfly[data-at=center] .orbcore{box-shadow:0 calc(12px*var(--k,1)) calc(28px*var(--k,1)) color-mix(in oklab,var(--evokai-orb-main,${ORB.main}) 38%,transparent)}
.orbfly[data-at=launcher][data-phase=idle] .orbcore{animation:ekpulse 4.2s ease-in-out infinite}
@keyframes ekpulse{0%,100%{box-shadow:0 calc(4px*var(--k,1)) calc(20px*var(--k,1)) color-mix(in oklab,var(--evokai-orb-main,${ORB.main}) 30%,transparent)}50%{box-shadow:0 calc(6px*var(--k,1)) calc(30px*var(--k,1)) color-mix(in oklab,var(--evokai-orb-main,${ORB.main}) 50%,transparent)}}
.orbfly .ringfx{position:absolute;left:-50%;top:-50%;width:200%;height:200%;pointer-events:none}
.splash{position:fixed;z-index:var(--ek-z);border-radius:50%;border:2px solid var(--ek-accent);pointer-events:none;animation:eksplash .6s ease-out forwards}
@keyframes eksplash{from{transform:scale(.9);opacity:.8}to{transform:scale(1.5);opacity:0}}
.oslot{position:relative;display:inline-grid;place-items:center;flex:none}
.oslot:not([data-here])>slot{visibility:hidden}
.oslot.launch{min-width:60px;min-height:60px;border-radius:50%}
.oslot.head{width:42px;height:42px;transition:width .45s cubic-bezier(.2,.8,.2,1),margin .45s cubic-bezier(.2,.8,.2,1)}
.oslot.mid{width:clamp(96px,22vh,168px);height:clamp(96px,22vh,168px)}

/* launcher, bubble and panel: one orb on screen, so the launcher hides while the panel is open */
.dock{position:fixed;bottom:24px;right:24px;z-index:var(--ek-z)}
:host([position=bottom-left]) .dock{right:auto;left:24px}
.launcher{position:relative;display:block;border-radius:999px;transition:transform .3s}
.launcher[data-hidden]{visibility:hidden;pointer-events:none}
.launcher:hover{transform:scale(1.05)}
.launcher:focus-visible{outline-offset:4px}
.vbubble{position:absolute;right:calc(100% + 16px);bottom:6px;width:min(20rem,calc(100vw - 100% - 64px));padding:11px 14px 12px;border-radius:20px 20px 6px 20px;background:var(--ek-surface);border:1px solid var(--ek-border);box-shadow:0 22px 44px -22px rgb(0 0 0/.45);transform-origin:right bottom;animation:ekbubble .35s cubic-bezier(.2,.8,.2,1) both}
.vbubble::after{content:"";position:absolute;right:-7px;bottom:16px;width:14px;height:14px;background:inherit;border-right:1px solid var(--ek-border);border-bottom:1px solid var(--ek-border);transform:rotate(-45deg);border-radius:0 0 4px 0}
.vbubble.small{width:auto;white-space:nowrap}
:host([position=bottom-left]) .vbubble{right:auto;left:calc(100% + 16px);border-radius:20px 20px 20px 6px;transform-origin:left bottom;animation-name:ekbubblel}
:host([position=bottom-left]) .vbubble::after{right:auto;left:-7px;transform:rotate(135deg)}
@keyframes ekbubble{from{opacity:0;transform:translateX(12px) scale(.96)}}
@keyframes ekbubblel{from{opacity:0;transform:translateX(-12px) scale(.96)}}
.vst{display:flex;align-items:center;gap:8px;font-size:10.5px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--ek-accent)}
.hop{display:inline-flex;gap:3px}.hop b{width:4px;height:4px;border-radius:50%;background:currentColor;animation:ekhop 1s ease-in-out infinite}
.hop b:nth-child(2){animation-delay:.15s}.hop b:nth-child(3){animation-delay:.3s}
@keyframes ekhop{40%{transform:translateY(-3px);opacity:.4}}
.vtxt{margin:4px 0 0;font:1rem/1.4 var(--ek-display);color:var(--ek-text);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.vtxt .heard{font-style:italic}
.vchip{display:flex;align-items:center;gap:8px;margin-top:8px;padding:7px 10px;border-radius:12px;background:var(--ek-muted);font-size:12.5px}
.vchip svg{flex:none;color:var(--ek-subtle)}
.vct{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vchip button{font-weight:600;color:var(--ek-accent)}
.vchip button:hover{text-decoration:underline}
.acts{position:absolute;left:50%;margin-left:-18px;bottom:100%;padding-bottom:12px;display:flex;flex-direction:column;gap:8px;opacity:0;transform:translateY(8px);pointer-events:none;transition:opacity .2s,transform .2s}
.dock:is([data-mini],[data-idle]):is(:hover,:has(:focus-visible)) .acts{opacity:1;transform:none;pointer-events:auto}
.ab{display:grid;place-items:center;width:36px;height:36px;border-radius:50%;background:var(--ek-surface);box-shadow:0 0 0 1px var(--ek-border),0 8px 18px -10px rgb(0 0 0/.5);color:var(--ek-subtle)}
.ab:hover{color:var(--ek-text)}
.ab.fin{background:var(--ek-brand);color:var(--ek-on-brand);box-shadow:none}
.ab.muted{color:var(--ek-danger)}
.panel{position:fixed;z-index:var(--ek-z);bottom:24px;right:24px;top:64px;width:min(25.5rem,calc(100vw - 32px));display:flex;flex-direction:column;overflow:hidden;
background:var(--ek-surface);border:1px solid var(--ek-border);border-radius:var(--ek-radius);box-shadow:0 40px 90px -40px rgb(0 0 0/.45);transform-origin:var(--ek-origin,40px 40px)}
:host([position=bottom-left]) .panel{right:auto;left:24px}
.panel[data-box=pre]{opacity:0;pointer-events:none}
.panel[data-box=in]{animation:ekboxin .32s cubic-bezier(.2,.8,.2,1) both}
.panel[data-box=out]{animation:ekboxout .24s cubic-bezier(.4,0,1,1) both;pointer-events:none}
@keyframes ekboxin{from{opacity:0;transform:scale(.5)}}
@keyframes ekboxout{to{opacity:0;transform:scale(.5)}}
@media (max-width:640px){.panel{inset:0;width:auto;border-radius:0;border:0}}
.aurora{position:absolute;inset:0 0 auto;height:160px;pointer-events:none;overflow:hidden;opacity:.55}
.aurora i{position:absolute;width:220px;height:220px;border-radius:50%;filter:blur(60px);background:var(--ek-aurora-1);top:-140px;left:-40px;animation:ekdrift 18s ease-in-out infinite alternate}
.aurora i:nth-child(2){background:var(--ek-aurora-2);left:auto;right:-60px;top:-160px;animation-duration:23s}
.aurora i:nth-child(3){background:var(--ek-aurora-3);left:40%;top:-180px;opacity:.6;animation-duration:29s}
@keyframes ekdrift{to{transform:translate(30px,20px) scale(1.1)}}
header{position:relative;display:flex;align-items:center;gap:12px;padding:20px 20px 8px}
.logo{width:42px;height:42px;object-fit:contain;border-radius:12px}
.ht{flex:1;min-width:0}
.title{font:700 1.25rem/1.2 var(--ek-display);color:var(--ek-text)}
.ht .sub{font-size:12px}
.hb{display:flex}
.panel[data-layer=voice] .oslot.head{width:0;margin-right:-12px;overflow:hidden}
.panel[data-layer=voice] .tag::before{content:"";display:inline-block;width:7px;height:7px;margin-right:6px;border-radius:50%;background:var(--ek-accent);vertical-align:1px;animation:ekblink 1.6s ease-in-out infinite}
.ib.min{color:var(--ek-accent);animation:ekpop .25s ease-out}
@keyframes ekpop{from{opacity:0;transform:scale(.6)}}

/* the two layers of the panel's body, crossfading: the chat and voice mode */
.body{position:relative;flex:1;min-height:0}
.layer{position:absolute;inset:0;display:flex;flex-direction:column;transition:opacity .3s,transform .4s cubic-bezier(.2,.8,.2,1),visibility 0s .4s}
.layer.voice{opacity:0;visibility:hidden;align-items:center;padding:6px 22px 0}
.panel[data-layer=voice] .layer.chat{opacity:0;transform:translateY(14px);visibility:hidden}
.panel[data-layer=chat] .layer.chat,.panel[data-layer=voice] .layer.voice{opacity:1;visibility:visible;transform:none;transition-delay:.15s,.15s,0s}
.stagebox{flex:1;min-height:0;width:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;text-align:center}
.layer.voice .status{margin:0}
.layer.voice .subtitles{min-height:4.2em;max-width:20rem;font-size:1.15rem;line-height:1.4;letter-spacing:-.005em}
.vnote{margin:0;max-width:20rem;font-size:12px}
.vcards{width:100%;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:10px;text-align:left}
.layer.voice .vcard{padding:12px 14px;background:var(--ek-surface)}
.layer.voice .controls{padding:14px 0 6px;gap:12px}
.layer.voice .round{width:46px;height:46px}
.layer.voice .switch{margin-bottom:12px;font-size:13px}
.history{position:relative;margin:8px 16px 0;max-height:15rem;overflow-y:auto;border-radius:16px;background:color-mix(in oklab,var(--ek-muted) 80%,transparent)}
.history button{display:block;width:100%;padding:10px 16px;text-align:left;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.history button:hover{background:var(--ek-muted)}
.history button[aria-current]{color:var(--ek-accent)}
.history p{margin:0;padding:12px 16px;font-size:14px}
.list{position:relative;flex:1;overflow-y:auto;padding:16px 20px;display:flex;flex-direction:column;gap:20px;font-size:14px}
.starters{display:flex;flex-direction:column;justify-content:flex-end;gap:8px;flex:1}
.starter{text-align:left;padding:10px 14px;border-radius:16px;border:1px solid var(--ek-border);color:var(--ek-subtle);transition:border-color .15s,color .15s}
.starter:hover{border-color:var(--ek-accent);color:var(--ek-text)}
.msg.user{display:flex;justify-content:flex-end}
.msg.assistant{display:flex;flex-direction:column;gap:12px}
.bubble{max-width:85%;padding:10px 16px;border-radius:20px 20px 6px 20px;background:var(--ek-brand);color:var(--ek-on-brand);white-space:pre-wrap;overflow-wrap:anywhere}
.answers{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:6px;max-width:85%}
.tool{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--ek-subtle)}
.cite{font-size:.68em;line-height:0;margin:0 1px;font-weight:600}
.cite a,.cite span{display:inline-block;min-width:1.5em;padding:2px 4px;border-radius:999px;background:var(--ek-muted);color:var(--ek-brand);text-decoration:none;text-align:center;border:1px solid var(--ek-border)}
.cite a:hover,.cite a:focus-visible{background:var(--ek-brand);color:var(--ek-on-brand)}
.sources{margin-top:10px;padding-top:8px;border-top:1px solid var(--ek-border);font-size:12px}
.sources-title{font-weight:600;color:var(--ek-subtle);text-transform:uppercase;letter-spacing:.04em;font-size:11px;margin-bottom:6px}
.sources ol{list-style:none;margin:0;padding:0;display:grid;gap:6px}
.sources li{display:flex;gap:8px;align-items:flex-start}
.src-n{flex:none;min-width:1.6em;height:1.6em;border-radius:999px;background:var(--ek-muted);border:1px solid var(--ek-border);color:var(--ek-brand);font-weight:600;display:inline-flex;align-items:center;justify-content:center;font-size:11px}
.src-body{display:flex;flex-direction:column;gap:1px;min-width:0}
.src-title{font-weight:600;color:var(--ek-text);text-decoration:none;overflow-wrap:anywhere}
a.src-title:hover{text-decoration:underline;color:var(--ek-brand)}
.src-where{color:var(--ek-subtle)}
.src-snippet{color:var(--ek-subtle);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tool i{width:6px;height:6px;border-radius:50%;background:var(--ek-s1)}
.tool.start i{background:var(--ek-accent);animation:ekblink 1s ease-in-out infinite}
.tool.error i{background:var(--ek-danger)}
@keyframes ekblink{50%{opacity:.3}}
.nav{font-size:12px;color:var(--ek-subtle)}
.err{font-size:12px;color:var(--ek-danger)}
.files{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.composer>.files{padding:0 8px 8px}
.bubble .files{margin-bottom:6px}
.file{display:inline-flex;align-items:center;gap:6px;max-width:200px;padding:4px 8px;border-radius:10px;font-size:12px;box-shadow:inset 0 0 0 1px var(--ek-border)}
.file img{width:28px;height:28px;object-fit:cover;border-radius:6px}
.fname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.shot{max-width:160px;max-height:160px;border-radius:12px;object-fit:cover}
.fb{display:flex;gap:4px;color:color-mix(in oklab,var(--ek-subtle) 70%,transparent)}
.fb button{padding:4px;border-radius:6px}.fb button:hover{color:var(--ek-text)}
.fb button[aria-pressed=true]{color:var(--ek-accent)}
.dots{display:flex;gap:4px}.dots i{width:6px;height:6px;border-radius:50%;background:var(--ek-accent);animation:ekblink 1.2s ease-in-out infinite}
.dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}
.notice{margin:0 16px 4px;display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:16px;background:var(--ek-muted);font-size:13px;color:var(--ek-subtle);border:1px solid var(--ek-border)}
.spin{width:14px;height:14px;flex:none;border-radius:50%;border:2px solid var(--ek-accent);border-top-color:transparent;animation:ekspin 1s linear infinite}
@keyframes ekspin{to{transform:rotate(1turn)}}
.composer{position:relative;padding:8px 16px 16px}
.field{display:flex;align-items:flex-end;gap:8px;padding:8px 8px 8px 16px;border-radius:24px;background:var(--ek-muted);border:1px solid var(--ek-border)}
.field:focus-within{border-color:var(--ek-accent)}
textarea{flex:1;align-self:center;min-height:1.5rem;max-height:10rem;resize:none;border:0;background:transparent;color:var(--ek-text);font:15px/1.5 var(--ek-font);outline:none}
textarea::placeholder{color:var(--ek-subtle)}
textarea:focus-visible{outline:none}
.cb{display:grid;place-items:center;width:36px;height:36px;border-radius:999px;color:var(--ek-subtle);transition:background .15s,color .15s}
.cb:hover:not(:disabled){background:var(--ek-surface);color:var(--ek-text)}
.cb.on,.cb.send{background:var(--ek-brand);color:var(--ek-on-brand)}
.cb.send:hover:not(:disabled){background:var(--ek-brand);color:var(--ek-on-brand);opacity:.9}

/* buttons, header menu */
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:32px;padding:6px 14px;border-radius:999px;font-size:13px;line-height:1.2;box-shadow:inset 0 0 0 1px var(--ek-border);transition:background .15s,box-shadow .15s,color .15s}
.btn:hover:not(:disabled){box-shadow:inset 0 0 0 1.5px var(--ek-accent)}
.btn.primary{background:var(--ek-brand);color:var(--ek-on-brand);box-shadow:none}.btn.primary:hover:not(:disabled){opacity:.9;box-shadow:none}
.btn.danger{background:var(--ek-danger);color:var(--ek-surface);box-shadow:none}.btn.danger:hover:not(:disabled){opacity:.9;box-shadow:none}
.btn.quiet{box-shadow:none;color:var(--ek-subtle);padding:6px 10px}.btn.quiet:hover:not(:disabled){color:var(--ek-text);background:color-mix(in oklab,var(--ek-text) 6%,transparent);box-shadow:none}
.btn.quiet.danger{background:none;color:var(--ek-danger)}.btn.quiet.danger:hover:not(:disabled){background:color-mix(in oklab,var(--ek-danger) 10%,transparent);color:var(--ek-danger)}
.menuwrap{position:relative;display:inline-flex}
.scrim{position:fixed;inset:0;z-index:2}
.menu{position:absolute;right:0;top:38px;z-index:3;min-width:13rem;padding:6px;border-radius:16px;border:1px solid var(--ek-border);background:var(--ek-surface);box-shadow:0 18px 40px -18px rgb(0 0 0/.4);animation:ekin .14s ease-out}
.menu [role=menuitem]{display:flex;align-items:center;gap:10px;width:100%;padding:9px 12px;border-radius:10px;text-align:left;font-size:14px;white-space:nowrap;color:var(--ek-text)}
.menu [role=menuitem] svg{color:var(--ek-subtle);flex:none}
.menu [role=menuitem]:hover,.menu [role=menuitem]:focus-visible{background:var(--ek-muted);outline:none}
@keyframes ekin{from{opacity:0;transform:translateY(-4px)}}

/* memory */
.memory{position:relative;flex:1;min-height:0;display:flex;flex-direction:column}
.mhead{display:flex;align-items:center;gap:6px;padding:8px 16px 4px}
.mtitle{margin:0;font:600 15px/1.35 var(--ek-display);color:var(--ek-text);outline:none}
.mscroll{flex:1;overflow-y:auto;padding:4px 20px 16px;display:flex;flex-direction:column;gap:18px}
.mintro{margin:0;font-size:13px}
.mstate{display:flex;flex-direction:column;align-items:flex-start;gap:12px;margin:8px 0 0;font-size:14px}
.mstate p{margin:0}
div.mstate[role=status]{flex-direction:row;align-items:center}
.moff{padding:16px;border-radius:16px;background:var(--ek-muted);border:1px solid var(--ek-border)}
.mgroup h3{margin:0 0 6px;font-weight:600;color:var(--ek-subtle);text-transform:uppercase;letter-spacing:.04em;font-size:11px}
.mgroup ul{list-style:none;margin:0;padding:0;border-radius:16px;border:1px solid var(--ek-border);overflow:hidden}
.mitem{display:flex;align-items:flex-start;gap:8px;padding:10px 6px 10px 14px}
.mitem+.mitem{border-top:1px solid var(--ek-border)}
.mbody{flex:1;min-width:0}
.mtext{margin:0;font-size:14px;overflow-wrap:anywhere;white-space:pre-wrap}
.mmeta{margin:2px 0 0;font-size:11.5px;color:var(--ek-subtle)}
.mbody .err{margin:4px 0 0}
.mact{display:flex;flex:none;opacity:.55;transition:opacity .15s}
.mitem:hover .mact,.mitem:focus-within .mact{opacity:1}
.mact .ib{width:28px;height:28px}
.mitem.editing{display:block;padding:10px;background:var(--ek-muted)}
.medit{display:flex;flex-direction:column;gap:8px}
.medit textarea{width:100%;min-height:4.5rem;padding:8px 10px;border-radius:12px;border:1px solid var(--ek-border);background:var(--ek-surface);font-size:14px}
.medit textarea:focus{border-color:var(--ek-accent)}
.medit textarea[aria-invalid=true]{border-color:var(--ek-danger)}
.medit .err{margin:0}
.conn .mtext{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-weight:500}
.cdot{width:8px;height:8px;border-radius:50%;background:var(--ek-rest)}
.cdot[data-status=connected]{background:var(--ek-s1);box-shadow:0 0 0 3px color-mix(in oklab,var(--ek-s1) 25%,transparent)}
.cdot[data-status=expired]{background:var(--ek-danger)}
.cstate{font-weight:400;font-size:12px;color:var(--ek-subtle)}
.conn .mact{opacity:1;align-self:center;padding-right:8px}
.conn .mconfirm{margin-top:8px}
.connect .ct{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:500}
.connect p{margin:0;font-size:13px}
.connect[data-state=connected] .ct{color:var(--ek-subtle)}
.mfoot{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;padding:10px 20px 16px;border-top:1px solid var(--ek-border)}
.mfoot>.btn.quiet:first-child{margin-left:-10px}.mfoot>.btn.quiet:last-child{margin-right:-10px}
.mconfirm{flex:1;display:flex;flex-direction:column;gap:10px;font-size:13px}
.mconfirm p{margin:0}
.mask{margin:0 16px 8px;display:flex;flex-direction:column;gap:10px;padding:12px 14px;border-radius:16px;background:var(--ek-muted);border:1px solid var(--ek-border);font-size:13px;animation:ekin .2s ease-out}
.mask p{margin:0;color:var(--ek-text)}

/* usage */
.usage{position:relative;display:inline-flex}
.ring{width:24px;height:24px;transform:rotate(-90deg)}
.ring circle{transition:stroke-dasharray .6s ease,stroke .6s ease}
.tip{position:absolute;right:0;top:40px;z-index:2;width:15rem;padding:14px;border-radius:16px;border:1px solid var(--ek-border);background:var(--ek-surface);font-size:12px;line-height:1.6;box-shadow:0 18px 40px -18px rgb(0 0 0/.4);display:none}
.tip.on{display:block}
.tip b{display:block;margin-bottom:8px}
.tip .grid{display:grid;grid-template-columns:1fr auto;column-gap:12px;margin-bottom:8px}

/* markdown */
.md{overflow-wrap:anywhere}
.md>*:first-child{margin-top:0}.md>*:last-child{margin-bottom:0}
.md p,.md ul,.md ol,.md blockquote,.md pre,.md .tablewrap{margin:0 0 10px}
.md h3,.md h4,.md h5,.md h6{font:600 15px/1.35 var(--ek-display);margin:14px 0 6px}
.md ul,.md ol{padding-left:20px}
.md li+li{margin-top:2px}
.md a{color:var(--ek-accent);text-decoration:underline;text-underline-offset:2px}
.md code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--ek-muted);padding:1px 5px;border-radius:6px}
.md pre{background:var(--ek-muted);padding:10px 12px;border-radius:12px;overflow-x:auto}
.md pre code{background:none;padding:0}
.md blockquote{border-left:3px solid var(--ek-border);padding-left:12px;color:var(--ek-subtle)}
.md hr{border:0;border-top:1px solid var(--ek-border);margin:12px 0}
.tablewrap{overflow-x:auto}
.md table{border-collapse:collapse;font-size:13px;width:100%}
.md th,.md td{border-bottom:1px solid var(--ek-border);padding:6px 8px;text-align:left;vertical-align:top}
.md th{font-weight:600}

/* visuals */
.viz{margin:0;display:flex;flex-direction:column;gap:12px}
.viz figcaption{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
.vt{font-size:13px;font-weight:500}.vs{flex:none;font-size:11px;color:var(--ek-subtle)}
.chart{width:100%;overflow:visible}
.picture{display:block;max-width:100%;height:auto;border-radius:6px}
.tx{fill:var(--ek-subtle)}.tx.strong{fill:var(--ek-text);font-weight:500}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:11px;color:var(--ek-subtle)}
.legend span{display:inline-flex;align-items:center;gap:6px}.legend i{display:inline-block;width:12px;height:2px;border-radius:2px}
.bars{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.bars li{display:grid;grid-template-columns:minmax(0,7.5rem) 1fr auto;align-items:center;gap:12px;font-size:12px}
.bl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ek-subtle)}.bars li.hi .bl,.bars li.hi .num{color:var(--ek-text);font-weight:500}
.bt{height:6px}.bt span{display:block;height:100%;border-radius:999px}
.shares{list-style:none;margin:0;padding:0;flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;font-size:12px}
.shares li{display:grid;grid-template-columns:8px minmax(0,1fr) auto auto;align-items:center;gap:8px}
.shares i{width:8px;height:8px;border-radius:3px}.sl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sv{width:4.5rem;text-align:right}
.stack{display:flex;height:10px;gap:2px;overflow:hidden;border-radius:999px}
.donut{display:flex;align-items:center;gap:20px}.donut svg{width:112px;height:112px;flex:none}
.kpis{display:grid;gap:16px 20px;margin:0}.kpis.two{grid-template-columns:1fr 1fr}
.kpis div{border-top:1px solid var(--ek-border);padding-top:10px}
.kpis dt{font-size:11px;color:var(--ek-subtle)}.kpis dd{margin:0}
.kv{font:600 1.375rem/1.2 var(--ek-display)}.kpis.one .kv{font-size:1.875rem}
.kn{font-size:11px;color:var(--ek-subtle)}.kn .up{color:var(--ek-accent);font-weight:500}.kn .down{color:var(--ek-danger);font-weight:500}
.steps{list-style:none;margin:0;padding:0}
.steps li{position:relative;display:grid;grid-template-columns:20px 1fr;gap:12px;padding-bottom:16px}
.steps li:last-child{padding-bottom:0}
.steps li:not(:last-child)::before{content:"";position:absolute;left:9.5px;top:22px;bottom:2px;width:1px;background:var(--ek-border)}
.steps li[data-state=done]:not(:last-child)::before{background:var(--ek-s1)}
.dot{position:relative;margin-top:2px;display:grid;place-items:center;width:20px;height:20px;border-radius:50%;box-shadow:inset 0 0 0 1px var(--ek-border);color:var(--ek-surface)}
[data-state=done] .dot{background:var(--ek-s1);box-shadow:none}[data-state=current] .dot{box-shadow:inset 0 0 0 1.5px var(--ek-s1)}
.dot svg{width:12px;height:12px}.dot i{width:8px;height:8px;border-radius:50%;background:var(--ek-hi)}
.st{min-width:0}.st .sl{display:block;font-size:13px;line-height:20px;white-space:normal}.st .sn{display:block;font-size:11px;color:var(--ek-subtle)}
[data-state=todo] .st .sl{color:var(--ek-subtle)}[data-state=current] .st .sl{font-weight:500}
.root .chip,.chip{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;font-size:12.5px;line-height:1;box-shadow:inset 0 0 0 1px var(--ek-border)}
.chip:hover:not(:disabled){box-shadow:inset 0 0 0 1.5px var(--ek-accent)}
.chip.on{background:var(--ek-brand);color:var(--ek-on-brand);box-shadow:none}
.chip.faded{opacity:.4}.chip.sub{color:var(--ek-subtle)}
.chip kbd{font:600 10px var(--ek-font);opacity:.7}
.root .tree{list-style:none;margin:4px 0 0 12px;padding:0;border-left:1px solid var(--ek-border)}
.tree li{position:relative;display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:6px 0 6px 16px;font-size:12px}
.tree li::before{content:"";position:absolute;left:0;top:50%;width:10px;height:1px;background:var(--ek-border)}
.tl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.root{display:contents}
.troot{display:flex;align-items:baseline;gap:8px}
.compare{width:100%;table-layout:fixed;border-collapse:collapse;font-size:12px}
.compare th{padding-bottom:6px;text-align:left;font-weight:500;font-size:11px;color:var(--ek-subtle)}.compare th:first-child{width:34%}
.compare td{border-top:1px solid var(--ek-border);padding:6px 8px 6px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ek-subtle)}
.compare tr.diff td:nth-child(2){color:var(--ek-text)}.compare tr.diff td:nth-child(3){color:var(--ek-text);font-weight:500}
.compare td i{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--ek-hi);margin-right:6px}
.card{padding:14px;border-radius:16px;box-shadow:inset 0 0 0 1px var(--ek-border);display:flex;flex-direction:column;gap:10px}
.q{margin:0;font-size:13px;font-weight:500;line-height:1.35}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.free{display:flex;gap:8px;align-items:center}
.free input{flex:1;min-width:0;height:32px;border-radius:999px;border:1px solid var(--ek-border);background:var(--ek-surface);color:var(--ek-text);padding:0 12px;font:14px var(--ek-font)}
.end{display:flex;justify-content:flex-end;gap:8px}
.confirm .ct{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:500}
.confirm .ct i{width:8px;height:8px;border-radius:50%;background:var(--ek-subtle)}
.confirm[data-state=open] .ct i{background:var(--ek-accent);box-shadow:0 0 0 3px color-mix(in oklab,var(--ek-accent) 25%,transparent)}
.confirm[data-state=approved] .ct i{background:var(--ek-s1)}
.confirm ul{list-style:none;margin:0;padding:0;font-size:13px}
.confirm li{border-top:1px solid var(--ek-border);padding:8px 0}.confirm li:first-child{border-top:0}
.confirm li small{display:block;color:var(--ek-subtle);font-size:12px;margin-top:2px}
.msg.user .fb{justify-content:flex-end}
.confirm:not([data-state=open]) li{color:var(--ek-subtle)}

/* voice stage */
.stage{position:fixed;inset:0;z-index:calc(var(--ek-z) + 1);background:var(--ek-veil);-webkit-backdrop-filter:blur(24px) saturate(1.5);backdrop-filter:blur(24px) saturate(1.5);color:var(--ek-text)}
.center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;padding:0 16px;text-align:center}
.orbwrap{position:relative;display:grid;place-items:center}
.ringfx{position:absolute;pointer-events:none}
.big{border-radius:50%;will-change:transform;filter:drop-shadow(0 12px 28px color-mix(in oklab,var(--evokai-orb-main,${ORB.main}) 38%,transparent))}
.big canvas{display:block;border-radius:50%}
.status{margin-top:12px;min-height:16px;font-size:11px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:var(--ek-subtle)}
.subtitles{min-height:4.5rem;max-width:35rem;margin:0;font:1.5rem/1.35 var(--ek-display)}
.said{color:var(--ek-text)}.tosay{color:var(--ek-subtle);opacity:.45}
.controls{display:flex;align-items:center;gap:14px}
.round{display:grid;place-items:center;width:52px;height:52px;border-radius:999px;background:var(--ek-surface);box-shadow:0 0 0 1px var(--ek-border);transition:background .15s,transform .15s}
.round:hover{background:var(--ek-muted)}
.round.muted{background:color-mix(in oklab,var(--ek-danger) 14%,var(--ek-surface));color:var(--ek-danger)}
.round.finish{background:var(--ek-brand);color:var(--ek-on-brand);box-shadow:none}.round.finish:hover{transform:scale(1.05)}
.switch{display:inline-flex;align-items:center;gap:10px;padding:6px 12px;border-radius:999px;font-size:14px;color:var(--ek-subtle)}
.switch:hover{color:var(--ek-text)}
.track{position:relative;width:36px;height:20px;border-radius:999px;background:color-mix(in oklab,var(--ek-subtle) 35%,transparent);transition:background .2s}
.knob{position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgb(0 0 0/.3);transition:left .2s}
.switch[aria-checked=true] .track{background:var(--ek-brand)}.switch[aria-checked=true] .knob{left:18px}
.cards{position:absolute;right:48px;top:50%;transform:translateY(-50%);width:19rem;display:none;flex-direction:column;gap:12px}
@media (min-width:1024px){.cards{display:flex}}
.vcard{padding:16px;border-radius:16px;background:color-mix(in oklab,var(--ek-surface) 90%,transparent);box-shadow:0 0 0 1px var(--ek-border)}

@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}.launcher:hover,.round.finish:hover{transform:none}}
`

/**
 * The stylesheet of a shadow root, in the cascade layer `evokai`: a skin (unlayered CSS) always wins over it, whatever
 * its selectors. `unstyled` keeps only the tokens and a reset, for a skin that draws everything itself.
 */
export function baseCss(unstyled = false): string {
  return `@layer evokai{${TOKENS}${unstyled ? '' : LOOK}}`
}

/** The default stylesheet (tokens and look). */
export const CSS = baseCss()
